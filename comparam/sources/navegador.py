"""Abre páginas con un navegador real (Chromium sin ventana) para portales que cargan los
anuncios con JavaScript después de abrir la página (Habi, Houm).

Requiere el paquete ``playwright`` y el navegador instalado (``playwright install chromium``).
Está APAGADO por defecto (en Render daba error 502); se enciende con COMPARAM_NAVEGADOR=1. Solo se abre un navegador a la vez para no
agotar la memoria del servidor.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time

from .base import FuenteError

_CANDADO = threading.Lock()
# Páginas ya abiertas: {url: (momento, html)}. Abrir un navegador es lento y gasta memoria.
_CACHE: dict[str, tuple[float, str]] = {}
CACHE_SEGUNDOS = 30 * 60

# build.sh instala el navegador en <proyecto>/.playwright: se usa si existe.
_LOCAL = os.path.join(os.path.dirname(__file__), "..", "..", ".playwright")
if "PLAYWRIGHT_BROWSERS_PATH" not in os.environ and os.path.isdir(_LOCAL):
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = os.path.abspath(_LOCAL)


def disponible() -> bool:
    if os.environ.get("COMPARAM_NAVEGADOR", "0") != "1":
        return False
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        return False
    return True


def renderizar(url: str, espera_ms: int = 2_000, desplazamientos: int = 3, timeout_ms: int = 25_000) -> str:
    """Devuelve el HTML de la página después de ejecutar su JavaScript y bajar por ella (para que
    carguen los anuncios que aparecen al desplazarse).

    Cada página se abre en un proceso aparte con tiempo límite: si una página traba el navegador
    (pasó con Houm en arriendo), ese proceso se cierra a la fuerza y no bloquea a los demás."""
    if not disponible():
        raise FuenteError("navegador no disponible en el servidor")
    if (c := _CACHE.get(url)) and time.time() - c[0] < CACHE_SEGUNDOS:
        return c[1]
    with _CANDADO:
        if (c := _CACHE.get(url)) and time.time() - c[0] < CACHE_SEGUNDOS:  # otro hilo la abrió
            return c[1]
        limite = timeout_ms / 1000 + espera_ms * 3 / 1000 + 30
        cmd = [sys.executable, "-m", "comparam.sources.navegador", url, str(espera_ms), str(desplazamientos),
               str(timeout_ms)]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        try:
            salida, error = proc.communicate(timeout=limite)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)  # también los procesos del navegador
            except ProcessLookupError:
                pass
            proc.communicate()
            raise FuenteError(f"el navegador se trabó con la página (más de {limite:.0f} s)") from None
        if proc.returncode != 0:
            ultima = (error.decode("utf-8", "replace").strip().splitlines() or ["error desconocido"])[-1]
            raise FuenteError(f"el navegador no pudo abrir la página ({ultima[:120]})")
        html = salida.decode("utf-8", "replace")
        if len(_CACHE) > 40:
            _CACHE.clear()
        _CACHE[url] = (time.time(), html)
        return html


def _renderizar_aqui(url: str, espera_ms: int, desplazamientos: int, timeout_ms: int) -> str:
    """Abre la página con Chromium en este proceso (lo llama renderizar() en un proceso aparte)."""
    from playwright.sync_api import Error as PWError
    from playwright.sync_api import sync_playwright

    # Opciones para gastar poca memoria (el plan gratis de Render tiene 512 MB).
    opciones = {"headless": True, "args": [
        "--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu", "--disable-extensions",
        "--renderer-process-limit=1", "--disable-background-networking", "--mute-audio",
        "--js-flags=--max-old-space-size=192"]}
    if ruta := os.environ.get("COMPARAM_CHROMIUM"):
        opciones["executable_path"] = ruta
    with sync_playwright() as p:
        navegador = p.chromium.launch(**opciones)
        try:
            pagina = navegador.new_page(locale="es-CO", viewport={"width": 1280, "height": 900})
            pagina.set_default_timeout(timeout_ms)
            # No se descargan imágenes, videos ni fuentes: ahorra memoria y tiempo.
            pagina.route("**/*", lambda r: r.abort() if r.request.resource_type in
                         ("image", "media", "font") else r.continue_())
            pagina.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            try:
                pagina.wait_for_load_state("networkidle", timeout=espera_ms * 2)
            except PWError:
                pass
            for _ in range(desplazamientos):
                pagina.mouse.wheel(0, 2_500)
                pagina.wait_for_timeout(espera_ms // desplazamientos)
            return pagina.content()
        finally:
            navegador.close()


if __name__ == "__main__":
    _url, _espera, _desp, _timeout = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
    sys.stdout.write(_renderizar_aqui(_url, _espera, _desp, _timeout))
