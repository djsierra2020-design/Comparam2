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
from .extractor import contar_bloques_json, extraer_anuncios

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
            url = self.url_busqueda(barrio, operacion, pagina)
            html = self._get(url)
            nuevos = extraer_anuncios(html, self.nombre, self.base_url, operacion) if html else []
            if pagina == 1 and not nuevos:
                if not html:
                    raise FuenteError(f"{self.nombre}: la búsqueda no existe en el portal (404) — {url}")
                raise FuenteError(f"{self.nombre}: el portal respondió pero la app no encontró anuncios "
                                  f"en la página «{_titulo(html)}» — {url}")
            # Si el portal repite la misma página (p. ej. redirige a la primera), se deja de paginar.
            vistos = {(a.url, a.precio, a.area_m2) for a in resultados}
            nuevos = [a for a in nuevos if (a.url, a.precio, a.area_m2) not in vistos]
            if not nuevos:
                break
            resultados += nuevos
            time.sleep(self.pausa)
        if self.detalle:  # abre máximo 20 fichas para no tardar demasiado
            pendientes = [a for a in resultados if a.url and not a.telefonos and a.es_dueno_directo]
            for anuncio in sorted(pendientes, key=lambda a: a.precio_m2)[:20]:
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


    def diagnosticar(self, barrio: str = "", operacion: str = "venta") -> dict:
        """Qué responde el portal a la primera página de búsqueda (para la página /diagnostico)."""
        url = self.url_busqueda(barrio, operacion, 1)
        info = {"portal": self.nombre, "url": url}
        try:
            r = self.sesion.get(url, timeout=self.timeout)
        except requests.RequestException as e:
            return {**info, "error": f"no se pudo conectar: {e.__class__.__name__}"}
        html = r.text
        anuncios = extraer_anuncios(html, self.nombre, self.base_url, operacion)
        info.update(
            estado=r.status_code, url_final=r.url, bytes=len(html), titulo=_titulo(html),
            bloques_json=contar_bloques_json(html), anuncios=len(anuncios),
            dueno_directo=sum(a.es_dueno_directo for a in anuncios),
            ejemplos=[f"{a.titulo[:50]} · ${a.precio:,.0f} · {a.area_m2:g} m² · {a.publicado_por or 'anunciante ?'}"
                      for a in anuncios[:5]],
        )
        if not anuncios:
            info["pistas"] = _pistas(html)
        return info


def _pistas(html: str) -> list[str]:
    """Para páginas donde no se reconocen anuncios: nombres de los datos que parecen tener precio y
    textos visibles con precios, para poder adaptar el extractor."""
    from .extractor import _bloques_json, _recorrer

    pistas: list[str] = []
    for bloque in _bloques_json(html):
        for d in _recorrer(bloque):
            claves = [k for k in d if re.search(r"price|precio|valor|amount|canon", k, re.I)]
            if claves:
                pistas.append("datos: " + ", ".join(list(d)[:30]))
            if len(pistas) >= 3:
                break
        if len(pistas) >= 3:
            break
    if not pistas:
        rutas: list[str] = []

        def mapa(n, ruta="", nivel=0):
            if nivel > 4 or len(rutas) > 25:
                return
            if isinstance(n, dict):
                for k, v in list(n.items())[:15]:
                    if isinstance(v, (dict, list)):
                        tam = f"[{len(v)}]" if isinstance(v, list) else ""
                        rutas.append(f"{ruta}/{k}{tam}")
                        mapa(v, f"{ruta}/{k}", nivel + 1)
            elif isinstance(n, list) and n:
                mapa(n[0], ruta + "/0", nivel + 1)

        for bloque in _bloques_json(html):
            mapa(bloque)
        if rutas:
            pistas.append("estructura: " + " · ".join(rutas[:25]))
    soup = BeautifulSoup(html, "html.parser")
    for x in soup(["script", "style", "noscript", "svg"]):
        x.decompose()
    texto = soup.get_text(" ", strip=True)
    for m in list(re.finditer(r"\$\s?\d{1,3}(?:[.,]\d{3}){2,}", texto))[:3]:
        pistas.append("texto: …" + texto[max(0, m.start() - 60):m.end() + 90] + "…")
    if not any(p.startswith("texto") for p in pistas):
        pistas.append(f"texto: la página no muestra precios como texto visible ({len(texto)} caracteres de texto).")
    return pistas


def _titulo(html: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html or "", re.I | re.S)
    return re.sub(r"\s+", " ", m.group(1)).strip()[:80] if m else "sin título"


class FincaRaiz(PortalWeb):
    nombre = "fincaraiz"
    base_url = "https://www.fincaraiz.com.co"
    plantilla = base_url + "/{operacion}/apartamentos/{barrio}/bogota/pagina{pagina}"
    plantilla_ciudad = base_url + "/{operacion}/apartamentos/bogota-dc/pagina{pagina}"


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


class PortalEmpresa(PortalWeb):
    """Portales de empresas que compran/venden o administran inmuebles (no son dueños directos).

    Sus anuncios se marcan como empresa: sirven para calcular el precio del m² de cada barrio y
    aparecen en la lista si se marca «Incluir inmobiliarias». Su búsqueda es por ciudad; cuando
    se busca un barrio, se filtran aquí los anuncios de ese barrio.
    """
    empresa = ""

    def url_busqueda(self, barrio: str, operacion: str, pagina: int) -> str:
        return self.plantilla_ciudad.format(operacion=self.operaciones[operacion], barrio="", pagina=pagina)

    def buscar(self, barrio: str, operacion: str = "venta") -> list[Listing]:
        if operacion not in self.operaciones:
            return []
        self.detalle = False  # no hay teléfonos de propietarios que buscar
        objetivo = slugify(barrio)
        anuncios = super().buscar("", operacion)
        for a in anuncios:
            a.publicado_por, a.es_dueno_directo = f"{self.empresa} (empresa)", False
        if objetivo:
            anuncios = [a for a in anuncios
                        if objetivo in slugify(f"{a.barrio} {a.ubicacion} {a.titulo} {a.url}")]
        return anuncios


class Habi(PortalEmpresa):
    # Habi compra apartamentos y los revende; solo tiene venta.
    nombre = "habi"
    empresa = "Habi"
    base_url = "https://habi.co"
    operaciones = {"venta": "venta"}
    plantilla_ciudad = base_url + "/venta-apartamentos/bogota?page={pagina}"


class Houm(PortalEmpresa):
    nombre = "houm"
    empresa = "Houm"
    base_url = "https://houm.com"
    plantilla_ciudad = base_url + "/co/{operacion}-propiedades/bogota?page={pagina}"
