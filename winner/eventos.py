"""Eventos de varios días, satélites y tickets.

- Un evento grande es un torneo ``final`` (Día 2) con varios días clasificatorios ``dia1`` (Día 1A, 1B…).
- Al cerrar un día, los jugadores vivos guardan sus fichas y quedan clasificados al Día 2.
  Si un jugador clasifica en más de un día, vale su stack mayor.
- Al empezar el Día 2 se traen los clasificados con sus fichas y se sortean los asientos.
- Un ``satelite`` premia tickets: cada ticket vale una entrada al torneo destino o a cualquiera de sus días.
"""
from __future__ import annotations

import math

from . import caja
from .db import ErrorNegocio, ahora
from .torneos import torneo, vencer_reservas

TIPOS = {"normal": "Torneo", "dia1": "Día clasificatorio", "final": "Final (Día 2)", "satelite": "Satélite"}


def validar_tipo(db, datos: dict) -> dict:
    """Revisa la relación del torneo con su final o destino según el tipo."""
    tipo = datos.get("tipo") or "normal"
    if tipo not in TIPOS:
        raise ErrorNegocio("Tipo de torneo inválido")
    limpio = {"tipo": tipo, "principal_id": None, "satelite_de": None, "modo_clasifica": None, "clasifica_valor": None}
    if tipo == "dia1":
        final = torneo(db, int(datos.get("principal_id") or 0)) if datos.get("principal_id") else None
        if not final or final["tipo"] != "final":
            raise ErrorNegocio("Un día clasificatorio debe pertenecer a un torneo final (Día 2)")
        if final["estado"] != "programado":
            raise ErrorNegocio("La final ya empezó: no se pueden agregar días clasificatorios")
        limpio["principal_id"] = final["id"]
        limpio.update(validar_clasificacion(datos.get("modo_clasifica"), datos.get("clasifica_valor")))
    elif tipo == "satelite":
        destino = torneo(db, int(datos.get("satelite_de") or 0)) if datos.get("satelite_de") else None
        if not destino or destino["tipo"] not in ("final", "normal"):
            raise ErrorNegocio("Elija el torneo al que da cupos el satélite")
        if destino["estado"] in ("finalizado", "cancelado"):
            raise ErrorNegocio("El torneo destino ya terminó")
        limpio["satelite_de"] = destino["id"]
    return limpio


def dias_de(db, final_id) -> list[dict]:
    return db.todos("SELECT t.*, (SELECT COUNT(*) FROM entradas e WHERE e.torneo_id = t.id) AS entradas, "
                    "(SELECT COUNT(*) FROM entradas e WHERE e.torneo_id = t.id AND e.estado = 'clasificado') AS clasificados "
                    "FROM torneos t WHERE t.principal_id = ? ORDER BY t.fecha, t.id", (final_id,))


MODOS_CLASIFICA = {
    "nivel": "Al terminar el nivel",
    "porcentaje": "Hasta quedar este % de las entradas",
    "jugadores": "Hasta quedar este número de jugadores",
}


def validar_clasificacion(modo, valor) -> dict:
    """El administrador decide en cada día clasificatorio cómo se define quién clasifica."""
    if modo in (None, ""):
        raise ErrorNegocio("Elija cómo se decide quién clasifica en este día")
    if modo not in MODOS_CLASIFICA:
        raise ErrorNegocio("Forma de clasificar inválida")
    try:
        valor = int(str(valor).strip())
    except (TypeError, ValueError):
        raise ErrorNegocio("Escriba el valor (nivel, porcentaje o número de jugadores)") from None
    if valor < 1 or (modo == "porcentaje" and valor > 100):
        raise ErrorNegocio("Valor inválido para la forma de clasificar")
    return {"modo_clasifica": modo, "clasifica_valor": valor}


def texto_clasificacion(t: dict) -> str:
    if t.get("tipo") != "dia1" or not t.get("modo_clasifica"):
        return ""
    v = t["clasifica_valor"]
    return {"nivel": f"El día termina al acabar el nivel {v}",
            "porcentaje": f"Clasifica el {v} % de las entradas",
            "jugadores": f"Clasifican {v} jugadores"}[t["modo_clasifica"]]


