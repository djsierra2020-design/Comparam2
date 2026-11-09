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
    assert "arriendo-apartamentos-bogota" in Houm().url_busqueda("", "arriendo", 1)
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
    monkeypatch.delenv("COMPARAM_NAVEGADOR", raising=False)
    assert [f.nombre for f in crear_fuentes()] == ["fincaraiz", "ciencuadras"]
    monkeypatch.setenv("COMPARAM_NAVEGADOR", "1")
    assert [f.nombre for f in crear_fuentes()] == ["fincaraiz", "ciencuadras", "habi", "houm"]


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
