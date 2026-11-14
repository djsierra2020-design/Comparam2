import json
from pathlib import Path

from comparam.busqueda import buscar, buscar_ciudad, es_del_barrio, zona_de
from comparam.models import Listing
from comparam.config import EJEMPLO
from comparam.parsing import parece_persona, clasificar_anunciante, extract_phones, parse_number, slugify
from comparam.sources import ArchivoCSV
from comparam.sources.base import FuenteError, Source
from comparam.sources.extractor import extraer_anuncios

FIX = Path(__file__).parent / "fixtures" / "portal.html"


def test_parse_number():
    assert parse_number("$ 450.000.000") == 450_000_000
    assert parse_number("72,5 m²") == 72.5
    assert parse_number("72.5") == 72.5
    assert parse_number("1.250") == 1250
    assert parse_number(80) == 80
    assert parse_number("") is None


def test_extract_phones():
    assert extract_phones("Llamar al +57 310 123 4567 o 3101234567") == ["310 123 4567"]
    assert extract_phones("Fijo 601-234-5678") == ["601 234 5678"]
    assert extract_phones("Precio 350000000") == []


def test_clasificar():
    assert clasificar_anunciante("Dueño directo") == ("dueño directo", True)
    assert clasificar_anunciante("", "Inmobiliaria Bogotá SAS") == ("inmobiliaria", False)
    assert clasificar_anunciante("", "", "Abstenerse inmobiliarias")[1] is True
    assert clasificar_anunciante() == ("", False)


def test_slugify():
    assert slugify("Chicó Navarra") == "chico-navarra"


def test_extractor_portal():
    anuncios = extraer_anuncios(FIX.read_text(), "prueba", "https://portal.co")
    assert len(anuncios) == 3
    por_url = {a.url: a for a in anuncios}
    a1 = por_url["https://portal.co/inmueble/1"]
    assert a1.es_dueno_directo and a1.telefonos == ["300 000 1111"] and a1.precio_m2 == 5_000_000
    assert a1.barrio == "Cedritos" and a1.habitaciones == 3
    assert not por_url["https://portal.co/inmueble/2"].es_dueno_directo
    a3 = por_url["https://otro.com/3"]
    assert a3.es_dueno_directo and a3.telefonos == ["300 000 2222"]


def test_busqueda_ordena_y_filtra_dueno():
    r = buscar("cedritos", [ArchivoCSV(EJEMPLO)])
    m2 = [a.precio_m2 for a in r.anuncios]
    assert m2 == sorted(m2)
    assert all(a.es_dueno_directo for a in r.anuncios)
    assert len(r.anuncios) == 3  # excluye inmobiliaria y arriendo
    assert r.anuncios[0].telefonos == ["300 000 0002", "300 000 0003"]


def test_busqueda_incluye_inmobiliarias_y_arriendo():
    assert len(buscar("Cedritos", [ArchivoCSV(EJEMPLO)], solo_dueno=False).anuncios) == 4
    assert len(buscar("Cedritos", [ArchivoCSV(EJEMPLO)], operacion="arriendo").anuncios) == 1


def test_errores_de_fuente_no_rompen_busqueda():
    class Caida(Source):
        nombre = "caida"

        def buscar(self, barrio, operacion="venta"):
            raise FuenteError("caida: sin conexión")

    r = buscar("Chapinero", [Caida(), ArchivoCSV(EJEMPLO)])
    assert r.errores == ["caida: sin conexión"] and r.anuncios


def test_web(monkeypatch):
    import app as webapp
    c = webapp.app.test_client()
    assert c.get("/").status_code == 200
    html = c.get("/?barrio=Cedritos&fuente=ejemplo").get_data(as_text=True)
    assert "300 000 0002" in html and "Inmobiliaria Ejemplo" not in html
    assert '<span class="tag portal">Ejemplo</span>' in html
    html = c.get("/?modo=ciudad&fuente=ejemplo").get_data(as_text=True)
    assert "su barrio" in html and "Fontibón" in html
    for escrito in ("toda bogotá", "Bogotá", "Toda Bogota"):
        html = c.get(f"/?barrio={escrito}&fuente=ejemplo").get_data(as_text=True)
        assert "Barrios y localidades comparables" in html
    j = c.get("/api/ciudad?fuente=ejemplo").get_json()
    assert j["anuncios"][0]["vs_zona_pct"] < 0 and "Cedritos" in j["zonas"]
    j = c.get("/api/buscar?barrio=Chapinero&fuente=ejemplo").get_json()
    assert [a["precio_m2"] for a in j["anuncios"]] == sorted(a["precio_m2"] for a in j["anuncios"])


def test_valor_zona():
    r = buscar("cedritos", [ArchivoCSV(EJEMPLO)])
    # 4 anuncios de venta en Cedritos (incluida la inmobiliaria): 5.000.000, 5.384.615, 6.000.000, 6.209.677
    assert r.zona.anuncios == 4
    assert round(r.zona.mediana) == round((5_384_615.38 + 6_000_000) / 2)
    assert r.zona.diferencia(r.anuncios[0].precio_m2) < 0


def test_api_valor_zona():
    import app as webapp
    j = webapp.app.test_client().get("/api/buscar?barrio=Cedritos&fuente=ejemplo").get_json()
    assert len(j["valor_zona"]["anuncios_usados"]) == 4
    assert j["valor_zona"]["anuncios"] == 4 and j["anuncios"][0]["vs_zona_pct"] < 0


def test_web_muestra_numero_de_anuncios():
    import app as webapp
    html = webapp.app.test_client().get("/?barrio=Cedritos&fuente=ejemplo").get_data(as_text=True)
    assert "Anuncios leídos por portal" not in html
    assert "Calculado con" in html and "4 anuncios" in html and "Ver los 4 anuncios usados" in html
    assert "Apto con balcón" in html  # el de inmobiliaria aparece en el cálculo, no en la lista


def _l(titulo, barrio="", ubicacion=""):
    return Listing("t", titulo, barrio, 400_000_000, 100, ubicacion=ubicacion)


def test_es_del_barrio():
    assert es_del_barrio(_l("Apartamento En Venta En Fontibón, Bogotá"), "Fontibon")
    assert not es_del_barrio(_l("Apartamento En Venta En Chapinero, Bogotá"), "Fontibón")
    assert not es_del_barrio(_l("Apartamento En Venta En Casa Blanca Suba, Bogotá"), "Fontibón")
    # barrio dentro de la localidad buscada
    assert es_del_barrio(_l("Apto en Modelia, Bogotá", "Modelia", "Modelia | Fontibón | Bogotá"), "fontibon")
    # sub-barrio que el portal devolvió al buscar la localidad (Ciencuadras en Bosa): se conserva
    assert es_del_barrio(_l("Apartamento en San Bernardino XXII", "San Bernardino XXII"), "Bosa")
    assert es_del_barrio(_l("Apto amplio", "Salitre", "Salitre | Bogotá"), "Fontibón")
    assert es_del_barrio(_l("Apartamento en Bogotá, d.c."), "Fontibón")  # sin datos: se acepta


def test_destacados_de_otros_barrios_se_descartan():
    class Portal:
        def buscar(self, barrio, operacion="venta"):
            return [_l("Apartamento En Venta En Fontibón, Bogotá"),
                    _l("Apartamento En Venta En Chapinero, Bogotá")]

    r = buscar("Fontibón", [Portal()], solo_dueno=False)
    assert [a.titulo for a in r.anuncios] == ["Apartamento En Venta En Fontibón, Bogotá"]
    assert r.zona.anuncios == 1 and len(r.otros_barrios) == 1