def objetivo_clasificados(db, t: dict) -> int | None:
    """Cuántos clasifican si el día se juega hasta un porcentaje o un número fijo de jugadores."""
    if t["tipo"] != "dia1" or t.get("modo_clasifica") not in ("porcentaje", "jugadores"):
        return None
    if t["modo_clasifica"] == "jugadores":
        return t["clasifica_valor"]
    entradas = db.valor("SELECT COUNT(*) FROM entradas WHERE torneo_id = ?", (t["id"],))
    return max(1, math.ceil(entradas * t["clasifica_valor"] / 100))


def aviso_cierre(db, t: dict, vivos: int) -> str | None:
    """Texto del aviso «cierre el día» cuando se cumple la regla que fijó el administrador."""
    if t["tipo"] != "dia1" or t["estado"] != "en_curso" or not t.get("modo_clasifica"):
        return None
    if t["modo_clasifica"] == "nivel":
        if t["nivel"] >= t["clasifica_valor"]:
            return (f"Nivel {t['nivel']}: es el último nivel del día. Al terminarlo, cierre el día "
                    f"({vivos} jugadores clasificarían).")
        return None
    objetivo = objetivo_clasificados(db, t)
    if vivos <= objetivo:
        regla = f"{t['clasifica_valor']} % de las entradas" if t["modo_clasifica"] == "porcentaje" else "el número fijado"
        return f"Quedan {vivos} jugadores: se llegó a {regla} ({objetivo}). Cierre el día."
    return None


# --- Días clasificatorios ----------------------------------------------------------------------

def clasificados_vigentes(db, final_id) -> list[dict]:
    """Un registro por jugador: el de su stack mayor."""
    return db.todos(
        "SELECT c.*, j.nombre, j.numero_caja, t.nombre AS dia FROM clasificados c "
        "JOIN jugadores j ON j.id = c.jugador_id JOIN torneos t ON t.id = c.torneo_origen "
        "WHERE c.principal_id = ? AND c.id = (SELECT x.id FROM clasificados x WHERE x.principal_id = c.principal_id "
        "AND x.jugador_id = c.jugador_id ORDER BY x.fichas DESC, x.id LIMIT 1) ORDER BY c.fichas DESC", (final_id,))


def cerrar_dia(db, personal_id, torneo_id) -> dict:
    """Cierra un día clasificatorio: los vivos guardan sus fichas y clasifican a la final."""
    with db.transaccion() as c:
        vencer_reservas(db)
        t = torneo(db, torneo_id)
        if t["tipo"] != "dia1":
            raise ErrorNegocio("Solo se cierra así un día clasificatorio")
        if t["estado"] != "en_curso":
            raise ErrorNegocio("El día no está en curso")
        if db.valor("SELECT COUNT(*) FROM entradas WHERE torneo_id = ? AND estado = 'reservado'", (torneo_id,)):
            raise ErrorNegocio("Hay jugadores decidiendo si recompran; espere a que se resuelva")
        sin_asiento = db.valor("SELECT COUNT(*) FROM entradas WHERE torneo_id = ? AND estado = 'inscrito'", (torneo_id,))
        if sin_asiento:
            raise ErrorNegocio(f"Hay {sin_asiento} inscrito(s) sin asiento (alternos o ausentes): siéntelos o "
                               "resuelva su inscripción antes de cerrar el día")
        vivos = db.todos("SELECT * FROM entradas WHERE torneo_id = ? AND estado = 'sentado'", (torneo_id,))
        if not vivos:
            raise ErrorNegocio("No hay jugadores vivos para clasificar")
        final = torneo(db, t["principal_id"])
        for e in vivos:
            previo = db.valor("SELECT MAX(fichas) FROM clasificados WHERE principal_id = ? AND jugador_id = ?",
                              (final["id"], e["jugador_id"]))
            c.execute("INSERT INTO clasificados (principal_id, jugador_id, torneo_origen, entrada_id, fichas, fecha) "
                      "VALUES (?,?,?,?,?,?)", (final["id"], e["jugador_id"], torneo_id, e["id"], e["fichas"], ahora()))
            c.execute("UPDATE entradas SET estado = 'clasificado' WHERE id = ?", (e["id"],))
            if previo is not None and previo >= e["fichas"]:
                texto = (f"Terminó {t['nombre']} con {caja.pesos(e['fichas'])[1:]} fichas. Para {final['nombre']} "
                         f"se conserva su stack mayor de {caja.pesos(previo)[1:]}.")
            else:
                texto = f"Clasificó a {final['nombre']} con {caja.pesos(e['fichas'])[1:]} fichas."
            db.notificar(e["jugador_id"], "clasificacion", "¡Clasificado!", texto)
        c.execute("UPDATE torneos SET estado = 'finalizado', finalizado_en = ?, registro_abierto = 0, "
                  "recompras_abiertas = 0, addon_abierto = 0 WHERE id = ?", (ahora(), torneo_id))
        c.execute("UPDATE mesas SET estado = 'cerrada', cerrada_en = ? WHERE torneo_id = ? AND estado = 'abierta'",
                  (ahora(), torneo_id))
        db.auditar(personal_id, "cerrar_dia", "torneo", torneo_id, f"{len(vivos)} clasificados a #{final['id']}")
    return {"clasificados": len(vivos), "final_id": final["id"]}


