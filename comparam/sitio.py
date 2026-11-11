"""Sitio público (GitHub Pages): portada de Comparam y páginas del precio del m² por barrio y
localidad, en venta y arriendo, generadas cada día con el historial de la revisión diaria.

Uso:  python -m comparam.sitio  [carpeta de salida, por defecto «sitio»]

Las páginas solo muestran cifras resumidas y enlaces a los anuncios originales: nunca nombres ni
teléfonos de anunciantes (son páginas públicas indexadas por Google).
"""
from __future__ import annotations

import os
import statistics
import sys
from datetime import date, timedelta
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import diario
from .config import RAIZ
from .formato import pesos
from .localidades import LOCALIDADES, localidad_de
from .parsing import slugify

SITIO_URL = os.environ.get("SITIO_URL", "https://djsierra2020-design.github.io/Comparam2").rstrip("/")
APP_URL = os.environ.get("APP_URL", "https://comparam2.onrender.com").rstrip("/")
MIN_ANUNCIOS = 5          # mínimo de anuncios para publicar una página
DIAS_VIGENCIA = 3         # anuncios vistos en los últimos N días
OPERACIONES = {"venta": "venta", "arriendo": "arriendo"}
NOMBRE_PORTAL = {"fincaraiz": "Finca Raíz", "ciencuadras": "Ciencuadras", "rentola": "Rentola",
                 "habi": "Habi", "houm": "Houm", "metrocuadrado": "Metrocuadrado", "mercadolibre": "MercadoLibre"}
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
         "noviembre", "diciembre"]


def _miles(n) -> str:
    return f"{n:,.0f}".replace(",", ".")


def _plantillas() -> Environment:
    env = Environment(loader=FileSystemLoader(RAIZ / "templates" / "sitio"),
                      autoescape=select_autoescape(["html", "xml"]))
    env.filters.update(pesos=pesos, miles=_miles, portal=lambda f: NOMBRE_PORTAL.get(f, f))
    return env


def _estadisticas(anuncios: list[dict], dia: date) -> dict:
    m2 = sorted(e["precio"] / e["area_m2"] for e in anuncios)
    dias = []
    for e in anuncios:
        n, _ = diario.dias_publicado(e, dia)
        if n is not None:
            dias.append(n)
    baratos = sorted(anuncios, key=lambda e: e["precio"] / e["area_m2"])[:5]
    return {
        "anuncios": len(anuncios), "mediana": statistics.median(m2), "promedio": statistics.fmean(m2),
        "minimo": m2[0], "maximo": m2[-1],
        "duenos": sum(1 for e in anuncios if e.get("dueno_directo")),
        "dias_mediana": round(statistics.median(dias)) if dias else None,
        "precio_mediano": statistics.median(e["precio"] for e in anuncios),
        "area_mediana": statistics.median(e["area_m2"] for e in anuncios),
        "baratos": [{"titulo": e.get("titulo") or "Apartamento", "precio": e["precio"], "area": e["area_m2"],
                     "m2": e["precio"] / e["area_m2"], "url": e.get("url", ""), "fuente": e.get("fuente", ""),
                     "habitaciones": e.get("habitaciones"), "dueno": bool(e.get("dueno_directo"))}
                    for e in baratos],
        "portales": sorted({e.get("fuente", "") for e in anuncios} - {""}),
    }