def test_extractor_guarda_ubicacion():
    a = {x.url: x for x in extraer_anuncios(FIX.read_text(), "p", "https://portal.co")}
    assert "Cedritos" in a["https://portal.co/inmueble/1"].ubicacion


def test_zona_de():
    assert zona_de(_l("Apartamento En Venta En Chapinero, Bogotá")) == "Chapinero"
    assert zona_de(_l("Apto", "Modelia", "Modelia | Fontibón")) == "Modelia"
    assert zona_de(_l("Apto", "", "Bogotá | Hayuelos")) == "Hayuelos"
    assert zona_de(_l("Apartamento en Bogotá, d.c.")) == ""
    assert zona_de(_l("Apartamento En Venta En Casa Blanca Suba, Bogotá")) == "Casa Blanca Suba"


def test_buscar_ciudad_compara_con_su_barrio():
    rc = buscar_ciudad([ArchivoCSV(EJEMPLO)])
    # Chapinero Alto (1 anuncio) se compara con la localidad Chapinero; Teusaquillo (1) queda por fuera
    assert set(rc.zonas) == {"Cedritos", "Fontibón", "Chapinero", "localidad Chapinero"}
    assert all(o.anuncio.es_dueno_directo for o in rc.oportunidades)
    difs = [o.diferencia for o in rc.oportunidades]
    assert difs == sorted(difs)
    # El más barato en pesos/m² de Fontibón queda primero frente a SU barrio
    assert rc.por_fuente == {"ejemplo": 13}
    assert rc.oportunidades[0].zona == "Fontibón" and rc.oportunidades[0].diferencia < 0
    assert rc.zona_pequena == 1


def test_portal_sin_anuncios_avisa():
    import pytest
    from comparam.sources import FincaRaiz

    class Resp:
        def __init__(self, status, text):
            self.status_code, self.text, self.url = status, text, ""

    class Sesion:
        headers = {}

        def __init__(self, resp):
            self.resp = resp

        def get(self, url, timeout=None):
            return self.resp

    vacia = FincaRaiz(sesion=Sesion(Resp(200, "<html><title>Cargando…</title></html>")), pausa=0)
    with pytest.raises(FuenteError, match="no encontró anuncios"):
        vacia.buscar("Fontibón")
    with pytest.raises(FuenteError, match="404"):
        FincaRaiz(sesion=Sesion(Resp(404, "")), pausa=0).buscar("Fontibón")
    ok = FincaRaiz(sesion=Sesion(Resp(200, FIX.read_text())), pausa=0, paginas=1, detalle=False)
    d = ok.diagnosticar("Cedritos")
    assert d["estado"] == 200 and d["anuncios"] == 3 and d["dueno_directo"] == 2


def test_persona_natural_cuenta_como_dueno():
    for n in ("Julia Clemencia Toro", "Ingrid Beltr", "Andrea", "José Pérez"):
        assert parece_persona(n), n
    for n in ("Inmobiliaria XYZ SAS", "Bienes Raíces Ltda", "Metro123", "Grupo Habitat", "Arriendos y Ventas"):
        assert not parece_persona(n), n
    assert clasificar_anunciante("", "Julia Clemencia Toro") == ("persona natural · Julia Clemencia Toro", True)
    assert clasificar_anunciante("Inmobiliaria", "Julia Toro")[1] is False  # el portal dice que es agencia
    assert clasificar_anunciante("", "Inmobiliaria XYZ SAS")[1] is False


def test_extractor_tarjetas_html():
    html = (Path(__file__).parent / "fixtures" / "tarjetas.html").read_text()
    a = extraer_anuncios(html, "ciencuadras", "https://www.ciencuadras.com")
    assert len(a) == 2
    a1, a2 = sorted(a, key=lambda x: x.precio)
    assert (a2.precio, a2.area_m2, a2.habitaciones, a2.banos) == (450_000_000, 75, 3, 2)  # no toma la admin
    assert a2.url == "https://www.ciencuadras.com/inmueble/apartamento-en-venta-chapinero-101"
    assert a2.publicado_por == "inmobiliaria" and not a2.es_dueno_directo
    assert a1.es_dueno_directo and a1.area_m2 == 60 and "Chapinero Alto" in a1.titulo


def test_extractor_estado_angular():
    html = (Path(__file__).parent / "fixtures" / "angular.html").read_text()
    a = extraer_anuncios(html, "ciencuadras", "https://www.ciencuadras.com")
    assert len(a) == 1 and a[0].precio == 500_000_000 and a[0].es_dueno_directo


def test_portales_empresa_no_son_dueno_directo(monkeypatch):
    from comparam.sources import Habi, Houm, navegador

    urls = []
    monkeypatch.setattr(navegador, "renderizar", lambda url, **kw: urls.append(url) or FIX.read_text())
    h = Habi(pausa=0, paginas=1)
    todos = h.buscar("")
    assert len(todos) == 3 and all(not a.es_dueno_directo and a.publicado_por == "Habi (empresa)" for a in todos)
    assert "habi.co" in urls[-1]
    assert [a.barrio for a in h.buscar("Cedritos")] == ["Cedritos"]  # filtra el barrio localmente
    assert Habi().buscar("", "arriendo") == []  # Habi no arrienda
    assert len(Houm(pausa=0, paginas=1).buscar("", "venta")) == 3
    assert urls[-1].startswith("https://houm.com/co/venta-apartamentos-bogota")
    assert Houm().buscar("", "arriendo") == []  # solo venta por ahora
    assert Houm(paginas=8).paginas == 1  # con navegador se lee una página


def test_navegador_apagado_por_defecto(monkeypatch):
    from comparam.sources import navegador
    monkeypatch.delenv("COMPARAM_NAVEGADOR", raising=False)
    assert not navegador.disponible()


