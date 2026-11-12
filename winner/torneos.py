"""Torneos: inscripción única, sentado automático, balanceo, cierre de mesas, fichas y eliminaciones."""
from __future__ import annotations

import json
import math
import random
import secrets
from datetime import timedelta

from . import caja
from . import seguridad as seg
from .db import BaseDatos, ErrorNegocio, ahora, parse_fecha

VIVAS = ("inscrito", "sentado", "reservado")
_azar = random.SystemRandom()

CAMPOS_TORNEO = {
    "nombre": str, "fecha": str, "buyin": int, "stack_inicial": int, "asientos_por_mesa": int,
    "registro_abierto": int, "preinscripcion_movil": int, "reentrada_max": int, "recompra_max": int,
    "recompra_precio": int, "recompra_fichas": int, "recompras_abiertas": int, "addon_precio": int,
    "addon_fichas": int, "addon_abierto": int, "tope_compra": int, "nivel": int,
}


def _fmt(dt) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def torneo(db: BaseDatos, torneo_id: int) -> dict:
    t = db.uno("SELECT * FROM torneos WHERE id = ?", (torneo_id,))
    if not t:
        raise ErrorNegocio("Torneo no encontrado", 404)
    return t


def entrada(db: BaseDatos, entrada_id: int) -> dict:
    e = db.uno("SELECT * FROM entradas WHERE id = ?", (entrada_id,))
    if not e:
        raise ErrorNegocio("Entrada no encontrada", 404)
    return e


def _limpiar_campos(datos: dict) -> dict:
    limpio = {}
    for k, tipo in CAMPOS_TORNEO.items():
        if k not in datos:
            continue
        v = datos[k]
        if tipo is int:
            if v in (None, ""):
                v = None if k == "tope_compra" else 0
            elif isinstance(v, bool):
                v = int(v)
            else:
                v = int(str(v).replace(".", "").replace(",", "").replace("$", "").strip())
            if v is not None and v < 0:
                raise ErrorNegocio(f"Valor inválido para {k}")
        else:
            v = str(v).strip()
        limpio[k] = v
    return limpio


def crear_torneo(db, personal_id, datos: dict) -> int:
    d = _limpiar_campos(datos)
    for requerido in ("nombre", "fecha", "buyin", "stack_inicial"):
        if not d.get(requerido):
            raise ErrorNegocio(f"Falta el campo {requerido}")
    d.setdefault("asientos_por_mesa", 9)
    if not 2 <= d["asientos_por_mesa"] <= 10:
        raise ErrorNegocio("Asientos por mesa entre 2 y 10")
    with db.transaccion() as c:
        cols = ", ".join(d)
        cur = c.execute(f"INSERT INTO torneos ({cols}, creado_en) VALUES ({', '.join('?' * len(d))}, ?)",
                        (*d.values(), ahora()))
        db.auditar(personal_id, "crear_torneo", "torneo", cur.lastrowid, json.dumps(d, ensure_ascii=False))
        return cur.lastrowid


def actualizar_torneo(db, personal_id, torneo_id, datos: dict) -> None:
    d = _limpiar_campos(datos)
    if db.valor("SELECT 1 FROM mesas WHERE torneo_id = ?", (torneo_id,)):
        d.pop("asientos_por_mesa", None)  # ya hay mesas armadas con ese tamaño
    if not d:
        return
    with db.transaccion() as c:
        t = torneo(db, torneo_id)
        if t["estado"] in ("finalizado", "cancelado"):
            raise ErrorNegocio("El torneo ya terminó")
        c.execute(f"UPDATE torneos SET {', '.join(f'{k} = ?' for k in d)} WHERE id = ?", (*d.values(), torneo_id))
        db.auditar(personal_id, "actualizar_torneo", "torneo", torneo_id, json.dumps(d, ensure_ascii=False))


def iniciar_torneo(db, personal_id, torneo_id) -> None:
    with db.transaccion() as c:
        t = torneo(db, torneo_id)
        if t["estado"] != "programado":
            raise ErrorNegocio("El torneo ya fue iniciado")
        if not db.valor("SELECT 1 FROM mesas WHERE torneo_id = ? AND estado = 'abierta'", (torneo_id,)):
            raise ErrorNegocio("Abra al menos una mesa antes de iniciar")
        c.execute("UPDATE torneos SET estado = 'en_curso' WHERE id = ?", (torneo_id,))
        db.auditar(personal_id, "iniciar_torneo", "torneo", torneo_id)


