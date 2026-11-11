"""Extractor genérico de anuncios a partir del HTML de un portal.

Los portales colombianos (Finca Raíz, Metrocuadrado, Ciencuadras) son
aplicaciones Next.js/Angular que incrustan los resultados en JSON dentro del
HTML (``__NEXT_DATA__``, ``application/ld+json``, estado serializado...). En
lugar de depender de la estructura exacta de cada portal —que cambia seguido—
recorremos todo el JSON y reconocemos los objetos que "parecen" un anuncio:
tienen un precio y un área.
"""
from __future__ import annotations

import json
import re
from typing import Any, Iterator
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..models import Listing
from ..parsing import clasificar_anunciante, extract_phones, parece_persona, parse_number

K_PRECIO_VENTA = ("salePrice", "sale_price", "precioVenta", "mvalorventa", "valorVenta")
K_PRECIO_ARRIENDO = ("rentPrice", "rent_price", "precioArriendo", "mvalorarriendo", "valorArriendo", "canon")
K_PRECIO = ("price", "precio", "valor", "amount", "priceValue")
K_AREA = ("area", "builtArea", "built_area", "areaConstruida", "areac", "mareac", "marea", "m2",
          "surface", "floorSize", "squareMeters", "privateArea", "areaPrivada")
K_TITULO = ("title", "titulo", "name", "mnombre", "nombre")
K_DESC = ("description", "descripcion", "mdescripcion", "comment", "comentario")
K_URL = ("link", "url", "permalink", "slug", "path", "detailUrl", "linkDetalle")
K_BARRIO = ("neighborhood", "neighbourhood", "barrio", "mbarrio", "sector", "commonNeighborhood")
K_HAB = ("rooms", "bedrooms", "habitaciones", "mnrocuartos", "numberOfRooms")
K_BANOS = ("bathrooms", "banos", "baños", "mnrobanos", "numberOfBathroomsTotal")
K_FECHA = ("publicationDate", "publication_date", "publishedAt", "published_at", "datePublished", "createdAt",
           "created_at", "creationDate", "fechaPublicacion", "fecha_publicacion", "fechaCreacion", "date_created",
           "fecha", "mfechapublicacion", "activationDate")
K_TEL = ("phone", "phones", "telephone", "telefono", "telefonos", "cellphone", "celular",
         "whatsapp", "contactPhone", "mobile")
K_ANUNCIANTE = ("owner", "publisher", "advertiser", "client", "seller", "contact", "inmobiliaria",
                "company", "agency", "user", "offeredBy", "realEstate")
K_TIPO_ANUNCIANTE = ("type", "tipo", "ownerType", "publisherType", "typeOwner", "clientType",
                     "sellerType", "userType", "@type")


def _primero(d: dict, claves) -> Any:
    for k in claves:
        if k in d and d[k] not in (None, "", [], {}):
            return d[k]
    return None


def _num(v) -> float | None:
    if isinstance(v, dict):  # p.ej. {"value": 70, "unitCode": "MTK"} o {"amount": ...}
        v = _primero(v, ("value", "amount", "valor", "number"))
    if isinstance(v, list) and v:
        v = v[0]
    return parse_number(v)


def _texto(v) -> str:
    if isinstance(v, dict):
        v = _primero(v, ("name", "nombre", "title", "label", "value"))
    if isinstance(v, list):
        return " ".join(_texto(x) for x in v)
    return "" if v is None else str(v)


def _telefonos(d: dict, profundidad: int = 0) -> list[str]:
    """Busca teléfonos en las claves conocidas del anuncio y del anunciante."""
    encontrados: list[str] = []
    for k, v in d.items():
        if any(k.lower() == t.lower() or k.lower().endswith(t.lower()) for t in K_TEL):
            vals = v if isinstance(v, list) else [v]
            for x in vals:
                x = _primero(x, ("number", "numero", "value", "phone")) if isinstance(x, dict) else x
                encontrados += extract_phones(str(x))
        elif isinstance(v, dict) and profundidad < 2 and k in K_ANUNCIANTE:
            encontrados += _telefonos(v, profundidad + 1)
    return list(dict.fromkeys(encontrados))


