"""Anuncios de portales que solo se pueden leer con navegador (Metrocuadrado, Habi, Houm).

En Render no hay memoria para abrir un navegador en cada búsqueda (daba error 502). Por eso la
revisión diaria de GitHub los abre una vez al día y los guarda en el historial; esta fuente los
devuelve desde ahí, como si fuera un portal más.
"""
from __future__ import annotations

from datetime import date, timedelta

from ..models import Listing
from ..parsing import slugify
from .base import Source

PORTALES_NAVEGADOR = ("habi", "houm")


class Guardados(Source):
    nombre = "guardados"

    def __init__(self, dias: int = 2):
        self.dias = dias

    def buscar(self, barrio: str, operacion: str = "venta") -> list[Listing]:
        from .. import diario

        historial = diario.cargar_json(diario.HISTORIAL, {})
        desde = (diario.hoy() - timedelta(days=self.dias)).isoformat()
        objetivo = slugify(barrio)
        salida = []
        for e in historial.values():
            if e.get("fuente") not in PORTALES_NAVEGADOR or e.get("operacion") != operacion:
                continue
            if e.get("ultima_vez", "") < desde:  # ya no se ve en el portal
                continue
            if objetivo and objetivo not in slugify(f"{e.get('zona', '')} {e.get('barrio', '')} {e.get('titulo', '')}"):
                continue
            salida.append(Listing(
                fuente=e["fuente"], titulo=e.get("titulo") or "Apartamento", barrio=e.get("barrio") or e.get("zona", ""),
                precio=e["precio"], area_m2=e["area_m2"], url=e.get("url", ""),
                publicado_por=e.get("anunciante", ""), es_dueno_directo=bool(e.get("dueno_directo")),
                verificar=bool(e.get("verificar")), habitaciones=e.get("habitaciones"), banos=e.get("banos"),
                publicado=e.get("publicado", ""),
            ))
        return salida
