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
    from .eventos import validar_tipo
    with db.transaccion() as c:
        d.update(validar_tipo(db, datos))
        if d["tipo"] == "final" and "registro_abierto" not in datos:
            d["registro_abierto"] = 0  # a la final se entra clasificando; el director puede abrir entrada directa
        cols = ", ".join(d)
        cur = c.execute(f"INSERT INTO torneos ({cols}, creado_en) VALUES ({', '.join('?' * len(d))}, ?)",
                        (*d.values(), ahora()))
        db.auditar(personal_id, "crear_torneo", "torneo", cur.lastrowid, json.dumps(d, ensure_ascii=False))
        return cur.lastrowid


def actualizar_torneo(db, personal_id, torneo_id, datos: dict) -> None:
    d = _limpiar_campos(datos)
    if "modo_clasifica" in datos or "clasifica_valor" in datos:
        from .eventos import validar_clasificacion
        if torneo(db, torneo_id)["tipo"] != "dia1":
            raise ErrorNegocio("Solo los días clasificatorios tienen forma de clasificar")
        d.update(validar_clasificacion(datos.get("modo_clasifica"), datos.get("clasifica_valor")))
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
        abiertas = mesas_abiertas_sala(db)
        maximo = db.ajuste("mesas_sala")
        if abiertas + cantidad > maximo:
            raise ErrorNegocio(f"La sala tiene {maximo} mesas y ya hay {abiertas} abiertas. "
                               "Los jugadores que lleguen quedan en la lista de alternos.", 409)
        # Los números son las mesas físicas de la sala, compartidas entre torneos simultáneos.
        ocupadas = {r["numero"] for r in db.todos(
            "SELECT m.numero FROM mesas m JOIN torneos t ON t.id = m.torneo_id "
            "WHERE m.estado = 'abierta' AND t.estado IN ('programado','en_curso')")}
        libres = [n for n in range(1, maximo + 1) if n not in ocupadas][:cantidad]
        ids = []
        for numero in libres:
            previa = db.uno("SELECT id FROM mesas WHERE torneo_id = ? AND numero = ?", (torneo_id, numero))
            if previa:  # la mesa ya se usó en este torneo y se había cerrado: se reabre
                c.execute("UPDATE mesas SET estado = 'abierta', cerrada_en = NULL, asientos = ? WHERE id = ?",
                          (t["asientos_por_mesa"], previa["id"]))
                ids.append(previa["id"])
            else:
                ids.append(c.execute("INSERT INTO mesas (torneo_id, numero, asientos) VALUES (?,?,?)",
                                     (torneo_id, numero, t["asientos_por_mesa"])).lastrowid)
        db.auditar(personal_id, "abrir_mesas", "torneo", torneo_id, "mesas " + ", ".join(map(str, libres)))
        return ids


def mapa_sala(db) -> list[dict]:
    """Las mesas físicas de la sala y qué torneo usa cada una."""
    uso = {r["numero"]: r for r in db.todos(
        "SELECT m.numero, t.id AS torneo_id, t.nombre FROM mesas m JOIN torneos t ON t.id = m.torneo_id "
        "WHERE m.estado = 'abierta' AND t.estado IN ('programado','en_curso')")}
    return [{"numero": n, "torneo_id": uso[n]["torneo_id"] if n in uso else None,
             "torneo": uso[n]["nombre"] if n in uso else None} for n in range(1, db.ajuste("mesas_sala") + 1)]


def mesas_abiertas_sala(db) -> int:
    """Mesas físicas en uso por todos los torneos activos de la sala."""
    return db.valor("SELECT COUNT(*) FROM mesas m JOIN torneos t ON t.id = m.torneo_id "
                    "WHERE m.estado = 'abierta' AND t.estado IN ('programado','en_curso')")


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


PAGOS_VENTANILLA = {"efectivo": "efectivo", "datafono": "datáfono", "transferencia": "transferencia"}


