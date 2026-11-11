from .base import Source, FuenteError
from .portales import FincaRaiz, Metrocuadrado, Ciencuadras, Habi, Houm, MercadoLibre, Properati, Rentola, Mitula
from .archivo import ArchivoCSV

# Metrocuadrado, Habi y Houm necesitan navegador: solo se abren donde está encendido (la revisión
# diaria de GitHub). En Render se leen sus anuncios guardados (sources/guardados.py).
# MercadoLibre (captcha de «Seguridad») y Properati (HTTP 401) bloquean las consultas automáticas:
# quedan fuera; no se intenta saltar esas protecciones.
# Metrocuadrado tampoco muestra anuncios a un navegador automático (0 incluso con navegador en
# GitHub): queda fuera; no se intenta evadir esa detección.
PORTALES = {"fincaraiz": FincaRaiz, "ciencuadras": Ciencuadras, "rentola": Rentola, "mitula": Mitula,
            "habi": Habi, "houm": Houm}

__all__ = ["Source", "FuenteError", "FincaRaiz", "Metrocuadrado", "Ciencuadras", "Habi", "Houm",
           "MercadoLibre", "Properati", "Rentola", "Mitula", "ArchivoCSV", "PORTALES"]
