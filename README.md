# Comparam — apartamento más económico por m² en Bogotá

Escribes un barrio de Bogotá y la app te devuelve la lista de apartamentos
ordenada de **menor a mayor precio por metro cuadrado**. Por defecto solo muestra
anuncios **de dueño directo (sin inmobiliaria)**, junto con su **teléfono**
y un botón de WhatsApp.

## Usarla desde el celular (publicarla gratis en Render)

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/djsierra2020-design/Comparam2)

1. Abre el botón de arriba e inicia sesión en Render con tu cuenta de GitHub.
2. Pulsa **Deploy Blueprint** y espera unos minutos.
3. Render te da una dirección tipo `https://comparam-xxxx.onrender.com`: ábrela en el celular.

**Actualización automática:** el archivo `.github/workflows/deploy-render.yml` corre las pruebas
y avisa a Render cada vez que cambia `main`. Requiere guardar el *Deploy Hook* de Render
(Settings → Deploy Hook) como secreto `RENDER_DEPLOY_HOOK` en GitHub
(Settings → Secrets and variables → Actions).

El plan gratis se «duerme» si nadie lo usa; la primera búsqueda después puede tardar ~1 minuto.

## Torneo de ajedrez (sistema suizo)

Abre `/ajedrez` (o directamente `static/ajedrez.html` en el navegador, sin servidor).
Al empezar pregunta si el torneo es **individual** o **por equipos de 4**; luego se inscriben
jugadores/equipos, se generan las rondas por sistema suizo (sin repetir rivales y alternando colores)
y se anotan los resultados. Pestañas: inscritos, **Programación** (rondas) y **Clasificación**.

- Individual: 1 punto por victoria, ½ tablas. Desempates: Buchholz corte 1, Buchholz, Sonneborn-Berger.
- Equipos: ganar el encuentro da **1 punto** (empate ½). El primer desempate es la **suma de los resultados
  de cada partida** (puntos de partidas de los 4 tableros); después Buchholz y Sonneborn-Berger.

El número de rondas no se pregunta: se calcula según el reglamento del sistema suizo
(log₂ de los participantes redondeado hacia arriba, más una ronda para definir los puestos cuando hay más de 4;
como máximo participantes − 1 y 11 rondas) y queda fijo al generar la ronda 1.

Se puede inscribir jugadores/equipos antes de la ronda 1 y, como tarde, para la ronda 2
(empiezan con 0 puntos por no jugar la ronda 1). Al generar la ronda 2 la inscripción se cierra.

Los datos se guardan en el navegador y se pueden exportar/importar en `.json`.

## Instalación

```bash
pip install -r requirements.txt
```

## Uso

### Web
```bash
python app.py          # abre http://localhost:5000
```
Escribe el barrio (con autocompletado), elige venta o arriendo y busca.
Filtros: incluir inmobiliarias, solo con teléfono, área mínima, precio máximo y fuentes.

API JSON: `GET /api/buscar?barrio=Cedritos&operacion=venta&con_telefono=1`

### Terminal
```bash
python -m comparam.cli Cedritos
python -m comparam.cli "Chicó Navarra" --arriendo --con-telefono --exportar resultados.csv
python -m comparam.cli Cedritos --fuente ejemplo     # datos de ejemplo, sin internet
```

## Cómo funciona

1. **Fuentes**: Finca Raíz, Metrocuadrado, Ciencuadras, Habi y Houm (estos dos son empresas: sus anuncios cuentan para el precio del m² del barrio pero no como dueño directo) (`comparam/sources/portales.py`),
   más cualquier CSV propio (`--csv archivo.csv` o variable `COMPARAM_CSV`).
2. **Extracción**: los portales incrustan los resultados como JSON en el HTML
   (`__NEXT_DATA__`, JSON-LD…). El extractor genérico (`comparam/sources/extractor.py`)
   recorre ese JSON y reconoce cualquier objeto con precio y área, así que resiste
   cambios menores en la estructura de los portales.
3. **Dueño directo vs. inmobiliaria**: usa el tipo de anunciante que publica el portal
   y, si no existe, el texto del anuncio («dueño directo», «sin intermediarios»,
   «abstenerse inmobiliarias», «SAS», «inmobiliaria»…). Ver `comparam/parsing.py`.