def construir(salida: Path, dia: date | None = None, historial: dict | None = None) -> dict:
    """Genera el sitio en `salida`. Devuelve un resumen (páginas creadas)."""
    dia = dia or diario.hoy()
    historial = historial if historial is not None else diario.cargar_json(diario.HISTORIAL, {})
    desde = (dia - timedelta(days=DIAS_VIGENCIA)).isoformat()
    vigentes = [e for e in historial.values()
                if e.get("ultima_vez", "") >= desde and e.get("area_m2") and e.get("precio")]

    # Agrupar por barrio (zona) y por localidad
    grupos: dict[tuple[str, str, str], dict] = {}  # (operacion, tipo, slug) -> {nombre, anuncios}
    loc_slugs = {slugify(l) for l in LOCALIDADES}
    for e in vigentes:
        op = e["operacion"]
        zona = (e.get("zona") or "").strip()
        loc = localidad_de(zona, e.get("barrio", ""), e.get("titulo", ""))
        if zona and slugify(zona) not in ("bogota", *loc_slugs):
            g = grupos.setdefault((op, "barrio", slugify(zona)), {"nombre": zona, "anuncios": [], "localidad": loc})
            g["anuncios"].append(e)
        if loc:
            g = grupos.setdefault((op, "localidad", slugify(loc)), {"nombre": loc, "anuncios": [], "localidad": loc})
            g["anuncios"].append(e)
    paginas = {k: v for k, v in grupos.items() if len(v["anuncios"]) >= MIN_ANUNCIOS}

    ciudad = {op: _estadisticas([e for e in vigentes if e["operacion"] == op], dia)
              for op in OPERACIONES if any(e["operacion"] == op for e in vigentes)}
    stats = {k: _estadisticas(v["anuncios"], dia) for k, v in paginas.items()}

    env = _plantillas()
    fecha_txt = f"{MESES[dia.month - 1]} de {dia.year}"
    comun = {"SITIO_URL": SITIO_URL, "APP_URL": APP_URL, "hoy": dia, "fecha_txt": fecha_txt,
             "dia_txt": f"{dia.day} de {MESES[dia.month - 1]} de {dia.year}"}

    def url(op: str, tipo: str, slug: str) -> str:
        return f"precio-m2/{op}/{'localidad-' if tipo == 'localidad' else ''}{slug}/"

    def escribir(ruta: str, html: str) -> None:
        destino = salida / ruta
        if ruta.endswith("/") or not ruta:
            destino = destino / "index.html"
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(html, encoding="utf-8")

    salida.mkdir(parents=True, exist_ok=True)
    listado = {op: sorted([(paginas[k]["nombre"], k[1], url(*k), stats[k]) for k in paginas if k[0] == op],
                          key=lambda x: (x[1] != "localidad", x[0]))
               for op in OPERACIONES}
    for (op, tipo, slug), g in paginas.items():
        st = stats[(op, tipo, slug)]
        # Comparación con la localidad (para barrios) y con Bogotá
        loc_st = stats.get((op, "localidad", slugify(g["localidad"]))) if tipo == "barrio" and g["localidad"] else None
        vecinos = [(paginas[k]["nombre"], url(*k)) for k in paginas
                   if k[0] == op and k[1] == "barrio" and k[2] != slug
                   and paginas[k]["localidad"] == g["localidad"]][:8]
        otra_op = "arriendo" if op == "venta" else "venta"
        escribir(url(op, tipo, slug), env.get_template("zona.html").render(
            **comun, op=op, otra_op=otra_op, tipo=tipo, nombre=g["nombre"], localidad=g["localidad"], st=st,
            loc_st=loc_st, ciudad_st=ciudad.get(op), vecinos=vecinos, ruta=url(op, tipo, slug),
            url_otra=url(otra_op, tipo, slug) if (otra_op, tipo, slug) in paginas else None,
            raiz="../../../"))
    for op in OPERACIONES:
        if listado[op]:
            escribir(f"precio-m2/{op}/", env.get_template("listado.html").render(
                **comun, op=op, filas=listado[op], ciudad_st=ciudad.get(op), raiz="../../"))
    escribir("", env.get_template("portada.html").render(
        **comun, ciudad=ciudad, listado=listado, total=len(vigentes), raiz="./"))
    todas = [""] + [f"precio-m2/{op}/" for op in OPERACIONES if listado[op]] + [url(*k) for k in paginas]
    (salida / "sitemap.xml").write_text(env.get_template("sitemap.xml").render(
        **comun, rutas=todas), encoding="utf-8")
    (salida / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {SITIO_URL}/sitemap.xml\n",
                                       encoding="utf-8")
    (salida / ".nojekyll").write_text("", encoding="utf-8")
    for icono in ("icon-192.png", "favicon.png", "apple-touch-icon.png"):
        origen = RAIZ / "static" / icono
        if origen.exists():
            (salida / icono).write_bytes(origen.read_bytes())
    return {"paginas": len(todas), "zonas": len(paginas), "anuncios": len(vigentes)}


if __name__ == "__main__":
    carpeta = Path(sys.argv[1] if len(sys.argv) > 1 else "sitio")
    r = construir(carpeta)
    print(f"Sitio generado en {carpeta}: {r['paginas']} páginas ({r['zonas']} barrios/localidades), "
          f"{r['anuncios']} anuncios vigentes")
