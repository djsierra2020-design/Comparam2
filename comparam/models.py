from __future__ import annotations

from dataclasses import dataclass, field, asdict


@dataclass
class Listing:
    fuente: str
    titulo: str
    barrio: str
    precio: float  # COP
    area_m2: float
    url: str = ""
    telefonos: list[str] = field(default_factory=list)
    publicado_por: str = ""  # "dueño directo", "inmobiliaria" o "" si se desconoce
    es_dueno_directo: bool = False
    # Persona (no empresa) a la que el portal marca como cuenta profesional: puede ser el dueño con un
    # plan pagado o un agente independiente. Se muestra junto a los dueños, con etiqueta «verificar».
    verificar: bool = False
    habitaciones: int | None = None
    banos: int | None = None
    parqueaderos: int | None = None  # 0 = el anuncio dice que no tiene; None = no lo dice
    descripcion: str = ""
    ubicacion: str = ""  # todo el texto de ubicación del anuncio (barrio, localidad, zona, ciudad)
    publicado: str = ""  # fecha de publicación AAAA-MM-DD, si el portal la indica

    @property
    def precio_m2(self) -> float:
        return self.precio / self.area_m2 if self.area_m2 else float("inf")

    def to_dict(self) -> dict:
        d = asdict(self)
        d["precio_m2"] = round(self.precio_m2)
        return d