4. **Teléfonos**: se toman de los campos de contacto del anuncio, del texto de la
   descripción (celulares `3xx xxx xxxx` y fijos `601 xxx xxxx`) y, para anuncios de
   dueño directo sin teléfono, de los enlaces `tel:`/WhatsApp de la ficha del anuncio.
5. Se eliminan duplicados entre portales y se ordena por precio/m².
6. **Precio de oferta del m² de la zona**: mediana del precio/m² de todos los anuncios encontrados
   en el barrio (dueños directos e inmobiliarias, antes de los filtros). Cada apartamento
   muestra cuánto está por debajo o por encima de ese valor. La página indica con cuántos
   anuncios se calculó y permite ver la lista. No es el avalúo catastral.

### Buscar en toda Bogotá
Botón **«Buscar en toda Bogotá»** (o `python -m comparam.cli` sin barrio). La app lee varias
páginas de cada portal para toda la ciudad, agrupa los anuncios por barrio, calcula la mediana
del precio del m² de cada barrio (con al menos 3 anuncios) y ordena los de dueño directo por
**cuánto más baratos están frente a su propio barrio**. Solo abre la ficha de los 15 mejores
para buscar teléfonos. Los resultados se guardan 15 minutos para que cambiar filtros sea rápido.

Páginas por portal: variable `COMPARAM_PAGINAS_CIUDAD` (por defecto 5). Más páginas = más
anuncios de dueño directo, pero la búsqueda tarda más.

### Habi y Houm (navegador)
Estos portales cargan los anuncios con JavaScript, así que se abren con un navegador sin ventana
(Playwright + Chromium). En Render: **Build Command** `bash build.sh` y **Start Command**
`gunicorn app:app --bind 0.0.0.0:$PORT --timeout 300 --workers 1 --threads 4`. Está **apagado por
defecto** (en Render daba error 502); se enciende con `COMPARAM_NAVEGADOR=1`.

### Envío diario de anuncios nuevos
Cada mañana (6:00 a.m. Bogotá) GitHub Actions corre `python -m comparam.diario`
(`.github/workflows/diario.yml`): revisa los barrios de `data/diario_config.json`, guarda el
historial en `data/historial/anuncios.json` (primera y última vez visto, cambios de precio, fecha de
publicación si el portal la da) y el resumen del día en `data/diario/AAAA-MM-DD.json`. Luego
actualiza Render. La página **/nuevos** muestra los dueños directos nuevos con el mensaje listo
para WhatsApp. El primer día solo se guarda el punto de partida. No se guardan nombres ni
teléfonos (el repositorio es público).

### Si un portal cambia su URL
Las URL de búsqueda se pueden cambiar sin tocar código:
```bash
export COMPARAM_URL_FINCARAIZ="https://www.fincaraiz.com.co/{operacion}/apartamentos/{barrio}/bogota/pagina{pagina}"
export COMPARAM_URL_CIUDAD_FINCARAIZ="https://www.fincaraiz.com.co/{operacion}/apartamentos/bogota/pagina{pagina}"
```

### Importar tus propios anuncios (CSV)
Columnas reconocidas: `titulo, barrio, operacion, precio, area_m2, telefono,
publicado_por, habitaciones, banos, url, descripcion` (ver `data/ejemplo.csv`).

## Limitaciones
- Algunos portales ocultan el teléfono tras un botón «Ver teléfono» que exige
  iniciar sesión; en esos casos la app muestra «Teléfono no publicado — ver anuncio».
- Los portales pueden bloquear consultas automáticas o cambiar su estructura; cada
  fuente falla de forma independiente y el error se muestra sin detener la búsqueda.
- Usa la herramienta de forma razonable (pocas páginas, con pausas) y respeta los
  términos de uso de cada portal. Los teléfonos son datos personales: úsalos solo para
  contactar sobre el inmueble.
- `data/ejemplo.csv` contiene datos **ficticios** solo para probar la app.

## Pruebas
```bash
python -m pytest
```
