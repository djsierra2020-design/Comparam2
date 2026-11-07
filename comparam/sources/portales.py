"""Fuentes en línea: portales inmobiliarios de Colombia.

Las URL de búsqueda se pueden ajustar sin tocar código con variables de
entorno (``COMPARAM_URL_FINCARAIZ``, etc.), porque los portales cambian sus
rutas con frecuencia. Marcadores disponibles: ``{operacion}``, ``{barrio}``,
``{pagina}``.
"""
from __future__ import annotations

import os
import re
import time

import requests
from bs4 import BeautifulSoup

from ..models import Listing
from ..parsing import extract_phones, slugify
from .base import FuenteError, Source
from .extractor import extraer_anuncios

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    "Accept-Language": "es-CO,es;q=0.9",
}


class PortalWeb(Source):
    nombre = "portal"
    base_url = ""
    plantilla = ""  # URL de búsqueda en un barrio
    plantilla_ciudad = ""  # URL de búsqueda en toda Bogotá
    operaciones = {"venta": "venta", "arriendo": "arriendo"}

    def __init__(self, paginas: int = 2, detalle: bool = True, timeout: float = 20,
                 sesion: requests.Session | None = None, pausa: float = 1.0):
        self.paginas = paginas
        self.detalle = detalle
        self.timeout = timeout
        self.sesion = sesion or requests.Session()
        self.sesion.headers.update(HEADERS)
        self.pausa = pausa
        env = f"COMPARAM_URL_{self.nombre.upper().replace(' ', '')}"
        self.plantilla = os.environ.get(env, self.plantilla)
        self.plantilla_ciudad = os.environ.get(env.replace("_URL_", "_URL_CIUDAD_"), self.plantilla_ciudad)

    def url_busqueda(self, barrio: str, operacion: str, pagina: int) -> str:
        plantilla = self.plantilla if barrio.strip() else self.plantilla_ciudad
        return plantilla.format(operacion=self.operaciones[operacion], barrio=slugify(barrio),
                                     pagina=pagina)

    def _get(self, url: str) -> str:
        try:
            r = self.sesion.get(url, timeout=self.timeout)
        except requests.RequestException as e:
            raise FuenteError(f"{self.nombre}: no se pudo conectar ({e.__class__.__name__})") from e
        if r.status_code == 404:
            return ""
        if r.status_code >= 400:
            raise FuenteError(f"{self.nombre}: respuesta HTTP {r.status_code} en {url}")
        return r.text

    def buscar(self, barrio: str, operacion: str = "venta") -> list[Listing]:
        resultados: list[Listing] = []
        for pagina in range(1, self.paginas + 1):
            html = self._get(self.url_busqueda(barrio, operacion, pagina))
            nuevos = extraer_anuncios(html, self.nombre, self.base_url, operacion) if html else []
            if not nuevos:
                break
            resultados += nuevos
            time.sleep(self.pausa)
        if self.detalle:
            for anuncio in resultados:
                if anuncio.url and not anuncio.telefonos and anuncio.es_dueno_directo:
                    self.completar_detalle(anuncio, operacion)
        return resultados

    def completar_detalle(self, anuncio: Listing, operacion: str) -> None:
        """Abre la ficha del anuncio para buscar teléfonos públicos."""
        try:
            html = self._get(anuncio.url)
        except FuenteError:
            return
        if not html:
            return
        soup = BeautifulSoup(html, "html.parser")
        tels = [a["href"][4:] for a in soup.select('a[href^="tel:"]')]
        tels += [re.sub(r"\D", "", a["href"]) for a in soup.select('a[href*="wa.me"], a[href*="whatsapp"]')]
        detalle = extraer_anuncios(html, self.nombre, self.base_url, operacion)
        for d in detalle:
            tels += d.telefonos
            if d.publicado_por and not anuncio.publicado_por:
                anuncio.publicado_por, anuncio.es_dueno_directo = d.publicado_por, d.es_dueno_directo
        anuncio.telefonos = extract_phones(*tels)
        time.sleep(self.pausa)


class FincaRaiz(PortalWeb):
    nombre = "fincaraiz"
    base_url = "https://www.fincaraiz.com.co"
    plantilla = base_url + "/{operacion}/apartamentos/{barrio}/bogota/pagina{pagina}"
    plantilla_ciudad = base_url + "/{operacion}/apartamentos/bogota/pagina{pagina}"


class Metrocuadrado(PortalWeb):
    nombre = "metrocuadrado"
    base_url = "https://www.metrocuadrado.com"
    plantilla = base_url + "/apartamentos/{operacion}/bogota/{barrio}/?page={pagina}"
    plantilla_ciudad = base_url + "/apartamentos/{operacion}/bogota/?page={pagina}"


class Ciencuadras(PortalWeb):
    nombre = "ciencuadras"
    base_url = "https://www.ciencuadras.com"
    plantilla = base_url + "/{operacion}/bogota/apartamento/{barrio}?page={pagina}"
    plantilla_ciudad = base_url + "/{operacion}/bogota/apartamento?page={pagina}"
