# Winner Poker: torneos, mesas y caja del jugador

Sistema propio para la operación de torneos en los casinos de Winner Group. Resuelve lo que hoy falla:
**nadie sabe dónde está sentado cada jugador, el control de mesas y el balanceo**. Se monta en `/winner`
dentro de esta misma app Flask.

| Dirección | Para quién |
|---|---|
| `/winner/personal` | Panel de sala: cajero, crupier, floor, director, supervisor y administrador |
| `/winner/app` | App del jugador (celular): saldo, QR, asiento y avisos |
| `/winner/pantalla/<torneo>` | Pantalla de la sala (TV): cada jugador con su mesa y asiento, y los cambios de mesa recientes |

## Fase 1 (lista)

- **Inscripción única**: la base de datos no deja tener dos inscripciones vivas del mismo jugador en un torneo.
- **Sentado automático**: se escanea el QR del jugador (o se escribe el código) y queda en un asiento al azar de la mesa con menos jugadores.
- **Tablero en vivo**: cada mesa con sus asientos, nombre, fichas y estado. Se actualiza cada 4 s.
- **Balanceo**: avisa cuando hay 2 o más jugadores de diferencia entre mesas y lo corrige con un botón.
- **Sala de 30 mesas de 9** (270 asientos, configurable en Administración). No deja abrir más mesas que las que tiene la sala.
- **Lista de alternos**: con aforo de 300 y 270 asientos, quien llega con la sala llena queda en espera por orden de llegada,
  ve su turno en la app y entra apenas se libera un asiento («Sentar alternos»). Nadie se adelanta, ni las re-entradas.
  Mientras haya alternos esperando, el sistema no sugiere cerrar mesas.
- **Torneos simultáneos**: las mesas son las físicas de la sala (1 a 30) y se comparten. Cada torneo toma las mesas
  libres, el panel muestra un mapa de la sala (qué torneo usa cada mesa) y una mesa que cierra un torneo queda libre
  para otro. Un jugador puede estar inscrito en dos torneos a la vez.
- **Eventos de varios días**: una final (Día 2) con días clasificatorios (Día 1A, 1B… los que se necesiten). Cada día
  se juega como un torneo y al cerrarlo los que siguen vivos guardan sus fichas y clasifican. **El administrador decide en
  cada día cómo se clasifica**: nivel fijo (el día termina al acabar el nivel N), porcentaje de las entradas o número fijo
  de jugadores. Puede cambiarlo durante el día, y el sistema avisa cuándo cerrar. Un jugador que cae en un día puede jugar
  otro, y si clasifica dos veces se queda con su **stack mayor**. Todo el premio es para la final; los días
  clasificatorios no pagan. A la final se entra solo clasificando (el director puede abrir entrada directa). Al empezar el
  Día 2 se traen los clasificados con sus fichas y se sortean los asientos.
- **Satélites**: la tabla de premios puede tener **tickets** (cupos) para el torneo destino, más premios en dinero.
  El ticket vale el buy-in del destino y sirve en cualquiera de sus días clasificatorios. Si un ganador ya tenía cupo,
  **no se le devuelve dinero, pero puede vender el ticket**: el traspaso se hace en la caja, con el dueño presente,
  su documento y un código a su celular. El pago entre jugadores no pasa por la caja. El supervisor puede anular un ticket.
- **Cierre de mesas**: avisa cuándo sobra una mesa. Al cerrarla, reparte a sus jugadores en los asientos libres de las mesas con menos gente. Si no caben, no mueve a nadie.
- **Historial de movimientos**: quién se movió, de qué mesa y asiento a cuál, el motivo, la hora y quién lo hizo. El jugador recibe aviso en la app.
- **Fichas**: conteos oficiales con historial, líderes, stack promedio y diferencia entre las fichas esperadas (entradas × stack + recompras + add-ons) y las contadas.
- **Eliminación** confirmada por crupier o floor. Si el jugador puede recomprar, su asiento queda reservado unos minutos (configurable) y se libera solo al vencer.
- **Caja con saldo**: apertura en persona con documento, vinculación del celular por código, depósitos, retiros (documento + código al celular + supervisor si el monto es alto), bloqueo inmediato y correcciones. El libro de movimientos **no se puede borrar ni modificar**: lo impide la propia base de datos.
- **App del jugador** en solo lectura: saldo, movimientos, QR de inscripción, asiento asignado y avisos.
- **Premios**: tabla de premios × posiciones finales → ajustes por acuerdo (el total no cambia) → confirmación del director → acreditación en caja. Cada paso queda en la auditoría.
- **Reportes**: cuadre de caja diario con arqueo, resumen por torneo y auditoría.

