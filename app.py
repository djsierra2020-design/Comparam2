"""Interfaz web: python app.py  →  http://localhost:5000"""
from __future__ import annotations

import os

from flask import Flask, jsonify, render_template, request, send_from_directory

from comparam.barrios import BARRIOS
from comparam.busqueda import buscar, buscar_ciudad
from comparam.config import PAGINAS_CIUDAD, crear_fuentes
from comparam.formato import pesos
from comparam.sources import PORTALES

app = Flask(__name__)
app.jinja_env.filters["pesos"] = pesos
NOMBRES_PORTAL = {"fincaraiz": "Finca Raíz", "metrocuadrado": "Metrocuadrado", "ciencuadras": "Ciencuadras",
                  "habi": "Habi", "houm": "Houm", "mercadolibre": "MercadoLibre", "properati": "Properati", "rentola": "Rentola",
                  "ejemplo": "Ejemplo"}
app.jinja_env.filters["portal"] = lambda f: NOMBRES_PORTAL.get(f, f)
FUENTES = [*PORTALES, "ejemplo"]


def historial_de(anuncio, operacion: str):
    """Días publicado y bajada de precio de un anuncio, si está en el historial diario."""
    from comparam import diario
    try:
        return diario.por_url(anuncio.url, operacion)
    except Exception:  # noqa: BLE001 - el historial es opcional: nunca debe romper la página
        return None


app.jinja_env.globals["hist"] = historial_de


def miles(n) -> str:
    return f"{n:,.0f}".replace(",", ".")


app.jinja_env.filters["miles"] = miles


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


def _url_con_inmobiliarias() -> str:
    from urllib.parse import urlencode
    args = request.args.to_dict(flat=False)
    args["todos"] = ["1"]
    return "/?" + urlencode(args, doseq=True)


app.jinja_env.globals["url_con_inmobiliarias"] = _url_con_inmobiliarias


@app.get("/")
def inicio():
    if _es_ciudad():
        elegidas, rc = _consulta_ciudad()
        return render_template("index.html", barrio="", barrios=BARRIOS, fuentes=FUENTES, elegidas=elegidas,
                               p=_params(), r=None, rc=rc, args=request.args)
    barrio, elegidas, params, resultado = _consulta()
    return render_template("index.html", barrio=barrio, barrios=BARRIOS, fuentes=FUENTES,
                           elegidas=elegidas, p=params, r=resultado, args=request.args)


def _mensaje_whatsapp(lista: list[dict], fecha: str) -> str:
    """Texto listo para pegar en WhatsApp con los anuncios nuevos."""
    if not lista:
        return f"🏠 Nuevos dueños directos – {fecha}\nHoy no aparecieron anuncios nuevos en sus zonas."
    lineas = [f"🏠 *Nuevos dueños directos – {fecha}*"]
    for op in ("venta", "arriendo"):
        grupo = [e for e in lista if e["operacion"] == op]
        if not grupo:
            continue
        lineas.append(f"\n*{'VENTA' if op == 'venta' else 'ARRIENDO'}*")
        for i, e in enumerate(grupo, 1):
            partes = [e["zona"], f"{e['area_m2']:g} m²", pesos(e["precio"])]
            if e.get("habitaciones"):
                partes.append(f"{e['habitaciones']} hab")
            if e.get("vs_zona") is not None:
                partes.append(f"{abs(e['vs_zona'])}% {'bajo' if e['vs_zona'] < 0 else 'sobre'} la zona")
            nota = " _(verificar: puede ser agente)_" if e.get("verificar") else ""
            lineas.append(f"{i}. {' · '.join(partes)}{nota}\n{e['url']}")
    return "\n".join(lineas)


@app.get("/nuevos")
def ver_nuevos():
    from comparam import diario
    dias = int(request.args.get("dias", "1")) if request.args.get("dias", "1").isdigit() else 1
    lista = diario.nuevos(dias=max(1, min(dias, 30)))
    resumen = diario.ultimo_resumen()
    config = diario.cargar_json(diario.CONFIG, {})
    fecha = diario.hoy().strftime("%d/%m/%Y")
    return render_template("nuevos.html", lista=lista, resumen=resumen, config=config, dias=dias,
                           mensaje=_mensaje_whatsapp(lista, fecha), fecha=fecha)


