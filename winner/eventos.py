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
    limpio = {"tipo": tipo, "principal_id": None, "satelite_de": None, "porcentaje_clasifica": None}
    if tipo == "dia1":
        final = torneo(db, int(datos.get("principal_id") or 0)) if datos.get("principal_id") else None
        if not final or final["tipo"] != "final":
            raise ErrorNegocio("Un día clasificatorio debe pertenecer a un torneo final (Día 2)")
        if final["estado"] != "programado":
            raise ErrorNegocio("La final ya empezó: no se pueden agregar días clasificatorios")
        limpio["principal_id"] = final["id"]
        pct = datos.get("porcentaje_clasifica")
        if pct not in (None, ""):
            pct = int(pct)
            if not 1 <= pct <= 100:
                raise ErrorNegocio("El porcentaje que clasifica debe estar entre 1 y 100")
            limpio["porcentaje_clasifica"] = pct
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


def objetivo_clasificados(db, t: dict) -> int | None:
    """Cuántos clasifican si el día se juega hasta un porcentaje de las entradas."""
    if t["tipo"] != "dia1" or not t["porcentaje_clasifica"]:
        return None
    entradas = db.valor("SELECT COUNT(*) FROM entradas WHERE torneo_id = ?", (t["id"],))
    return max(1, math.ceil(entradas * t["porcentaje_clasifica"] / 100))


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
    return db.todos("SELECT k.*, t.nombre AS destino, o.nombre AS origen FROM tickets k "
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
                          "Si la regla del casino lo indica, pase el cupo al siguiente con «Ajustar».")
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