def traer_clasificados(db, personal_id, final_id) -> int:
    """Inscribe en la final a cada clasificado con su stack mayor (sin cobro). Luego se sortean asientos."""
    with db.transaccion() as c:
        final = torneo(db, final_id)
        if final["tipo"] != "final":
            raise ErrorNegocio("Este torneo no es una final de varios días")
        if final["estado"] in ("finalizado", "cancelado"):
            raise ErrorNegocio("La final ya terminó")
        abiertos = [d["nombre"] for d in dias_de(db, final_id) if d["estado"] in ("programado", "en_curso")]
        if abiertos:
            raise ErrorNegocio("Faltan días por cerrar: " + ", ".join(abiertos))
        nuevos = 0
        from .torneos import _nueva_entrada
        for cl in clasificados_vigentes(db, final_id):
            if db.valor("SELECT 1 FROM entradas WHERE torneo_id = ? AND jugador_id = ?", (final_id, cl["jugador_id"])):
                continue
            eid = _nueva_entrada(db, final, cl["jugador_id"], 1, "clasificado")
            c.execute("UPDATE entradas SET fichas = ?, fichas_iniciales = ?, pago = 'clasificado' WHERE id = ?",
                      (cl["fichas"], cl["fichas"], eid))
            db.notificar(cl["jugador_id"], "inscripcion", f"Inscrito en {final['nombre']}",
                         f"Su stack: {caja.pesos(cl['fichas'])[1:]} fichas. Muestre su QR en el ingreso.")
            nuevos += 1
        db.auditar(personal_id, "traer_clasificados", "torneo", final_id, f"{nuevos} clasificados")
        return nuevos


# --- Satélites y tickets -------------------------------------------------------------------------

def tickets_jugador(db, jugador_id, estado="disponible") -> list[dict]:
    return db.todos("SELECT k.*, t.nombre AS destino, o.nombre AS origen, "
                    "(SELECT COUNT(*) FROM traspasos_ticket x WHERE x.ticket_id = k.id) AS traspasos FROM tickets k "
                    "JOIN torneos t ON t.id = k.destino_id LEFT JOIN torneos o ON o.id = k.origen_torneo "
                    "WHERE k.jugador_id = ? AND (? IS NULL OR k.estado = ?) ORDER BY k.id DESC",
                    (jugador_id, estado, estado))


def ticket_para(db, jugador_id, t: dict) -> dict | None:
    """Ticket disponible del jugador que sirve para entrar a ``t`` (destino directo o su final)."""
    destinos = [t["id"]] + ([t["principal_id"]] if t.get("principal_id") else [])
    return db.uno(f"SELECT * FROM tickets WHERE jugador_id = ? AND estado = 'disponible' AND destino_id IN "
                  f"({','.join('?' * len(destinos))}) ORDER BY id LIMIT 1", (jugador_id, *destinos))


