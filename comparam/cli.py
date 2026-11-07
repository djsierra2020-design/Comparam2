"""Uso: python -m comparam.cli Chapinero [--arriendo] [--todos] [--fuente ejemplo]"""
from __future__ import annotations

import argparse
import csv
import sys

from .busqueda import buscar
from .config import crear_fuentes
from .formato import pesos
from .sources import PORTALES


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Apartamentos más económicos por m² en un barrio de Bogotá")
    p.add_argument("barrio", nargs="*", help="barrio (si se omite: toda Bogotá, comparando cada "
                   "apartamento con el precio del m² de su propio barrio)")
    p.add_argument("--arriendo", action="store_true", help="buscar arriendos (por defecto: venta)")
    p.add_argument("--todos", action="store_true", help="incluir anuncios de inmobiliarias")
    p.add_argument("--con-telefono", action="store_true", help="solo anuncios con teléfono visible")
    p.add_argument("--fuente", action="append", choices=[*PORTALES, "ejemplo"],
                   help="fuente(s) a consultar (por defecto todos los portales)")
    p.add_argument("--csv", action="append", help="archivo CSV adicional con anuncios")
    p.add_argument("--area-min", type=float)
    p.add_argument("--precio-max", type=float)
    p.add_argument("--paginas", type=int, default=2)
    p.add_argument("-n", "--limite", type=int, default=30)
    p.add_argument("--exportar", help="guardar resultados en un CSV")
    a = p.parse_args(argv)

    barrio = " ".join(a.barrio)
    if not barrio:
        return _ciudad(a)
    fuentes = crear_fuentes(a.fuente, a.csv, a.paginas)
    r = buscar(barrio, fuentes, "arriendo" if a.arriendo else "venta", solo_dueno=not a.todos,
               con_telefono=a.con_telefono, area_min=a.area_min, precio_max=a.precio_max, limite=a.limite)
    for e in r.errores:
        print(f"⚠ {e}", file=sys.stderr)
    if not r.anuncios:
        print(f"No se encontraron apartamentos en {barrio} ({r.total_encontrados} anuncios revisados).")
        return 1
    z = r.zona
    print(f"\nApartamentos en {barrio} — del más económico al más caro por m²")
    if z:
        print(f"Precio de oferta del m² en la zona: {pesos(z.mediana)} — calculado con {z.anuncios} anuncios "
              f"(mediana; "
              f"promedio {pesos(z.promedio)}, rango {pesos(z.minimo)} – {pesos(z.maximo)})")
    print()
    for i, x in enumerate(r.anuncios, 1):
        tels = ", ".join(x.telefonos) or "ver anuncio"
        vs = f"  ({z.diferencia(x.precio_m2):+.0f}% vs zona)" if z else ""
        print(f"{i:>2}. {pesos(x.precio_m2)}/m²{vs}  |  {pesos(x.precio)}  |  {x.area_m2:g} m²  |  {x.titulo[:50]}")
        print(f"    📞 {tels}  |  {x.publicado_por or 'anunciante desconocido'}  |  {x.fuente}  {x.url}")
    if a.exportar:
        with open(a.exportar, "w", newline="", encoding="utf-8") as f:
            campos = ["precio_m2", "precio", "area_m2", "titulo", "barrio", "telefonos", "publicado_por", "fuente", "url"]
            w = csv.DictWriter(f, fieldnames=campos, extrasaction="ignore")
            w.writeheader()
            for x in r.anuncios:
                d = x.to_dict()
                d["telefonos"] = " / ".join(x.telefonos)
                w.writerow(d)
        print(f"\nGuardado en {a.exportar}")
    return 0


def _ciudad(a) -> int:
    from .busqueda import buscar_ciudad
    from .config import PAGINAS_CIUDAD

    fuentes = crear_fuentes(a.fuente, a.csv, max(a.paginas, PAGINAS_CIUDAD), detalle=False)
    rc = buscar_ciudad(fuentes, "arriendo" if a.arriendo else "venta", solo_dueno=not a.todos,
                       con_telefono=a.con_telefono, area_min=a.area_min, precio_max=a.precio_max, limite=a.limite)
    for e in rc.errores:
        print(f"⚠ {e}", file=sys.stderr)
    print(f"\nToda Bogotá: {rc.total_encontrados} anuncios revisados, {len(rc.zonas)} barrios comparables\n")
    if not rc.oportunidades:
        print("No se encontraron apartamentos para comparar.")
        return 1
    for i, o in enumerate(rc.oportunidades, 1):
        x = o.anuncio
        print(f"{i:>2}. {o.diferencia:+.0f}% vs {o.zona} ({pesos(o.valor.mediana)}/m², {o.valor.anuncios} anuncios)"
              f"  |  {pesos(x.precio_m2)}/m²  |  {pesos(x.precio)}  |  {x.area_m2:g} m²  |  {x.titulo[:45]}")
        print(f"    📞 {', '.join(x.telefonos) or 'ver anuncio'}  |  {x.publicado_por or 'anunciante desconocido'}"
              f"  |  {x.fuente}  {x.url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