def test_navegador_lee_anuncios_cargados_con_javascript(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPARAM_NAVEGADOR", "1")
    import pytest
    from comparam.sources import navegador
    from comparam.sources.extractor import extraer_anuncios

    pagina = tmp_path / "spa.html"
    pagina.write_text('''<html><body><div id="app">Cargando…</div><script>
      setTimeout(function () {
        document.getElementById("app").innerHTML =
          '<div class="card"><a href="/inmueble/1"><h3>Apartamento en Cedritos</h3></a>' +
          '<p>$ 350.000.000</p><p>70 m²</p></div>';
      }, 300);
    </script></body></html>''')
    try:
        html = navegador.renderizar(pagina.as_uri(), espera_ms=1_000, desplazamientos=1, timeout_ms=20_000)
    except Exception as e:  # sin navegador instalado (p. ej. en CI)
        pytest.skip(f"navegador no disponible: {e}")
    assert "Cargando" not in html
    a = extraer_anuncios(html, "habi", "https://habi.co")
    assert len(a) == 1 and a[0].precio == 350_000_000 and a[0].area_m2 == 70

def test_diagnostico_da_pistas_y_paginacion_no_repite():
    from comparam.sources import FincaRaiz

    class Resp:
        status_code, url = 200, ""

        def __init__(self, text):
            self.text = text

    class Sesion:
        headers = {}

        def __init__(self, text):
            self.text, self.llamadas = text, 0

        def get(self, url, timeout=None):
            self.llamadas += 1
            return Resp(self.text)

    raro = ('<html><title>X</title><script id="__NEXT_DATA__" type="application/json">'
            '{"props":{"lista":[{"mPrecio":"450","otro":1}]}}</script><p>Desde $ 450.000.000 en Chapinero</p></html>')
    d = FincaRaiz(sesion=Sesion(raro), pausa=0).diagnosticar("")
    assert d["anuncios"] == 0 and any("mPrecio" in p for p in d["pistas"])
    assert any("$ 450.000.000" in p for p in d["pistas"])
    # Un portal que siempre devuelve la misma página no se lee 5 veces
    ses = Sesion(FIX.read_text())
    assert len(FincaRaiz(sesion=ses, pausa=0, paginas=5, detalle=False).buscar("")) == 3
    assert ses.llamadas == 2


def test_localidad_de():
    from comparam.localidades import localidad_de
    assert localidad_de("Cedritos") == "Usaquén"
    assert localidad_de("Apartamento en Venta en Chicó Navarra, Bogotá") == "Chapinero"
    assert localidad_de("San Bernardino XXII") == "Bosa"
    assert localidad_de("Modelia | Fontibón") == "Fontibón"
    assert localidad_de("Barrio desconocido") == ""


def test_ciudad_usa_localidad_si_el_barrio_tiene_pocos():
    class Portal:
        nombre = "p"

        def buscar(self, barrio, operacion="venta"):
            l = [_l("Apartamento en Venta en Cedritos, Bogotá"), _l("Apartamento en Venta en Lisboa, Bogotá"),
                 _l("Apartamento en Venta en Toberín, Bogotá"), _l("Apartamento en Venta en Usaquén, Bogotá")]
            l[0].precio, l[0].es_dueno_directo = 300_000_000, True
            return l

    from comparam import busqueda
    busqueda._CACHE.clear()
    rc = buscar_ciudad([Portal()])
    assert len(rc.oportunidades) == 1
    o = rc.oportunidades[0]
    assert (o.zona, o.nivel, o.valor.anuncios) == ("localidad Usaquén", "localidad", 4)
    assert o.diferencia < 0


def test_total_publicados():
    from comparam.sources.extractor import total_publicados
    assert total_publicados('<script id="__NEXT_DATA__" type="application/json">{"a":{"total":18432,"x":[]}}</script>', 25) == 18432
    assert total_publicados('<script type="application/json">{"hits":{"total":{"value":950}}}</script>', 20) == 950
    assert total_publicados("<html><h1>3.214 apartamentos en venta en Bogotá</h1></html>", 25) == 3214
    assert total_publicados("<html><p>2 baños</p></html>", 25) is None
    assert total_publicados('<script type="application/json">{"total":5}</script>', 25) is None  # menor que lo leído


def test_publicados_se_suman_en_la_busqueda():
    class P:
        nombre = "p"

        def __init__(self, total):
            self.total_publicados = total

        def buscar(self, barrio, operacion="venta"):
            return [_l("Apartamento en Venta en Cedritos, Bogotá")] * 2

    r = buscar("Cedritos", [P(1500), P(None)], solo_dueno=False)
    assert r.publicados == 1500 + 2
    import app as webapp
    assert webapp.miles(18432) == "18.432"


def test_descarta_precios_imposibles_y_casas():
    from comparam.sources.extractor import es_valido
    assert es_valido("Apartamento en Venta en Cedritos", 350_000_000, 70, "venta")
    assert not es_valido("Torres de Madeira - Apartamento", 3_240_000_000, 63, "venta")  # 51 M/m²
    assert not es_valido("Casa de Barrio - Casa en Venta en Parques de Salamanca", 832_600_000, 181, "venta")
    html = """<div class="c"><a href="/1"><h3>Torres - Apartamento en Venta en San Cristóbal Norte</h3></a>
      <p>$ 324.000.000</p><p>$ 3.240.000.000</p><p>63 m²</p></div>
      <div class="c"><a href="/2"><h3>Casa de Barrio - Casa en Venta en Parques</h3></a><p>$ 832.600.000</p><p>181 m²</p></div>"""
    a = extraer_anuncios(html, "habi", "https://habi.co")
    assert len(a) == 1 and a[0].precio == 324_000_000
    assert zona_de(a[0]) == "San Cristóbal Norte"


def test_metrocuadrado_usa_navegador(monkeypatch):
    from comparam.sources import Metrocuadrado, navegador

    urls = []
    monkeypatch.setattr(navegador, "renderizar", lambda url, **kw: urls.append(url) or FIX.read_text())
    m = Metrocuadrado(pausa=0, paginas=8, detalle=False)
    assert m.paginas == 1
    a = m.buscar("Cedritos")
    assert urls[0].startswith("https://www.metrocuadrado.com/apartamentos/venta/bogota/cedritos")
    assert len(a) == 3 and any(x.es_dueno_directo for x in a)  # no se marcan como empresa
    assert m.diagnosticar("Cedritos")["estado"] == "navegador"


def test_app_instalable():
    import json
    import app as webapp
    c = webapp.app.test_client()
    html = c.get("/").get_data(as_text=True)
    assert 'rel="manifest"' in html and "apple-touch-icon" in html and "Cedritos" in html
    m = json.loads(c.get("/static/manifest.webmanifest").get_data(as_text=True))
    assert m["display"] == "standalone" and {i["sizes"] for i in m["icons"]} >= {"192x192", "512x512"}
    sw = c.get("/sw.js")
    assert sw.status_code == 200 and "javascript" in sw.content_type and "offline.html" in sw.get_data(as_text=True)
    for f in ("icon-192.png", "icon-512.png", "icon-maskable-512.png", "apple-touch-icon.png", "offline.html"):
        assert c.get(f"/static/{f}").status_code == 200, f


def test_app_gastos():
    import json
    import app as webapp
    c = webapp.app.test_client()
    r = c.get("/gastos")
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and "text/html" in r.content_type
    assert "gastos.webmanifest" in html and "Cierre del día" in html
    for cat in ("Consignaciones", "Transporte", "Comida", "Póker", "Niños"):
        assert cat in html, cat
    m = json.loads(c.get("/static/gastos.webmanifest").get_data(as_text=True))
    assert m["start_url"].startswith("/gastos") and m["display"] == "standalone"
    for i in m["icons"]:
        assert c.get(i["src"]).status_code == 200, i["src"]
    assert "/gastos" in c.get("/sw.js").get_data(as_text=True)


def test_diario_detecta_nuevos(tmp_path, monkeypatch):
    from datetime import date
    from comparam import diario

    monkeypatch.setattr(diario, "HISTORIAL", tmp_path / "historial" / "anuncios.json")
    monkeypatch.setattr(diario, "CARPETA_DIA", tmp_path / "diario")
    anuncios = {"hoy": [_l("Apartamento en Venta en Cedritos, Bogotá")]}
    anuncios["hoy"][0].url, anuncios["hoy"][0].es_dueno_directo = "https://p.co/1", True

    class Portal:
        nombre = "fincaraiz"

        def buscar(self, barrio, operacion="venta"):
            return list(anuncios["hoy"])

    cfg = {"barrios": ["Cedritos"], "operaciones": ["venta"], "toda_bogota": False}
    r1 = diario.correr(cfg, lambda op: [Portal()], date(2026, 11, 8))
    assert r1["base"] and r1["nuevos"] == [] and r1["revisados"] == 1
    assert diario.nuevos(dia=date(2026, 11, 8)) == []  # el punto de partida no cuenta como nuevo

    nuevo = _l("Apartamento en Venta en Cedritos, Bogotá")
    nuevo.url, nuevo.es_dueno_directo, nuevo.precio, nuevo.area_m2 = "https://p.co/2", True, 300_000_000, 60
    anuncios["hoy"][0].precio = 380_000_000  # el anterior bajó de precio
    anuncios["hoy"].append(nuevo)
    r2 = diario.correr(cfg, lambda op: [Portal()], date(2026, 11, 9))
    assert not r2["base"] and len(r2["nuevos"]) == 1
    lista = diario.nuevos(dia=date(2026, 11, 9))
    assert [e["url"] for e in lista] == ["https://p.co/2"] and lista[0]["dias_texto"] == "publicado hoy"
    h = diario.cargar_json(diario.HISTORIAL, {})
    viejo = next(e for e in h.values() if e["url"] == "https://p.co/1")
    assert viejo["precios"] == [["2026-11-08", 400000000], ["2026-11-09", 380000000]]
    assert diario.dias_publicado(viejo, date(2026, 11, 8))[1] == "publicado antes del 2026-11-08"
    assert diario.dias_publicado(viejo, date(2026, 11, 9)) == (1, "publicado hace más de 1 día")
    assert "telefonos" not in viejo and "publicado_por" not in viejo  # sin datos personales

    import app as webapp
    msg = webapp._mensaje_whatsapp(lista, "09/11/2026")
    assert "*VENTA*" in msg and "https://p.co/2" in msg and "Cedritos" in msg
    monkeypatch.setattr(diario, "hoy", lambda: date(2026, 11, 9))
    html = webapp.app.test_client().get("/nuevos").get_data(as_text=True)
    assert "https://p.co/2" in html and "Copiar mensaje" in html


def test_fecha_publicacion():
    from comparam.sources.extractor import _fecha
    assert _fecha({"publicationDate": "2026-10-01T10:00:00Z"}) == "2026-10-01"
    assert _fecha({"fecha_publicacion": "5/9/2026"}) == "2026-09-05"
    assert _fecha({"createdAt": 1790000000000}) == "2026-09-21"
    assert _fecha({"otra": 1}) == ""


def test_diario_toda_bogota_e_historial_en_tarjetas(tmp_path, monkeypatch):
    from datetime import date
    from comparam import diario

    monkeypatch.setattr(diario, "HISTORIAL", tmp_path / "historial" / "anuncios.json")
    monkeypatch.setattr(diario, "CARPETA_DIA", tmp_path / "diario")
    dia = {"n": date(2026, 11, 8)}
    monkeypatch.setattr(diario, "hoy", lambda: dia["n"])
    norte = _l("Apartamento en Venta en Cedritos, Bogotá")
    norte.url, norte.es_dueno_directo = "https://p.co/n", True
    sur = _l("Apartamento en Venta en Bosa, Bogotá")
    sur.url, sur.es_dueno_directo = "https://p.co/s", True

    class Portal:
        nombre = "fincaraiz"

        def buscar(self, barrio, operacion="venta"):
            return [norte] if barrio else [norte, sur]

    cfg = {"barrios": ["Cedritos"], "operaciones": ["venta"]}
    diario.correr(cfg, lambda op: [Portal()], date(2026, 11, 8))
    sur2 = _l("Apartamento en Venta en Bosa, Bogotá")
    sur2.url, sur2.precio = "https://p.co/s2", 380_000_000
    sur.precio = 360_000_000  # bajó 10%
    original = Portal.buscar
    Portal.buscar = lambda self, barrio, operacion="venta": [norte] if barrio else [norte, sur, sur2]
    r = diario.correr(cfg, lambda op: [Portal()], date(2026, 11, 20))
    Portal.buscar = original
    assert r["nuevos"] == [] and r["nuevos_ciudad"] == {"venta": 1}  # lo de Bosa no es del cliente
    h = diario.cargar_json(diario.HISTORIAL, {})
    assert {e["zona"] for e in h.values()} == {"Cedritos", "Bosa"}
    assert not next(e for e in h.values() if e["url"] == "https://p.co/n").get("ciudad")
    dia["n"] = date(2026, 11, 20)
    i = diario.por_url("https://p.co/s", "venta")
    assert i["baja_pct"] == 10 and i["texto"] == "publicado hace más de 12 días" and i["dias"] == 12
    assert diario.por_url("https://p.co/s2", "venta")["texto"] == "publicado hoy"
    assert diario.por_url("https://p.co/s", "arriendo") is None


def test_sin_navegador_se_omiten_habi_y_houm(monkeypatch):
    from comparam.config import crear_fuentes
    from comparam.sources import PORTALES
    monkeypatch.delenv("COMPARAM_NAVEGADOR", raising=False)
    base = ["fincaraiz", "ciencuadras", "rentola"]
    # Sin navegador (Render): Metrocuadrado, Habi y Houm salen de los anuncios guardados
    assert [f.nombre for f in crear_fuentes()] == base + ["guardados"]
    assert [f.nombre for f in crear_fuentes(list(PORTALES))] == base + ["guardados"]
    assert [f.nombre for f in crear_fuentes(["fincaraiz"])] == ["fincaraiz"]
    monkeypatch.setenv("COMPARAM_NAVEGADOR", "1")  # revisión diaria de GitHub
    assert [f.nombre for f in crear_fuentes()] == base + ["habi", "houm"]


def test_sonda_solo_portales():
    import app as webapp
    c = webapp.app.test_client()
    assert c.get("/api/sonda?url=https://evil.example.com/").status_code == 400
    assert c.get("/api/sonda?url=http://www.fincaraiz.com.co/").status_code == 400
    assert c.get("/api/sonda?url=https://fincaraiz.com.co.evil.com/").status_code == 400


def test_persona_con_cuenta_profesional_es_verificar():
    import json
    datos = {"props": {"lista": [
        {"title": "Apartamento en Venta en Cedritos, Bogotá", "price": 325000000, "area": 63, "link": "/a/1",
         "owner": {"type": "Inmobiliaria", "name": "OLGA PATRICIA ROJAS QUINTERO"}},
        {"title": "Apartamento en Venta en Cedritos, Bogotá", "price": 400000000, "area": 70, "link": "/a/2",
         "owner": {"type": "Inmobiliaria", "name": "Inmobiliaria Norte SAS"}},
        {"title": "Apartamento en Venta en Cedritos, Bogotá", "price": 300000000, "area": 60, "link": "/a/3",
         "owner": {"name": "Andrea"}},
        {"title": "Apartamento en Venta en Cedritos, Bogotá", "price": 410000000, "area": 71, "link": "/a/4",
         "owner": {"type": "Inmobiliaria", "name": "PROVIVENDA"}},
    ]}}
    html = f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(datos)}</script>'
    a = {x.url[-1]: x for x in extraer_anuncios(html, "fincaraiz", "https://f.co")}
    assert a["1"].verificar and not a["1"].es_dueno_directo
    assert a["1"].publicado_por == "verificar · OLGA PATRICIA ROJAS QUINTERO"
    assert not a["2"].verificar and a["2"].publicado_por == "inmobiliaria"
    assert a["3"].es_dueno_directo and not a["3"].verificar
    assert not a["4"].verificar and a["4"].publicado_por == "inmobiliaria"  # marca de una palabra

    class P:
        nombre = "fincaraiz"

        def buscar(self, barrio, operacion="venta"):
            return list(a.values())

    r = buscar("Cedritos", [P()])
    assert {x.url[-1] for x in r.anuncios} == {"1", "3"} and r.ocultos == 2  # «verificar» se muestra
    assert buscar("Cedritos", [P()], solo_dueno=False).ocultos == 0


