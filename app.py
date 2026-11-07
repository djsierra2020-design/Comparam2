"""Interfaz web: python app.py  →  http://localhost:5000"""
from __future__ import annotations

import os

from flask import Flask, jsonify, render_template, request

from comparam.barrios import BARRIOS
from comparam.busqueda import buscar, buscar_ciudad
from comparam.config import PAGINAS_CIUDAD, crear_fuentes
from comparam.formato import pesos
from comparam.sources import PORTALES

app = Flask(__name__)
app.jinja_env.filters["pesos"] = pesos
FUENTES = [*PORTALES, "ejemplo"]


def _float(nombre):
    v = request.args.get(nombre, "").replace(".", "").replace(",", "").strip()
    return float(v) if v.isdigit() else None


def _consulta():
    barrio = request.args.get("barrio", "").strip()
    elegidas = [f for f in request.args.getlist("fuente") if f in FUENTES] or list(PORTALES)
    params = _params()
    resultado = buscar(barrio, crear_fuentes(elegidas), **params) if barrio else None
    return barrio, elegidas, params, resultado


def _params():
    return dict(
        operacion="arriendo" if request.args.get("operacion") == "arriendo" else "venta",
        solo_dueno=request.args.get("todos") != "1",
        con_telefono=request.args.get("con_telefono") == "1",
        area_min=_float("area_min"), precio_max=_float("precio_max"),
    )


def _consulta_ciudad():
    elegidas = [f for f in request.args.getlist("fuente") if f in FUENTES] or list(PORTALES)
    fuentes = crear_fuentes(elegidas, paginas=PAGINAS_CIUDAD, detalle=False)
    return elegidas, buscar_ciudad(fuentes, **_params())


_CIUDAD = {"bogota", "toda-bogota", "todo-bogota", "toda-la-ciudad", "todas", "todos", "bogota-d-c"}


def _es_ciudad() -> bool:
    """Botón «Buscar en toda Bogotá», o escribir «toda Bogotá» / «Bogotá» como barrio."""
    from comparam.parsing import slugify
    return request.args.get("modo") == "ciudad" or slugify(request.args.get("barrio", "")) in _CIUDAD


@app.get("/")
def inicio():
    if _es_ciudad():
        elegidas, rc = _consulta_ciudad()
        return render_template("index.html", barrio="", barrios=BARRIOS, fuentes=FUENTES, elegidas=elegidas,
                               p=_params(), r=None, rc=rc, args=request.args)
    barrio, elegidas, params, resultado = _consulta()
    return render_template("index.html", barrio=barrio, barrios=BARRIOS, fuentes=FUENTES,
                           elegidas=elegidas, p=params, r=resultado, args=request.args)


@app.get("/diagnostico")
def diagnostico():
    """Muestra qué responde cada portal a la primera página de búsqueda."""
    barrio = request.args.get("barrio", "").strip()
    operacion = "arriendo" if request.args.get("operacion") == "arriendo" else "venta"
    portales = [cls(paginas=1, detalle=False) for cls in PORTALES.values()]
    return render_template("diagnostico.html", barrio=barrio,
                           filas=[p.diagnosticar(barrio, operacion) for p in portales])


@app.get("/api/ciudad")
def api_ciudad():
    _, rc = _consulta_ciudad()
    return jsonify(errores=rc.errores, revisados=rc.total_encontrados, sin_barrio=rc.sin_zona,
                   por_portal=rc.por_fuente,
                   zonas={z: dict(mediana_m2=round(v.mediana), anuncios=v.anuncios) for z, v in rc.zonas.items()},
                   anuncios=[{**o.anuncio.to_dict(), "zona": o.zona, "zona_mediana_m2": round(o.valor.mediana),
                              "vs_zona_pct": round(o.diferencia, 1)} for o in rc.oportunidades])


@app.get("/api/buscar")
def api():
    barrio, _, _, resultado = _consulta()
    if not barrio:
        return jsonify(error="Falta el parámetro 'barrio'"), 400
    z = resultado.zona
    zona = None if not z else dict(mediana_m2=round(z.mediana), promedio_m2=round(z.promedio),
                                   minimo_m2=round(z.minimo), maximo_m2=round(z.maximo), anuncios=z.anuncios,
                                   anuncios_usados=[a.to_dict() for a in resultado.usados_zona])
    anuncios = []
    for a in resultado.anuncios:
        d = a.to_dict()
        d["vs_zona_pct"] = round(z.diferencia(a.precio_m2), 1) if z else None
        anuncios.append(d)
    return jsonify(barrio=barrio, errores=resultado.errores, revisados=resultado.total_encontrados,
                   descartados_otros_barrios=len(resultado.otros_barrios), por_portal=resultado.por_fuente,
                   valor_zona=zona, anuncios=anuncios)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