def _anunciante(d: dict) -> tuple[str, str]:
    """Devuelve (tipo, nombre) del anunciante si el anuncio lo trae."""
    tipo, nombre = "", ""
    for k in K_ANUNCIANTE:
        sub = d.get(k)
        if isinstance(sub, dict):
            tipo = tipo or _texto(_primero(sub, K_TIPO_ANUNCIANTE))
            nombre = nombre or _texto(_primero(sub, ("name", "nombre", "companyName", "razonSocial")))
        elif isinstance(sub, str) and not nombre:
            nombre = sub
    tipo = tipo or _texto(_primero(d, ("ownerType", "publisherType", "typeOwner", "clientType",
                                       "sellerType", "publicadoPor", "tipoAnunciante")))
    if d.get("isOwner") is True or d.get("dueñoDirecto") is True or d.get("duenoDirecto") is True:
        tipo = "dueño directo"
    if d.get("isAgency") is True or d.get("isRealEstate") is True:
        tipo = "inmobiliaria"
    return tipo, nombre


def _barrio(d: dict) -> str:
    b = _primero(d, K_BARRIO)
    if b is None:
        for k in ("location", "locations", "address", "ubicacion", "direccion"):
            sub = d.get(k)
            if isinstance(sub, dict) and (b := _primero(sub, K_BARRIO)) is not None:
                break
    if isinstance(b, list):  # algunos portales traen varios barrios: se toma el primero
        b = b[0] if b else None
    return _texto(b)


K_UBICACION = K_BARRIO + ("locality", "localidad", "zone", "zona", "city", "ciudad", "district",
                          "upz", "name", "nombre", "label")


def _ubicacion(d: dict) -> str:
    """Junta todo el texto de ubicación del anuncio (barrio, localidad, zona...)."""
    partes = [_texto(d.get(k)) for k in K_BARRIO + ("locality", "localidad", "zone", "zona", "upz", "district")
              if d.get(k) and not isinstance(d.get(k), bool)]
    for k in ("location", "locations", "address", "ubicacion", "direccion"):
        sub = d.get(k)
        subs = sub if isinstance(sub, list) else [sub]
        for x in subs:
            if isinstance(x, dict):
                for kk in K_UBICACION:
                    v = x.get(kk)
                    if isinstance(v, list):
                        partes += [_texto(i) for i in v]
                    elif v:
                        partes.append(_texto(v))
            elif isinstance(x, str):
                partes.append(x)
    return " | ".join(dict.fromkeys(p for p in partes if p))


def _fecha(d: dict) -> str:
    """Fecha de publicación en formato AAAA-MM-DD (acepta ISO, «DD/MM/AAAA» y marcas de tiempo)."""
    from datetime import datetime, timezone

    v = _primero(d, K_FECHA)
    if v is None or isinstance(v, bool):
        return ""
    if isinstance(v, (int, float)):
        seg = v / 1000 if v > 10**11 else v  # milisegundos o segundos
        try:
            return datetime.fromtimestamp(seg, tz=timezone.utc).date().isoformat() if seg > 10**9 else ""
        except (OverflowError, OSError, ValueError):
            return ""
    t = str(v)
    if m := re.match(r"(\d{4})-(\d{2})-(\d{2})", t):
        return m.group(0)
    if m := re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", t):
        return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    return ""


def _recorrer(nodo: Any) -> Iterator[dict]:
    pila = [nodo]
    while pila:
        n = pila.pop()
        if isinstance(n, dict):
            yield n
            pila.extend(n.values())
        elif isinstance(n, list):
            pila.extend(n)


def _bloques_json(html: str) -> Iterator[Any]:
    soup = BeautifulSoup(html, "html.parser")
    for s in soup.find_all("script"):
        tipo = (s.get("type") or "").lower()
        txt = s.string or s.get_text() or ""
        if not txt.strip():
            continue
        if "json" in tipo or s.get("id") in ("__NEXT_DATA__", "serverApp-state", "ng-state"):
            try:
                yield json.loads(txt)
            except json.JSONDecodeError:
                # Angular (TransferState) escapa el JSON: &q; = comillas, &a; = &, &s; = ', &l; = <, &g; = >
                if "&q;" in txt:
                    t = (txt.replace("&q;", '"').replace("&s;", "'").replace("&l;", "<")
                         .replace("&g;", ">").replace("&a;", "&"))
                    try:
                        yield json.loads(t)
                    except json.JSONDecodeError:
                        pass
        else:  # window.__INITIAL_STATE__ = {...};
            m = re.search(r"=\s*(\{.*\})\s*;?\s*$", txt, re.S)
            if m:
                try:
                    yield json.loads(m.group(1))
                except json.JSONDecodeError:
                    pass


