"""Utilidades para interpretar textos de anuncios inmobiliarios colombianos."""
from __future__ import annotations

import re
import unicodedata

# Palabras que indican que quien publica es el propietario.
_DUENO = re.compile(
    r"due[ñn]o\s+directo|propietario\s+directo|directamente\s+(con\s+el\s+)?(due[ñn]o|propietario)"
    r"|sin\s+intermediari|no\s+(se\s+atienden\s+|atiendo\s+|acepto\s+)?inmobiliarias"
    r"|abstenerse\s+inmobiliarias|particular",
    re.I,
)
# Palabras que indican inmobiliaria / agente.
_INMOBILIARIA = re.compile(
    r"inmobiliaria|bienes\s+ra[ií]ces|real\s+estate|propiedad\s+ra[ií]z|agente|asesor\s+comercial"
    r"|broker|corredor|\bs\.?a\.?s\b|\bltda\b",
    re.I,
)

# Celulares colombianos (3xx xxx xxxx) y fijos de Bogotá (601 xxx xxxx), con o sin +57.
_TEL = re.compile(
    r"(?<!\d)(?:\+?57[\s.-]?)?(?:(3\d{2})[\s.-]?(\d{3})[\s.-]?(\d{4})|(60\d)[\s.-]?(\d{3})[\s.-]?(\d{4}))(?!\d)"
)


def slugify(texto: str) -> str:
    t = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    t = re.sub(r"[^a-zA-Z0-9]+", "-", t.strip().lower())
    return t.strip("-")


def parse_number(valor) -> float | None:
    """Convierte '$ 450.000.000', '450000000', '72,5 m²' o 72.5 en float."""
    if valor is None or isinstance(valor, bool):
        return None
    if isinstance(valor, (int, float)):
        return float(valor)
    s = str(valor)
    s = re.sub(r"[^\d.,]", "", s)
    if not s:
        return None
    # En Colombia el punto es separador de miles y la coma decimal.
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        ent, _, dec = s.rpartition(",")
        s = f"{ent.replace(',', '')}.{dec}" if len(dec) <= 2 else s.replace(",", "")
    elif s.count(".") > 1 or re.search(r"\.\d{3}$", s):
        s = s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        return None


def extract_phones(*textos: str) -> list[str]:
    vistos: list[str] = []
    for texto in textos:
        if not texto:
            continue
        for m in _TEL.finditer(str(texto)):
            g = [x for x in m.groups() if x]
            numero = " ".join(g)
            if numero not in vistos:
                vistos.append(numero)
    return vistos


_EMPRESA = re.compile(
    r"inmobiliari|bienes|ra[ií]z|ra[ií]ces|propiedad|inversion|constructor|consult|asesor|agencia|grupo|group"
    r"|realty|real\s*e?state|\blab\b|\bstudio\b|\bteam\b|\bgroup\b|\bpartners\b|broker|corredor|capital|servicios|soluciones|proyectos|negocios|gesti[oó]n"
    r"|\bs\.?a\.?s\b|\bs\.?a\b|ltda|\bcia\b|&|\.com|\bde\s+colombia\b|home|house|homes|living|arriendos",
    re.I,
)


def parece_persona(nombre: str) -> bool:
    """«Julia Clemencia Toro» sí; «Inmobiliaria XYZ SAS», «Bienes Raíces Ltda» o «Metro123» no."""
    n = (nombre or "").strip()
    if not n or _EMPRESA.search(n) or re.search(r"\d", n):
        return False
    palabras = n.split()
    return 1 <= len(palabras) <= 5 and all(re.fullmatch(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ.'-]+", p) for p in palabras)


def clasificar_anunciante(publicado_por: str = "", nombre: str = "", descripcion: str = "") -> tuple[str, bool]:
    """Devuelve (etiqueta, es_dueno_directo)."""
    tipo = f"{publicado_por} {nombre}".strip()
    if tipo:
        if _DUENO.search(tipo) or re.search(r"\b(owner|private|propietario|due[ñn]o|persona)\b", tipo, re.I):
            return "dueño directo", True
        if _INMOBILIARIA.search(tipo) or re.search(r"\b(agency|company|realestate|constructora)\b", tipo, re.I):
            return "inmobiliaria", False
    if descripcion and _DUENO.search(descripcion):
        return "dueño directo", True
    # Publicado a nombre de una persona (no de una empresa): en los portales casi siempre es el propietario.
    if not publicado_por.strip() and parece_persona(nombre):
        return f"persona natural · {nombre.strip()}", True
    if descripcion and _INMOBILIARIA.search(descripcion):
        return "inmobiliaria", False
    return "", False


_SIN_PARQ = re.compile(r"\b(?:sin|no\s+(?:tiene|incluye|cuenta\s+con|hay))\s+(?:parqueaderos?|garajes?|parking)\b", re.I)
_N_PARQ = re.compile(r"(?<![\d.,])(\d{1,2})\s*(?:parqueaderos?|parq\.?|garajes?|garages?|parking|estacionamientos?)\b"
                     r"(?!\s+(?:de\s+)?(?:visitantes|comunal))", re.I)
_UN_PARQ = re.compile(r"\b(?:con|incluye|tiene|cuenta\s+con|m[aá]s|y)\s+(?:un\s+|su\s+)?(?:parqueadero|garaje|garage)"
                      r"(?!\s+(?:de\s+)?(?:visitantes|comunal))|\b(?:parqueadero|garaje)\s+(?:privado|propio|cubierto|"
                      r"independiente|asignado|exclusivo|incluido|doble|sencillo)\b", re.I)


def parqueaderos_en_texto(*textos: str) -> int | None:
    """Parqueaderos que menciona el texto del anuncio: «2 parqueaderos», «incluye parqueadero» (1),
    «sin parqueadero» (0). None si no lo menciona (no se ignoran los de visitantes o comunales)."""
    t = " ".join(x for x in textos if x)
    if not t:
        return None
    if _SIN_PARQ.search(t):
        return 0
    if m := _N_PARQ.search(t):
        n = int(m.group(1))
        return n if n <= 10 else None
    if _UN_PARQ.search(t):
        return 1
    return None
