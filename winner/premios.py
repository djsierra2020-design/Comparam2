"""Premios automáticos: tabla de premios × posiciones finales → crédito en la caja del jugador.

Flujo: finalizar torneo → borrador con posiciones → (ajustes por empates/acuerdos) →
el director confirma → se acreditan. Cada paso queda en la auditoría.
"""
from __future__ import annotations

import json

from . import caja
from .db import ErrorNegocio, ahora
from .torneos import torneo, vencer_reservas


def definir_tabla(db, personal_id, torneo_id, filas) -> None:
    """``filas``: lista de {posicion, monto, tipo}. tipo 'ticket' (solo satélites) = un cupo al torneo destino."""
    t = torneo(db, torneo_id)
    if t["tipo"] == "dia1":
        raise ErrorNegocio("Los días clasificatorios no pagan premios: los premios van en la final")
    tabla = {}
    for f in filas:
        pos, tipo = int(f["posicion"]), f.get("tipo") or "dinero"
        if tipo == "ticket":
            if t["tipo"] != "satelite":
                raise ErrorNegocio("Solo los satélites premian con tickets")
            monto = torneo(db, t["satelite_de"])["buyin"]  # el ticket vale el buy-in del destino
        elif tipo == "dinero":
            monto = caja.monto_valido(f["monto"])
        else:
            raise ErrorNegocio("Tipo de premio inválido")
        if pos < 1 or pos in tabla:
            raise ErrorNegocio("Posiciones inválidas o repetidas en la tabla de premios")
        tabla[pos] = (monto, tipo)
    if sorted(tabla) != list(range(1, len(tabla) + 1)):
        raise ErrorNegocio("La tabla debe cubrir las posiciones 1, 2, 3… sin saltos")
    with db.transaccion() as c:
        torneo(db, torneo_id)
        if db.valor("SELECT estado FROM premiaciones WHERE torneo_id = ?", (torneo_id,)) in ("confirmada", "acreditada"):
            raise ErrorNegocio("Los premios ya fueron confirmados")
        c.execute("DELETE FROM premios_tabla WHERE torneo_id = ?", (torneo_id,))
        c.executemany("INSERT INTO premios_tabla (torneo_id, posicion, monto, tipo) VALUES (?,?,?,?)",
                      [(torneo_id, p, m, k) for p, (m, k) in sorted(tabla.items())])
        db.auditar(personal_id, "tabla_premios", "torneo", torneo_id, json.dumps(tabla))


def tabla(db, torneo_id) -> list[dict]:
    return db.todos("SELECT posicion, monto, tipo FROM premios_tabla WHERE torneo_id = ? ORDER BY posicion", (torneo_id,))


def posiciones(db, torneo_id) -> list[dict]:
    """Clasificación: vivos por fichas; luego eliminados del último al primero. Una fila por jugador."""
    filas = db.todos(
        "SELECT e.jugador_id, j.nombre, j.numero_caja, e.estado, e.fichas, e.eliminado_en FROM entradas e "
        "JOIN jugadores j ON j.id = e.jugador_id WHERE e.torneo_id = ? AND e.numero = "
        "(SELECT MAX(numero) FROM entradas x WHERE x.torneo_id = e.torneo_id AND x.jugador_id = e.jugador_id)",
        (torneo_id,))
    vivos = sorted((f for f in filas if f["estado"] != "eliminado"), key=lambda f: -f["fichas"])
    fuera = sorted((f for f in filas if f["estado"] == "eliminado"), key=lambda f: f["eliminado_en"] or "", reverse=True)
    return [{**f, "posicion": i} for i, f in enumerate(vivos + fuera, start=1)]


