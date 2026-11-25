"""Lector de páginas web públicas para Landings IA.

Devuelve el texto útil (títulos, párrafos, listas, reseñas) y las fotos de una página,
para que la IA use el contenido real del sitio del cliente o la estructura de una LP de
referencia. Solo lee direcciones públicas: rechaza IPs privadas o locales (SSRF).
"""
from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

MAX_BYTES = 2_000_000
MAX_TEXTO = 12_000
MAX_FOTOS = 16
AGENTE = "Mozilla/5.0 (compatible; LandingsIA/1.0; +https://landings-ia.onrender.com)"
_NO_FOTO = re.compile(r"(logo|icon|sprite|favicon|avatar|badge|pixel|spinner|placeholder|loader|emoji|flag)", re.I)
_EXT_FOTO = re.compile(r"\.(jpe?g|png|webp)(\?|#|$)", re.I)


class LectorError(Exception):
    pass


def _es_publica(url: str) -> bool:
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        return False
    try:
        infos = socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == "https" else 80))
    except OSError:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                or ip.is_multicast or ip.is_unspecified):
            return False
    return True


def _descargar(url: str) -> tuple[str, bytes]:
    for _ in range(5):  # sigue redirecciones a mano para revisar cada destino
        if not _es_publica(url):
            raise LectorError("La dirección no es una página pública válida.")
        try:
            r = requests.get(url, timeout=15, allow_redirects=False, stream=True,
                             headers={"User-Agent": AGENTE, "Accept": "text/html,*/*;q=0.8"})
        except requests.RequestException as e:
            raise LectorError(f"No se pudo abrir la página ({e.__class__.__name__}).") from e
        if r.is_redirect and r.headers.get("Location"):
            url = urljoin(url, r.headers["Location"])
            r.close()
            continue
        if r.status_code >= 400:
            raise LectorError(f"La página respondió {r.status_code}.")
        if "html" not in r.headers.get("Content-Type", "text/html"):
            raise LectorError("La dirección no es una página web (HTML).")
        contenido = r.raw.read(MAX_BYTES, decode_content=True)
        r.close()
        return url, contenido
    raise LectorError("Demasiadas redirecciones.")


def _fotos(soup: BeautifulSoup, base: str) -> list[str]:
    candidatas = [m.get("content") for m in soup.select('meta[property="og:image"], meta[name="twitter:image"]')]
    for img in soup.find_all("img"):
        src = img.get("data-src") or img.get("data-lazy-src") or img.get("src") or ""
        if not src and img.get("srcset"):
            src = img["srcset"].split(",")[-1].strip().split(" ")[0]
        candidatas.append(src)
    vistas, fotos = set(), []
    for src in candidatas:
        if not src or src.startswith("data:"):
            continue
        u = urljoin(base, src.strip())
        if u in vistas or not _EXT_FOTO.search(u) or _NO_FOTO.search(u):
            continue
        vistas.add(u)
        fotos.append(u)
        if len(fotos) >= MAX_FOTOS:
            break
    return fotos


def leer_pagina(url: str) -> dict:
    url = (url or "").strip()
    if not url:
        raise LectorError("Falta la dirección.")
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    url, contenido = _descargar(url)
    soup = BeautifulSoup(contenido, "html.parser")
    titulo = soup.title.get_text(" ", strip=True) if soup.title else ""
    meta = soup.find("meta", attrs={"name": "description"})
    descripcion = meta.get("content", "").strip() if meta else ""
    fotos = _fotos(soup, url)
    for t in soup(["script", "style", "noscript", "svg", "iframe", "form"]):
        t.decompose()
    lineas, vistas = [], set()
    for el in soup.find_all(["h1", "h2", "h3", "h4", "p", "li", "blockquote", "td", "figcaption"]):
        if el.find(["p", "li", "h1", "h2", "h3", "h4", "blockquote"]):
            continue  # el texto se toma del elemento más interno
        txt = re.sub(r"\s+", " ", el.get_text(" ", strip=True))
        if len(txt) < 3 or txt.lower() in vistas:
            continue
        vistas.add(txt.lower())
        lineas.append(("## " if el.name in ("h1", "h2", "h3", "h4") else "") + txt)
    texto = "\n".join(lineas)[:MAX_TEXTO]
    if not texto:
        raise LectorError("La página no tiene texto legible (puede que se arme con JavaScript).")
    return {"url": url, "titulo": titulo, "descripcion": descripcion, "texto": texto, "imagenes": fotos}
