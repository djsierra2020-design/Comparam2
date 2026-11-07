#!/usr/bin/env bash
# Instalación en Render (Build Command: bash build.sh).
# Instala las dependencias y el navegador sin ventana que se usa para Habi y Houm.
set -euo pipefail
pip install -r requirements.txt
# El navegador se guarda dentro del proyecto para que esté disponible al ejecutar la app.
PLAYWRIGHT_BROWSERS_PATH="$(pwd)/.playwright" python -m playwright install --only-shell chromium \
  || echo "AVISO: no se pudo instalar el navegador; Habi y Houm quedarán sin anuncios."
