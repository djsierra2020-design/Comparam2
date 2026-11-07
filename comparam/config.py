from __future__ import annotations

import os
from pathlib import Path

from .sources import PORTALES, ArchivoCSV, Source

RAIZ = Path(__file__).resolve().parent.parent
EJEMPLO = RAIZ / "data" / "ejemplo.csv"


def crear_fuentes(nombres: list[str] | None = None, csv: list[str] | None = None,
                  paginas: int = 2, detalle: bool = True) -> list[Source]:
    """Construye las fuentes. Por defecto: los tres portales + CSV de COMPARAM_CSV."""
    nombres = nombres if nombres is not None else list(PORTALES)
    fuentes: list[Source] = []
    for n in nombres:
        if n == "ejemplo":
            fuentes.append(ArchivoCSV(EJEMPLO))
        elif n in PORTALES:
            fuentes.append(PORTALES[n](paginas=paginas, detalle=detalle))
        else:
            raise ValueError(f"Fuente desconocida: {n}")
    rutas = list(csv or []) + [p for p in os.environ.get("COMPARAM_CSV", "").split(os.pathsep) if p]
    fuentes += [ArchivoCSV(r) for r in rutas]
    return fuentes


# Páginas que se leen de cada portal al buscar en toda Bogotá (más páginas = más anuncios, más lento).
PAGINAS_CIUDAD = int(os.environ.get("COMPARAM_PAGINAS_CIUDAD", "5"))