def test_casas_en_la_descripcion_no_es_inmobiliaria():
    assert clasificar_anunciante("", "", "Conjunto con casas y zonas verdes")[0] != "inmobiliaria"
    assert clasificar_anunciante("", "", "Vende Inmobiliaria Norte S.A.S.")[0] == "inmobiliaria"


def test_aviso_de_ocultos_y_ver_todos():
    import app as webapp
    html = webapp.app.test_client().get("/?barrio=Cedritos&modo=barrio&fuente=ejemplo").get_data(as_text=True)
    assert "de inmobiliarias ocultos" in html and "todos=1" in html and "Ver todos" in html
    msg = webapp._mensaje_whatsapp([{"operacion": "venta", "zona": "Cedritos", "area_m2": 63, "precio": 325000000,
                                     "url": "https://f.co/1", "verificar": True}], "09/11/2026")
    assert "verificar" in msg


def test_mercadolibre_dueno_directo_y_barrio():
    from comparam.sources import MercadoLibre, Properati

    class Resp:
        status_code = 200

        def __init__(self, text, url):
            self.text, self.url = text, url

    class Sesion:
        headers = {}

        def __init__(self, final=None):
            self.urls, self.final = [], final

        def get(self, url, timeout=None):
            self.urls.append(url)
            return Resp(FIX.read_text() if len(self.urls) == 1 else "<html></html>", self.final or url)

    ses = Sesion()
    ml = MercadoLibre(sesion=ses, pausa=0, paginas=2)
    a = ml.buscar("")
    assert ses.urls[0] == "https://listado.mercadolibre.com.co/apartamentos-venta-en-bogota-dc/directo/_Desde_1_NoIndex_True"
    assert "_Desde_49_" in ses.urls[1]
    assert len(a) == 3
    # el anuncio que el portal marca como inmobiliaria no se vuelve dueño; los demás sí (filtro /directo/)
    assert sorted(x.es_dueno_directo for x in a) == [False, True, True]
    assert [x.barrio for x in MercadoLibre(sesion=Sesion(), pausa=0).buscar("Cedritos")] == ["Cedritos"]
    # si el portal ignora el filtro (redirige sin /directo/), no se marcan como dueños
    sin = MercadoLibre(sesion=Sesion(final="https://listado.mercadolibre.com.co/apartamentos"), pausa=0, paginas=1)
    assert sum(x.es_dueno_directo for x in sin.buscar("")) == 2  # solo los que ya eran dueños por sus datos
    assert Properati().url_busqueda("Cedritos", "arriendo", 2) == \
        "https://www.properati.com.co/s/bogota-d-c/apartamento/arriendo?page=2"


