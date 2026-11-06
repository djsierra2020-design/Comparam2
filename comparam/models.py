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
    habitaciones: int | None = None
    banos: int | None = None
    descripcion: str = ""

    @property
    def precio_m2(self) -> float:
        return self.precio / self.area_m2 if self.area_m2 else float("inf")

    def to_dict(self) -> dict:
        d = asdict(self)
        d["precio_m2"] = round(self.precio_m2)
        return d