def abrir_mesas(db, personal_id, torneo_id, cantidad=1) -> list[int]:
    cantidad = int(cantidad)
    if not 1 <= cantidad <= 50:
        raise ErrorNegocio("Cantidad de mesas inválida")
    with db.transaccion() as c:
        t = torneo(db, torneo_id)
        if t["estado"] in ("finalizado", "cancelado"):
            raise ErrorNegocio("El torneo ya terminó")
        siguiente = db.valor("SELECT COALESCE(MAX(numero), 0) FROM mesas WHERE torneo_id = ?", (torneo_id,))
        ids = []
        for i in range(cantidad):
            ids.append(c.execute("INSERT INTO mesas (torneo_id, numero, asientos) VALUES (?,?,?)",
                                 (torneo_id, siguiente + i + 1, t["asientos_por_mesa"])).lastrowid)
        db.auditar(personal_id, "abrir_mesas", "torneo", torneo_id, f"{cantidad} mesa(s)")
        return ids


# --- Inscripción -------------------------------------------------------------------------------

def _entradas_jugador(db, torneo_id, jugador_id) -> list[dict]:
    return db.todos("SELECT * FROM entradas WHERE torneo_id = ? AND jugador_id = ? ORDER BY numero",
                    (torneo_id, jugador_id))


def opciones_jugador(db, t: dict, jugador_id: int) -> dict:
    """Qué puede comprar el jugador en este torneo ahora mismo."""
    entradas = _entradas_jugador(db, t["id"], jugador_id)
    viva = next((e for e in entradas if e["estado"] in VIVAS), None)
    activo = t["estado"] in ("programado", "en_curso")
    reentradas_usadas = max(0, len(entradas) - 1)
    return {
        "entrada_viva": viva,
        "puede_inscribirse": activo and bool(t["registro_abierto"]) and not entradas,
        "puede_reentrar": (activo and bool(t["registro_abierto"]) and bool(entradas)
                           and (viva is None or viva["estado"] == "reservado")
                           and reentradas_usadas < t["reentrada_max"]),
        "puede_recomprar": (t["estado"] == "en_curso" and bool(t["recompras_abiertas"]) and viva is not None
                            and viva["estado"] == "reservado" and t["recompra_precio"] > 0
                            and viva["recompras"] < t["recompra_max"]),
        "puede_addon": (t["estado"] == "en_curso" and bool(t["addon_abierto"]) and t["addon_precio"] > 0
                        and viva is not None and viva["estado"] == "sentado" and viva["addons"] == 0),
        "reentradas_usadas": reentradas_usadas,
    }


def _cobrar(db, jugador_id, t, tipo, monto, *, origen, personal_id, pago, descripcion) -> int:
    """Cobra una compra de torneo. ``pago``: 'saldo' (de la caja) o 'efectivo' (en ventanilla)."""
    if origen == "app":
        if not db.ajuste("compras_desde_celular"):
            raise ErrorNegocio("Las compras desde el celular aún no están habilitadas; acérquese a la caja", 403)
        caja.verificar_topes_app(db, jugador_id, t, monto)
    if pago == "efectivo":
        caja.registrar_movimiento(db, jugador_id, "deposito", monto, origen="caja", personal_id=personal_id,
                                  medio="efectivo", descripcion=f"Pago en ventanilla: {descripcion}")
    return caja.registrar_movimiento(db, jugador_id, tipo, -monto, origen=origen, personal_id=personal_id,
                                     torneo_id=t["id"], medio="ventanilla" if pago == "efectivo" else "saldo",
                                     descripcion=descripcion)