def test_rentola_solo_arriendo_y_dominio_alterno():
    import requests
    from comparam.sources import Rentola

    class Resp:
        status_code = 200

        def __init__(self, url):
            self.url = url
            self.text = FIX.read_text().replace("400000000", "2000000").replace("500000000", "2500000") \
                .replace("$ 300.000.000", "$ 1.800.000") if "page=1" in url else "<html></html>"

    class Sesion:
        headers = {}

        def __init__(self):
            self.urls = []

        def get(self, url, timeout=None):
            self.urls.append(url)
            if url.startswith("https://rentola.com.co"):
                raise requests.ConnectionError("no resuelve")
            return Resp(url)

    ses = Sesion()
    assert Rentola(sesion=ses).buscar("", "venta") == [] and ses.urls == []
    a = Rentola(sesion=ses, pausa=0, paginas=1).buscar("", "arriendo")
    assert ses.urls[0] == "https://rentola.com.co/arriendo?location=348474&page=1"
    assert ses.urls[1] == "https://rentola.co.com/arriendo?location=348474&page=1"
    assert len(a) >= 2 and all(x.fuente == "rentola" for x in a)


def test_diagnostico_portal_sin_la_operacion():
    from comparam.sources import Habi, Rentola
    assert "no tiene venta" in Rentola().diagnosticar("", "venta")["error"]
    assert "no tiene arriendo" in Habi().diagnosticar("", "arriendo")["error"]


def test_guardados_devuelve_portales_con_navegador(tmp_path, monkeypatch):
    from datetime import date
    from comparam import diario
    from comparam.sources.guardados import Guardados

    monkeypatch.setattr(diario, "HISTORIAL", tmp_path / "anuncios.json")
    monkeypatch.setattr(diario, "hoy", lambda: date(2026, 11, 10))
    base = {"area_m2": 70, "precios": [], "habitaciones": 2, "banos": 1, "verificar": False, "publicado": "2026-10-01"}
    diario.guardar_json(diario.HISTORIAL, {
        "a": {**base, "fuente": "houm", "operacion": "venta", "zona": "Cedritos", "barrio": "Cedritos",
              "titulo": "Apartamento en Cedritos", "url": "https://m.co/1", "precio": 400000000,
              "dueno_directo": True, "anunciante": "dueño directo", "ultima_vez": "2026-11-10"},
        "b": {**base, "fuente": "houm", "operacion": "venta", "zona": "Bosa", "barrio": "Bosa",
              "titulo": "Apartamento en Bosa", "url": "https://m.co/2", "precio": 200000000,
              "dueno_directo": False, "anunciante": "inmobiliaria", "ultima_vez": "2026-11-09"},
        "c": {**base, "fuente": "houm", "operacion": "venta", "zona": "Cedritos", "barrio": "Cedritos",
              "titulo": "Viejo", "url": "https://m.co/3", "precio": 300000000,
              "dueno_directo": True, "anunciante": "dueño directo", "ultima_vez": "2026-11-01"},
        "d": {**base, "fuente": "fincaraiz", "operacion": "venta", "zona": "Cedritos", "barrio": "Cedritos",
              "titulo": "De Finca Raíz", "url": "https://f.co/4", "precio": 300000000,
              "dueno_directo": True, "anunciante": "dueño directo", "ultima_vez": "2026-11-10"},
    })
    g = Guardados()
    assert [a.url for a in g.buscar("Cedritos", "venta")] == ["https://m.co/1"]  # ni viejos ni Finca Raíz
    assert {a.url for a in g.buscar("", "venta")} == {"https://m.co/1", "https://m.co/2"}
    assert g.buscar("Cedritos", "arriendo") == []
    a = g.buscar("Cedritos", "venta")[0]
    assert a.fuente == "houm" and a.es_dueno_directo and a.publicado_por == "dueño directo"
    r = buscar("Cedritos", [g])
    assert len(r.anuncios) == 1 and r.anuncios[0].fuente == "houm"


def test_diagnostico_no_se_cae_si_un_portal_falla(monkeypatch):
    import app as webapp
    from comparam.sources import Rentola

    def explota(self, barrio="", operacion="venta"):
        raise ValueError("dato raro")

    monkeypatch.setattr(Rentola, "diagnosticar", explota)
    r = webapp.app.test_client().get("/diagnostico?operacion=arriendo")
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and "falla de la app: ValueError: dato raro" in html and "fincaraiz" in html