def _cobrar(db, jugador_id, t, tipo, monto, *, origen, personal_id, pago, descripcion, entrada_id=None,
            referencia=None):
    """Cobra una compra de torneo. ``pago``: 'saldo' (de la caja), 'efectivo' (en ventanilla) o 'ticket'."""
    if pago == "ticket":
        if tipo not in ("inscripcion", "reentrada"):
            raise ErrorNegocio("El ticket solo sirve como entrada al torneo")
        from .eventos import usar_ticket
        usar_ticket(db, jugador_id, t, entrada_id)
        return None
    if origen == "app":
        if not db.ajuste("compras_desde_celular"):
            raise ErrorNegocio("Las compras desde el celular aún no están habilitadas; acérquese a la caja", 403)
        caja.verificar_topes_app(db, jugador_id, t, monto)
    if pago in PAGOS_VENTANILLA:
        caja.registrar_movimiento(db, jugador_id, "deposito", monto, origen="caja", personal_id=personal_id,
                                  medio=pago, descripcion=f"Pago en ventanilla ({PAGOS_VENTANILLA[pago]}"
                                  + (f", comprobante {referencia}" if referencia else "") + f"): {descripcion}")
    return caja.registrar_movimiento(db, jugador_id, tipo, -monto, origen=origen, personal_id=personal_id,
                                     torneo_id=t["id"], medio="ventanilla" if pago in PAGOS_VENTANILLA else "saldo",
                                     descripcion=descripcion)


def _nueva_entrada(db, t, jugador_id, numero, origen) -> int:
    for _ in range(5):
        codigo = secrets.token_hex(5).upper()
        if not db.valor("SELECT 1 FROM entradas WHERE codigo_qr = ?", (codigo,)):
            break
    try:
        return db.conexion().execute(
            "INSERT INTO entradas (torneo_id, jugador_id, numero, estado, fichas, fichas_iniciales, codigo_qr, origen, "
            "creado_en) VALUES (?,?,?,?,?,?,?,?,?)",
            (t["id"], jugador_id, numero, "inscrito", t["stack_inicial"], t["stack_inicial"], codigo, origen,
             ahora())).lastrowid
    except Exception as e:  # sqlite3.IntegrityError
        if "UNIQUE" in str(e):
            raise ErrorNegocio("El jugador ya tiene una inscripción activa en este torneo") from None
        raise


def inscribir(db, torneo_id, jugador_id, *, origen, personal_id=None, pago="saldo", pin=None,
              sentar_ya=False, referencia=None) -> dict:
    """Inscripción única por jugador y torneo. Desde la app exige PIN y paga con el saldo de caja."""
    if origen == "app":
        if pago != "saldo":
            raise ErrorNegocio("Desde el celular solo se paga con el saldo de caja")
    elif pago not in ("saldo", "ticket", *PAGOS_VENTANILLA):
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
        if op["entrada_viva"] and op["entrada_viva"]["estado"] == "clasificado":
            raise ErrorNegocio("El jugador ya clasificó en este día")
        if op["entrada_viva"] and op["entrada_viva"]["estado"] != "reservado":
            raise ErrorNegocio("El jugador ya está inscrito en este torneo")
        if t["tipo"] == "final" and not t["registro_abierto"]:
            raise ErrorNegocio("A la final se entra clasificando en un día clasificatorio (o con entrada directa "
                               "si el director la abre)")
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
                descripcion=f"{nombre} {t['nombre']}", entrada_id=eid, referencia=referencia)
        db.conexion().execute("UPDATE entradas SET pago = ? WHERE id = ?", (pago, eid))
        db.auditar(personal_id, tipo, "entrada", eid, f"{origen}/{pago}")
        if sentar_ya or tipo == "reentrada" and origen == "app":
            sentar_o_esperar(db, eid, personal_id, motivo=tipo)
        else:
            db.notificar(jugador_id, "inscripcion", f"Inscrito: {t['nombre']}",
                         "Muestre su código QR en el ingreso para que le asignen asiento.")
        return entrada(db, eid)


def verificar_pago_recibido(pago, pago_confirmado, referencia) -> str | None:
    """En ventanilla no se registra nada sin que el cajero confirme que recibió el dinero."""
    if pago not in PAGOS_VENTANILLA:
        return None
    if pago_confirmado is not True:
        raise ErrorNegocio("Confirme que recibió el pago antes de registrar")
    referencia = (referencia or "").strip()
    if pago in ("datafono", "transferencia") and len(referencia) < 4:
        raise ErrorNegocio("Escriba el número de comprobante del datáfono o de la transferencia")
    return referencia or None


