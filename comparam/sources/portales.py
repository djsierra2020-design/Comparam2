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
from .extractor import contar_bloques_json, extraer_anuncios, total_publicados

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    "Accept-Language": "es-CO,es;q=0.9",
}


class PortalWeb(Source):
    nombre = "portal"
    total_publicados: int | None = None  # cuántos anuncios dice tener el portal para la búsqueda
    base_url = ""
    plantilla = ""  # URL de búsqueda en un barrio
    plantilla_ciudad = ""  # URL de búsqueda en toda Bogotá
    por_pagina = 48  # para {desde} en portales que paginan por posición
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
                                pagina=pagina, desde=(pagina - 1) * self.por_pagina + 1)

    def _get(self, url: str) -> str:
        try:
            r = self.sesion.get(url, timeout=self.timeout)
        except requests.RequestException as e:
            raise FuenteError(f"{self.nombre}: no se pudo conectar ({e.__class__.__name__})") from e
        self.ultima_respuesta = r
        if r.status_code == 404:
            return ""
        if r.status_code >= 400:
            raise FuenteError(f"{self.nombre}: respuesta HTTP {r.status_code} en {url}")
        return r.text

    tiempo_max = 120  # segundos por búsqueda: un portal lento no frena toda la revisión

    def buscar(self, barrio: str, operacion: str = "venta") -> list[Listing]:
        resultados: list[Listing] = []
        inicio = time.time()
        for pagina in range(1, self.paginas + 1):
            if pagina > 1 and time.time() - inicio > self.tiempo_max:
                break
            url = self.url_busqueda(barrio, operacion, pagina)
            html = self._get(url)
            nuevos = extraer_anuncios(html, self.nombre, self.base_url, operacion) if html else []
            if pagina == 1:
                self.total_publicados = total_publicados(html, len(nuevos)) if html else None
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
        if operacion not in self.operaciones:
            return {"portal": self.nombre, "url": "—", "error": f"este portal no tiene {operacion}"}
        url = self.url_busqueda(barrio, operacion, 1)
        info = {"portal": self.nombre, "url": url}
        if getattr(self, "usa_navegador", False):
            try:
                html = self._get(url)
            except FuenteError as e:
                return {**info, "error": str(e)}
            r = type("R", (), {"status_code": "navegador", "url": url})()
        else:
            try:
                r = self.sesion.get(url, timeout=self.timeout)
            except requests.RequestException as e:
                return {**info, "error": f"no se pudo conectar: {e.__class__.__name__}"}
            html = r.text
        anuncios = extraer_anuncios(html, self.nombre, self.base_url, operacion)
        info.update(
            estado=r.status_code, url_final=r.url, bytes=len(html), titulo=_titulo(html),
            bloques_json=contar_bloques_json(html), anuncios=len(anuncios),
            total_publicado=total_publicados(html, len(anuncios)),
            dueno_directo=sum(a.es_dueno_directo for a in anuncios),
            ejemplos=[f"{a.titulo[:50]} · ${a.precio:,.0f} · {a.area_m2:g} m² · barrio: {a.barrio or '?'} · "
                      f"{a.publicado_por or 'anunciante ?'}" for a in anuncios[:5]],
        )
        from .extractor import MUESTRAS
        if anuncios and self.nombre in MUESTRAS:
            info["muestra"] = MUESTRAS[self.nombre]
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


class ConNavegador:
    """Para portales que solo muestran los anuncios después de ejecutar JavaScript: se abren con un
    navegador sin ventana (ver navegador.py)."""
    usa_navegador = True
    # Abrir un navegador es lento y gasta memoria: 1 página por defecto. En la revisión diaria de
    # GitHub (más memoria) se sube con COMPARAM_PAGINAS_NAVEGADOR.
    paginas_navegador = int(os.environ.get("COMPARAM_PAGINAS_NAVEGADOR", "1"))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.paginas = min(self.paginas, self.paginas_navegador)

    def _get(self, url: str) -> str:
        from . import navegador
        try:
            return navegador.renderizar(url)
        except FuenteError as e:
            raise FuenteError(f"{self.nombre}: {e}") from e


class FincaRaiz(PortalWeb):
    nombre = "fincaraiz"
    base_url = "https://www.fincaraiz.com.co"
    plantilla = base_url + "/{operacion}/apartamentos/{barrio}/bogota/pagina{pagina}"
    plantilla_ciudad = base_url + "/{operacion}/apartamentos/bogota-dc/pagina{pagina}"


class Metrocuadrado(ConNavegador, PortalWeb):
    # La página llega solo con 6 enlaces (JSON-LD); los anuncios se cargan con JavaScript.
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


