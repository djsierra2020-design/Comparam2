from .base import Source, FuenteError
from .portales import FincaRaiz, Metrocuadrado, Ciencuadras, Habi, Houm, MercadoLibre, Properati, Rentola
from .archivo import ArchivoCSV

# Metrocuadrado queda fuera: necesita navegador y con él la app daba error 502 en Render.
PORTALES = {"fincaraiz": FincaRaiz, "ciencuadras": Ciencuadras, "mercadolibre": MercadoLibre,
            "properati": Properati, "rentola": Rentola, "habi": Habi, "houm": Houm}

__all__ = ["Source", "FuenteError", "FincaRaiz", "Metrocuadrado", "Ciencuadras", "Habi", "Houm",
           "MercadoLibre", "Properati", "Rentola", "ArchivoCSV", "PORTALES"]