def test_rentola_diagnostico_prueba_el_segundo_dominio(monkeypatch):
    import requests
    from comparam.sources import Rentola

    class Resp:
        status_code, text = 200, "<html><title>Rentola</title></html>"

        def __init__(self, url):
            self.url = url

    class Sesion:
        headers = {}

        def get(self, url, timeout=None):
            if url.startswith("https://rentola.com.co"):
                raise requests.ConnectionError("no resuelve")
            return Resp(url)

    d = Rentola(sesion=Sesion()).diagnosticar("", "arriendo")
    assert d["url"].startswith("https://rentola.co.com/") and d["estado"] == 200


def test_busqueda_con_todos_los_portales_no_falla(monkeypatch):
    """Rentola tiene un atributo «ruta» (texto) que rompía el conteo por portal: error 500 en la app."""
    import requests
    import app as webapp

    def sin_red(self, url, **kw):
        raise requests.ConnectionError("sin red")

    monkeypatch.setattr(requests.Session, "get", sin_red)
    for q in ("/?barrio=Cedritos&modo=barrio", "/?barrio=Cedritos&modo=barrio&operacion=arriendo",
              "/?modo=ciudad", "/api/buscar?barrio=Cedritos"):
        assert webapp.app.test_client().get(q).status_code == 200, q


def test_portal_trabado_no_frena_la_busqueda(monkeypatch):
    import threading
    from comparam import busqueda

    monkeypatch.setattr(busqueda, "TIEMPO_MAX_PORTAL", 1)
    soltar = threading.Event()

    class Trabado:
        nombre = "rentola"

        def buscar(self, barrio, operacion="venta"):
            soltar.wait(30)
            return []

    class Bueno:
        nombre = "fincaraiz"

        def buscar(self, barrio, operacion="venta"):
            return [_l("Apartamento en Venta en Cedritos, Bogotá")]

    import time
    t = time.time()
    r = buscar("Cedritos", [Trabado(), Bueno()], solo_dueno=False)
    soltar.set()
    assert time.time() - t < 5
    assert len(r.anuncios) == 1 and any("rentola: tardó más de 1 s" in e for e in r.errores)


def test_navegador_se_corta_si_la_pagina_se_traba(tmp_path, monkeypatch):
    import pytest
    from comparam.sources import navegador

    monkeypatch.setenv("COMPARAM_NAVEGADOR", "1")
    pagina = tmp_path / "trabada.html"
    # La página bloquea el hilo de JavaScript para siempre: el navegador queda trabado.
    pagina.write_text("<html><body><script>while (true) {}</script></body></html>")
    import time
    t = time.time()
    with pytest.raises(navegador.FuenteError) as e:
        navegador.renderizar(pagina.as_uri(), espera_ms=200, desplazamientos=1, timeout_ms=3_000)
    if "no pudo abrir" in str(e.value) and "Executable" in str(e.value):
        pytest.skip("navegador no instalado")
    assert time.time() - t < 60


def test_mercadolibre_api(monkeypatch):
    from comparam.config import crear_fuentes
    from comparam.sources import mercadolibre_api as ml

    monkeypatch.delenv("ML_CLIENT_ID", raising=False)
    monkeypatch.delenv("ML_CLIENT_SECRET", raising=False)
    assert "mercadolibre" not in [f.nombre for f in crear_fuentes()]  # sin credenciales no se usa
    monkeypatch.setenv("ML_CLIENT_ID", "123")
    monkeypatch.setenv("ML_CLIENT_SECRET", "abc")
    ml._TOKEN.update(valor=None, vence=0)
    assert "mercadolibre" in [f.nombre for f in crear_fuentes()]

    def res(id_, precio, area, op="Venta", tipo="Apartamento", barrio="Cedritos", agencia=False):
        return {"id": id_, "title": f"Apto {barrio}", "price": precio, "currency_id": "COP",
                "permalink": f"https://ml.co/{id_}", "date_created": "2026-10-01T10:00:00Z",
                "location": {"neighborhood": {"name": barrio}, "city": {"name": "Bogotá"}},
                "seller": {"real_estate_agency": agencia},
                "attributes": [{"id": "OPERATION", "value_name": op}, {"id": "PROPERTY_TYPE", "value_name": tipo},
                               {"id": "COVERED_AREA", "value_name": f"{area} m²", "value_struct": {"number": area}},
                               {"id": "BEDROOMS", "value_name": "3"}]}

    class Resp:
        def __init__(self, code, data):
            self.status_code, self._d, self.text = code, data, str(data)

        def json(self):
            return self._d

    class Sesion:
        def __init__(self):
            self.llamadas = []

        def post(self, url, data=None, timeout=None):
            assert data["grant_type"] == "client_credentials" and data["client_secret"] == "abc"
            return Resp(200, {"access_token": "TOK", "expires_in": 21600})

        def get(self, url, params=None, headers=None, timeout=None):
            self.llamadas.append(params)
            assert headers["Authorization"] == "Bearer TOK"
            return Resp(200, {"paging": {"total": 4210}, "results": [
                res(1, 350_000_000, 70), res(2, 2_000_000, 50, op="Arriendo"), res(3, 400_000_000, 120, tipo="Casa"),
                res(4, 500_000_000, 80, barrio="Chicó", agencia=True)]})

    ses = Sesion()
    a = ml.MercadoLibreAPI(paginas=1, sesion=ses).buscar("Cedritos", "venta")
    assert [x.url for x in a] == ["https://ml.co/1"]  # ni arriendo, ni casa, ni otro barrio
    x = a[0]
    assert x.es_dueno_directo and x.area_m2 == 70 and x.habitaciones == 3 and x.publicado == "2026-10-01"
    assert ses.llamadas[0]["q"] == "apartamento venta Cedritos bogota" and ses.llamadas[0]["category"] == "MCO1459"
    todos = ml.MercadoLibreAPI(paginas=1, sesion=Sesion()).buscar("", "venta")
    assert {y.url for y in todos} == {"https://ml.co/1", "https://ml.co/4"}
    assert not next(y for y in todos if y.url.endswith("/4")).es_dueno_directo

    class Negada(Sesion):
        def post(self, url, data=None, timeout=None):
            return Resp(403, {"message": "forbidden"})

    ml._TOKEN.update(valor=None, vence=0)
    import pytest
    with pytest.raises(ml.FuenteError, match="HTTP 403"):
        ml.MercadoLibreAPI(sesion=Negada()).buscar("", "venta")
    ml._TOKEN.update(valor=None, vence=0)


def test_mercadolibre_api_renueva_token_si_le_niegan(monkeypatch):
    from comparam.sources import mercadolibre_api as ml
    monkeypatch.setenv("ML_CLIENT_ID", "1")
    monkeypatch.setenv("ML_CLIENT_SECRET", "s")
    ml._TOKEN.update(valor="VIEJO", vence=10**12)

    class Resp:
        def __init__(self, code, data):
            self.status_code, self._d, self.text = code, data, ""

        def json(self):
            return self._d

    class Sesion:
        def post(self, url, data=None, timeout=None):
            return Resp(200, {"access_token": "NUEVO", "expires_in": 21600})

        def get(self, url, params=None, headers=None, timeout=None):
            if headers["Authorization"] == "Bearer VIEJO":
                return Resp(403, {"message": "forbidden"})
            return Resp(200, {"paging": {"total": 0}, "results": []})

    assert ml.MercadoLibreAPI(paginas=1, sesion=Sesion()).buscar("", "venta") == []
    assert ml._TOKEN["valor"] == "NUEVO"
    ml._TOKEN.update(valor=None, vence=0)