def extraer_anuncios(html: str, fuente: str, base_url: str, operacion: str = "venta",
                     barrio_por_defecto: str = "") -> list[Listing]:
    anuncios = extraer_de_json(list(_bloques_json(html)), fuente, base_url, operacion, barrio_por_defecto)
    return anuncios or extraer_de_tarjetas(html, fuente, base_url, operacion, barrio_por_defecto)


_PRECIO_TXT = re.compile(r"\$\s?(\d{1,3}(?:[.,]\d{3}){2,})")
_AREA_TXT = re.compile(r"(\d{2,4}(?:[.,]\d{1,2})?)\s?(?:m²|m2|mts2?|mt2|metros)", re.I)
_TELEFONO_TXT = re.compile(r"tel(?:é|e)fono|whatsapp|llamar", re.I)


def _datos_tarjeta(texto: str, operacion: str) -> tuple[float, float] | None:
    minimo = 300_000 if operacion == "arriendo" else 20_000_000
    precios = [p for m in _PRECIO_TXT.finditer(texto) if (p := parse_number(m.group(1))) and p >= minimo]
    areas = [a for m in _AREA_TXT.finditer(texto) if (a := parse_number(m.group(1))) and 15 <= a <= 2_000]
    if not precios or not areas:
        return None
    # El precio más alto que dé un precio por m² razonable (descarta la administración y
    # números pegados por error).
    bajo, alto = M2_ARRIENDO if operacion == "arriendo" else M2_VENTA
    buenos = [p for p in precios if bajo <= p / areas[0] <= alto]
    return (max(buenos), areas[0]) if buenos else None


_RUIDO_TITULO = re.compile(r"\b(anterior|siguiente|previous|next|ver fotos|ver m[aá]s)\b", re.I)


def _limpiar_titulo(t: str) -> str:
    """Quita del título los botones del carrusel y los datos que ya se muestran aparte (precio,
    área, números sueltos), p. ej. «Anterior Siguiente $ 250.000.000 30 m² 1 1 Apartamento…»."""
    t = _RUIDO_TITULO.sub(" ", t)
    t = _PRECIO_TXT.sub(" ", t)
    t = re.sub(r"\$\s*", " ", t)
    t = _AREA_TXT.sub(" ", t)
    t = re.sub(r"(?<![\w-])\d(?![\w-])", " ", t)  # cifras sueltas (habitaciones, baños)
    return re.sub(r"\s+", " ", t).strip(" ·-|")


