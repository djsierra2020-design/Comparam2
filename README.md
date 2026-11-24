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

## Mis gastos (`/gastos`)

App de gastos personales para responder «¿en qué se me va la plata?». Ábrela en
`https://<tu-app>.onrender.com/gastos` y, desde el menú del navegador, **Agregar a la pantalla
de inicio** (se instala como una app aparte, con su propio ícono).

- **Cierre del día**: cada día te pregunta cuánto tienes **en el banco** y **en efectivo**.
- **Cuadre**: compara con el cierre anterior y con lo que anotaste. Lo que falta aparece como
  **«Se te fueron sin anotar $X»**, y con un toque lo asignas a una categoría.
- **Categorías**: Consignaciones, Transporte, Comida, Póker, Niños y Otros (puedes agregar más).
  Las ganancias de póker se anotan como ingreso, así el resumen muestra el **póker neto**.
- **Resumen** por 7 días, mes, mes pasado o todo: en qué se fue la plata (incluido lo «sin
  explicar»), promedio diario y la evolución de tu dinero.
- **Recordatorio diario**: Ajustes → *Agregar recordatorio diario al calendario* (archivo .ics).
- **Privacidad**: los datos se guardan solo en tu celular (no en el servidor). Descarga una
  copia de seguridad de vez en cuando (Ajustes); también se puede exportar a Excel (CSV).
- Funciona sin internet después de abrirla una vez.

## Generador de landing pages (`/landing`)

Crea landing pages para campañas de **Google Ads** listas para subir a **WordPress**, con IA.
Ábrelo en `https://<tu-app>.onrender.com/landing`.

1. **IA**: elige **Google Gemini** (clave gratis en <https://aistudio.google.com/apikey>) o **Claude**
   (clave en <https://console.anthropic.com>, cobra por uso). La clave se guarda solo en tu navegador
   y se envía directo a Google/Anthropic. Con «Demostración sin IA» puedes probar sin clave.
2. **Negocio y campaña**: qué vendes, ciudad, público, oferta, palabras clave, objetivo y tono.
   En «Datos reales» pon años de experiencia, reseñas, licencias, etc.: la IA solo usa esas cifras.
3. **Referencias**: sube capturas de las landings que te gustan.
   - *Plantilla optimizada*: la IA escribe los textos y toma colores y tono de las referencias
     Diseños: **Servicio local / presupuesto** (azul marino y rojo, formulario en tarjeta, franja de
     confianza con íconos, galería «Nuestro trabajo»), **Reparación urgente / llamadas** (titular en
     mayúsculas con la ciudad resaltada, botón «Llama al…», tarjetas numeradas de «señales» y barra
     roja fija para llamar), además de hero con formulario, centrado y oscuro/premium.
   - *Imitar mis referencias*: la IA diseña el HTML completo copiando el estilo de las capturas.
4. **Contacto**: WhatsApp, teléfono y a dónde llegan los datos del formulario (webhook,
   formsubmit.co o, si se deja vacío, por WhatsApp).
5. **Google Ads**: ID y etiqueta de conversión (y GA4 opcional). La página registra la conversión
   al enviar el formulario o tocar WhatsApp/llamar, guarda `gclid`/UTM en campos ocultos y cambia
   el titular con la palabra clave si la URL final lleva `?kw={keyword}`.

Además genera los **textos del anuncio** (15 títulos ≤30 caracteres, 4 descripciones ≤90,
palabras clave y negativas) con CSV para Google Ads Editor. Puedes editar los textos sobre la vista
previa, pedir cambios con «Ajustar con IA» y guardar tus landings en el navegador.

**Exportar**: `⬇️ HTML` (página completa) o `⬇️ WordPress`, que descarga un *bloque HTML* para pegar
en un bloque «HTML personalizado» (o widget HTML de Elementor) y una *plantilla de página PHP* para
subir al tema.

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

### Metrocuadrado, Habi y Houm (navegador en la revisión diaria)
Estos portales cargan los anuncios con JavaScript y abrirlos con navegador en Render (512 MB) daba
error 502. Por eso la revisión diaria de GitHub instala Chromium y los abre una vez al día
(`COMPARAM_NAVEGADOR=1`, `COMPARAM_PAGINAS_NAVEGADOR=3`); sus anuncios quedan en el historial y la
app los muestra desde ahí (`comparam/sources/guardados.py`, anuncios vistos en las últimas 48 h).

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