def usar_ticket(db, jugador_id, t: dict, entrada_id: int) -> dict:
    k = ticket_para(db, jugador_id, t)
    if not k:
        raise ErrorNegocio("El jugador no tiene un ticket válido para este torneo")
    db.conexion().execute("UPDATE tickets SET estado = 'usado', entrada_id = ?, usado_en = ? WHERE id = ?",
                          (entrada_id, ahora(), k["id"]))
    return k


def crear_ticket(db, personal_id, jugador_id, destino_id, origen_torneo, valor) -> int:
    destino = torneo(db, destino_id)
    cur = db.conexion().execute(
        "INSERT INTO tickets (jugador_id, destino_id, origen_torneo, valor, creado_en, personal_id) VALUES (?,?,?,?,?,?)",
        (jugador_id, destino_id, origen_torneo, valor, ahora(), personal_id))
    db.notificar(jugador_id, "premio", "¡Ganó un cupo!",
                 f"Tiene un ticket para {destino['nombre']} (vale {caja.pesos(valor)}). Úselo al inscribirse en la caja.")
    return cur.lastrowid


def solicitar_codigo_traspaso(db, personal_id, ticket_id) -> str:
    """Envía al dueño del ticket un código para autorizar la venta (traspaso) en la caja."""
    from . import seguridad as seg
    with db.transaccion():
        k = db.uno("SELECT * FROM tickets WHERE id = ?", (ticket_id,))
        if not k or k["estado"] != "disponible":
            raise ErrorNegocio("Solo se traspasa un ticket disponible")
        dueno = caja.jugador(db, k["jugador_id"])
        if dueno["estado"] != "activo" or not dueno["celular_verificado"]:
            raise ErrorNegocio("La cuenta del dueño debe estar activa y con celular vinculado")
        destino = torneo(db, k["destino_id"])
        db.auditar(personal_id, "codigo_traspaso", "ticket", ticket_id)
        return seg.generar_codigo(db, dueno, "retiro", texto=(
            f"Winner Poker: código para TRASPASAR su ticket de {destino['nombre']}: {{c}}. "
            "Si usted no está en la caja vendiendo su cupo, no lo entregue."))


def traspasar_ticket(db, personal_id, ticket_id, comprador, documento, codigo) -> dict:
    """Venta de un cupo entre jugadores: el ticket pasa al comprador. El casino no mueve dinero.

    Lo hace el dueño en persona en la caja, con su documento y el código que le llega al celular.
    """
    from . import seguridad as seg
    k = db.uno("SELECT * FROM tickets WHERE id = ?", (ticket_id,))
    if not k or k["estado"] != "disponible":
        raise ErrorNegocio("Solo se traspasa un ticket disponible")
    dueno = caja.jugador(db, k["jugador_id"])
    if seg.hash_documento(documento or "", dueno.get("tipo_doc") or "CC") != dueno["doc_hash"]:
        raise ErrorNegocio("El documento no corresponde al dueño del ticket")
    texto = str(comprador or "").strip().upper()
    nuevo = db.uno("SELECT * FROM jugadores WHERE numero_caja = ? OR id = ?",
                   (texto, int(texto) if texto.isdigit() else -1))
    if not nuevo:
        raise ErrorNegocio("No se encontró la caja del comprador (use su número de caja, p. ej. W000123)", 404)
    if nuevo["id"] == dueno["id"]:
        raise ErrorNegocio("El comprador es el mismo dueño")
    if nuevo["estado"] != "activo":
        raise ErrorNegocio("La cuenta del comprador está bloqueada")
    destino = torneo(db, k["destino_id"])
    if destino["estado"] in ("finalizado", "cancelado"):
        raise ErrorNegocio("El torneo del ticket ya terminó")
    with db.transaccion() as c:
        seg.verificar_codigo(db, dueno["id"], "retiro", codigo)
        c.execute("UPDATE tickets SET jugador_id = ? WHERE id = ? AND estado = 'disponible'", (nuevo["id"], ticket_id))
        c.execute("INSERT INTO traspasos_ticket (ticket_id, de_jugador, a_jugador, personal_id, fecha) VALUES (?,?,?,?,?)",
                  (ticket_id, dueno["id"], nuevo["id"], personal_id, ahora()))
        db.auditar(personal_id, "traspaso_ticket", "ticket", ticket_id,
                   f"de {dueno['numero_caja']} a {nuevo['numero_caja']} ({destino['nombre']})")
        db.notificar(dueno["id"], "seguridad", "Ticket traspasado",
                     f"Su ticket para {destino['nombre']} pasó a {nuevo['nombre']} ({nuevo['numero_caja']}).")
        db.notificar(nuevo["id"], "premio", "Recibió un ticket",
                     f"Ahora tiene un ticket para {destino['nombre']} (vale {caja.pesos(k['valor'])}). Úselo al inscribirse en la caja.")
    return {"ticket_id": ticket_id, "de": dueno["numero_caja"], "a": nuevo["numero_caja"], "nombre": nuevo["nombre"]}


