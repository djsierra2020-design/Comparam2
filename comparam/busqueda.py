from __future__ import annotations

import re
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
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
    otros_barrios: list[Listing] = field(default_factory=list)  # descartados: son de otro barrio


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


def _sin_duplicados(anuncios: list[Listing]) -> list[Listing]:
    unicos: list[Listing] = []
    for a in anuncios:
        if not any(_duplicado(a, u) for u in unicos):
            unicos.append(a)
    return unicos


def _filtrar(anuncios, solo_dueno, con_telefono, area_min, precio_max) -> list[Listing]:
    return [
        a for a in anuncios
        if (not solo_dueno or a.es_dueno_directo)
        and (not con_telefono or a.telefonos)
        and (area_min is None or a.area_m2 >= area_min)
        and (precio_max is None or a.precio <= precio_max)
    ]


def _recolectar(fuentes: list[Source], barrio: str, operacion: str) -> tuple[list[Listing], list[str]]:
    """Consulta todas las fuentes al mismo tiempo (cada una en su hilo)."""
    def una(f):
        try:
            return f.buscar(barrio, operacion), None
        except FuenteError as e:
            return [], str(e)

    todos: list[Listing] = []
    errores: list[str] = []
    if not fuentes:
        return todos, errores
    with ThreadPoolExecutor(max_workers=len(fuentes)) as ex:
        for anuncios, error in ex.map(una, fuentes):
            todos += anuncios
            if error:
                errores.append(error)
    return todos, errores


_LUGAR_TITULO = re.compile(r"\ben\s+((?:(?!\ben\s)[^,|])+?),\s*bogot", re.I)


def es_del_barrio(a: Listing, barrio: str) -> bool:
    """Los portales mezclan anuncios «destacados» de otros barrios en los resultados.

    Se acepta el anuncio si su ubicación, barrio o título mencionan el barrio buscado. Se descarta
    si dice ser de otro lugar (p. ej. título «Apartamento en venta en Chapinero, Bogotá»). Si no
    trae ningún dato de ubicación, se acepta.
    """
    objetivo = slugify(barrio)
    if not objetivo:
        return True
    textos = [a.ubicacion, a.barrio, a.titulo]
    if any(objetivo in slugify(t) for t in textos if t):
        return True
    lugar = _LUGAR_TITULO.search(a.titulo or "")
    tiene_ubicacion = bool(a.ubicacion or a.barrio) and slugify(a.ubicacion or a.barrio) not in ("bogota", "bogota-d-c")
    return not (tiene_ubicacion or lugar)


def buscar(barrio: str, fuentes: list[Source], operacion: str = "venta", solo_dueno: bool = True,
           con_telefono: bool = False, area_min: float | None = None, precio_max: float | None = None,
           limite: int | None = None) -> Resultado:
    """Busca en todas las fuentes y ordena de menor a mayor precio por m²."""
    todos, errores = _recolectar(fuentes, barrio, operacion)
    otros = [a for a in todos if not es_del_barrio(a, barrio)]
    todos = [a for a in todos if es_del_barrio(a, barrio)]
    for a in todos:
        a.barrio = a.barrio or barrio
    unicos = _sin_duplicados(todos)
    filtrados = _filtrar(unicos, solo_dueno, con_telefono, area_min, precio_max)
    filtrados.sort(key=lambda a: a.precio_m2)
    return Resultado(filtrados[:limite] if limite else filtrados, errores, len(unicos), valor_zona(unicos),
                     sorted(unicos, key=lambda a: a.precio_m2), otros)


# --------------------------------------------------------------------------- toda Bogotá

_GENERICOS = {"", "bogota", "bogota-d-c", "bogota-dc", "cundinamarca", "colombia"}


def zona_de(a: Listing) -> str:
    """Nombre del barrio del anuncio: campo barrio, o «En X, Bogotá» del título, o su ubicación."""
    candidatos = [a.barrio]
    if m := _LUGAR_TITULO.search(a.titulo or ""):
        candidatos.append(m.group(1))
    candidatos += (a.ubicacion or "").split("|")
    for c in candidatos:
        c = (c or "").strip()
        if slugify(c) not in _GENERICOS:
            return c
    return ""


@dataclass
class Oportunidad:
    anuncio: Listing
    zona: str
    valor: ValorZona

    @property
    def diferencia(self) -> float:
        return self.valor.diferencia(self.anuncio.precio_m2)


@dataclass
class ResultadoCiudad:
    oportunidades: list[Oportunidad]
    zonas: dict[str, ValorZona]
    errores: list[str] = field(default_factory=list)
    total_encontrados: int = 0
    sin_zona: int = 0  # anuncios sin barrio identificable
    zona_pequena: int = 0  # anuncios de barrios con muy pocos anuncios para comparar


_CACHE: dict[tuple, tuple[float, list[Listing], list[str]]] = {}
CACHE_SEGUNDOS = 15 * 60


def buscar_ciudad(fuentes: list[Source], operacion: str = "venta", solo_dueno: bool = True,
                  con_telefono: bool = False, area_min: float | None = None,
                  precio_max: float | None = None, min_por_zona: int = 3, limite: int | None = 50,
                  detalle: int = 15) -> ResultadoCiudad:
    """Busca en toda Bogotá y ordena por cuánto más barato está cada apartamento frente a la
    mediana del precio del m² de SU barrio (calculada con todos los anuncios de ese barrio)."""
    clave = (tuple(sorted(f"{f.nombre}:{getattr(f, 'paginas', '')}:{getattr(f, 'ruta', '')}" for f in fuentes)),
             operacion)
    cache = _CACHE.get(clave)
    if cache and time.time() - cache[0] < CACHE_SEGUNDOS:
        unicos, errores = cache[1], cache[2]
    else:
        todos, errores = _recolectar(fuentes, "", operacion)
        unicos = _sin_duplicados(todos)
        if not errores or unicos:
            _CACHE[clave] = (time.time(), unicos, errores)

    grupos: dict[str, list[Listing]] = {}
    nombres: dict[str, str] = {}
    sin_zona = 0
    for a in unicos:
        z = zona_de(a)
        if not z:
            sin_zona += 1
            continue
        k = slugify(z)
        grupos.setdefault(k, []).append(a)
        nombres.setdefault(k, z)
    zonas = {nombres[k]: v for k, g in grupos.items()
             if len(g) >= min_por_zona and (v := valor_zona(g))}

    oportunidades, pequena = [], 0
    for k, g in grupos.items():
        if nombres[k] not in zonas:
            pequena += len(g)
            continue
        for a in _filtrar(g, solo_dueno, con_telefono, area_min, precio_max):
            oportunidades.append(Oportunidad(a, nombres[k], zonas[nombres[k]]))
    oportunidades.sort(key=lambda o: o.diferencia)
    if limite:
        oportunidades = oportunidades[:limite]

    # Solo se abren las fichas de los mejores resultados (abrir todas tardaría demasiado).
    por_nombre = {f.nombre: f for f in fuentes}
    for o in [o for o in oportunidades if not o.anuncio.telefonos and o.anuncio.url][:detalle]:
        f = por_nombre.get(o.anuncio.fuente)
        if hasattr(f, "completar_detalle"):
            f.completar_detalle(o.anuncio, operacion)

    return ResultadoCiudad(oportunidades, dict(sorted(zonas.items(), key=lambda kv: kv[1].mediana)),
                           errores, len(unicos), sin_zona, pequena)
