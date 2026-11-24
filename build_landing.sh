#!/usr/bin/env bash
# Arma el sitio estático «Landings IA» (Render → servicio landings-ia).
# La app funciona entera en el navegador, así que no necesita servidor.
set -euo pipefail
rm -rf landing_dist && mkdir -p landing_dist/static
cp static/landing.html landing_dist/index.html
cp static/landing-192.png static/landing-512.png landing_dist/static/
cp static/landing-sw.js landing_dist/sw.js
# En su propio dominio la app vive en la raíz, no en /landing.
sed -e 's#"start_url": "/landing"#"start_url": "/"#' -e 's#"scope": "/landing"#"scope": "/"#' \
  static/landing.webmanifest > landing_dist/static/landing.webmanifest
echo "Sitio listo en landing_dist/"
