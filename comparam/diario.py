"""Revisión diaria: guarda los anuncios de las zonas configuradas y detecta los nuevos.

Uso:  python -m comparam.diario            (lo corre GitHub Actions cada mañana)

Archivos (en data/, guardados en el repositorio):
- diario_config.json      qué barrios y operaciones revisar.
- historial/anuncios.json todos los anuncios vistos: primera y última vez que se vieron,
                          precios a lo largo del tiempo y fecha de publicación si el portal la da.
- diario/AAAA-MM-DD.json  resumen del día: anuncios nuevos y precio del m² de cada barrio.

El repositorio es público: NO se guardan nombres ni teléfonos de los anunciantes, solo datos del
inmueble y el enlace al anuncio original.

El primer día no hay «nuevos»: todo lo que existe se guarda como punto de partida (base).
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .busqueda import buscar
from .config import RAIZ, crear_fuentes
from .models import Listing

DATOS = RAIZ / "data"
CONFIG = DATOS / "diario_config.json"
HISTORIAL = DATOS / "historial" / "anuncios.json"
CARPETA_DIA = DATOS / "diario"
BOGOTA = ZoneInfo("America/Bogota")


def hoy() -> date:
    return datetime.now(BOGOTA).date()


def clave(a: Listing, operacion: str) -> str:
    base = a.url or f"{a.fuente}|{a.titulo}|{a.precio:.0f}|{a.area_m2:g}"
    return hashlib.sha1(f"{operacion}|{base}".encode()).hexdigest()[:16]


def cargar_json(ruta: Path, defecto):
    try:
        return json.loads(ruta.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return defecto


def guardar_json(ruta: Path, datos) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps(datos, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def registrar(historial: dict, a: Listing, operacion: str, zona: str, dia: str, base: bool) -> bool:
    """Agrega o actualiza un anuncio en el historial. Devuelve True si es nuevo."""
    k = clave(a, operacion)
    e = historial.get(k)
    if e is None:
        historial[k] = {
            "operacion": operacion, "zona": zona, "fuente": a.fuente, "titulo": a.titulo[:120],
            "barrio": a.barrio[:60], "url": a.url, "precio": round(a.precio), "area_m2": a.area_m2,
            "habitaciones": a.habitaciones, "banos": a.banos, "dueno_directo": a.es_dueno_directo,
            "publicado": a.publicado, "primera_vez": dia, "ultima_vez": dia, "base": base,
            "precios": [[dia, round(a.precio)]],
        }
        return True
    e["ultima_vez"] = dia
    e["dueno_directo"] = e["dueno_directo"] or a.es_dueno_directo
    if a.publicado and not e.get("publicado"):
        e["publicado"] = a.publicado
    if round(a.precio) != e["precios"][-1][1]:  # cambió el precio
        e["precios"].append([dia, round(a.precio)])
        e["precio"] = round(a.precio)
    return False


def correr(config: dict | None = None, fuentes_por_operacion=None, dia: date | None = None) -> dict:
    config = config or cargar_json(CONFIG, {})
    dia_txt = (dia or hoy()).isoformat()
    historial = cargar_json(HISTORIAL, {})
    base = not historial  # primer día: todo es punto de partida
    resumen = {"fecha": dia_txt, "base": base, "nuevos": [], "zonas": {}, "revisados": 0, "errores": []}
    for operacion in config.get("operaciones", ["venta", "arriendo"]):
        for zona in config.get("barrios", []):
            fuentes = (fuentes_por_operacion(operacion) if fuentes_por_operacion
                       else crear_fuentes(paginas=config.get("paginas", 3), detalle=False))
            r = buscar(zona, fuentes, operacion, solo_dueno=False)
            resumen["errores"] += [f"{operacion} {zona}: {e}" for e in r.errores]
            if r.zona:
                resumen["zonas"][f"{operacion}|{zona}"] = {"mediana_m2": round(r.zona.mediana),
                                                           "anuncios": r.zona.anuncios}
            for a in r.usados_zona:
                resumen["revisados"] += 1
                if registrar(historial, a, operacion, zona, dia_txt, base) and not base:
                    resumen["nuevos"].append(clave(a, operacion))
    guardar_json(HISTORIAL, historial)
    guardar_json(CARPETA_DIA / f"{dia_txt}.json", resumen)
    return resumen


def dias_publicado(e: dict, dia: date | None = None) -> tuple[int | None, str]:
    """(días, texto) que lleva publicado un anuncio del historial."""
    dia = dia or hoy()
    if e.get("publicado"):
        n = (dia - date.fromisoformat(e["publicado"])).days
        return n, f"publicado hace {n} día{'s' if n != 1 else ''}"
    if e.get("base"):
        return None, f"publicado antes del {e['primera_vez']}"
    n = (dia - date.fromisoformat(e["primera_vez"])).days
    return n, "publicado hoy" if n == 0 else f"publicado hace {n} día{'s' if n != 1 else ''}"


def nuevos(dias: int = 1, solo_dueno: bool = True, dia: date | None = None) -> list[dict]:
    """Anuncios vistos por primera vez en los últimos `dias` días (sin contar el punto de partida)."""
    dia = dia or hoy()
    historial = cargar_json(HISTORIAL, {})
    zonas = ultimo_resumen().get("zonas", {})
    salida = []
    for k, e in historial.items():
        if e.get("base") or (solo_dueno and not e.get("dueno_directo")):
            continue
        if (dia - date.fromisoformat(e["primera_vez"])).days >= dias:
            continue
        z = zonas.get(f"{e['operacion']}|{e['zona']}")
        m2 = e["precio"] / e["area_m2"] if e.get("area_m2") else None
        salida.append({**e, "clave": k, "precio_m2": round(m2) if m2 else None,
                       "vs_zona": round((m2 / z["mediana_m2"] - 1) * 100) if m2 and z else None,
                       "dias_texto": dias_publicado(e, dia)[1]})
    return sorted(salida, key=lambda e: (e["operacion"], e["zona"], e.get("precio_m2") or 0))


def ultimo_resumen() -> dict:
    archivos = sorted(CARPETA_DIA.glob("*.json"))
    return cargar_json(archivos[-1], {}) if archivos else {}


if __name__ == "__main__":
    r = correr()
    print(f"{r['fecha']}: {r['revisados']} anuncios revisados, {len(r['nuevos'])} nuevos"
          f"{' (punto de partida)' if r['base'] else ''}")
    for e in r["errores"]:
        print("⚠", e)
