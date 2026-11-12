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

Con `WINNER_DEMO=1` (así está en `render.yaml`) la app arranca con datos ficticios: 40 jugadores y un torneo en curso.
- Personal: `admin`, `director`, `floor`, `crupier`, `cajero` y `supervisor`, todos con la clave `winner2026`.
- Jugador: celulares `3000000000` a `3000000039`. El código SMS aparece en la misma pantalla.

En el plan gratis de Render el disco se borra con cada despliegue, así que la demo vuelve a empezar desde cero.

## Producción: falta definir

| Variable | Para qué |
|---|---|
| `WINNER_SECRET` | Clave larga y aleatoria (protege la huella del documento) |
| `WINNER_SMS_WEBHOOK` | URL del proveedor de SMS (recibe `{celular, texto}`) |
| `WINNER_DB` | Ruta de la base en un **disco persistente** con cifrado |
| (quitar) `WINNER_DEMO` | Para que no se carguen datos ficticios ni se muestren los SMS |

También hay que servir la app solo por HTTPS.

**Pendiente en la app:** huella digital (WebAuthn) como alternativa al PIN, notificaciones push (hoy los avisos
aparecen al abrir la app), lista de espera de cash y mover a jugadores al asiento equivalente según el botón al balancear
(hoy se elige al azar).

## Pruebas

```bash
python -m pytest tests/test_winner.py
```

Incluye un torneo completo de 300 jugadores (el aforo máximo de Winner) hasta el ganador. Después de cada eliminación
verifica que ninguna mesa tenga más de un jugador de diferencia con otra y que estén abiertas solo las mesas necesarias.

**Prueba de carga** (300 celulares con la app abierta + 3 pantallas de floor, gunicorn con 1 proceso y 4 hilos):
0 errores, el celular responde en 3 ms (mediana) y el tablero de 34 mesas en 16 ms. Para el torneo real se recomienda
como mínimo un servidor pago (no el plan gratis de Render, que tiene muy poca CPU y se duerme).