def inscripcion_manual(db, personal_id, torneo_id, *, tipo_doc, documento, nombre=None, celular=None,
                       pago="efectivo", referencia=None, pago_confirmado=False, documento_verificado=False,
                       sentar_ya=False) -> dict:
    """Inscripción en ventanilla con documento. Si el jugador no tiene caja, se le abre en el momento.

    Nada queda registrado si el cajero no confirma que recibió el pago (o si el saldo no alcanza).
    """
    referencia = verificar_pago_recibido(pago, pago_confirmado, referencia)
    if not documento_verificado:
        raise ErrorNegocio("Verifique el documento original del jugador")
    with db.transaccion():
        j = caja.por_documento(db, tipo_doc, documento)
        nuevo = j is None
        if nuevo:
            if not (nombre or "").strip():
                raise ErrorNegocio("Jugador nuevo: escriba el nombre completo", 409)
            j = caja.jugador(db, caja.abrir_caja(db, personal_id, nombre, documento, celular or None, True,
                                                 tipo_doc=tipo_doc)["jugador"]["id"])
        e = inscribir(db, torneo_id, j["id"], origen="caja", personal_id=personal_id, pago=pago,
                      sentar_ya=sentar_ya, referencia=referencia)
        asiento = db.uno("SELECT m.numero AS mesa, e.asiento, e.espera_desde IS NOT NULL AS alterno FROM entradas e "
                         "LEFT JOIN mesas m ON m.id = e.mesa_id WHERE e.id = ?", (e["id"],))
        return {"entrada": e, "asiento": asiento, "jugador": caja.publico(caja.jugador(db, j["id"])), "nuevo": nuevo}


def consultar_documento(db, torneo_id, tipo_doc, documento) -> dict:
    """Lo que el cajero necesita ver antes de cobrar: quién es, su saldo y si ya está inscrito."""
    t = torneo(db, torneo_id)
    j = caja.por_documento(db, tipo_doc, documento)
    if not j:
        return {"existe": False, "buyin": t["buyin"], "torneo": t["nombre"]}
    op = opciones_jugador(db, t, j["id"])
    from .eventos import ticket_para
    viva = op["entrada_viva"]
    return {"existe": True, "jugador": caja.publico(j), "saldo": caja.saldo(db, j["id"]), "buyin": t["buyin"],
            "torneo": t["nombre"], "ya_inscrito": viva is not None and viva["estado"] != "reservado",
            "puede": op["puede_inscribirse"] or op["puede_reentrar"],
            "es_reentrada": op["puede_reentrar"] and not op["puede_inscribirse"],
            "tiene_ticket": ticket_para(db, j["id"], t) is not None}


# --- Asientos ------------------------------------------------------------------------------------

def _ocupacion(db, torneo_id) -> list[dict]:
    mesas = db.todos("SELECT * FROM mesas WHERE torneo_id = ? AND estado = 'abierta' ORDER BY numero", (torneo_id,))
    por_mesa = {m["id"]: {} for m in mesas}
    for r in db.todos("SELECT * FROM entradas WHERE torneo_id = ? AND estado IN ('sentado','reservado') "
                      "AND mesa_id IS NOT NULL", (torneo_id,)):
        if r["mesa_id"] in por_mesa:
            por_mesa[r["mesa_id"]][r["asiento"]] = r
    for m in mesas:
        m["ocupados"] = por_mesa[m["id"]]
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
            if mesas_abiertas_sala(db) < db.ajuste("mesas_sala"):
                raise ErrorNegocio("No hay asientos libres: abra una mesa nueva", 409)
            raise ErrorNegocio("Sala llena: el jugador queda en la lista de alternos", 409)
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
    if e["estado"] == "eliminado":
        raise ErrorNegocio("Esa entrada ya fue eliminada", 409)
    resultado = sentar_o_esperar(db, e["id"], personal_id, "sentado")
    resultado["nombre"] = db.valor("SELECT nombre FROM jugadores WHERE id = ?", (e["jugador_id"],))
    return resultado


