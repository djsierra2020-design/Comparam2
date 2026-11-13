"""Reloj del torneo y estructura de ciegas.

El reloj guarda solo dos datos: los segundos de juego acumulados y desde cuándo está corriendo. Con eso y la
estructura (niveles y descansos en orden) se calcula todo: nivel actual, tiempo restante, próximo descanso y
tiempo total. Las pantallas reciben esos mismos datos y cuentan los segundos por su cuenta, así todas muestran
lo mismo sin preguntarle al servidor cada segundo.
"""
from __future__ import annotations

import re

from .db import ErrorNegocio, ahora, parse_fecha
from .torneos import torneo

# Ciega grande de cada nivel en la plantilla estándar (la chica es la mitad).
CIEGAS_ESTANDAR = [200, 300, 400, 500, 600, 800, 1000, 1200, 1500, 2000, 2500, 3000, 4000, 5000, 6000, 8000,
                   10000, 12000, 15000, 20000, 25000, 30000, 40000, 50000, 60000, 80000, 100000, 120000, 150000,
                   200000]


def estructura(db, torneo_id) -> list[dict]:
    return db.todos("SELECT tipo, ciega_chica, ciega_grande, ante, minutos FROM estructura WHERE torneo_id = ? "
                    "ORDER BY orden", (torneo_id,))


def plantilla_estandar(minutos=20, descanso_cada=4, minutos_descanso=10, niveles=20, ante_desde=4) -> list[dict]:
    """Estructura típica: ciegas crecientes, ante de ciega grande desde el nivel ``ante_desde``."""
    filas = []
    for n, bb in enumerate(CIEGAS_ESTANDAR[:niveles], start=1):
        filas.append({"tipo": "nivel", "ciega_chica": bb // 2, "ciega_grande": bb,
                      "ante": bb if n >= ante_desde else 0, "minutos": minutos})
        if descanso_cada and n % descanso_cada == 0 and n < niveles:
            filas.append({"tipo": "descanso", "ciega_chica": 0, "ciega_grande": 0, "ante": 0, "minutos": minutos_descanso})
    return filas


_LINEA = re.compile(r"^(\d[\d.]*)\s*[/\-]\s*(\d[\d.]*)(?:\s*[/\-]\s*(\d[\d.]*))?\s+(\d+)\s*(?:min\w*)?$", re.I)
_DESCANSO = re.compile(r"^(?:descanso|d|break)\s+(\d+)\s*(?:min\w*)?$", re.I)


def leer_texto(texto: str) -> list[dict]:
    """Una línea por nivel: «100/200 20» (ciegas y minutos), «100/200/200 20» (con ante) o «descanso 15»."""
    filas = []
    for i, linea in enumerate((texto or "").splitlines(), start=1):
        linea = linea.strip()
        if not linea or linea.startswith("#"):
            continue
        if m := _DESCANSO.match(linea):
            filas.append({"tipo": "descanso", "ciega_chica": 0, "ciega_grande": 0, "ante": 0, "minutos": int(m[1])})
            continue
        m = _LINEA.match(linea)
        if not m:
            raise ErrorNegocio(f"Línea {i} no se entiende: «{linea}». Use «100/200 20», «100/200/200 20» o «descanso 15»")
        num = lambda x: int(x.replace(".", "")) if x else 0  # noqa: E731
        filas.append({"tipo": "nivel", "ciega_chica": num(m[1]), "ciega_grande": num(m[2]), "ante": num(m[3]),
                      "minutos": int(m[4])})
    return filas


def a_texto(filas: list[dict]) -> str:
    salida = []
    for f in filas:
        if f["tipo"] == "descanso":
            salida.append(f"descanso {f['minutos']}")
        else:
            ante = f"/{f['ante']}" if f["ante"] else ""
            salida.append(f"{f['ciega_chica']}/{f['ciega_grande']}{ante} {f['minutos']}")
    return "\n".join(salida)


def definir_estructura(db, personal_id, torneo_id, filas: list[dict]) -> None:
    if not filas or not any(f["tipo"] == "nivel" for f in filas):
        raise ErrorNegocio("La estructura debe tener al menos un nivel")
    for n, f in enumerate(filas, start=1):
        if f["tipo"] == "nivel" and (f["ciega_chica"] <= 0 or f["ciega_grande"] < f["ciega_chica"] or f["ante"] < 0):
            raise ErrorNegocio(f"Nivel {n}: revise las ciegas (la grande debe ser mayor o igual a la chica)")
        if not 1 <= f["minutos"] <= 240:
            raise ErrorNegocio(f"Fila {n}: los minutos deben estar entre 1 y 240")
    with db.transaccion() as c:
        t = torneo(db, torneo_id)
        if t["estado"] in ("finalizado", "cancelado"):
            raise ErrorNegocio("El torneo ya terminó")
        c.execute("DELETE FROM estructura WHERE torneo_id = ?", (torneo_id,))
        c.executemany("INSERT INTO estructura (torneo_id, orden, tipo, ciega_chica, ciega_grande, ante, minutos) "
                      "VALUES (?,?,?,?,?,?,?)",
                      [(torneo_id, i, f["tipo"], f["ciega_chica"], f["ciega_grande"], f["ante"], f["minutos"])
                       for i, f in enumerate(filas, start=1)])
        db.auditar(personal_id, "estructura", "torneo", torneo_id, f"{len(filas)} filas")
        _sincronizar_nivel(db, torneo(db, torneo_id))


