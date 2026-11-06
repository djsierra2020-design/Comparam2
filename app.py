"""Interfaz web: python app.py  →  http://localhost:5000"""
from __future__ import annotations

import os

from flask import Flask, jsonify, render_template, request

from comparam.barrios import BARRIOS
from comparam.busqueda import buscar
from comparam.config import crear_fuentes
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
    params = dict(
        operacion="arriendo" if request.args.get("operacion") == "arriendo" else "venta",
        solo_dueno=request.args.get("todos") != "1",
        con_telefono=request.args.get("con_telefono") == "1",
        area_min=_float("area_min"), precio_max=_float("precio_max"),
    )
    resultado = buscar(barrio, crear_fuentes(elegidas), **params) if barrio else None
    return barrio, elegidas, params, resultado


@app.get("/")
def inicio():
    barrio, elegidas, params, resultado = _consulta()
    return render_template("index.html", barrio=barrio, barrios=BARRIOS, fuentes=FUENTES,
                           elegidas=elegidas, p=params, r=resultado, args=request.args)


@app.get("/api/buscar")
def api():
    barrio, _, _, resultado = _consulta()
    if not barrio:
        return jsonify(error="Falta el parámetro 'barrio'"), 400
    return jsonify(barrio=barrio, errores=resultado.errores, revisados=resultado.total_encontrados,
                   anuncios=[a.to_dict() for a in resultado.anuncios])


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
