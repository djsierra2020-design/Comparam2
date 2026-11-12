"""Reportes: cuadre de caja diario, resumen por torneo e historial para auditoría."""
from __future__ import annotations

from . import caja
from .db import ErrorNegocio, ahora


def cuadre_diario(db, dia: str) -> dict:
    inicio, fin = caja.rango_dia_utc(dia)
    por_tipo = db.todos("SELECT tipo, medio, COUNT(*) AS cantidad, SUM(monto) AS total FROM movimientos "
                        "WHERE fecha >= ? AND fecha < ? GROUP BY tipo, medio ORDER BY tipo", (inicio, fin))
    efectivo = db.valor("SELECT COALESCE(SUM(monto), 0) FROM movimientos WHERE medio = 'efectivo' "
                        "AND fecha >= ? AND fecha < ?", (inicio, fin))
    return {"dia": dia, "por_tipo": por_tipo, "efectivo_esperado": efectivo,
            "saldos_jugadores": db.valor("SELECT COALESCE(SUM(monto), 0) FROM movimientos WHERE fecha < ?", (fin,)),
            "arqueos": db.todos("SELECT a.*, p.nombre FROM arqueos a JOIN personal p ON p.id = a.personal_id "
                                "WHERE dia = ? ORDER BY a.id", (dia,))}


def registrar_arqueo(db, personal_id, dia, contado, nota=None) -> dict:
    """El cajero cuenta el efectivo; se guarda la diferencia contra lo que dice el libro."""
    contado = int(str(contado or "0").replace(".", "").replace("$", "").strip() or 0)
    if contado < 0:
        raise ErrorNegocio("Monto inválido")
    with db.transaccion() as c:
        esperado = cuadre_diario(db, dia)["efectivo_esperado"]
        c.execute("INSERT INTO arqueos (dia, personal_id, efectivo_contado, efectivo_esperado, diferencia, nota, fecha) "
                  "VALUES (?,?,?,?,?,?,?)", (dia, personal_id, contado, esperado, contado - esperado, nota, ahora()))
        db.auditar(personal_id, "arqueo", "caja", None, f"{dia}: contado {contado}, esperado {esperado}")
    return cuadre_diario(db, dia)


def resumen_torneo(db, torneo_id) -> dict:
    from .premios import premiacion, tabla
    from .torneos import resumen_fichas, torneo
    t = torneo(db, torneo_id)
    filas = db.todos("SELECT tipo, COUNT(*) AS cantidad, -SUM(monto) AS total FROM movimientos "
                     "WHERE torneo_id = ? AND tipo IN ('inscripcion','reentrada','recompra','addon') GROUP BY tipo",
                     (torneo_id,))
    correcciones = db.valor("SELECT COALESCE(SUM(monto), 0) FROM movimientos WHERE torneo_id = ? AND tipo = 'correccion'",
                            (torneo_id,))
    recaudado = sum(f["total"] for f in filas) - correcciones
    pagado = db.valor("SELECT COALESCE(SUM(monto), 0) FROM movimientos WHERE torneo_id = ? AND tipo = 'premio'",
                      (torneo_id,))
    total_tabla = sum(f["monto"] for f in tabla(db, torneo_id))
    jugadores = db.valor("SELECT COUNT(DISTINCT jugador_id) FROM entradas WHERE torneo_id = ?", (torneo_id,))
    return {"torneo": t, "por_tipo": filas, "correcciones": correcciones, "recaudado": recaudado,
            "jugadores_unicos": jugadores, "tabla_premios": total_tabla, "premios_pagados": pagado,
            "diferencia_recaudo_premios": recaudado - total_tabla, "fichas": resumen_fichas(db, torneo_id),
            "premiacion": premiacion(db, torneo_id)}
