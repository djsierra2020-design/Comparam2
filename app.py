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
                  "habi": "Habi", "houm": "Houm", "mercadolibre": "MercadoLibre", "properati": "Properati", "rentola": "Rentola", "mitula": "Mitula",
                  "metrocuadrado": "Metrocuadrado",
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


ML_REDIRECT = os.environ.get("ML_REDIRECT_URI", "https://comparam2.onrender.com")


@app.get("/ml/conectar")
def ml_conectar():
    """Prueba: la persona autoriza a comparam2 en MercadoLibre y se hace una búsqueda de prueba."""
    from flask import redirect
    from comparam.sources import mercadolibre_api as ml
    if not ml.configurado():
        return _pagina_ml("Faltan ML_CLIENT_ID y ML_CLIENT_SECRET en Render.", ok=False)
    return redirect(ml.url_autorizacion(ML_REDIRECT))


def _pagina_ml(mensaje: str, ok: bool, ejemplos: list | None = None, total=None) -> str:
    from markupsafe import escape
    filas = "".join(f"<li>{escape(e)}</li>" for e in (ejemplos or []))
    return (f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Comparam · MercadoLibre</title>
<style>body{{font:16px/1.5 system-ui,sans-serif;max-width:640px;margin:0 auto;padding:24px 16px;background:#f3f5f7;color:#14181f}}
.c{{background:#fff;border-radius:18px;padding:18px;box-shadow:0 2px 10px rgba(0,0,0,.06)}}
h1{{font-size:22px;margin:0 0 8px;color:{'#067647' if ok else '#b42318'}}} a{{color:#0d7466}}</style></head><body>
<div class="c"><h1>{'✓ MercadoLibre funciona' if ok else '✗ MercadoLibre no funcionó'}</h1><p>{escape(mensaje)}</p>
{f'<p>Total de anuncios que reporta MercadoLibre: <b>{escape(total)}</b></p>' if total is not None else ''}
{f'<ol>{filas}</ol>' if filas else ''}<p><a href="/">Volver a Comparam</a></p></div></body></html>""")


def _ml_regreso():
    """Regreso desde MercadoLibre (la dirección registrada es la raíz del sitio)."""
    from comparam.sources import mercadolibre_api as ml
    if not ml.estado_valido(request.args.get("state", "")):
        return _pagina_ml("La autorización expiró o no es válida. Vuelve a intentar desde /ml/conectar.", ok=False)
    try:
        tok = ml.canjear_codigo(request.args["code"], ML_REDIRECT)
        fuente = ml.MercadoLibreAPI(paginas=1, token=tok["access_token"])
        anuncios = fuente.buscar("Cedritos", "venta")
    except ml.FuenteError as e:
        return _pagina_ml(str(e), ok=False)
    ejemplos = [f"{a.titulo[:60]} · {pesos(a.precio)} · {a.area_m2:g} m² · {a.publicado_por or 'anunciante ?'}"
                for a in anuncios[:5]]
    return _pagina_ml(f"Con tu autorización, la búsqueda de prueba (apartamentos en venta en Cedritos) "
                      f"trajo {len(anuncios)} anuncios. No se guardó ningún dato de tu cuenta.", ok=True,
                      ejemplos=ejemplos, total=fuente.total_publicados)


@app.get("/")
def inicio():
    if request.args.get("code") and request.args.get("state"):
        return _ml_regreso()
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


@app.get("/gastos")
def gastos():
    """App de gastos personales. Funciona por completo en el celular: los datos se guardan
    en el navegador (localStorage), no en el servidor."""
    r = send_from_directory(app.static_folder, "gastos.html")
    r.headers["Cache-Control"] = "no-cache"
    return r


@app.get("/diagnostico")
def diagnostico():
    """Muestra qué responde cada portal a la primera página de búsqueda."""
    barrio = request.args.get("barrio", "").strip()
    operacion = "arriendo" if request.args.get("operacion") == "arriendo" else "venta"
    portales = [cls(paginas=1, detalle=False) for cls in PORTALES.values()]
    from comparam.sources import mercadolibre_api
    portales.append(mercadolibre_api.MercadoLibreAPI(paginas=1))  # muestra si faltan las credenciales
    def una(p):
        # Un portal que falle no debe tumbar la página completa (antes daba «Error interno»).
        try:
            return p.diagnosticar(barrio, operacion)
        except Exception as e:  # noqa: BLE001 - se muestra en la página para poder corregirlo
            app.logger.exception("diagnóstico de %s", p.nombre)
            return {"portal": p.nombre, "url": "—", "error": f"falla de la app: {e.__class__.__name__}: {str(e)[:200]}"}

    return render_template("diagnostico.html", barrio=barrio, operacion=operacion, filas=[una(p) for p in portales])


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