def sentar_o_esperar(db, entrada_id, personal_id=None, motivo="sentado") -> dict:
    """Sienta al jugador o, si la sala está llena, lo pone en la lista de alternos por orden de llegada."""
    with db.transaccion() as c:
        if not db.valor("SELECT 1 FROM entradas WHERE id = ? AND espera_desde IS NOT NULL", (entrada_id,)):
            # Si ya hay alternos esperando, nadie se les adelanta.
            e = entrada(db, entrada_id)
            if not db.valor("SELECT 1 FROM entradas WHERE torneo_id = ? AND estado = 'inscrito' "
                            "AND espera_desde IS NOT NULL", (e["torneo_id"],)):
                try:
                    return sentar(db, entrada_id, personal_id, motivo)
                except ErrorNegocio as err:
                    if err.codigo != 409:  # 409 = no hay asiento libre en las mesas de este torneo
                        raise
            c.execute("UPDATE entradas SET espera_desde = ? WHERE id = ?", (ahora(), entrada_id))
        e = entrada(db, entrada_id)
        turno = db.valor("SELECT COUNT(*) FROM entradas WHERE torneo_id = ? AND estado = 'inscrito' "
                         "AND espera_desde IS NOT NULL AND (espera_desde < ? OR (espera_desde = ? AND id <= ?))",
                         (e["torneo_id"], e["espera_desde"], e["espera_desde"], e["id"]))
        db.notificar(e["jugador_id"], "asiento", f"Lista de alternos: turno {turno}",
                     "Las mesas están llenas. Le avisaremos cuando tenga asiento; permanezca cerca de la sala.")
        return {"entrada_id": e["id"], "jugador_id": e["jugador_id"], "alterno": True, "turno": turno}


def alternos(db, torneo_id) -> list[dict]:
    return db.todos("SELECT e.id, e.codigo_qr, e.espera_desde, j.nombre FROM entradas e "
                    "JOIN jugadores j ON j.id = e.jugador_id WHERE e.torneo_id = ? AND e.estado = 'inscrito' "
                    "AND e.espera_desde IS NOT NULL ORDER BY e.espera_desde, e.id", (torneo_id,))


def sentar_alternos(db, personal_id, torneo_id) -> list[dict]:
    """Sienta a los alternos en orden de llegada mientras haya asientos libres."""
    movimientos = []
    with db.transaccion():
        vencer_reservas(db)
        for a in alternos(db, torneo_id):
            if not any(_libres(m) for m in _ocupacion(db, torneo_id)):
                break
            movimientos.append(sentar(db, a["id"], personal_id, "alterno"))
        if movimientos:
            db.auditar(personal_id, "sentar_alternos", "torneo", torneo_id, f"{len(movimientos)} alterno(s)")
    return movimientos