def finalizar(db, personal_id, torneo_id) -> dict:
    """Cierra el torneo y arma el borrador de premiación cruzando tabla y posiciones."""
    with db.transaccion() as c:
        vencer_reservas(db)
        t = torneo(db, torneo_id)
        if t["tipo"] == "dia1":
            raise ErrorNegocio("Un día clasificatorio se cierra con «Cerrar día»: los vivos clasifican a la final")
        if t["estado"] != "en_curso":
            raise ErrorNegocio("Solo se finaliza un torneo en curso")
        premios = tabla(db, torneo_id)
        if not premios:
            raise ErrorNegocio("Defina la tabla de premios antes de finalizar")
        if db.valor("SELECT COUNT(*) FROM entradas WHERE torneo_id = ? AND estado = 'reservado'", (torneo_id,)):
            raise ErrorNegocio("Hay jugadores con reserva de recompra pendiente; espere o resuélvalas")
        pos = posiciones(db, torneo_id)
        vivos = sum(1 for p in pos if p["estado"] != "eliminado")
        if vivos > len(premios):
            raise ErrorNegocio(f"Aún quedan {vivos} jugadores y solo se pagan {len(premios)} puestos")
        if len(pos) < len(premios):
            raise ErrorNegocio("Hay más premios que jugadores; ajuste la tabla")
        c.execute("UPDATE torneos SET estado = 'finalizado', finalizado_en = ?, registro_abierto = 0, "
                  "recompras_abiertas = 0, addon_abierto = 0 WHERE id = ?", (ahora(), torneo_id))
        c.execute("UPDATE mesas SET estado = 'cerrada', cerrada_en = ? WHERE torneo_id = ? AND estado = 'abierta'",
                  (ahora(), torneo_id))
        c.execute("INSERT INTO premiaciones (torneo_id, estado, preparada_en) VALUES (?, 'borrador', ?)",
                  (torneo_id, ahora()))
        por_pos = {p["posicion"]: p for p in pos}
        for fila in premios:
            c.execute("INSERT INTO premiacion_detalle (torneo_id, posicion, jugador_id, monto_tabla, monto_final, tipo) "
                      "VALUES (?,?,?,?,?,?)", (torneo_id, fila["posicion"], por_pos[fila["posicion"]]["jugador_id"],
                                                fila["monto"], fila["monto"], fila["tipo"]))
        db.auditar(personal_id, "finalizar_torneo", "torneo", torneo_id,
                   f"{len(pos)} jugadores; ganador #{pos[0]['jugador_id']}")
    return premiacion(db, torneo_id)


def premiacion(db, torneo_id) -> dict | None:
    p = db.uno("SELECT p.*, a.nombre AS confirmada_por_nombre, b.nombre AS acreditada_por_nombre "
               "FROM premiaciones p LEFT JOIN personal a ON a.id = p.confirmada_por "
               "LEFT JOIN personal b ON b.id = p.acreditada_por WHERE p.torneo_id = ?", (torneo_id,))
    if not p:
        return None
    p["detalle"] = db.todos("SELECT d.*, j.nombre, j.numero_caja FROM premiacion_detalle d "
                            "JOIN jugadores j ON j.id = d.jugador_id WHERE d.torneo_id = ? ORDER BY d.posicion",
                            (torneo_id,))
    p["total_tabla"] = sum(d["monto_tabla"] for d in p["detalle"])
    p["total_final"] = sum(d["monto_final"] for d in p["detalle"])
    from .eventos import advertencias_satelite
    p["advertencias"] = advertencias_satelite(db, torneo(db, torneo_id), p["detalle"]) if p["estado"] == "borrador" else []
    return p