def test_mercadolibre_autorizacion_de_usuario(monkeypatch):
    from urllib.parse import parse_qs, urlparse
    import app as webapp
    from comparam.sources import mercadolibre_api as ml

    monkeypatch.setenv("ML_CLIENT_ID", "864")
    monkeypatch.setenv("ML_CLIENT_SECRET", "secreto")
    c = webapp.app.test_client()
    r = c.get("/ml/conectar")
    assert r.status_code == 302
    q = parse_qs(urlparse(r.location).query)
    assert r.location.startswith("https://auth.mercadolibre.com.co/authorization")
    assert q["client_id"] == ["864"] and q["redirect_uri"] == ["https://comparam2.onrender.com"]
    estado = q["state"][0]
    assert ml.estado_valido(estado) and not ml.estado_valido("123.falso")

    capturado = {}

    def canjear(code, redirect_uri, sesion=None):
        capturado.update(code=code, redirect_uri=redirect_uri)
        return {"access_token": "USR", "refresh_token": "R"}

    def buscar(self, barrio, operacion="venta"):
        assert self.token_usuario == "USR"
        self.total_publicados = 1234
        return [_l("Apartamento en Venta en Cedritos, Bogotá")]

    monkeypatch.setattr(ml, "canjear_codigo", canjear)
    monkeypatch.setattr(ml.MercadoLibreAPI, "buscar", buscar)
    html = c.get(f"/?code=TG-abc&state={estado}").get_data(as_text=True)
    assert "MercadoLibre funciona" in html and "1234" in html and "trajo 1 anuncios" in html
    assert capturado == {"code": "TG-abc", "redirect_uri": "https://comparam2.onrender.com"}
    assert "no es válida" in c.get("/?code=x&state=1.malo").get_data(as_text=True)


def test_barrio_en_titulos_de_habi_y_houm():
    assert zona_de(_l("Modelia imperial 2 - apartamento en venta en modelia, fontibón")) == "Modelia"
    assert zona_de(_l("Torre Estacion - Apartamento en Venta en San Victorino, Los Mártires")) == "San Victorino"
    assert zona_de(_l("Apartamento Usaquén, Bogotá")) == "Usaquén"
    html = """<div class="c"><a href="/v/1"><h3>Modelia imperial 2 - apartamento en venta en modelia, fontibón</h3></a>
      <p>$ 300.000.000</p><p>60 m²</p></div>"""
    a = extraer_anuncios(html, "habi", "https://habi.co")
    assert a[0].barrio == "Modelia"


def test_historial_actualiza_titulo_y_barrio(tmp_path, monkeypatch):
    from datetime import date
    from comparam import diario
    monkeypatch.setattr(diario, "HISTORIAL", tmp_path / "h.json")
    monkeypatch.setattr(diario, "CARPETA_DIA", tmp_path / "d")
    h = {}
    viejo = _l("Anterior Siguiente $ 250.000.000 30 m² Apartamento Usaquén, Bogotá")
    viejo.url = "https://houm.com/1"
    diario.registrar(h, viejo, "venta", "Bogotá", "2026-11-09", False, ciudad=True)
    nuevo = _l("Apartamento Usaquén, Bogotá", "Usaquén")
    nuevo.url = "https://houm.com/1"
    diario.registrar(h, nuevo, "venta", "Usaquén", "2026-11-10", False, ciudad=True)
    e = next(iter(h.values()))
    assert (e["titulo"], e["barrio"], e["zona"]) == ("Apartamento Usaquén, Bogotá", "Usaquén", "Usaquén")


def test_tarjetas_sin_enlace_normal_rentola():
    from comparam.sources.extractor import MUESTRAS, extraer_de_tarjetas
    html = """<div class="card" data-url="/arriendo/123"><div><span>$ 1.200.000 / mes</span><span>68 m²</span>
    <span>3 habitaciones</span><p>Chapinero Alto, Bogotá</p></div></div>
    <div class="card" onclick="location.href='https://rentola.co.com/a/9'"><span>$ 2.000.000</span><span>50 m²</span>
    <p>Apartamento en arriendo Cedritos, Bogotá D.C.</p></div>"""
    a = extraer_de_tarjetas(html, "rentola", "https://rentola.co.com", "arriendo")
    assert [(x.barrio, x.url) for x in a] == [("Chapinero Alto", "https://rentola.co.com/arriendo/123"),
                                              ("Cedritos", "https://rentola.co.com/a/9")]
    assert a[0].habitaciones == 3 and MUESTRAS["rentola"].startswith("tarjeta:")


def test_tarjeta_rentola_real():
    from datetime import date, timedelta
    from comparam.sources.extractor import extraer_de_tarjetas
    html = """<div class="space-y-1"><span data-testid="t">hace 12 días</span><span>4 habitaciones</span>
      <span>apartamento de 80m²</span><span>Calle 20C, Fontibón, 110921 Bogota, Colombia</span>
      <span>$1.000.000 / mes</span></div>"""
    a = extraer_de_tarjetas(html, "rentola", "https://rentola.co.com", "arriendo")
    assert len(a) == 1
    x = a[0]
    assert (x.barrio, x.titulo, x.precio, x.area_m2, x.habitaciones) == \
        ("Fontibón", "Apartamento en Fontibón", 1_000_000, 80, 4)
    assert x.publicado == (date.today() - timedelta(days=12)).isoformat()


def test_persona_con_varios_avisos_es_agente(tmp_path, monkeypatch):
    from datetime import date
    from comparam import diario
    from comparam.busqueda import marcar_agentes

    def anuncio(url, quien, precio):
        a = _l("Apartamento en Venta en Quinta paredes, Bogotá")
        a.url, a.precio, a.publicado_por = url, precio, quien
        a.verificar = quien.startswith("verificar")
        a.es_dueno_directo = quien.startswith("persona natural")
        return a

    lista = [anuncio("u1", "verificar · SANDRA REY", 560e6), anuncio("u2", "verificar · Sandra Rey", 420e6),
             anuncio("u3", "persona natural · Andrea", 300e6)]
    assert marcar_agentes(lista) == 2
    assert lista[0].publicado_por == "agente · SANDRA REY · 2 avisos" and not lista[0].verificar
    assert lista[2].es_dueno_directo  # una sola publicación: sigue siendo dueño directo

    class P:
        nombre = "fincaraiz"

        def buscar(self, barrio, operacion="venta"):
            return [anuncio("u1", "verificar · SANDRA REY", 560e6), anuncio("u2", "verificar · SANDRA REY", 420e6),
                    anuncio("u3", "persona natural · Andrea", 300e6)]

    r = buscar("Quinta Paredes", [P()])
    assert [a.url for a in r.anuncios] == ["u3"] and r.ocultos == 2

    # Revisión diaria: guarda la marca de agente (sin el nombre) y deja de contar como dueño
    monkeypatch.setattr(diario, "HISTORIAL", tmp_path / "h.json")
    monkeypatch.setattr(diario, "CARPETA_DIA", tmp_path / "d")
    diario.correr({"barrios": ["Quinta Paredes"], "operaciones": ["venta"], "toda_bogota": False},
                  lambda op: [P()], date(2026, 11, 11))
    h = diario.cargar_json(diario.HISTORIAL, {})
    sandra = [e for e in h.values() if e["url"] in ("u1", "u2")]
    assert all(e["agente"] == 2 and not e["dueno_directo"] and not e["verificar"] for e in sandra)
    assert "SANDRA" not in json.dumps(h, ensure_ascii=False).upper()