def _nueva_entrada(db, t, jugador_id, numero, origen) -> int:
    for _ in range(5):
        codigo = secrets.token_hex(5).upper()
        if not db.valor("SELECT 1 FROM entradas WHERE codigo_qr = ?", (codigo,)):
            break
    try:
        return db.conexion().execute(
            "INSERT INTO entradas (torneo_id, jugador_id, numero, estado, fichas, codigo_qr, origen, creado_en) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (t["id"], jugador_id, numero, "inscrito", t["stack_inicial"], codigo, origen, ahora())).lastrowid
    except Exception as e:  # sqlite3.IntegrityError
        if "UNIQUE" in str(e):
            raise ErrorNegocio("El jugador ya tiene una inscripción activa en este torneo") from None
        raise


def inscribir(db, torneo_id, jugador_id, *, origen, personal_id=None, pago="saldo", pin=None,
              sentar_ya=False) -> dict:
    """Inscripción única por jugador y torneo. Desde la app exige PIN y paga con el saldo de caja."""
    if origen == "app":
        if pago != "saldo":
            raise ErrorNegocio("Desde el celular solo se paga con el saldo de caja")
    elif pago not in ("saldo", "efectivo"):
        raise ErrorNegocio("Medio de pago inválido")
    with db.transaccion():
        vencer_reservas(db)
        t = torneo(db, torneo_id)
        j = caja.jugador(db, jugador_id)
        if j["estado"] != "activo":
            raise ErrorNegocio("La cuenta del jugador está bloqueada")
        if origen == "app":
            seg.confirmar_pin(db, jugador_id, pin)
            if not t["preinscripcion_movil"]:
                raise ErrorNegocio("Este torneo no admite inscripción desde el celular")
        op = opciones_jugador(db, t, jugador_id)
        if op["entrada_viva"] and op["entrada_viva"]["estado"] != "reservado":
            raise ErrorNegocio("El jugador ya está inscrito en este torneo")
        if op["puede_inscribirse"]:
            tipo, numero = "inscripcion", 1
        elif op["puede_reentrar"]:
            tipo, numero = "reentrada", len(_entradas_jugador(db, torneo_id, jugador_id)) + 1
            if op["entrada_viva"]:  # estaba en reserva tras eliminación: se cierra esa entrada
                _resolver_reserva(db, op["entrada_viva"]["id"], "confirmada")
                _marcar_eliminada(db, op["entrada_viva"])
        elif t["estado"] not in ("programado", "en_curso"):
            raise ErrorNegocio("El torneo no está abierto")
        elif not t["registro_abierto"]:
            raise ErrorNegocio("La inscripción de este torneo está cerrada")
        else:
            raise ErrorNegocio("Ya usó todas las re-entradas permitidas")
        nombre = "Inscripción" if tipo == "inscripcion" else f"Re-entrada #{numero - 1}"
        eid = _nueva_entrada(db, t, jugador_id, numero, origen)
        _cobrar(db, jugador_id, t, tipo, t["buyin"], origen=origen, personal_id=personal_id, pago=pago,
                descripcion=f"{nombre} {t['nombre']}")
        db.auditar(personal_id, tipo, "entrada", eid, f"{origen}/{pago}")
        if sentar_ya or tipo == "reentrada" and origen == "app":
            sentar(db, eid, personal_id, motivo=tipo)
        else:
            db.notificar(jugador_id, "inscripcion", f"Inscrito: {t['nombre']}",
                         "Muestre su código QR en el ingreso para que le asignen asiento.")
        return entrada(db, eid)


# --- Asientos ------------------------------------------------------------------------------------

def _ocupacion(db, torneo_id) -> list[dict]:
    mesas = db.todos("SELECT * FROM mesas WHERE torneo_id = ? AND estado = 'abierta' ORDER BY numero", (torneo_id,))
    for m in mesas:
        m["ocupados"] = {r["asiento"]: r for r in db.todos(
            "SELECT * FROM entradas WHERE mesa_id = ? AND estado IN ('sentado','reservado')", (m["id"],))}
    return mesas


def _libres(m) -> list[int]:
    return [a for a in range(1, m["asientos"] + 1) if a not in m["ocupados"]]


def _mover(db, e, mesa, asiento, motivo, personal_id) -> dict:
    db.conexion().execute("UPDATE entradas SET mesa_id = ?, asiento = ?, estado = CASE WHEN estado = 'inscrito' "
                          "THEN 'sentado' ELSE estado END WHERE id = ?", (mesa["id"], asiento, e["id"]))
    de_mesa = db.valor("SELECT numero FROM mesas WHERE id = ?", (e["mesa_id"],)) if e["mesa_id"] else None
    db.conexion().execute(
        "INSERT INTO cambios_asiento (torneo_id, entrada_id, de_mesa, de_asiento, a_mesa, a_asiento, motivo, "
        "personal_id, fecha) VALUES (?,?,?,?,?,?,?,?,?)",
        (e["torneo_id"], e["id"], de_mesa, e["asiento"] if e["mesa_id"] else None, mesa["numero"], asiento,
         motivo, personal_id, ahora()))
    textos = {"balanceo": "Cambio de mesa por balanceo", "cierre_mesa": "Cambio de mesa: su mesa se cerró"}
    db.notificar(e["jugador_id"], "asiento", f"Mesa {mesa['numero']} · Asiento {asiento}",
                 textos.get(motivo, "Asiento asignado") + f": mesa {mesa['numero']}, asiento {asiento}.")
    mesa["ocupados"][asiento] = e
    return {"entrada_id": e["id"], "jugador_id": e["jugador_id"], "de_mesa": de_mesa,
            "nombre": db.valor("SELECT nombre FROM jugadores WHERE id = ?", (e["jugador_id"],)),
            "de_asiento": e["asiento"] if e["mesa_id"] else None, "a_mesa": mesa["numero"], "a_asiento": asiento}


def sentar(db, entrada_id, personal_id=None, motivo="sentado") -> dict:
    """Asigna un asiento al azar en la mesa con menos jugadores."""
    with db.transaccion():
        e = entrada(db, entrada_id)
        if e["estado"] != "inscrito":
            raise ErrorNegocio("Esa entrada ya está sentada o eliminada" if e["estado"] != "reservado"
                               else "La entrada está en reserva de recompra")
        t = torneo(db, e["torneo_id"])
        if t["estado"] in ("finalizado", "cancelado"):
            raise ErrorNegocio("El torneo ya terminó")
        candidatas = [m for m in _ocupacion(db, e["torneo_id"]) if _libres(m)]
        if not candidatas:
            raise ErrorNegocio("No hay asientos libres: abra una mesa nueva", 409)
        menor = min(len(m["ocupados"]) for m in candidatas)
        mesa = _azar.choice([m for m in candidatas if len(m["ocupados"]) == menor])
        return _mover(db, e, mesa, _azar.choice(_libres(mesa)), motivo, personal_id)


def sentar_por_qr(db, codigo, personal_id) -> dict:
    codigo = (codigo or "").strip().upper()
    if codigo.startswith("WINNER:"):
        codigo = codigo.split(":", 1)[1]
    e = db.uno("SELECT * FROM entradas WHERE codigo_qr = ?", (codigo,))
    if not e:
        raise ErrorNegocio("Código QR no válido", 404)
    if e["estado"] in ("sentado", "reservado"):
        m = db.uno("SELECT numero FROM mesas WHERE id = ?", (e["mesa_id"],))
        raise ErrorNegocio(f"Ya está sentado en mesa {m['numero']}, asiento {e['asiento']}", 409)
    resultado = sentar(db, e["id"], personal_id, "sentado")
    resultado["nombre"] = db.valor("SELECT nombre FROM jugadores WHERE id = ?", (e["jugador_id"],))
    return resultado


def sentar_inscritos(db, personal_id, torneo_id) -> list[dict]:
    with db.transaccion():
        return [sentar(db, e["id"], personal_id) for e in db.todos(
            "SELECT id FROM entradas WHERE torneo_id = ? AND estado = 'inscrito' ORDER BY id", (torneo_id,))]


def balancear(db, personal_id, torneo_id) -> list[dict]:
    """Mueve jugadores de la mesa más llena a la más vacía hasta que la diferencia sea de 1 o menos."""
    movimientos = []
    with db.transaccion():
        vencer_reservas(db)
        mesas = _ocupacion(db, torneo_id)
        while len(mesas) > 1:
            llena = max(mesas, key=lambda m: len(m["ocupados"]))
            vacia = min(mesas, key=lambda m: len(m["ocupados"]))
            if len(llena["ocupados"]) - len(vacia["ocupados"]) <= 1 or not _libres(vacia):
                break
            sentados = [e for e in llena["ocupados"].values() if e["estado"] == "sentado"]
            if not sentados:
                break
            e = _azar.choice(sentados)
            del llena["ocupados"][e["asiento"]]
            movimientos.append(_mover(db, e, vacia, _azar.choice(_libres(vacia)), "balanceo", personal_id))
        if movimientos:
            db.auditar(personal_id, "balancear", "torneo", torneo_id, f"{len(movimientos)} cambio(s)")
    return movimientos


def cerrar_mesa(db, personal_id, torneo_id, mesa_id) -> list[dict]:
    """Cierra (rompe) una mesa y reparte a sus jugadores en las mesas con menos jugadores."""
    movimientos = []
    with db.transaccion() as c:
        vencer_reservas(db)
        mesas = _ocupacion(db, torneo_id)
        cerrar = next((m for m in mesas if m["id"] == int(mesa_id)), None)
        if not cerrar:
            raise ErrorNegocio("Mesa no encontrada o ya cerrada", 404)
        otras = [m for m in mesas if m is not cerrar]
        jugadores = list(cerrar["ocupados"].values())
        if jugadores and sum(len(_libres(m)) for m in otras) < len(jugadores):
            raise ErrorNegocio("No hay asientos libres suficientes en las otras mesas", 409)
        _azar.shuffle(jugadores)
        for e in jugadores:
            destino = min((m for m in otras if _libres(m)), key=lambda m: len(m["ocupados"]))
            movimientos.append(_mover(db, e, destino, _azar.choice(_libres(destino)), "cierre_mesa", personal_id))
        c.execute("UPDATE mesas SET estado = 'cerrada', cerrada_en = ? WHERE id = ?", (ahora(), cerrar["id"]))
        db.auditar(personal_id, "cerrar_mesa", "mesa", cerrar["id"],
                   f"mesa {cerrar['numero']}, {len(jugadores)} jugador(es) reasignados")
    return movimientos


def sugerencias(db, torneo_id) -> list[dict]:
    vencer_reservas(db)
    mesas = _ocupacion(db, torneo_id)
    if not mesas:
        return []
    salida = []
    conteo = [len(m["ocupados"]) for m in mesas]
    vivos = sum(conteo)
    asientos = mesas[0]["asientos"]
    necesarias = max(1, math.ceil(vivos / asientos))
    if len(mesas) > necesarias:
        menor = min(mesas, key=lambda m: (len(m["ocupados"]), -m["numero"]))
        salida.append({"tipo": "cerrar_mesa", "mesa_id": menor["id"], "mesa": menor["numero"],
                       "texto": f"Quedan {vivos} jugadores: caben en {necesarias} mesa(s). "
                                f"Cierre la mesa {menor['numero']}."})
    if max(conteo) - min(conteo) > 1:
        llena = max(mesas, key=lambda m: len(m["ocupados"]))
        vacia = min(mesas, key=lambda m: len(m["ocupados"]))
        salida.append({"tipo": "balancear",
                       "texto": f"Mesa {llena['numero']} tiene {len(llena['ocupados'])} y mesa {vacia['numero']} "
                                f"tiene {len(vacia['ocupados'])}: balancee."})
    return salida


# --- Eliminación, reserva de asiento y recompras ---------------------------------------------------

def _marcar_eliminada(db, e) -> None:
    db.conexion().execute("UPDATE entradas SET estado = 'eliminado', fichas = 0, "
                          "eliminado_en = COALESCE(eliminado_en, ?) WHERE id = ?", (ahora(), e["id"]))


def _resolver_reserva(db, entrada_id, estado) -> None:
    db.conexion().execute("UPDATE reservas SET estado = ?, resuelta_en = ? WHERE entrada_id = ? AND estado = 'activa'",
                          (estado, ahora(), entrada_id))


def eliminar(db, personal_id, entrada_id) -> dict:
    """Confirmada por crupier/floor. Si puede recomprar o re-entrar, guarda el asiento unos minutos."""
    with db.transaccion() as c:
        vencer_reservas(db)
        e = entrada(db, entrada_id)
        if e["estado"] != "sentado":
            raise ErrorNegocio("Solo se puede eliminar a un jugador sentado")
        t = torneo(db, e["torneo_id"])
        if t["estado"] != "en_curso":
            raise ErrorNegocio("El torneo no está en curso")
        c.execute("UPDATE entradas SET estado = 'reservado', fichas = 0, eliminado_en = ?, eliminado_por = ? "
                  "WHERE id = ?", (ahora(), personal_id, entrada_id))
        op = opciones_jugador(db, t, e["jugador_id"])
        opciones = [o for o, ok in (("recompra", op["puede_recomprar"]), ("reentrada", op["puede_reentrar"])) if ok]
        db.auditar(personal_id, "eliminar", "entrada", entrada_id)
        if opciones:  # sin app, la recompra se hace en ventanilla dentro del mismo plazo
            minutos = db.ajuste("minutos_reserva_asiento")
            vence = _fmt(parse_fecha(ahora()) + timedelta(minutes=minutos))
            c.execute("INSERT INTO reservas (entrada_id, jugador_id, torneo_id, opciones, vence_en, creada_en) "
                      "VALUES (?,?,?,?,?,?)", (entrada_id, e["jugador_id"], t["id"], ",".join(opciones), vence, ahora()))
            nombres = {"recompra": f"recompra ({caja.pesos(t['recompra_precio'])})",
                       "reentrada": f"re-entrada ({caja.pesos(t['buyin'])})"}
            db.notificar(e["jugador_id"], "eliminacion", f"Eliminado en {t['nombre']}",
                         f"Su asiento queda reservado {minutos} min. Puede hacer "
                         + " o ".join(nombres[o] for o in opciones) + " desde la app con su PIN.")
            return {"estado": "reservado", "opciones": opciones, "vence_en": vence}
        _marcar_eliminada(db, entrada(db, entrada_id))
        db.notificar(e["jugador_id"], "eliminacion", f"Eliminado en {t['nombre']}", "Gracias por jugar.")
        return {"estado": "eliminado", "opciones": opciones}


def vencer_reservas(db) -> int:
    """Libera los asientos cuya reserva venció. Se llama en cada consulta (no hay procesos de fondo)."""
    with db.transaccion():
        vencidas = db.todos("SELECT * FROM reservas WHERE estado = 'activa' AND vence_en < ?", (ahora(),))
        for r in vencidas:
            _resolver_reserva(db, r["entrada_id"], "vencida")
            e = entrada(db, r["entrada_id"])
            if e["estado"] == "reservado":
                _marcar_eliminada(db, e)
                db.notificar(r["jugador_id"], "eliminacion", "Reserva vencida",
                             "Se venció el tiempo para recomprar y su asiento fue liberado.")
        return len(vencidas)


def reserva_activa(db, jugador_id) -> list[dict]:
    vencer_reservas(db)
    return db.todos("SELECT r.*, t.nombre AS torneo, t.recompra_precio, t.buyin, m.numero AS mesa, e.asiento "
                    "FROM reservas r JOIN torneos t ON t.id = r.torneo_id JOIN entradas e ON e.id = r.entrada_id "
                    "LEFT JOIN mesas m ON m.id = e.mesa_id WHERE r.jugador_id = ? AND r.estado = 'activa'",
                    (jugador_id,))


def recomprar(db, entrada_id, *, origen, personal_id=None, pago="saldo", pin=None) -> dict:
    """Recompra: misma entrada, mismo asiento, fichas de recompra."""
    with db.transaccion() as c:
        vencer_reservas(db)
        e = entrada(db, entrada_id)
        if origen == "app":
            seg.confirmar_pin(db, e["jugador_id"], pin)
        t = torneo(db, e["torneo_id"])
        op = opciones_jugador(db, t, e["jugador_id"])
        if not op["puede_recomprar"] or op["entrada_viva"]["id"] != e["id"]:
            raise ErrorNegocio("La recompra no está disponible (venció la reserva o se alcanzó el límite)")
        _cobrar(db, e["jugador_id"], t, "recompra", t["recompra_precio"], origen=origen, personal_id=personal_id,
                pago=pago, descripcion=f"Recompra #{e['recompras'] + 1} {t['nombre']}")
        c.execute("UPDATE entradas SET estado = 'sentado', fichas = ?, recompras = recompras + 1, "
                  "eliminado_en = NULL, eliminado_por = NULL WHERE id = ?", (t["recompra_fichas"], e["id"]))
        _resolver_reserva(db, e["id"], "confirmada")
        c.execute("INSERT INTO cambios_asiento (torneo_id, entrada_id, de_mesa, de_asiento, a_mesa, a_asiento, "
                  "motivo, personal_id, fecha) SELECT ?, ?, m.numero, ?, m.numero, ?, 'recompra', ?, ? "
                  "FROM mesas m WHERE m.id = ?", (t["id"], e["id"], e["asiento"], e["asiento"], personal_id,
                                                   ahora(), e["mesa_id"]))
        db.auditar(personal_id, "recompra", "entrada", e["id"], origen)
        mesa = db.valor("SELECT numero FROM mesas WHERE id = ?", (e["mesa_id"],))
        db.notificar(e["jugador_id"], "asiento", "Recompra confirmada",
                     f"Vuelva a su asiento: mesa {mesa}, asiento {e['asiento']}.")
        return entrada(db, e["id"])


def reentrar(db, entrada_id, *, origen, personal_id=None, pago="saldo", pin=None) -> dict:
    """Re-entrada desde la reserva: entrada nueva con stack inicial y asiento nuevo."""
    e = entrada(db, entrada_id)
    return inscribir(db, e["torneo_id"], e["jugador_id"], origen=origen, personal_id=personal_id,
                     pago=pago, pin=pin, sentar_ya=True)


def rechazar_reserva(db, jugador_id, entrada_id) -> None:
    with db.transaccion():
        e = entrada(db, entrada_id)
        if e["jugador_id"] != jugador_id or e["estado"] != "reservado":
            raise ErrorNegocio("No hay reserva activa")
        _resolver_reserva(db, entrada_id, "rechazada")
        _marcar_eliminada(db, e)


def addon(db, entrada_id, *, origen, personal_id=None, pago="saldo", pin=None) -> dict:
    with db.transaccion() as c:
        e = entrada(db, entrada_id)
        if origen == "app":
            seg.confirmar_pin(db, e["jugador_id"], pin)
        t = torneo(db, e["torneo_id"])
        op = opciones_jugador(db, t, e["jugador_id"])
        if not op["puede_addon"] or op["entrada_viva"]["id"] != e["id"]:
            raise ErrorNegocio("El add-on no está disponible")
        _cobrar(db, e["jugador_id"], t, "addon", t["addon_precio"], origen=origen, personal_id=personal_id,
                pago=pago, descripcion=f"Add-on {t['nombre']}")
        c.execute("UPDATE entradas SET fichas = fichas + ?, addons = addons + 1 WHERE id = ?",
                  (t["addon_fichas"], e["id"]))
        db.auditar(personal_id, "addon", "entrada", e["id"], origen)
        return entrada(db, e["id"])


# --- Fichas --------------------------------------------------------------------------------------

def registrar_conteo(db, personal_id, entrada_id, fichas) -> None:
    fichas = int(fichas)
    if fichas < 0:
        raise ErrorNegocio("Fichas inválidas")
    with db.transaccion() as c:
        e = entrada(db, entrada_id)
        if e["estado"] != "sentado":
            raise ErrorNegocio("Solo se cuentan fichas de jugadores sentados")
        t = torneo(db, e["torneo_id"])
        c.execute("INSERT INTO conteos (torneo_id, entrada_id, fichas, fichas_anteriores, nivel, personal_id, fecha) "
                  "VALUES (?,?,?,?,?,?,?)", (t["id"], e["id"], fichas, e["fichas"], t["nivel"], personal_id, ahora()))
        c.execute("UPDATE entradas SET fichas = ? WHERE id = ?", (fichas, e["id"]))


def resumen_fichas(db, torneo_id) -> dict:
    t = torneo(db, torneo_id)
    tot = db.uno("SELECT COUNT(*) AS entradas, COALESCE(SUM(recompras),0) AS recompras, "
                 "COALESCE(SUM(addons),0) AS addons FROM entradas WHERE torneo_id = ?", (torneo_id,))
    esperado = (tot["entradas"] * t["stack_inicial"] + tot["recompras"] * t["recompra_fichas"]
                + tot["addons"] * t["addon_fichas"])
    vivos = db.todos("SELECT e.id, e.fichas, e.estado, e.asiento, m.numero AS mesa, j.nombre, "
                     "(SELECT MAX(fecha) FROM conteos WHERE entrada_id = e.id) AS ultimo_conteo "
                     "FROM entradas e JOIN jugadores j ON j.id = e.jugador_id LEFT JOIN mesas m ON m.id = e.mesa_id "
                     "WHERE e.torneo_id = ? AND e.estado IN ('inscrito','sentado','reservado') "
                     "ORDER BY e.fichas DESC", (torneo_id,))
    en_juego = sum(v["fichas"] for v in vivos)
    return {"esperado": esperado, "en_juego": en_juego, "diferencia": en_juego - esperado,
            "jugadores": len(vivos), "promedio": en_juego // len(vivos) if vivos else 0,
            "lideres": vivos[:10], **tot}


def historial_conteos(db, torneo_id, limite=200) -> list[dict]:
    return db.todos("SELECT c.*, j.nombre, p.nombre AS registrado_por FROM conteos c "
                    "JOIN entradas e ON e.id = c.entrada_id JOIN jugadores j ON j.id = e.jugador_id "
                    "LEFT JOIN personal p ON p.id = c.personal_id WHERE c.torneo_id = ? ORDER BY c.id DESC LIMIT ?",
                    (torneo_id, limite))


def historial_asientos(db, torneo_id, limite=200) -> list[dict]:
    return db.todos("SELECT c.*, j.nombre, p.nombre AS hecho_por FROM cambios_asiento c "
                    "JOIN entradas e ON e.id = c.entrada_id JOIN jugadores j ON j.id = e.jugador_id "
                    "LEFT JOIN personal p ON p.id = c.personal_id WHERE c.torneo_id = ? ORDER BY c.id DESC LIMIT ?",
                    (torneo_id, limite))


def tablero(db, torneo_id) -> dict:
    """Ocupación en tiempo real: mesas con sus asientos."""
    vencer_reservas(db)
    t = torneo(db, torneo_id)
    mesas = []
    for m in _ocupacion(db, torneo_id):
        asientos = []
        for a in range(1, m["asientos"] + 1):
            e = m["ocupados"].get(a)
            asientos.append({"asiento": a, "entrada_id": e["id"] if e else None, "estado": e["estado"] if e else "libre",
                             "fichas": e["fichas"] if e else None,
                             "nombre": db.valor("SELECT nombre FROM jugadores WHERE id = ?", (e["jugador_id"],)) if e else None})
        mesas.append({"id": m["id"], "numero": m["numero"], "ocupados": len(m["ocupados"]), "asientos": asientos})
    inscritos = db.todos("SELECT e.id, e.codigo_qr, j.nombre FROM entradas e JOIN jugadores j ON j.id = e.jugador_id "
                         "WHERE e.torneo_id = ? AND e.estado = 'inscrito' ORDER BY e.id", (torneo_id,))
    vivos = db.valor("SELECT COUNT(*) FROM entradas WHERE torneo_id = ? AND estado IN ('inscrito','sentado','reservado')",
                     (torneo_id,))
    return {"torneo": t, "mesas": mesas, "sin_asiento": inscritos, "vivos": vivos,
            "entradas_totales": db.valor("SELECT COUNT(*) FROM entradas WHERE torneo_id = ?", (torneo_id,)),
            "sugerencias": sugerencias(db, torneo_id)}


def lista_torneos(db, solo_activos=False) -> list[dict]:
    filtro = "WHERE estado IN ('programado','en_curso')" if solo_activos else ""
    return db.todos(f"SELECT t.*, (SELECT COUNT(*) FROM entradas e WHERE e.torneo_id = t.id) AS entradas, "
                    f"(SELECT COUNT(*) FROM entradas e WHERE e.torneo_id = t.id AND e.estado IN "
                    f"('inscrito','sentado','reservado')) AS vivos FROM torneos t {filtro} ORDER BY t.fecha DESC, t.id DESC")
