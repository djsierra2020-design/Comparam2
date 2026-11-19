from __future__ import annotations

import abc

from ..models import Listing


class FuenteError(Exception):
    """Error al consultar una fuente de anuncios."""


class Source(abc.ABC):
    nombre = "fuente"
    tipo = "apartamento"  # «apartamento» o «casa»: lo fija crear_fuentes

    @abc.abstractmethod
    def buscar(self, barrio: str, operacion: str = "venta") -> list[Listing]:
        ...