def test_nombres_de_empresa_mal_escritos():
    assert not parece_persona("LAB G Real State")
    assert not parece_persona("Bogota Real Estate Team")
    assert parece_persona("GLORIA GAMBOA") and parece_persona("Esperanza Domínguez P")


def test_mitula():
    from comparam.busqueda import nombre_anunciante
    from comparam.sources import Mitula

    tarjeta_sin_anunciante = """<div class="c"><a href="/a/77"><h3>Apartamento en arriendo en Suba, Bogotá</h3></a>
      <p>$ 1.500.000</p><p>60 m²</p></div>"""

    class Resp:
        status_code = 200

        def __init__(self, url, text):
            self.url, self.text = url, text

    class Sesion:
        headers = {}

        def __init__(self, html):
            self.urls, self.html = [], html

        def get(self, url, timeout=None):
            self.urls.append(url)
            return Resp(url, self.html if "page=1" in url else "<html></html>")

    ses = Sesion(tarjeta_sin_anunciante)
    a = Mitula(sesion=ses, pausa=0, paginas=1).buscar("", "arriendo")
    assert ses.urls[0] == "https://casas.mitula.com.co/casas/arriendo-apartamentos-due%C3%B1o-directo-bogota?page=1"
    assert len(a) == 1 and a[0].verificar and a[0].publicado_por == "verificar (Mitula: «dueño directo»)"
    assert nombre_anunciante(a[0]) == ""  # no se cuenta como una persona con muchos avisos
    assert Mitula().url_busqueda("", "venta", 2).endswith("/casas/apartamentos-due%C3%B1o-directo-bogota?page=2")
    # Con datos del anunciante: el dueño confirmado se mantiene y la inmobiliaria también
    v = Mitula(sesion=Sesion(FIX.read_text()), pausa=0, paginas=1).buscar("", "venta")
    assert sorted((x.es_dueno_directo, x.verificar, x.publicado_por == "inmobiliaria") for x in v) == \
        [(False, False, True), (True, False, False), (True, False, False)]


def test_sitio_publico(tmp_path):
    from datetime import date
    from comparam import sitio

    def e(i, op, zona, precio, area, dueno=False):
        return {"operacion": op, "zona": zona, "barrio": zona, "titulo": f"Apartamento en Venta en {zona}, Bogotá",
                "url": f"https://f.co/{i}", "fuente": "fincaraiz", "precio": precio, "area_m2": area,
                "dueno_directo": dueno, "publicado": "2026-10-01", "primera_vez": "2026-11-08",
                "ultima_vez": "2026-11-11", "precios": [["2026-11-08", precio]], "habitaciones": 3}

    h = {str(i): e(i, "venta", "Cedritos", 300_000_000 + i * 10_000_000, 60 + i, dueno=i < 2) for i in range(6)}
    h.update({f"c{i}": e(f"c{i}", "venta", "Chapinero Alto", 500_000_000, 60) for i in range(3)})  # pocos: sin página
    r = sitio.construir(tmp_path, date(2026, 11, 11), h)
    ced = (tmp_path / "precio-m2/venta/cedritos/index.html").read_text()
    assert "Precio del metro cuadrado en Cedritos" in ced and "2 de 6" in ced and "https://f.co/0" in ced
    assert (tmp_path / "precio-m2/venta/localidad-usaquen/index.html").exists()
    assert not (tmp_path / "precio-m2/venta/chapinero-alto").exists()
    mapa = (tmp_path / "sitemap.xml").read_text()
    assert "/precio-m2/venta/cedritos/" in mapa and (tmp_path / "robots.txt").exists()
    assert "Probar la app" in (tmp_path / "index.html").read_text() and r["zonas"] == 2


def test_lista_para_cliente_sin_enlaces_y_con_telefono():
    import app as webapp
    c = webapp.app.test_client()
    html = c.get("/lista?barrio=Cedritos&fuente=ejemplo&operacion=venta&cliente=Ana").get_data(as_text=True)
    assert "Compara m²" in html and "Ana" in html
    assert "300 000 0001" in html          # dueño directo con teléfono
    assert "Inmobiliaria Ejemplo" not in html
    assert "fincaraiz.com" not in html and "href=\"http" not in html  # sin enlaces a portales
    assert c.get("/lista").status_code == 200
    assert "Lista para cliente" in c.get("/?barrio=Cedritos&fuente=ejemplo").get_data(as_text=True)


def test_busqueda_de_barrio_reutiliza_lo_traido(monkeypatch):
    from comparam import busqueda
    from comparam.sources import PORTALES
    llamadas = []
    cls = PORTALES["fincaraiz"]
    monkeypatch.setattr(cls, "buscar", lambda self, b, op="venta": llamadas.append(1) or [
        Listing("fincaraiz", "Apto en Cedritos, Bogotá", "Cedritos", 300e6, 60, telefonos=["3001234567"],
                es_dueno_directo=True, url="u1")])
    busqueda._CACHE_BARRIO.clear()
    f = [cls(paginas=1)]
    r1 = busqueda.buscar("Cedritos", f)
    r2 = busqueda.buscar("Cedritos", f, con_telefono=True)
    assert len(llamadas) == 1 and len(r1.anuncios) == len(r2.anuncios) == 1
    busqueda._CACHE_BARRIO.clear()


def test_parqueaderos_en_texto():
    from comparam.parsing import parqueaderos_en_texto as p
    assert p("Apto 3 hab, 2 baños, 2 parqueaderos") == 2
    assert p("Incluye parqueadero y depósito") == 1
    assert p("parqueadero privado cubierto") == 1
    assert p("Sin parqueadero, excelente ubicación") == 0
    assert p("No tiene garaje") == 0
    assert p("Conjunto con parqueadero de visitantes") is None
    assert p("Apto remodelado") is None and p("") is None


def test_parqueaderos_en_json():
    from comparam.sources.extractor import _parqueaderos
    assert _parqueaderos({"garage": 1}) == 1
    assert _parqueaderos({"parking": False}) == 0
    assert _parqueaderos({"technicalSheet": [{"field": "bedrooms", "value": 3}, {"field": "garage", "value": "2"}]}) == 2
    assert _parqueaderos({}, "Apto con parqueadero") == 1
    assert _parqueaderos({}) is None


def test_lista_filtra_parqueadero(monkeypatch):
    import app as webapp
    from comparam import busqueda
    from comparam.busqueda import Resultado
    anuncios = [Listing("fincaraiz", "Con", "Cedritos", 2e6, 60, telefonos=["3001112233"], es_dueno_directo=True,
                        parqueaderos=1),
                Listing("fincaraiz", "Sin dato", "Cedritos", 2e6, 60, telefonos=["3004445566"], es_dueno_directo=True)]
    monkeypatch.setattr(webapp, "buscar", lambda *a, **k: Resultado(list(anuncios)))
    html = webapp.app.test_client().get("/lista?barrio=Cedritos&operacion=arriendo&parq=si").get_data(as_text=True)
    assert "3001112233" in html and "3004445566" not in html and "1</b> parq." in html


def test_filtros_se_pueden_elegir_antes_de_buscar():
    import app as webapp
    html = webapp.app.test_client().get("/").get_data(as_text=True)
    assert 'id="filtros-form"' in html and 'id="hoja"' in html and 'id="f-parq"' in html
    assert 'data-op="arriendo"' in html and 'data-op="venta"' in html