def ajustar(db, personal_id, torneo_id, filas) -> dict:
    """Corrige jugador por posición o reparte montos (empates / acuerdos de mesa final).

    El total a pagar no puede cambiar: lo que se quita a una posición se suma a otra.
    """
    with db.transaccion() as c:
        p = premiacion(db, torneo_id)
        if not p:
            raise ErrorNegocio("El torneo aún no tiene premiación", 404)
        if p["estado"] != "borrador":
            raise ErrorNegocio("La premiación ya fue confirmada; no se puede ajustar")
        actual = {d["posicion"]: d for d in p["detalle"]}
        nuevos = {}
        for f in filas:
            pos = int(f["posicion"])
            if pos not in actual:
                raise ErrorNegocio(f"La posición {pos} no está en la tabla")
            monto = caja.monto_valido(f.get("monto_final", actual[pos]["monto_final"]))
            if actual[pos]["tipo"] == "ticket" and monto != actual[pos]["monto_tabla"]:
                raise ErrorNegocio(f"La posición {pos} es un ticket: se puede pasar a otro jugador, no cambiar su valor")
            jugador_id = int(f.get("jugador_id") or actual[pos]["jugador_id"])
            motivo = (f.get("motivo") or "").strip() or None
            if (monto != actual[pos]["monto_tabla"] or jugador_id != actual[pos]["jugador_id"]) and not motivo:
                raise ErrorNegocio(f"Escriba el motivo del ajuste de la posición {pos}")
            nuevos[pos] = (jugador_id, monto, motivo)
        final = {pos: nuevos.get(pos, (d["jugador_id"], d["monto_final"], d["motivo_ajuste"])) for pos, d in actual.items()}
        if len({v[0] for v in final.values()}) != len(final):
            raise ErrorNegocio("Un jugador aparece en dos posiciones")
        if sum(v[1] for v in final.values()) != p["total_tabla"]:
            raise ErrorNegocio(f"El total debe seguir siendo {caja.pesos(p['total_tabla'])} "
                               f"(quedaría {caja.pesos(sum(v[1] for v in final.values()))})")
        jugadores = {r["jugador_id"] for r in db.todos("SELECT jugador_id FROM entradas WHERE torneo_id = ?", (torneo_id,))}
        if any(v[0] not in jugadores for v in final.values()):
            raise ErrorNegocio("Solo se premia a jugadores que jugaron el torneo")
        for pos, (jid, monto, motivo) in final.items():
            c.execute("UPDATE premiacion_detalle SET jugador_id = ?, monto_final = ?, motivo_ajuste = ? "
                      "WHERE torneo_id = ? AND posicion = ?", (jid, monto, motivo, torneo_id, pos))
        db.auditar(personal_id, "ajustar_premios", "torneo", torneo_id,
                   json.dumps({k: list(v) for k, v in nuevos.items()}, ensure_ascii=False))
    return premiacion(db, torneo_id)


def confirmar(db, personal_id, torneo_id) -> dict:
    """El director confirma las posiciones y montos. Queda registrado quién y cuándo."""
    with db.transaccion() as c:
        p = premiacion(db, torneo_id)
        if not p or p["estado"] != "borrador":
            raise ErrorNegocio("No hay premiación en borrador para confirmar")
        c.execute("UPDATE premiaciones SET estado = 'confirmada', confirmada_por = ?, confirmada_en = ? "
                  "WHERE torneo_id = ?", (personal_id, ahora(), torneo_id))
        db.auditar(personal_id, "confirmar_posiciones", "torneo", torneo_id,
                   json.dumps([[d["posicion"], d["jugador_id"], d["monto_final"]] for d in p["detalle"]]))
    return premiacion(db, torneo_id)


def acreditar(db, personal_id, torneo_id) -> dict:
    """Acredita cada premio en la caja del jugador (movimiento 'premio') y le avisa."""
    with db.transaccion() as c:
        p = premiacion(db, torneo_id)
        if not p or p["estado"] != "confirmada":
            raise ErrorNegocio("Primero el director debe confirmar las posiciones")
        t = torneo(db, torneo_id)
        for d in p["detalle"]:
            if d["tipo"] == "ticket":
                from .eventos import crear_ticket
                kid = crear_ticket(db, personal_id, d["jugador_id"], t["satelite_de"], torneo_id, d["monto_final"])
                c.execute("UPDATE premiacion_detalle SET ticket_id = ? WHERE torneo_id = ? AND posicion = ?",
                          (kid, torneo_id, d["posicion"]))
                continue
            mid = caja.registrar_movimiento(db, d["jugador_id"], "premio", d["monto_final"], origen="sistema",
                                            personal_id=personal_id, torneo_id=torneo_id,
                                            descripcion=f"Premio {d['posicion']}° lugar {t['nombre']}")
            c.execute("UPDATE premiacion_detalle SET movimiento_id = ? WHERE torneo_id = ? AND posicion = ?",
                      (mid, torneo_id, d["posicion"]))
            db.notificar(d["jugador_id"], "premio", "¡Premio acreditado!",
                         f"{d['posicion']}° lugar en {t['nombre']}: {caja.pesos(d['monto_final'])} en su caja. "
                         "El retiro se hace en persona en la caja.")
        c.execute("UPDATE premiaciones SET estado = 'acreditada', acreditada_por = ?, acreditada_en = ? "
                  "WHERE torneo_id = ?", (personal_id, ahora(), torneo_id))
        db.auditar(personal_id, "acreditar_premios", "torneo", torneo_id, caja.pesos(p["total_final"]))
    return premiacion(db, torneo_id)
