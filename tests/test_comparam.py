from pathlib import Path

from comparam.busqueda import buscar, buscar_ciudad, es_del_barrio, zona_de
from comparam.models import Listing
from comparam.config import EJEMPLO
from comparam.parsing import clasificar_anunciante, extract_phones, parse_number, slugify
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
    html = c.get("/?modo=ciudad&fuente=ejemplo").get_data(as_text=True)
    assert "su barrio" in html and "Fontibón" in html
    for escrito in ("toda bogotá", "Bogotá", "Toda Bogota"):
        html = c.get(f"/?barrio={escrito}&fuente=ejemplo").get_data(as_text=True)
        assert "Barrios comparables" in html
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
    assert "Anuncios leídos por portal" in html
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
    assert not es_del_barrio(_l("Apto amplio", "Salitre", "Salitre | Bogotá"), "Fontibón")
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
    assert set(rc.zonas) == {"Cedritos", "Fontibón", "Chapinero"}
    assert all(o.anuncio.es_dueno_directo for o in rc.oportunidades)
    difs = [o.diferencia for o in rc.oportunidades]
    assert difs == sorted(difs)
    # El más barato en pesos/m² de Fontibón queda primero frente a SU barrio
    assert rc.por_fuente == {"ejemplo": 13}
    assert rc.oportunidades[0].zona == "Fontibón" and rc.oportunidades[0].diferencia < 0
    assert rc.zona_pequena == 2  # Teusaquillo y Chapinero Alto: 1 anuncio cada uno


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