## Fase 2 (programada, apagada)

La compra desde el celular (preinscripción, recompra y re-entrada con PIN y topes) ya está programada y probada,
pero está **apagada** hasta que el área jurídica de Winner la apruebe. Se enciende en Administración →
«Compras desde el celular» = 1.

## Seguridad

- Ingreso del jugador: código SMS al celular vinculado + PIN. Nunca solo con cédula o número de caja.
- Bloqueo tras 5 intentos fallidos y cierre de sesión por inactividad (configurables).
- El número de documento no se guarda en claro: solo su huella cifrada (HMAC) y los últimos 4 dígitos.
- Registro de cada ingreso y consulta de saldo, con fecha y dispositivo. Aviso al jugador por cada movimiento.
- Roles: cajero, crupier, floor, director de torneo, supervisor y administrador.

## Demo

`WINNER_DEMO=1` (así está en `render.yaml`) arranca la **práctica desde cero**: 30 jugadores con $1.000.000 de saldo
y un «Torneo de práctica» vacío, sin mesas ni inscritos. La página `/winner` trae la guía paso a paso para jugar un
torneo de 3 mesas completo (abrir mesas, inscribir, sentar por QR, eliminar, balancear, cerrar mesa y pagar premios).
`WINNER_DEMO=completo` arranca la sala de 30 mesas con tres torneos simultáneos: un torneo diario sencillo, un
satélite y el Main Event de varios días. En la demo, el admin puede volver a empezar con cualquiera de los dos desde
Administración → «Demo: volver a empezar».

En el plan gratis de Render el disco se borra con cada despliegue, así que la demo vuelve a empezar desde cero.

## Producción: falta definir

| Variable | Para qué |
|---|---|
| `WINNER_SECRET` | Clave larga y aleatoria (protege la huella del documento) |
| `WINNER_SMS_WEBHOOK` | URL del proveedor de SMS (recibe `{celular, texto}`) |
| `WINNER_DB` | Ruta de la base en un **disco persistente** con cifrado |
| (quitar) `WINNER_DEMO` | Para que no se carguen datos ficticios ni se muestren los SMS |

También hay que servir la app solo por HTTPS.

**Reglas confirmadas con Winner:** clasificar dos veces → se conserva el stack mayor; cupo repetido de satélite → no se
devuelve dinero, se puede vender (traspaso); todo el premio va a la final; la forma de clasificar la decide el
administrador en cada día.

**Pendiente en la app:** huella digital (WebAuthn) como alternativa al PIN, notificaciones push (hoy los avisos
aparecen al abrir la app), lista de espera de cash y mover a jugadores al asiento equivalente según el botón al balancear
(hoy se elige al azar).

## Pruebas

```bash
python -m pytest tests/test_winner.py
```

Incluye el escenario real: sala de 30 mesas de 9 con 300 inscritos (270 sentados + 30 alternos), jugado hasta el
ganador. Después de cada eliminación verifica que los alternos entren en orden de llegada, que ninguna mesa tenga más de
un jugador de diferencia con otra y que estén abiertas solo las mesas necesarias.

**Prueba de carga** (300 celulares con la app abierta + 3 pantallas de floor, gunicorn con 1 proceso y 4 hilos):
0 errores, el celular responde en 3 ms (mediana) y el tablero de 34 mesas en 16 ms. Para el torneo real se recomienda
como mínimo un servidor pago (no el plan gratis de Render, que tiene muy poca CPU y se duerme).