class PortalConNavegador(ConNavegador, PortalEmpresa):
    pass


class Habi(PortalConNavegador):
    # Habi compra apartamentos y los revende; solo tiene venta.
    nombre = "habi"
    empresa = "Habi"
    base_url = "https://habi.co"
    operaciones = {"venta": "venta"}
    plantilla_ciudad = base_url + "/venta-apartamentos/bogota?page={pagina}"


class Houm(PortalConNavegador):
    nombre = "houm"
    empresa = "Houm"
    base_url = "https://houm.com"
    # Dirección confirmada por el usuario: https://houm.com/co/venta-apartamentos-bogota
    # En arriendo («arriendo-apartamentos-bogota», supuesta) el navegador se traba siempre: solo
    # venta hasta tener la dirección real de arriendo.
    operaciones = {"venta": "venta"}
    plantilla_ciudad = base_url + "/co/{operacion}-apartamentos-bogota?page={pagina}"


class PortalCiudad(PortalWeb):
    """Portal que se consulta por ciudad; si se busca un barrio, se filtra aquí por su nombre."""
    paginas_ciudad = 3

    def url_busqueda(self, barrio: str, operacion: str, pagina: int) -> str:
        return super().url_busqueda("", operacion, pagina)

    def buscar(self, barrio: str, operacion: str = "venta") -> list[Listing]:
        objetivo = slugify(barrio)
        if objetivo:  # más páginas para que el filtro por barrio encuentre algo
            self.paginas = max(self.paginas, self.paginas_ciudad)
        anuncios = super().buscar("", operacion)
        if objetivo:
            anuncios = [a for a in anuncios
                        if objetivo in slugify(f"{a.barrio} {a.ubicacion} {a.titulo} {a.url}")]
        return anuncios


class MercadoLibre(PortalCiudad):
    """MercadoLibre Inmuebles con su filtro propio «Dueño directo» (/directo/).

    Como el portal ya filtra por dueño directo, sus anuncios se marcan como tales, pero solo si la
    página que respondió conserva el filtro (si el portal lo ignora, no se marcan)."""
    nombre = "mercadolibre"
    base_url = "https://listado.mercadolibre.com.co"
    plantilla_ciudad = base_url + "/apartamentos-{operacion}-en-bogota-dc/directo/_Desde_{desde}_NoIndex_True"
    operaciones = {"venta": "venta", "arriendo": "arriendo"}

    def buscar(self, barrio: str, operacion: str = "venta") -> list[Listing]:
        self.detalle = False
        anuncios = super().buscar(barrio, operacion)
        respuesta = getattr(self, "ultima_respuesta", None)
        con_filtro = respuesta is None or "/directo" in str(getattr(respuesta, "url", "/directo"))
        for a in anuncios:
            if con_filtro and not a.publicado_por.startswith("inmobiliaria"):
                a.es_dueno_directo, a.verificar = True, False
                a.publicado_por = a.publicado_por or "dueño directo (MercadoLibre)"
        return anuncios


class Properati(PortalCiudad):
    nombre = "properati"
    base_url = "https://www.properati.com.co"
    plantilla_ciudad = base_url + "/s/bogota-d-c/apartamento/{operacion}?page={pagina}"


class Rentola(PortalCiudad):
    """Rentola: agregador de arriendos (junta anuncios de muchos sitios). Solo arriendo.

    Dirección enviada por el usuario: /arriendo?location=348474 (código de Bogotá). Se escribió
    como rentola.co.com y antes como rentola.com.co: se prueba el primero que conecte."""
    nombre = "rentola"
    dominios = ("https://rentola.com.co", "https://rentola.co.com")
    base_url = dominios[0]
    operaciones = {"arriendo": "arriendo"}
    ruta = "/{operacion}?location=348474&page={pagina}"
    plantilla_ciudad = base_url + ruta

    def buscar(self, barrio: str, operacion: str = "venta") -> list[Listing]:
        if operacion not in self.operaciones:
            return []  # Rentola no tiene venta
        self.detalle = False
        error = None
        for dominio in self.dominios:
            self.base_url, self.plantilla_ciudad = dominio, dominio + self.ruta
            try:
                return super().buscar(barrio, operacion)
            except FuenteError as e:
                if "no se pudo conectar" not in str(e):
                    raise
                error = e
        raise error

    def diagnosticar(self, barrio: str = "", operacion: str = "venta") -> dict:
        info = {}
        for dominio in self.dominios:  # igual que buscar: el primer dominio que conecte
            self.base_url, self.plantilla_ciudad = dominio, dominio + self.ruta
            info = super().diagnosticar(barrio, operacion)
            if "no se pudo conectar" not in str(info.get("error", "")):
                return info
        return info
