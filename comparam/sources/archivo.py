"""Fuente local: un CSV con anuncios (exportados, copiados a mano o de ejemplo).

Columnas reconocidas (encabezados en español o inglés): titulo, barrio,
precio, area_m2, telefono(s), publicado_por, url, habitaciones, banos,
descripcion, operacion.
"""
from __future__ import annotations

import csv
from pathlib import Path

from ..models import Listing
from ..parsing import clasificar_anunciante, extract_phones, parse_number, slugify, parqueaderos_en_texto
from .base import Source

ALIAS = {
    "titulo": ("titulo", "title", "nombre"),
    "barrio": ("barrio", "neighborhood", "sector"),
    "precio": ("precio", "price", "valor"),
    "area_m2": ("area_m2", "area", "m2", "metros"),
    "telefonos": ("telefonos", "telefono", "phone", "celular", "contacto"),
    "publicado_por": ("publicado_por", "anunciante", "tipo_anunciante"),
    "url": ("url", "link", "enlace"),
    "habitaciones": ("habitaciones", "rooms"),
    "banos": ("banos", "baños", "bathrooms"),
    "descripcion": ("descripcion", "description"),
    "operacion": ("operacion", "tipo", "operation"),
}


class ArchivoCSV(Source):
    nombre = "archivo"

    def __init__(self, ruta: str | Path):
        self.ruta = Path(ruta)

    def _filas(self):
        with self.ruta.open(encoding="utf-8-sig", newline="") as f:
            for fila in csv.DictReader(f):
                norm = {slugify(k).replace("-", "_"): (v or "").strip() for k, v in fila.items() if k}
                yield {campo: next((norm[a] for a in alias if norm.get(a)), "") for campo, alias in ALIAS.items()}

    def buscar(self, barrio: str, operacion: str = "venta") -> list[Listing]:
        objetivo = slugify(barrio)
        out = []
        for f in self._filas():
            if objetivo and objetivo not in slugify(f["barrio"]):
                continue
            if f["operacion"] and slugify(f["operacion"]) != operacion:
                continue
            precio, area = parse_number(f["precio"]), parse_number(f["area_m2"])
            if not precio or not area:
                continue
            etiqueta, dueno = clasificar_anunciante(f["publicado_por"], "", f["descripcion"])
            hab, ban = parse_number(f["habitaciones"]), parse_number(f["banos"])
            out.append(Listing(
                fuente=self.ruta.stem, titulo=f["titulo"] or "Apartamento", barrio=f["barrio"],
                precio=precio, area_m2=area, url=f["url"],
                telefonos=extract_phones(f["telefonos"], f["descripcion"]),
                publicado_por=etiqueta or f["publicado_por"], es_dueno_directo=dueno,
                habitaciones=int(hab) if hab else None, banos=int(ban) if ban else None,
                parqueaderos=parqueaderos_en_texto(f["titulo"], f["descripcion"]), descripcion=f["descripcion"],
            ))
        return out