def anular_ticket(db, supervisor_id, ticket_id, motivo) -> None:
    if not (motivo or "").strip():
        raise ErrorNegocio("Escriba el motivo")
    with db.transaccion() as c:
        k = db.uno("SELECT * FROM tickets WHERE id = ?", (ticket_id,))
        if not k or k["estado"] != "disponible":
            raise ErrorNegocio("Solo se anula un ticket disponible")
        c.execute("UPDATE tickets SET estado = 'anulado' WHERE id = ?", (ticket_id,))
        db.auditar(supervisor_id, "anular_ticket", "ticket", ticket_id, motivo)


def advertencias_satelite(db, t: dict, detalle: list[dict]) -> list[str]:
    """Avisa si un ganador de cupo ya tiene ticket o ya está clasificado/inscrito en el destino."""
    if t["tipo"] != "satelite":
        return []
    salida = []
    destino = torneo(db, t["satelite_de"])
    for d in detalle:
        if d.get("tipo") != "ticket":
            continue
        motivos = []
        if db.valor("SELECT 1 FROM tickets WHERE jugador_id = ? AND destino_id = ? AND estado = 'disponible'",
                    (d["jugador_id"], destino["id"])):
            motivos.append("ya tiene un ticket")
        if db.valor("SELECT 1 FROM clasificados WHERE jugador_id = ? AND principal_id = ?", (d["jugador_id"], destino["id"])):
            motivos.append("ya está clasificado")
        if db.valor("SELECT 1 FROM entradas WHERE jugador_id = ? AND torneo_id = ? AND estado != 'eliminado'",
                    (d["jugador_id"], destino["id"])):
            motivos.append("ya está inscrito")
        if motivos:
            salida.append(f"{d['posicion']}° {d.get('nombre', '')}: {', '.join(motivos)} para {destino['nombre']}. "
                          "Igual recibe el ticket (no se devuelve dinero) y puede venderlo con un traspaso en caja.")
    return salida


def resumen_evento(db, final_id) -> dict:
    final = torneo(db, final_id)
    dias = dias_de(db, final_id)
    ids = [final_id] + [d["id"] for d in dias]
    marcas = ",".join("?" * len(ids))
    recaudo = -db.valor(f"SELECT COALESCE(SUM(monto), 0) FROM movimientos WHERE torneo_id IN ({marcas}) "
                        f"AND tipo IN ('inscripcion','reentrada','recompra','addon','correccion')", ids)
    por_ticket = db.valor(f"SELECT COUNT(*) FROM tickets WHERE estado = 'usado' AND entrada_id IN "
                          f"(SELECT id FROM entradas WHERE torneo_id IN ({marcas}))", ids)
    valor_tickets = db.valor(f"SELECT COALESCE(SUM(valor), 0) FROM tickets WHERE estado = 'usado' AND entrada_id IN "
                             f"(SELECT id FROM entradas WHERE torneo_id IN ({marcas}))", ids)
    return {"final": final, "dias": dias, "recaudo_dinero": recaudo, "entradas_con_ticket": por_ticket,
            "valor_tickets": valor_tickets, "clasificados": len(clasificados_vigentes(db, final_id)),
            "entradas_totales": sum(d["entradas"] for d in dias)}