def _transcurrido(t: dict) -> float:
    total = t["reloj_acumulado"] or 0
    if t["reloj_desde"]:
        total += (parse_fecha(ahora()) - parse_fecha(t["reloj_desde"])).total_seconds()
    return max(0.0, total)


def calcular(filas: list[dict], transcurrido: float) -> dict:
    """Dónde va el torneo después de ``transcurrido`` segundos de juego (misma cuenta que hace la pantalla)."""
    inicio = 0
    indice = len(filas) - 1
    for i, f in enumerate(filas):
        if transcurrido < inicio + f["minutos"] * 60:
            indice = i
            break
        inicio += f["minutos"] * 60
    else:
        inicio -= filas[-1]["minutos"] * 60
    actual = filas[indice]
    restante = max(0, inicio + actual["minutos"] * 60 - transcurrido)
    nivel = sum(1 for f in filas[:indice + 1] if f["tipo"] == "nivel")
    proximo_nivel = next((f for f in filas[indice + 1:] if f["tipo"] == "nivel"), None)
    hasta_descanso = None
    if actual["tipo"] != "descanso":
        acumulado = restante
        for f in filas[indice + 1:]:
            if f["tipo"] == "descanso":
                hasta_descanso = acumulado
                break
            acumulado += f["minutos"] * 60
    return {"indice": indice, "nivel": max(1, nivel), "actual": actual, "restante": restante,
            "inicio_actual": inicio, "proximo_nivel": proximo_nivel, "hasta_descanso": hasta_descanso,
            "transcurrido": transcurrido, "terminado": transcurrido >= sum(f["minutos"] * 60 for f in filas)}


def estado(db, torneo_id) -> dict | None:
    t = torneo(db, torneo_id)
    filas = estructura(db, torneo_id)
    if not filas:
        return None
    pos = calcular(filas, _transcurrido(t))
    _sincronizar_nivel(db, t, pos)
    return {"estructura": filas, "acumulado": t["reloj_acumulado"], "desde": t["reloj_desde"],
            "corriendo": t["reloj_desde"] is not None, "ahora": ahora(), **pos}


def _sincronizar_nivel(db, t, pos=None) -> None:
    """El campo «nivel» del torneo sigue al reloj (lo usan los avisos de cierre de día por nivel fijo)."""
    if pos is None:
        filas = estructura(db, t["id"])
        if not filas:
            return
        pos = calcular(filas, _transcurrido(t))
    if pos["nivel"] != t["nivel"]:
        db.conexion().execute("UPDATE torneos SET nivel = ? WHERE id = ?", (pos["nivel"], t["id"]))


ACCIONES = ("iniciar", "pausar", "siguiente", "anterior", "reiniciar_nivel", "ajustar")


def controlar(db, personal_id, torneo_id, accion, segundos=0) -> dict:
    """Controles del floor. Todo se reduce a mover los segundos acumulados y prender o apagar el reloj."""
    if accion not in ACCIONES:
        raise ErrorNegocio("Acción de reloj inválida")
    with db.transaccion() as c:
        t = torneo(db, torneo_id)
        filas = estructura(db, torneo_id)
        if not filas:
            raise ErrorNegocio("Defina primero la estructura de ciegas")
        if t["estado"] != "en_curso":
            raise ErrorNegocio("Inicie el torneo antes de usar el reloj")
        transcurrido = _transcurrido(t)
        pos = calcular(filas, transcurrido)
        corriendo = t["reloj_desde"] is not None
        if accion == "iniciar":
            corriendo = True
        elif accion == "pausar":
            corriendo = False
        elif accion == "siguiente":
            transcurrido = min(pos["inicio_actual"] + pos["actual"]["minutos"] * 60,
                               sum(f["minutos"] * 60 for f in filas))
        elif accion == "anterior":
            transcurrido = calcular(filas, max(0, pos["inicio_actual"] - 1))["inicio_actual"] if pos["indice"] else 0
        elif accion == "reiniciar_nivel":
            transcurrido = pos["inicio_actual"]
        elif accion == "ajustar":
            segundos = int(segundos)
            if not -3600 <= segundos <= 3600:
                raise ErrorNegocio("Ajuste máximo de una hora")
            # Sumar tiempo al nivel = restar tiempo jugado, sin salirse del nivel actual (máximo, el nivel completo).
            transcurrido = max(pos["inicio_actual"], transcurrido - segundos) if segundos > 0 else transcurrido - segundos
        c.execute("UPDATE torneos SET reloj_acumulado = ?, reloj_desde = ? WHERE id = ?",
                  (transcurrido, ahora() if corriendo else None, torneo_id))
        db.auditar(personal_id, "reloj", "torneo", torneo_id, f"{accion} {segundos or ''}".strip())
        return estado(db, torneo_id)
