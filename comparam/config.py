from __future__ import annotations

import os
from pathlib import Path

from .sources import PORTALES, ArchivoCSV, Source, navegador

RAIZ = Path(__file__).resolve().parent.parent
EJEMPLO = RAIZ / "data" / "ejemplo.csv"


def crear_fuentes(nombres: list[str] | None = None, csv: list[str] | None = None,
                  paginas: int = 2, detalle: bool = True) -> list[Source]:
    """Construye las fuentes. Por defecto: los tres portales + CSV de COMPARAM_CSV."""
    todos = nombres is None or set(nombres) >= set(PORTALES)
    nombres = nombres if nombres is not None else list(PORTALES)
    fuentes: list[Source] = []
    omitidos = []
    for n in nombres:
        if n == "ejemplo":
            fuentes.append(ArchivoCSV(EJEMPLO))
        elif n in PORTALES:
            # Los portales que necesitan navegador se omiten si está apagado (COMPARAM_NAVEGADOR).
            if getattr(PORTALES[n], "usa_navegador", False) and not navegador.disponible():
                omitidos.append(n)
                continue
            fuentes.append(PORTALES[n](paginas=paginas, detalle=detalle))
        else:
            raise ValueError(f"Fuente desconocida: {n}")
    # MercadoLibre por su API oficial, solo si están las credenciales de desarrollador.
    from .sources import mercadolibre_api
    if todos and mercadolibre_api.configurado():
        fuentes.append(mercadolibre_api.MercadoLibreAPI(paginas=paginas))
    if omitidos and todos:  # sin navegador: sus anuncios guardados por la revisión diaria
        from .sources.guardados import Guardados
        fuentes.append(Guardados())
    rutas = list(csv or []) + [p for p in os.environ.get("COMPARAM_CSV", "").split(os.pathsep) if p]
    fuentes += [ArchivoCSV(r) for r in rutas]
    return fuentes


# Páginas que se leen de cada portal al buscar en toda Bogotá (más páginas = más anuncios, más lento).
PAGINAS_CIUDAD = int(os.environ.get("COMPARAM_PAGINAS_CIUDAD", "8"))
