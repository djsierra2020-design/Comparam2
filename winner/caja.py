"""Caja con saldo del jugador: libro de movimientos inmodificable.

El dinero está físicamente en la caja de Winner. La app solo consulta el saldo y descuenta
compras de torneo ya autorizadas por el jugador; no recibe ni envía dinero.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from . import seguridad as seg
from .db import BaseDatos, ErrorNegocio, ahora, parse_fecha

BOGOTA = timezone(timedelta(hours=-5))
TIPOS_COMPRA = ("inscripcion", "recompra", "reentrada", "addon")
NOMBRES_TIPO = {
    "deposito": "Depósito en caja", "retiro": "Retiro en caja", "inscripcion": "Inscripción",
    "recompra": "Recompra", "reentrada": "Re-entrada", "addon": "Add-on", "premio": "Premio",
    "correccion": "Corrección",
}


def pesos(n: int) -> str:
    return ("-" if n < 0 else "") + "$" + f"{abs(int(n)):,}".replace(",", ".")


def dia_bogota(fecha_utc: str | None = None) -> str:
    f = parse_fecha(fecha_utc) if fecha_utc else datetime.now(timezone.utc)
    return f.astimezone(BOGOTA).strftime("%Y-%m-%d")


def rango_dia_utc(dia: str) -> tuple[str, str]:
    """Inicio y fin (UTC, texto comparable) de un día calendario de Bogotá."""
    inicio = datetime.strptime(dia, "%Y-%m-%d").replace(tzinfo=BOGOTA).astimezone(timezone.utc)
    fin = inicio + timedelta(days=1)
    fmt = "%Y-%m-%dT%H:%M:%S.%fZ"
    return inicio.strftime(fmt), fin.strftime(fmt)


def monto_valido(monto) -> int:
    try:
        m = int(str(monto).replace(".", "").replace(",", "").replace("$", "").strip())
    except ValueError:
        raise ErrorNegocio("Monto inválido") from None
    if m <= 0:
        raise ErrorNegocio("El monto debe ser mayor que cero")
    return m


def saldo(db: BaseDatos, jugador_id: int) -> int:
    return db.valor("SELECT COALESCE(SUM(monto), 0) FROM movimientos WHERE jugador_id = ?", (jugador_id,))


def jugador(db: BaseDatos, jugador_id: int) -> dict:
    j = db.uno("SELECT * FROM jugadores WHERE id = ?", (jugador_id,))
    if not j:
        raise ErrorNegocio("Jugador no encontrado", 404)
    return j


def publico(j: dict) -> dict:
    """Datos del jugador que se pueden mostrar (sin hashes)."""
    return {"id": j["id"], "numero_caja": j["numero_caja"], "nombre": j["nombre"],
            "documento": "****" + j["doc_ultimos"], "celular": seg.enmascarar(j["celular"]),
            "celular_verificado": bool(j["celular_verificado"]), "tiene_pin": j["pin_hash"] is not None,
            "estado": j["estado"], "motivo_bloqueo": j["motivo_bloqueo"], "tope_diario": j["tope_diario"]}


def registrar_movimiento(db, jugador_id, tipo, monto, *, origen, personal_id=None, medio="saldo",
                         torneo_id=None, descripcion=None, corrige_id=None, aprobado_por=None,
                         permitir_negativo=False) -> int:
    """Única forma de cambiar un saldo. Debe llamarse dentro de una transacción."""
    actual = saldo(db, jugador_id)
    nuevo = actual + monto
    if nuevo < 0 and not permitir_negativo:
        raise ErrorNegocio(f"Saldo insuficiente: disponible {pesos(actual)}, se requieren {pesos(-monto)}")
    cur = db.conexion().execute(
        "INSERT INTO movimientos (jugador_id, tipo, monto, saldo_despues, medio, torneo_id, corrige_id, "
        "descripcion, origen, personal_id, aprobado_por, fecha) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (jugador_id, tipo, monto, nuevo, medio, torneo_id, corrige_id, descripcion, origen,
         personal_id, aprobado_por, ahora()))
    signo = "+" if monto > 0 else "-"
    db.notificar(jugador_id, "movimiento", f"{NOMBRES_TIPO[tipo]}: {signo}{pesos(abs(monto))}",
                 f"{descripcion or NOMBRES_TIPO[tipo]}. Saldo: {pesos(nuevo)}.")
    return cur.lastrowid


def _siguiente_numero_caja(db) -> str:
    n = db.valor("SELECT COALESCE(MAX(id), 0) + 1 FROM jugadores")
    return f"W{n:06d}"


def abrir_caja(db, personal_id, nombre, documento, celular=None, documento_verificado=False) -> dict:
    """Apertura en persona en el casino. El celular se vincula con un código enviado a ese número."""
    if not documento_verificado:
        raise ErrorNegocio("Debe verificar el documento original del jugador antes de abrir la caja")
    nombre = (nombre or "").strip()
    if len(nombre) < 3:
        raise ErrorNegocio("Escriba el nombre completo")
    doc_hash = seg.hash_documento(documento)
    cel = seg.normalizar_celular(celular) if celular else None
    with db.transaccion() as c:
        if c.execute("SELECT 1 FROM jugadores WHERE doc_hash = ?", (doc_hash,)).fetchone():
            raise ErrorNegocio("Ya existe una caja con ese documento")
        if cel and c.execute("SELECT 1 FROM jugadores WHERE celular = ?", (cel,)).fetchone():
            raise ErrorNegocio("Ese celular ya está vinculado a otra caja")
        digitos = "".join(ch for ch in documento if ch.isdigit())
        cur = c.execute(
            "INSERT INTO jugadores (numero_caja, nombre, doc_hash, doc_ultimos, celular, creado_en, creado_por) "
            "VALUES (?,?,?,?,?,?,?)",
            (_siguiente_numero_caja(db), nombre, doc_hash, digitos[-4:], cel, ahora(), personal_id))
        jid = cur.lastrowid
        db.auditar(personal_id, "abrir_caja", "jugador", jid, "documento verificado en persona")
        j = jugador(db, jid)
        codigo = seg.generar_codigo(db, j, "vinculacion") if cel else None
    return {"jugador": publico(j), "codigo_enviado": bool(codigo), "_codigo": codigo}


def enviar_codigo_vinculacion(db, personal_id, jugador_id, celular=None) -> str:
    """Vincula (o cambia, p. ej. por pérdida) el celular. Solo en persona en la caja."""
    with db.transaccion() as c:
        j = jugador(db, jugador_id)
        if celular:
            cel = seg.normalizar_celular(celular)
            otro = c.execute("SELECT id FROM jugadores WHERE celular = ? AND id != ?", (cel, jugador_id)).fetchone()
            if otro:
                raise ErrorNegocio("Ese celular ya está vinculado a otra caja")
            if cel != j["celular"]:
                c.execute("UPDATE jugadores SET celular = ?, celular_verificado = 0, pin_hash = NULL WHERE id = ?",
                          (cel, jugador_id))
                seg.cerrar_sesiones_jugador(db, jugador_id)
                db.auditar(personal_id, "cambiar_celular", "jugador", jugador_id, seg.enmascarar(cel))
            j = jugador(db, jugador_id)
        return seg.generar_codigo(db, j, "vinculacion")


def vincular_celular(db, personal_id, jugador_id, codigo) -> None:
    with db.transaccion() as c:
        seg.verificar_codigo(db, jugador_id, "vinculacion", codigo)
        c.execute("UPDATE jugadores SET celular_verificado = 1 WHERE id = ?", (jugador_id,))
        db.auditar(personal_id, "vincular_celular", "jugador", jugador_id)
        db.notificar(jugador_id, "seguridad", "Celular vinculado",
                     "Este celular quedó vinculado a su caja Winner. Ingrese a la app y cree su PIN.")


def buscar_jugadores(db, texto) -> list[dict]:
    texto = (texto or "").strip()
    if not texto:
        return []
    filas = []
    digitos = "".join(ch for ch in texto if ch.isdigit())
    if len(digitos) >= 5:
        try:
            filas += db.todos("SELECT * FROM jugadores WHERE doc_hash = ?", (seg.hash_documento(digitos),))
        except ErrorNegocio:
            pass
        filas += db.todos("SELECT * FROM jugadores WHERE celular LIKE ?", (f"%{digitos[-10:]}%",))
    filas += db.todos("SELECT * FROM jugadores WHERE nombre LIKE ? OR numero_caja LIKE ? LIMIT 20",
                      (f"%{texto}%", f"%{texto.upper()}%"))
    vistos, salida = set(), []
    for j in filas:
        if j["id"] not in vistos:
            vistos.add(j["id"])
            salida.append({**publico(j), "saldo": saldo(db, j["id"])})
    return salida


def depositar(db, personal_id, jugador_id, monto, descripcion=None) -> int:
    m = monto_valido(monto)
    with db.transaccion():
        j = jugador(db, jugador_id)
        if j["estado"] != "activo":
            raise ErrorNegocio("La cuenta está bloqueada")
        mid = registrar_movimiento(db, jugador_id, "deposito", m, origen="caja", personal_id=personal_id,
                                   medio="efectivo", descripcion=descripcion or "Depósito en efectivo en caja")
        db.auditar(personal_id, "deposito", "movimiento", mid, pesos(m))
        return mid


def solicitar_codigo_retiro(db, personal_id, jugador_id) -> str:
    with db.transaccion():
        j = jugador(db, jugador_id)
        if j["estado"] != "activo":
            raise ErrorNegocio("La cuenta está bloqueada")
        if not j["celular_verificado"]:
            raise ErrorNegocio("La cuenta no tiene celular vinculado; vincúlelo antes de retirar")
        db.auditar(personal_id, "codigo_retiro", "jugador", jugador_id)
        return seg.generar_codigo(db, j, "retiro")


def retirar(db, personal_id, jugador_id, monto, documento, codigo,
            supervisor_usuario=None, supervisor_clave=None) -> int:
    """Retiro en persona: documento del dueño + código al celular vinculado (+ supervisor si es alto)."""
    m = monto_valido(monto)
    j = jugador(db, jugador_id)
    if j["estado"] != "activo":
        raise ErrorNegocio("La cuenta está bloqueada")
    if seg.hash_documento(documento) != j["doc_hash"]:
        with db.transaccion():
            db.auditar(personal_id, "retiro_rechazado", "jugador", jugador_id, "documento no coincide")
        raise ErrorNegocio("El documento no corresponde al dueño de la cuenta")
    aprobado_por = None
    if m > db.ajuste("tope_retiro_sin_supervisor"):
        if not supervisor_usuario:
            raise ErrorNegocio(f"Retiros mayores a {pesos(db.ajuste('tope_retiro_sin_supervisor'))} "
                               "requieren aprobación de un supervisor", 403)
        aprobado_por = seg.verificar_supervisor(db, supervisor_usuario, supervisor_clave)["id"]
    with db.transaccion():
        seg.verificar_codigo(db, jugador_id, "retiro", codigo)
        mid = registrar_movimiento(db, jugador_id, "retiro", -m, origen="caja", personal_id=personal_id,
                                   medio="efectivo", aprobado_por=aprobado_por,
                                   descripcion="Retiro en efectivo en caja")
        db.auditar(personal_id, "retiro", "movimiento", mid,
                   pesos(m) + (f" aprobado por supervisor #{aprobado_por}" if aprobado_por else ""))
        return mid


def corregir(db, supervisor_id, movimiento_id, motivo) -> int:
    """Corrige un movimiento con otro de signo contrario. El original queda intacto."""
    motivo = (motivo or "").strip()
    if len(motivo) < 5:
        raise ErrorNegocio("Explique el motivo de la corrección")
    with db.transaccion():
        mov = db.uno("SELECT * FROM movimientos WHERE id = ?", (movimiento_id,))
        if not mov:
            raise ErrorNegocio("Movimiento no encontrado", 404)
        if mov["tipo"] == "correccion":
            raise ErrorNegocio("No se puede corregir una corrección; registre un movimiento nuevo")
        if db.valor("SELECT 1 FROM movimientos WHERE corrige_id = ?", (movimiento_id,)):
            raise ErrorNegocio("Ese movimiento ya fue corregido")
        mid = registrar_movimiento(db, mov["jugador_id"], "correccion", -mov["monto"], origen="caja",
                                   personal_id=supervisor_id, aprobado_por=supervisor_id,
                                   torneo_id=mov["torneo_id"], corrige_id=movimiento_id,
                                   medio=mov["medio"], descripcion=f"Corrige movimiento #{movimiento_id}: {motivo}",
                                   permitir_negativo=True)
        db.auditar(supervisor_id, "correccion", "movimiento", mid, f"corrige #{movimiento_id}: {motivo}")
        return mid


def bloquear(db, personal_id, jugador_id, motivo) -> None:
    with db.transaccion() as c:
        jugador(db, jugador_id)
        c.execute("UPDATE jugadores SET estado = 'bloqueado', motivo_bloqueo = ? WHERE id = ?",
                  ((motivo or "Bloqueo solicitado en caja").strip(), jugador_id))
        seg.cerrar_sesiones_jugador(db, jugador_id)
        db.auditar(personal_id, "bloquear", "jugador", jugador_id, motivo)


def desbloquear(db, personal_id, jugador_id) -> None:
    with db.transaccion() as c:
        jugador(db, jugador_id)
        c.execute("UPDATE jugadores SET estado = 'activo', motivo_bloqueo = NULL, intentos_fallidos = 0, "
                  "bloqueado_hasta = NULL WHERE id = ?", (jugador_id,))
        db.auditar(personal_id, "desbloquear", "jugador", jugador_id)


def fijar_tope(db, personal_id, jugador_id, tope) -> None:
    with db.transaccion() as c:
        valor = monto_valido(tope) if tope not in (None, "") else None
        c.execute("UPDATE jugadores SET tope_diario = ? WHERE id = ?", (valor, jugador_id))
        db.auditar(personal_id, "tope_diario", "jugador", jugador_id, str(valor))


def compras_app_del_dia(db, jugador_id, dia=None) -> int:
    inicio, fin = rango_dia_utc(dia or dia_bogota())
    marcadores = ",".join("?" * len(TIPOS_COMPRA))
    return -db.valor(
        f"SELECT COALESCE(SUM(monto), 0) FROM movimientos WHERE jugador_id = ? AND origen = 'app' "
        f"AND tipo IN ({marcadores}) AND fecha >= ? AND fecha < ?", (jugador_id, *TIPOS_COMPRA, inicio, fin))


def compras_app_del_torneo(db, jugador_id, torneo_id) -> int:
    marcadores = ",".join("?" * len(TIPOS_COMPRA))
    return -db.valor(
        f"SELECT COALESCE(SUM(monto), 0) FROM movimientos WHERE jugador_id = ? AND origen = 'app' "
        f"AND tipo IN ({marcadores}) AND torneo_id = ?", (jugador_id, *TIPOS_COMPRA, torneo_id))


def verificar_topes_app(db, jugador_id, torneo, monto) -> None:
    j = jugador(db, jugador_id)
    tope = j["tope_diario"] or db.ajuste("tope_compra_diario")
    hoy = compras_app_del_dia(db, jugador_id)
    if hoy + monto > tope:
        raise ErrorNegocio(f"Supera su tope diario de compras desde el celular ({pesos(tope)}; "
                           f"hoy lleva {pesos(hoy)}). Acérquese a la caja.", 403)
    if torneo.get("tope_compra"):
        en_torneo = compras_app_del_torneo(db, jugador_id, torneo["id"])
        if en_torneo + monto > torneo["tope_compra"]:
            raise ErrorNegocio(f"Supera el tope de compras por celular de este torneo "
                               f"({pesos(torneo['tope_compra'])}). Acérquese a la caja.", 403)


def movimientos(db, jugador_id, limite=100) -> list[dict]:
    return db.todos("SELECT m.*, t.nombre AS torneo FROM movimientos m LEFT JOIN torneos t ON t.id = m.torneo_id "
                    "WHERE m.jugador_id = ? ORDER BY m.id DESC LIMIT ?", (jugador_id, limite))