@app.get("/sw.js")
def service_worker():
    # Se sirve desde la raíz para que controle toda la app (no solo /static).
    r = send_from_directory(app.static_folder, "sw.js", mimetype="application/javascript")
    r.headers["Cache-Control"] = "no-cache"
    return r


@app.get("/diagnostico")
def diagnostico():
    """Muestra qué responde cada portal a la primera página de búsqueda."""
    barrio = request.args.get("barrio", "").strip()
    operacion = "arriendo" if request.args.get("operacion") == "arriendo" else "venta"
    portales = [cls(paginas=1, detalle=False) for cls in PORTALES.values()]
    return render_template("diagnostico.html", barrio=barrio,
                           filas=[p.diagnosticar(barrio, operacion) for p in portales])


# Portales que /api/sonda puede consultar (no es un proxy abierto).
SONDA_DOMINIOS = ("fincaraiz.com.co", "metrocuadrado.com", "ciencuadras.com", "mercadolibre.com.co",
                  "properati.com.co", "habi.co", "houm.com", "puntopropiedad.com", "trovit.com.co",
                  "mitula.com.co", "lahaus.com", "nuroa.com.co", "olx.com.co", "rentola.com.co", "rentola.co.com")


@app.get("/api/sonda")
def sonda():
    """Herramienta de desarrollo: qué encuentra la app en una página de un portal inmobiliario.
    Parámetros: url (obligatorio), operacion, buscar (texto a ubicar en la página)."""
    import json as _json
    import re as _re
    from urllib.parse import urlparse

    import requests as _rq

    from comparam.sources.extractor import _bloques_json, _recorrer, extraer_anuncios, total_publicados
    from comparam.sources.portales import HEADERS, _pistas, _titulo

    url = request.args.get("url", "")
    host = (urlparse(url).hostname or "").lower()
    if urlparse(url).scheme != "https" or not any(host == d or host.endswith("." + d) for d in SONDA_DOMINIOS):
        return jsonify(error="Solo se permiten páginas https de portales inmobiliarios conocidos"), 400
    operacion = "arriendo" if request.args.get("operacion") == "arriendo" else "venta"
    try:
        r = _rq.get(url, headers=HEADERS, timeout=25)
    except _rq.RequestException as e:
        return jsonify(error=f"no se pudo conectar: {e.__class__.__name__}")
    html = r.text
    anuncios = extraer_anuncios(html, host, f"https://{host}", operacion)
    bloques = list(_bloques_json(html))
    # Muestra de claves de los objetos con precio, para adaptar el extractor
    muestras = []
    for b in bloques:
        for d in _recorrer(b):
            if any(_re.search(r"price|precio|valor", k, _re.I) for k in d) and len(muestras) < 3:
                muestras.append(_json.dumps(d, ensure_ascii=False, default=str)[:1500])
    contexto = []
    if q := request.args.get("buscar"):
        for m in list(_re.finditer(_re.escape(q), html, _re.I))[:5]:
            contexto.append(html[max(0, m.start() - 300):m.end() + 300])
    scripts = _re.findall(r"<script[^>]*(?:id|type)=[\"']([^\"']+)[\"']", html)[:30]
    return jsonify(
        estado=r.status_code, url_final=r.url, bytes=len(html), titulo=_titulo(html), scripts=scripts,
        bloques_json=len(bloques), anuncios=len(anuncios), total_publicado=total_publicados(html, len(anuncios)),
        dueno_directo=sum(a.es_dueno_directo for a in anuncios),
        ejemplos=[{k: v for k, v in a.to_dict().items() if k not in ("telefonos", "descripcion")} for a in anuncios[:4]],
        muestras_json=muestras, contexto=contexto, pistas=_pistas(html) if not anuncios else [],
    )


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