def extraer_de_tarjetas(html: str, fuente: str, base_url: str, operacion: str = "venta",
                        barrio_por_defecto: str = "") -> list[Listing]:
    """Lee los anuncios desde las tarjetas visibles de la página (precio, área y enlace), para
    portales que no incluyen los datos en JSON. Toma el elemento más pequeño que contenga un
    precio y un área, para no mezclar varios anuncios en uno."""
    soup = BeautifulSoup(html or "", "html.parser")
    for basura in soup(["script", "style", "noscript", "svg"]):
        basura.decompose()
    # Se parte de cada texto con un precio y se sube por sus contenedores hasta el primero que tenga
    # también un área: esa es la tarjeta. Así el costo crece con el número de precios y no con el
    # tamaño de la página (antes, en páginas grandes como Rentola, tardaba decenas de minutos).
    bloques = {"article", "li", "div", "a", "section"}
    tarjetas: dict[int, tuple] = {}
    for nodo in soup.find_all(string=_PRECIO_TXT):
        el = nodo.parent
        for _ in range(10):
            if el is None or el.name in ("body", "html", "[document]"):
                break
            if el.name in bloques:
                texto = el.get_text(" ", strip=True)
                if len(texto) > 2_000:
                    break
                if datos := _datos_tarjeta(texto, operacion):
                    tarjetas.setdefault(id(el), (el, texto, datos))
                    break
            el = el.parent
    elegidas = {id(el) for el, _, _ in tarjetas.values()}
    anuncios: list[Listing] = []
    vistos: set = set()
    for el, texto, (precio, area) in tarjetas.values():
        # Si una tarjeta contiene otra tarjeta elegida, es un contenedor: se omite.
        if any(id(p) in elegidas for p in el.find_all(list(bloques)) if p is not el):
            continue
        enlace = el if el.name == "a" and el.get("href") else el.find("a", href=True)
        if enlace is None:
            enlace = el.find_parent("a", href=True)
        url = urljoin(base_url, enlace["href"]) if enlace is not None else ""
        clave = (url, precio, area)
        if clave in vistos:
            continue
        vistos.add(clave)
        titulo_el = el.find(["h1", "h2", "h3", "h4"]) or enlace
        titulo = (titulo_el.get_text(" ", strip=True) if titulo_el is not None else "")[:200]
        titulo = _limpiar_titulo(titulo)[:120]
        if not barrio_por_defecto:
            from ..busqueda import zona_de  # barrio del título: «… en venta en Modelia, Fontibón»
            barrio_tarjeta = zona_de(Listing(fuente, titulo, "", 1, 1))
        else:
            barrio_tarjeta = barrio_por_defecto
        if _NO_APTO.search(titulo) or _NO_APTO.search(texto[:200]):
            continue
        etiqueta, dueno = clasificar_anunciante("", "", texto)
        hab = re.search(r"(\d+)\s*(?:hab|alcoba)", texto, re.I)
        ban = re.search(r"(\d+)\s*baño", texto, re.I)
        anuncios.append(Listing(
            fuente=fuente, titulo=titulo or "Apartamento", barrio=barrio_tarjeta, precio=precio,
            area_m2=area, url=url, telefonos=extract_phones(texto) if _TELEFONO_TXT.search(texto) else [],
            publicado_por=etiqueta, es_dueno_directo=dueno,
            habitaciones=int(hab.group(1)) if hab else None, banos=int(ban.group(1)) if ban else None,
            descripcion=texto[:500],
        ))
    return anuncios


# Rango razonable del precio por m² en Bogotá: fuera de él casi siempre es un error de lectura
# (p. ej. dos precios pegados) o no es un apartamento.
M2_VENTA = (700_000, 35_000_000)
M2_ARRIENDO = (5_000, 250_000)
_NO_APTO = re.compile(r"\b(casas?|lotes?|locales?|oficinas?|bodegas?|fincas?|consultorios?|edificios?|"
                      r"casalotes?|parqueaderos?)\s+en\s+(venta|arriendo)", re.I)


def es_valido(titulo: str, precio: float, area: float, operacion: str) -> bool:
    if _NO_APTO.search(titulo or ""):
        return False
    if not (15 <= area <= 2_000):
        return False
    bajo, alto = M2_ARRIENDO if operacion == "arriendo" else M2_VENTA
    return bajo <= precio / area <= alto


def contar_bloques_json(html: str) -> int:
    return sum(1 for _ in _bloques_json(html))


# Muestra de los datos crudos del primer anuncio de cada portal (para /diagnostico).
MUESTRAS: dict[str, str] = {}


def _muestra(d: dict) -> str:
    """Datos del anuncio en texto corto: claves y valores simples (sin listas largas ni textos)."""
    partes = []
    for k, v in d.items():
        if isinstance(v, dict):
            v = "{" + ", ".join(f"{kk}: {str(vv)[:40]}" for kk, vv in list(v.items())[:8]) + "}"
        elif isinstance(v, list):
            v = f"[{len(v)} elementos]"
        partes.append(f"{k}: {str(v)[:60]}")
    return " · ".join(partes)[:1500]


