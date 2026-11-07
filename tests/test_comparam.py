from pathlib import Path

from comparam.busqueda import buscar
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
    assert "Calculado con" in html and "4 anuncios" in html and "Ver los 4 anuncios usados" in html
    assert "Apto con balcón" in html  # el de inmobiliaria aparece en el cálculo, no en la lista
