"""Links temporales para clientes: /c/<token> muestra la lista de un barrio hasta una fecha.

El token lleva la búsqueda (barrio, operación, filtros) y la fecha de vencimiento, firmados con una
clave del servidor (LINK_SECRET o, si no está, ML_CLIENT_SECRET): no se guarda nada en el servidor y
nadie puede cambiar la búsqueda ni alargar la fecha sin invalidar la firma. Cada vez que el cliente
abre el link se hace la búsqueda de nuevo, así que ve los anuncios vigentes ese día.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from datetime import date, timedelta

DIAS_PERMITIDOS = (3, 7, 15, 30)


class LinkInvalido(Exception):
    pass


def _clave() -> bytes | None:
    secreto = os.environ.get("LINK_SECRET") or os.environ.get("ML_CLIENT_SECRET")
    if not secreto:
        return None
    return hmac.new(secreto.encode(), b"comparam-links-clientes", hashlib.sha256).digest()


def disponible() -> bool:
    return _clave() is not None


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _firma(cuerpo: str) -> str:
    return _b64(hmac.new(_clave(), cuerpo.encode(), hashlib.sha256).digest()[:16])


def crear(datos: dict, dias: int, hoy: date) -> tuple[str, date]:
    """Token para `datos` (búsqueda y filtros) que vence `dias` días después de `hoy`."""
    if not disponible():
        raise LinkInvalido("falta la clave del servidor (LINK_SECRET)")
    vence = hoy + timedelta(days=dias)
    cuerpo = _b64(json.dumps({**{k: v for k, v in datos.items() if v not in (None, "", 0, False)},
                              "v": vence.isoformat()}, separators=(",", ":"), ensure_ascii=False).encode())
    return f"{cuerpo}.{_firma(cuerpo)}", vence


def leer(token: str) -> tuple[dict, date]:
    """(datos, fecha de vencimiento). No revisa si ya venció: eso lo decide quien lo usa."""
    if not disponible():
        raise LinkInvalido("falta la clave del servidor")
    cuerpo, _, firma = (token or "").partition(".")
    if not cuerpo or not hmac.compare_digest(firma, _firma(cuerpo)):
        raise LinkInvalido("link alterado o inválido")
    try:
        datos = json.loads(_unb64(cuerpo))
        vence = date.fromisoformat(datos.pop("v"))
    except (ValueError, KeyError, TypeError) as e:
        raise LinkInvalido("link inválido") from e
    return datos, vence
