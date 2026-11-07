"""Abre páginas con un navegador real (Chromium sin ventana) para portales que cargan los
anuncios con JavaScript después de abrir la página (Habi, Houm).

Requiere el paquete ``playwright`` y el navegador instalado (``playwright install chromium``).
Se puede desactivar con COMPARAM_NAVEGADOR=0. Solo se abre un navegador a la vez para no
agotar la memoria del servidor.
"""
from __future__ import annotations

import os
import threading

from .base import FuenteError

_CANDADO = threading.Lock()

# build.sh instala el navegador en <proyecto>/.playwright: se usa si existe.
_LOCAL = os.path.join(os.path.dirname(__file__), "..", "..", ".playwright")
if "PLAYWRIGHT_BROWSERS_PATH" not in os.environ and os.path.isdir(_LOCAL):
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = os.path.abspath(_LOCAL)


def disponible() -> bool:
    if os.environ.get("COMPARAM_NAVEGADOR", "1") == "0":
        return False
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        return False
    return True


def renderizar(url: str, espera_ms: int = 4_000, desplazamientos: int = 4, timeout_ms: int = 45_000) -> str:
    """Devuelve el HTML de la página después de ejecutar su JavaScript y bajar por ella (para que
    carguen los anuncios que aparecen al desplazarse)."""
    if not disponible():
        raise FuenteError("navegador no disponible en el servidor")
    from playwright.sync_api import Error as PWError
    from playwright.sync_api import sync_playwright

    opciones = {"headless": True, "args": ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]}
    if ruta := os.environ.get("COMPARAM_CHROMIUM"):
        opciones["executable_path"] = ruta
    with _CANDADO:
        try:
            with sync_playwright() as p:
                navegador = p.chromium.launch(**opciones)
                try:
                    pagina = navegador.new_page(locale="es-CO", viewport={"width": 1280, "height": 900})
                    # No se descargan imágenes, videos ni fuentes: ahorra memoria y tiempo.
                    pagina.route("**/*", lambda r: r.abort() if r.request.resource_type in
                                 ("image", "media", "font") else r.continue_())
                    pagina.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                    try:
                        pagina.wait_for_load_state("networkidle", timeout=espera_ms * 3)
                    except PWError:
                        pass
                    for _ in range(desplazamientos):
                        pagina.mouse.wheel(0, 2_500)
                        pagina.wait_for_timeout(espera_ms // desplazamientos)
                    return pagina.content()
                finally:
                    navegador.close()
        except PWError as e:
            raise FuenteError(f"el navegador no pudo abrir la página ({str(e).splitlines()[0][:120]})") from e
