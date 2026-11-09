from .base import Source, FuenteError
from .portales import FincaRaiz, Metrocuadrado, Ciencuadras, Habi, Houm, MercadoLibre, Properati, Rentola
from .archivo import ArchivoCSV

# Metrocuadrado, Habi y Houm necesitan navegador: solo se abren donde está encendido (la revisión
# diaria de GitHub). En Render se leen sus anuncios guardados (sources/guardados.py).
# MercadoLibre (captcha de «Seguridad») y Properati (HTTP 401) bloquean las consultas automáticas:
# quedan fuera; no se intenta saltar esas protecciones.
PORTALES = {"fincaraiz": FincaRaiz, "ciencuadras": Ciencuadras, "rentola": Rentola,
            "metrocuadrado": Metrocuadrado, "habi": Habi, "houm": Houm}

__all__ = ["Source", "FuenteError", "FincaRaiz", "Metrocuadrado", "Ciencuadras", "Habi", "Houm",
           "MercadoLibre", "Properati", "Rentola", "ArchivoCSV", "PORTALES"]
