from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from .models import Listing
from .parsing import slugify
from .sources import FuenteError, Source


@dataclass
class Resultado:
    anuncios: list[Listing]
    errores: list[str] = field(default_factory=list)
    total_encontrados: int = 0
    zona: "ValorZona | None" = None
    usados_zona: list[Listing] = field(default_factory=list)  # anuncios con los que se calculó la zona


@dataclass
class ValorZona:
    """Valor del m² en el barrio, calculado con todos los anuncios encontrados
    (dueños directos e inmobiliarias), antes de aplicar los filtros."""
    mediana: float
    promedio: float
    minimo: float
    maximo: float
    anuncios: int

    def diferencia(self, precio_m2: float) -> float:
        """Porcentaje frente a la mediana: negativo = más barato que la zona."""
        return (precio_m2 / self.mediana - 1) * 100


def valor_zona(anuncios: list[Listing]) -> ValorZona | None:
    valores = [a.precio_m2 for a in anuncios if a.area_m2]
    if not valores:
        return None
    return ValorZona(statistics.median(valores), statistics.fmean(valores), min(valores), max(valores),
                     len(valores))


def _duplicado(a: Listing, b: Listing) -> bool:
    if a.url and a.url == b.url:
        return True
    mismos_tels = bool(set(a.telefonos) & set(b.telefonos))
    return (abs(a.precio - b.precio) < 1 and abs(a.area_m2 - b.area_m2) < 1
            and (mismos_tels or slugify(a.titulo) == slugify(b.titulo)))


def buscar(barrio: str, fuentes: list[Source], operacion: str = "venta", solo_dueno: bool = True,
           con_telefono: bool = False, area_min: float | None = None, precio_max: float | None = None,
           limite: int | None = None) -> Resultado:
    """Busca en todas las fuentes y ordena de menor a mayor precio por m²."""
    todos: list[Listing] = []
    errores: list[str] = []
    for f in fuentes:
        try:
            todos += f.buscar(barrio, operacion)
        except FuenteError as e:
            errores.append(str(e))
    unicos: list[Listing] = []
    for a in todos:
        if not any(_duplicado(a, u) for u in unicos):
            unicos.append(a)
    filtrados = [
        a for a in unicos
        if (not solo_dueno or a.es_dueno_directo)
        and (not con_telefono or a.telefonos)
        and (area_min is None or a.area_m2 >= area_min)
        and (precio_max is None or a.precio <= precio_max)
    ]
    filtrados.sort(key=lambda a: a.precio_m2)
    return Resultado(filtrados[:limite] if limite else filtrados, errores, len(unicos), valor_zona(unicos),
                     sorted(unicos, key=lambda a: a.precio_m2))
