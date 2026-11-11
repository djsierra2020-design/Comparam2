"""MercadoLibre por su API oficial (acceso de desarrollador), en lugar de leer sus páginas.

Requiere las variables ML_CLIENT_ID y ML_CLIENT_SECRET de la aplicación creada en
developers.mercadolibre.com.co (en Render y como secretos de GitHub). Sin ellas, esta fuente no se
usa. Se piden los anuncios de la categoría Inmuebles de Colombia (MCO1459) con texto de búsqueda
«apartamento venta|arriendo <barrio> bogota» y se filtran: solo apartamentos de la operación pedida.

Los nombres de campos de la API se leen con cuidado (pueden faltar): si la API cambia o niega el
acceso, se reporta el código y el mensaje como error de la fuente.
"""
from __future__ import annotations

import os
import re
import time

import requests

from ..models import Listing
from ..parsing import parse_number, slugify
from .base import FuenteError, Source

API = "https://api.mercadolibre.com"
CATEGORIA_INMUEBLES = os.environ.get("ML_CATEGORIA", "MCO1459")
_TOKEN: dict = {"valor": None, "vence": 0.0}


def configurado() -> bool:
    return bool(os.environ.get("ML_CLIENT_ID") and os.environ.get("ML_CLIENT_SECRET"))


def _atributo(r: dict, *ids: str):
    for a in r.get("attributes") or []:
        if a.get("id") in ids:
            vs = a.get("value_struct") or {}
            return vs.get("number") if vs.get("number") is not None else a.get("value_name")
    return None


class MercadoLibreAPI(Source):
    nombre = "mercadolibre"
    por_pagina = 50

    def __init__(self, paginas: int = 2, sesion: requests.Session | None = None, timeout: float = 20, **_):
        self.paginas = paginas
        self.sesion = sesion or requests.Session()
        self.timeout = timeout
        self.total_publicados = None

    # --- acceso -----------------------------------------------------------------------------
    def _token(self) -> str:
        if _TOKEN["valor"] and time.time() < _TOKEN["vence"]:
            return _TOKEN["valor"]
        if not configurado():
            raise FuenteError("mercadolibre: faltan ML_CLIENT_ID y ML_CLIENT_SECRET")
        try:
            r = self.sesion.post(f"{API}/oauth/token", timeout=self.timeout, data={
                "grant_type": "client_credentials", "client_id": os.environ["ML_CLIENT_ID"],
                "client_secret": os.environ["ML_CLIENT_SECRET"]})
        except requests.RequestException as e:
            raise FuenteError(f"mercadolibre: no se pudo conectar ({e.__class__.__name__})") from e
        if r.status_code != 200:
            raise FuenteError(f"mercadolibre: la API negó el acceso (HTTP {r.status_code}: {r.text[:150]})")
        d = r.json()
        _TOKEN.update(valor=d["access_token"], vence=time.time() + int(d.get("expires_in", 21600)) - 300)
        return _TOKEN["valor"]

    def _get(self, ruta: str, params: dict) -> dict:
        for intento in range(2):
            try:
                r = self.sesion.get(f"{API}{ruta}", params=params, timeout=self.timeout,
                                    headers={"Authorization": f"Bearer {self._token()}"})
            except requests.RequestException as e:
                raise FuenteError(f"mercadolibre: no se pudo conectar ({e.__class__.__name__})") from e
            if r.status_code in (401, 403) and intento == 0:
                # El token pudo pedirse antes de un cambio de permisos de la aplicación: uno nuevo.
                _TOKEN.update(valor=None, vence=0)
                continue
            break
        if r.status_code != 200:
            raise FuenteError(f"mercadolibre: la API respondió HTTP {r.status_code} ({r.text[:150]})")
        return r.json()

    # --- búsqueda ---------------------------------------------------------------------------
    def buscar(self, barrio: str, operacion: str = "venta") -> list[Listing]:
        texto = " ".join(x for x in ("apartamento", operacion, barrio, "bogota") if x)
        objetivo = slugify(barrio)
        salida: list[Listing] = []
        for pagina in range(self.paginas):
            d = self._get(f"/sites/MCO/search", {"category": CATEGORIA_INMUEBLES, "q": texto,
                                                  "offset": pagina * self.por_pagina, "limit": self.por_pagina})
            if pagina == 0:
                self.total_publicados = (d.get("paging") or {}).get("total")
            resultados = d.get("results") or []
            for r in resultados:
                a = self._anuncio(r, operacion)
                if a and (not objetivo or objetivo in slugify(f"{a.barrio} {a.ubicacion} {a.titulo}")):
                    salida.append(a)
            if len(resultados) < self.por_pagina:
                break
        return salida

    def _anuncio(self, r: dict, operacion: str) -> Listing | None:
        op = str(_atributo(r, "OPERATION") or "").lower()
        if op and ("venta" if operacion == "venta" else "arriendo") not in op and \
                not (operacion == "arriendo" and "alquiler" in op):
            return None
        tipo = str(_atributo(r, "PROPERTY_TYPE") or "").lower()
        if tipo and "apartamento" not in tipo:
            return None
        if (r.get("currency_id") or "COP") != "COP":
            return None
        precio = parse_number(r.get("price"))
        area = _atributo(r, "COVERED_AREA", "TOTAL_AREA")
        area = parse_number(area) if not isinstance(area, (int, float)) else float(area)
        if not precio or not area:
            return None
        loc = r.get("location") or {}
        partes = [((loc.get(k) or {}).get("name") or "") for k in ("neighborhood", "city", "state")]
        barrio = partes[0] or ""
        vendedor = r.get("seller") or {}
        inmobiliaria = vendedor.get("real_estate_agency")
        dueno = inmobiliaria is False
        hab, ban = _atributo(r, "BEDROOMS", "ROOMS"), _atributo(r, "FULL_BATHROOMS", "BATHROOMS")
        return Listing(
            fuente=self.nombre, titulo=(r.get("title") or "Apartamento")[:120], barrio=barrio,
            precio=float(precio), area_m2=float(area), url=r.get("permalink") or "",
            publicado_por="dueño directo (MercadoLibre)" if dueno else ("inmobiliaria" if inmobiliaria else ""),
            es_dueno_directo=dueno, habitaciones=int(parse_number(hab)) if parse_number(hab) else None,
            banos=int(parse_number(ban)) if parse_number(ban) else None,
            ubicacion=" | ".join(p for p in partes if p),
            publicado=(re.match(r"\d{4}-\d{2}-\d{2}", r.get("date_created") or "") or [""])[0],
        )

    def diagnosticar(self, barrio: str = "", operacion: str = "venta") -> dict:
        info = {"portal": self.nombre, "url": f"{API}/sites/MCO/search (API oficial)"}
        try:
            self.paginas = 1
            anuncios = self.buscar(barrio, operacion)
        except FuenteError as e:
            return {**info, "error": str(e)}
        return {**info, "estado": 200, "url_final": info["url"], "bytes": 0, "titulo": "API de MercadoLibre",
                "bloques_json": 1, "anuncios": len(anuncios), "total_publicado": self.total_publicados,
                "dueno_directo": sum(a.es_dueno_directo for a in anuncios),
                "ejemplos": [f"{a.titulo[:50]} · ${a.precio:,.0f} · {a.area_m2:g} m² · {a.publicado_por or '?'}"
                             for a in anuncios[:5]]}