def sentar_inscritos(db, personal_id, torneo_id) -> list[dict]:
    """Sienta a todos los inscritos sin asiento; los que no quepan pasan a la lista de alternos."""
    with db.transaccion():
        return [sentar_o_esperar(db, e["id"], personal_id) for e in db.todos(
            "SELECT id FROM entradas WHERE torneo_id = ? AND estado = 'inscrito' AND espera_desde IS NULL "
            "ORDER BY id", (torneo_id,))]


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
    en_espera = len(alternos(db, torneo_id))
    libres = sum(len(_libres(m)) for m in mesas)
    if en_espera and libres:
        salida.append({"tipo": "alternos", "texto": f"Hay {libres} asiento(s) libre(s) y {en_espera} alterno(s) "
                                                     "esperando: siéntelos en orden de llegada."})
    libres_sala = db.ajuste("mesas_sala") - mesas_abiertas_sala(db)
    if en_espera > libres and libres_sala > 0:
        faltan = min(libres_sala, math.ceil((en_espera - libres) / mesas[0]["asientos"]))
        salida.append({"tipo": "abrir_mesa", "cantidad": faltan,
                       "texto": f"{en_espera} alterno(s) esperando y la sala tiene {libres_sala} mesa(s) libre(s): "
                                f"abra {faltan} mesa(s)."})
    vivos = sum(conteo) + en_espera
    asientos = mesas[0]["asientos"]
    necesarias = max(1, math.ceil(vivos / asientos))
    en_curso = torneo(db, torneo_id)["estado"] == "en_curso"
    if en_curso and vivos and len(mesas) > necesarias:  # antes de empezar se arman las mesas, no se rompen
        menor = min(mesas, key=lambda m: (len(m["ocupados"]), -m["numero"]))
        salida.append({"tipo": "cerrar_mesa", "mesa_id": menor["id"], "mesa": menor["numero"],
                       "texto": f"Quedan {vivos} jugadores: caben en {necesarias} mesa(s). "
                                f"Cierre la mesa {menor['numero']}."})
    from .eventos import aviso_cierre
    texto = aviso_cierre(db, torneo(db, torneo_id), sum(conteo) + en_espera)
    if texto:
        salida.insert(0, {"tipo": "cerrar_dia", "texto": texto})
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
                 "COALESCE(SUM(addons),0) AS addons, COALESCE(SUM(COALESCE(fichas_iniciales, ?)), 0) AS iniciales "
                 "FROM entradas WHERE torneo_id = ?", (t["stack_inicial"], torneo_id))
    esperado = tot["iniciales"] + tot["recompras"] * t["recompra_fichas"] + tot["addons"] * t["addon_fichas"]
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
    nombres = {r["id"]: r["nombre"] for r in db.todos(
        "SELECT j.id, j.nombre FROM jugadores j JOIN entradas e ON e.jugador_id = j.id "
        "WHERE e.torneo_id = ? AND e.estado IN ('sentado','reservado')", (torneo_id,))}
    mesas = []
    for m in _ocupacion(db, torneo_id):
        asientos = []
        for a in range(1, m["asientos"] + 1):
            e = m["ocupados"].get(a)
            asientos.append({"asiento": a, "entrada_id": e["id"] if e else None, "estado": e["estado"] if e else "libre",
                             "fichas": e["fichas"] if e else None, "nombre": nombres.get(e["jugador_id"]) if e else None})
        mesas.append({"id": m["id"], "numero": m["numero"], "ocupados": len(m["ocupados"]), "asientos": asientos})
    inscritos = db.todos("SELECT e.id, e.codigo_qr, j.nombre FROM entradas e JOIN jugadores j ON j.id = e.jugador_id "
                         "WHERE e.torneo_id = ? AND e.estado = 'inscrito' AND e.espera_desde IS NULL ORDER BY e.id",
                         (torneo_id,))
    vivos = db.valor("SELECT COUNT(*) FROM entradas WHERE torneo_id = ? AND estado IN ('inscrito','sentado','reservado')",
                     (torneo_id,))
    return {"torneo": t, "mesas": mesas, "sin_asiento": inscritos, "alternos": alternos(db, torneo_id), "vivos": vivos,
            "mesas_sala": db.ajuste("mesas_sala"), "mesas_sala_abiertas": mesas_abiertas_sala(db),
            "sala": mapa_sala(db), "clasificados": db.valor("SELECT COUNT(*) FROM entradas WHERE torneo_id = ? AND "
                                                            "estado = 'clasificado'", (torneo_id,)),
            "entradas_totales": db.valor("SELECT COUNT(*) FROM entradas WHERE torneo_id = ?", (torneo_id,)),
            "sugerencias": sugerencias(db, torneo_id)}


def lista_torneos(db, solo_activos=False) -> list[dict]:
    filtro = "WHERE t.estado IN ('programado','en_curso')" if solo_activos else ""
    return db.todos(f"SELECT t.*, p.nombre AS principal, s.nombre AS destino, "
                    f"(SELECT COUNT(*) FROM entradas e WHERE e.torneo_id = t.id) AS entradas, "
                    f"(SELECT COUNT(*) FROM entradas e WHERE e.torneo_id = t.id AND e.estado IN "
                    f"('inscrito','sentado','reservado')) AS vivos FROM torneos t "
                    f"LEFT JOIN torneos p ON p.id = t.principal_id LEFT JOIN torneos s ON s.id = t.satelite_de "
                    f"{filtro} ORDER BY t.fecha DESC, t.id DESC")