def extraer_de_json(bloques: list, fuente: str, base_url: str, operacion: str = "venta",
                    barrio_por_defecto: str = "") -> list[Listing]:
    """Reconoce anuncios en cualquier estructura JSON de la página."""
    claves_precio = (K_PRECIO_ARRIENDO if operacion == "arriendo" else K_PRECIO_VENTA) + K_PRECIO
    vistos: set[tuple] = set()
    anuncios: list[Listing] = []
    for bloque in bloques:
        for d in _recorrer(bloque):
            precio = _num(_primero(d, claves_precio))
            area = _num(_primero(d, K_AREA))
            if not precio or not area:
                continue
            # Descarta valores absurdos (precios por m², administración, etc.).
            minimo = 300_000 if operacion == "arriendo" else 20_000_000
            if precio < minimo or not (15 <= area <= 2_000):
                continue
            titulo = _texto(_primero(d, K_TITULO))
            if not es_valido(titulo, precio, area, operacion):
                continue
            descripcion = _texto(_primero(d, K_DESC))
            url = _texto(_primero(d, K_URL))
            if url and not url.startswith("http"):
                url = urljoin(base_url, url)
            clave = (url or titulo, precio, area)
            if clave in vistos:
                continue
            vistos.add(clave)
            if not anuncios:
                MUESTRAS[fuente] = _muestra(d)
            tipo, nombre = _anunciante(d)
            etiqueta, dueno = clasificar_anunciante(tipo, nombre, descripcion)
            # Nombre y apellido (2+ palabras): una sola palabra suele ser una marca («PROVIVENDA»).
            verificar = (not dueno and etiqueta == "inmobiliaria" and parece_persona(nombre)
                         and len(nombre.split()) >= 2)
            if verificar:
                etiqueta = f"verificar · {nombre.strip()}"
            telefonos = list(dict.fromkeys(_telefonos(d) + extract_phones(descripcion)))
            anuncios.append(Listing(
                fuente=fuente,
                titulo=titulo or (f"Apartamento en {_barrio(d)}" if _barrio(d) else "Apartamento"),
                barrio=_barrio(d) or barrio_por_defecto,
                ubicacion=_ubicacion(d),
                precio=precio,
                area_m2=area,
                url=url,
                telefonos=telefonos,
                publicado_por=etiqueta or (nombre and f"{nombre}") or "",
                es_dueno_directo=dueno,
                verificar=verificar,
                habitaciones=int(h) if (h := _num(_primero(d, K_HAB))) else None,
                banos=int(b) if (b := _num(_primero(d, K_BANOS))) else None,
                descripcion=descripcion[:500],
                publicado=_fecha(d),
            ))
    return anuncios


_CLAVES_TOTAL = ("total", "totalHits", "totalResults", "totalCount", "total_results", "totalElements",
                 "numFound", "totalItems", "nbHits", "resultsCount", "totalListings", "totalRecords",
                 "numberOfItems", "total_count", "cantidad", "totalInmuebles")
_TOTAL_TXT = re.compile(r"(\d{1,3}(?:[.,]\d{3})+|\d{2,6})\s+(?:apartamentos|inmuebles|resultados|propiedades|anuncios)\b",
                        re.I)


def total_publicados(html: str, encontrados: int = 0) -> int | None:
    """Cuántos anuncios dice tener el portal para esa búsqueda (no solo los de esta página).
    Se busca en los datos de la página (total, totalHits...) y, si no, en el texto visible
    («1.234 apartamentos»). Devuelve None si el portal no lo indica."""
    def valido(n) -> bool:
        return isinstance(n, int) and encontrados <= n <= 500_000 and n > 0

    candidatos: list[int] = []
    for bloque in _bloques_json(html):
        for d in _recorrer(bloque):
            for k in _CLAVES_TOTAL:
                v = d.get(k)
                if isinstance(v, dict):  # {"value": 1234} (Elasticsearch)
                    v = v.get("value")
                if isinstance(v, str) and v.replace(".", "").replace(",", "").isdigit():
                    v = int(v.replace(".", "").replace(",", ""))
                if valido(v) and not isinstance(v, bool):
                    candidatos.append(v)
    if candidatos:
        return max(candidatos)
    soup = BeautifulSoup(html or "", "html.parser")
    for x in soup(["script", "style", "noscript"]):
        x.decompose()
    textos = [int(re.sub(r"[.,]", "", m.group(1))) for m in _TOTAL_TXT.finditer(soup.get_text(" ", strip=True))]
    textos = [n for n in textos if valido(n)]
    return max(textos) if textos else None
