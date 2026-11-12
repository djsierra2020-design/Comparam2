"""Claves, PIN, códigos al celular, sesiones, bloqueos y registro de accesos."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import secrets
from datetime import timedelta

from .db import BaseDatos, ErrorNegocio, ahora, parse_fecha

log = logging.getLogger("winner")

ROLES_PERSONAL = ("cajero", "crupier", "floor", "director", "supervisor", "admin")

# Qué puede hacer cada rol. El admin puede todo.
PERMISOS = {
    "caja": {"cajero", "supervisor", "admin"},
    "aprobar": {"supervisor", "admin"},
    "mesas": {"crupier", "floor", "director", "admin"},
    "floor": {"floor", "director", "admin"},
    "director": {"director", "admin"},
    "cash": {"floor", "director", "admin", "cajero"},
    "reportes": {"cajero", "director", "supervisor", "admin"},
    "auditoria": {"supervisor", "admin"},
    "admin": {"admin"},
}


def demo() -> bool:
    return os.environ.get("WINNER_DEMO") == "1"


def _secreto() -> bytes:
    s = os.environ.get("WINNER_SECRET")
    if not s:
        if not demo():
            log.warning("WINNER_SECRET no está definido: se usa una clave de desarrollo")
        s = "winner-desarrollo-no-usar-en-produccion"
    return s.encode()


def hash_clave(clave: str) -> str:
    sal = secrets.token_bytes(16)
    h = hashlib.pbkdf2_hmac("sha256", clave.encode(), sal, 200_000)
    return f"pbkdf2${sal.hex()}${h.hex()}"


def verificar_clave(clave: str, guardado: str | None) -> bool:
    if not guardado:
        return False
    try:
        _, sal, h = guardado.split("$")
    except ValueError:
        return False
    calc = hashlib.pbkdf2_hmac("sha256", clave.encode(), bytes.fromhex(sal), 200_000)
    return hmac.compare_digest(calc.hex(), h)


def hash_documento(documento: str) -> str:
    """El número de documento no se guarda en claro: solo su HMAC (para buscar) y los últimos 4."""
    limpio = re.sub(r"\D", "", documento)
    if len(limpio) < 5:
        raise ErrorNegocio("Número de documento inválido")
    return hmac.new(_secreto(), limpio.encode(), hashlib.sha256).hexdigest()


def normalizar_celular(celular: str) -> str:
    limpio = re.sub(r"\D", "", celular or "")
    if limpio.startswith("57") and len(limpio) == 12:
        limpio = limpio[2:]
    if not re.fullmatch(r"3\d{9}", limpio):
        raise ErrorNegocio("Celular inválido (debe ser un celular colombiano de 10 dígitos)")
    return limpio


def validar_pin(pin: str) -> str:
    if not re.fullmatch(r"\d{4,6}", pin or ""):
        raise ErrorNegocio("El PIN debe tener entre 4 y 6 dígitos")
    if len(set(pin)) == 1 or pin in "0123456789" or pin in "9876543210":
        raise ErrorNegocio("El PIN es demasiado fácil (evite 1111 o 1234)")
    return pin


def enmascarar(celular: str | None) -> str:
    return f"*** *** {celular[-4:]}" if celular else ""


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# --- SMS ---------------------------------------------------------------------------------

def enviar_sms(db: BaseDatos, celular: str, texto: str) -> None:
    """Envía un SMS por el webhook configurado (WINNER_SMS_WEBHOOK). En modo demo solo lo guarda."""
    if demo():
        db.conexion().execute("INSERT INTO sms_enviados (celular, texto, fecha) VALUES (?,?,?)",
                              (celular, texto, ahora()))
        return
    url = os.environ.get("WINNER_SMS_WEBHOOK")
    if not url:
        log.error("No hay proveedor de SMS configurado (WINNER_SMS_WEBHOOK); no se envió a %s",
                  enmascarar(celular))
        return
    import requests
    try:
        requests.post(url, json={"celular": "+57" + celular, "texto": texto}, timeout=10)
    except requests.RequestException:
        log.exception("Falló el envío de SMS a %s", enmascarar(celular))


# --- Accesos y bloqueos -------------------------------------------------------------------

def registrar_acceso(db, tipo, sujeto_id, evento, detalle=None, dispositivo=None, ip=None):
    db.conexion().execute(
        "INSERT INTO accesos (tipo, sujeto_id, evento, detalle, dispositivo, ip, fecha) "
        "VALUES (?,?,?,?,?,?,?)", (tipo, sujeto_id, evento, detalle, (dispositivo or "")[:200], ip, ahora()))


def _verificar_no_bloqueado(fila: dict) -> None:
    hasta = fila.get("bloqueado_hasta")
    if hasta and parse_fecha(hasta) > parse_fecha(ahora()):
        raise ErrorNegocio("Demasiados intentos fallidos. Intente de nuevo más tarde.", 429, confirmar=True)


def _fallo(db, tabla, fila_id) -> None:
    c = db.conexion()
    c.execute(f"UPDATE {tabla} SET intentos_fallidos = intentos_fallidos + 1 WHERE id = ?", (fila_id,))
    n = c.execute(f"SELECT intentos_fallidos FROM {tabla} WHERE id = ?", (fila_id,)).fetchone()[0]
    if n >= db.ajuste("intentos_maximos"):
        hasta = parse_fecha(ahora()) + timedelta(minutes=db.ajuste("minutos_bloqueo"))
        c.execute(f"UPDATE {tabla} SET bloqueado_hasta = ?, intentos_fallidos = 0 WHERE id = ?",
                  (hasta.strftime("%Y-%m-%dT%H:%M:%S.%fZ"), fila_id))


def _exito(db, tabla, fila_id) -> None:
    db.conexion().execute(
        f"UPDATE {tabla} SET intentos_fallidos = 0, bloqueado_hasta = NULL WHERE id = ?", (fila_id,))


# --- Códigos al celular --------------------------------------------------------------------

def generar_codigo(db: BaseDatos, jugador: dict, proposito: str) -> str:
    if not jugador.get("celular"):
        raise ErrorNegocio("La cuenta no tiene celular vinculado")
    codigo = f"{secrets.randbelow(1_000_000):06d}"
    vence = parse_fecha(ahora()) + timedelta(minutes=db.ajuste("minutos_codigo"))
    c = db.conexion()
    c.execute("UPDATE codigos SET usado = 1 WHERE jugador_id = ? AND proposito = ? AND usado = 0",
              (jugador["id"], proposito))
    c.execute("INSERT INTO codigos (jugador_id, proposito, codigo_hash, vence_en, creado_en) "
              "VALUES (?,?,?,?,?)", (jugador["id"], proposito, _token_hash(codigo),
                                     vence.strftime("%Y-%m-%dT%H:%M:%S.%fZ"), ahora()))
    textos = {
        "ingreso": "Winner Poker: su código de ingreso es {c}. No lo comparta con nadie.",
        "retiro": "Winner Poker: código para RETIRO en caja: {c}. Si usted no está en la caja, "
                  "no lo entregue y avise al casino.",
        "vinculacion": "Winner Poker: código para vincular este celular a su caja: {c}.",
    }
    enviar_sms(db, jugador["celular"], textos[proposito].format(c=codigo))
    return codigo


def verificar_codigo(db: BaseDatos, jugador_id: int, proposito: str, codigo: str) -> None:
    c = db.conexion()
    fila = c.execute("SELECT * FROM codigos WHERE jugador_id = ? AND proposito = ? AND usado = 0 "
                     "ORDER BY id DESC LIMIT 1", (jugador_id, proposito)).fetchone()
    if not fila or parse_fecha(fila["vence_en"]) < parse_fecha(ahora()):
        raise ErrorNegocio("El código venció o no existe. Solicite uno nuevo.")
    if not hmac.compare_digest(fila["codigo_hash"], _token_hash((codigo or "").strip())):
        intentos = fila["intentos"] + 1
        c.execute("UPDATE codigos SET intentos = ?, usado = ? WHERE id = ?",
                  (intentos, 1 if intentos >= 3 else 0, fila["id"]))
        raise ErrorNegocio("Código incorrecto", confirmar=True)
    c.execute("UPDATE codigos SET usado = 1 WHERE id = ?", (fila["id"],))


# --- Personal ------------------------------------------------------------------------------

def crear_personal(db, usuario, nombre, rol, clave, creado_por=None) -> int:
    if rol not in ROLES_PERSONAL:
        raise ErrorNegocio("Rol inválido")
    if len(clave) < 8:
        raise ErrorNegocio("La clave del personal debe tener al menos 8 caracteres")
    usuario = usuario.strip().lower()
    with db.transaccion() as c:
        if c.execute("SELECT 1 FROM personal WHERE usuario = ?", (usuario,)).fetchone():
            raise ErrorNegocio("Ese usuario ya existe")
        cur = c.execute("INSERT INTO personal (usuario, nombre, rol, clave_hash, creado_en) "
                        "VALUES (?,?,?,?,?)", (usuario, nombre.strip(), rol, hash_clave(clave), ahora()))
        db.auditar(creado_por, "crear_personal", "personal", cur.lastrowid, f"{usuario} ({rol})")
        return cur.lastrowid


def autenticar_personal(db, usuario, clave, dispositivo=None, ip=None) -> dict:
    with db.transaccion():
        p = db.uno("SELECT * FROM personal WHERE usuario = ?", ((usuario or "").strip().lower(),))
        if not p or not p["activo"]:
            registrar_acceso(db, "personal", None, "ingreso_fallido", usuario, dispositivo, ip)
            raise ErrorNegocio("Usuario o clave incorrectos", 401, confirmar=True)
        _verificar_no_bloqueado(p)
        if not verificar_clave(clave or "", p["clave_hash"]):
            _fallo(db, "personal", p["id"])
            registrar_acceso(db, "personal", p["id"], "ingreso_fallido", None, dispositivo, ip)
            raise ErrorNegocio("Usuario o clave incorrectos", 401, confirmar=True)
        _exito(db, "personal", p["id"])
        return p


def verificar_supervisor(db, usuario, clave) -> dict:
    """Aprobación en el momento: el supervisor escribe su usuario y clave en la pantalla del cajero."""
    p = autenticar_personal(db, usuario, clave)
    if p["rol"] not in PERMISOS["aprobar"]:
        raise ErrorNegocio("Esa persona no es supervisor", 403)
    return p


# --- Jugadores: ingreso con código + PIN ---------------------------------------------------

def jugador_por_celular(db, celular) -> dict | None:
    return db.uno("SELECT * FROM jugadores WHERE celular = ?", (normalizar_celular(celular),))


def solicitar_ingreso(db, celular, dispositivo=None, ip=None) -> str | None:
    """Envía el código al celular. No revela si el celular existe. Devuelve el código (solo pruebas)."""
    with db.transaccion():
        j = jugador_por_celular(db, celular)
        if not j or j["estado"] != "activo" or not j["celular_verificado"]:
            registrar_acceso(db, "jugador", j["id"] if j else None, "codigo_solicitado_rechazado",
                             None, dispositivo, ip)
            return None
        _verificar_no_bloqueado(j)
        registrar_acceso(db, "jugador", j["id"], "codigo_solicitado", None, dispositivo, ip)
        return generar_codigo(db, j, "ingreso")


def ingresar_jugador(db, celular, codigo, pin, pin_nuevo=None, dispositivo=None, ip=None) -> tuple[str, dict]:
    """Código al celular + PIN. Si el jugador aún no tiene PIN (primer ingreso), lo crea."""
    with db.transaccion():
        j = jugador_por_celular(db, celular)
        if not j or j["estado"] != "activo" or not j["celular_verificado"]:
            raise ErrorNegocio("No se pudo ingresar. Si su cuenta fue bloqueada, acérquese a la caja.", 401)
        _verificar_no_bloqueado(j)
        try:
            verificar_codigo(db, j["id"], "ingreso", codigo)
        except ErrorNegocio as e:
            _fallo(db, "jugadores", j["id"])
            registrar_acceso(db, "jugador", j["id"], "ingreso_fallido", "código", dispositivo, ip)
            e.confirmar = True
            raise
        if j["pin_hash"] is None:
            if not pin_nuevo:
                raise ErrorNegocio("Primer ingreso: cree su PIN", 409)
            db.conexion().execute("UPDATE jugadores SET pin_hash = ? WHERE id = ?",
                                  (hash_clave(validar_pin(pin_nuevo)), j["id"]))
        elif not verificar_clave(pin or "", j["pin_hash"]):
            _fallo(db, "jugadores", j["id"])
            registrar_acceso(db, "jugador", j["id"], "ingreso_fallido", "PIN", dispositivo, ip)
            raise ErrorNegocio("PIN incorrecto", 401, confirmar=True)
        _exito(db, "jugadores", j["id"])
        token = crear_sesion(db, "jugador", j["id"], dispositivo, ip)
        registrar_acceso(db, "jugador", j["id"], "ingreso", None, dispositivo, ip)
        if j["pin_hash"] is not None:
            db.notificar(j["id"], "seguridad", "Nuevo ingreso a su cuenta",
                         f"Se ingresó desde: {(dispositivo or 'dispositivo desconocido')[:80]}. "
                         "Si no fue usted, avise a la caja para bloquear la cuenta.")
        return token, j


def confirmar_pin(db, jugador_id, pin) -> None:
    """Se pide en cada compra. Los fallos cuentan para el bloqueo."""
    j = db.uno("SELECT * FROM jugadores WHERE id = ?", (jugador_id,))
    _verificar_no_bloqueado(j)
    if not verificar_clave(pin or "", j["pin_hash"]):
        _fallo(db, "jugadores", jugador_id)
        registrar_acceso(db, "jugador", jugador_id, "pin_fallido", "confirmación de compra")
        raise ErrorNegocio("PIN incorrecto", 401, confirmar=True)
    _exito(db, "jugadores", jugador_id)


# --- Sesiones ------------------------------------------------------------------------------

def crear_sesion(db, tipo, sujeto_id, dispositivo=None, ip=None) -> str:
    token = secrets.token_urlsafe(32)
    t = ahora()
    db.conexion().execute(
        "INSERT INTO sesiones (token_hash, tipo, sujeto_id, dispositivo, ip, creada_en, ultima_actividad) "
        "VALUES (?,?,?,?,?,?,?)", (_token_hash(token), tipo, sujeto_id, (dispositivo or "")[:200], ip, t, t))
    return token


def sesion_valida(db, token, tipo) -> dict | None:
    """Devuelve la sesión si sigue activa; aplica el cierre por inactividad."""
    if not token:
        return None
    s = db.uno("SELECT * FROM sesiones WHERE token_hash = ? AND tipo = ? AND cerrada = 0",
               (_token_hash(token), tipo))
    if not s:
        return None
    minutos = db.ajuste("minutos_inactividad_jugador" if tipo == "jugador" else "minutos_inactividad_personal")
    if parse_fecha(s["ultima_actividad"]) + timedelta(minutes=minutos) < parse_fecha(ahora()):
        db.conexion().execute("UPDATE sesiones SET cerrada = 1 WHERE token_hash = ?", (s["token_hash"],))
        return None
    if tipo == "jugador":
        j = db.uno("SELECT estado FROM jugadores WHERE id = ?", (s["sujeto_id"],))
        if not j or j["estado"] != "activo":
            return None
    else:
        p = db.uno("SELECT activo FROM personal WHERE id = ?", (s["sujeto_id"],))
        if not p or not p["activo"]:
            return None
    db.conexion().execute("UPDATE sesiones SET ultima_actividad = ? WHERE token_hash = ?",
                          (ahora(), s["token_hash"]))
    return s


def cerrar_sesion(db, token) -> None:
    if token:
        db.conexion().execute("UPDATE sesiones SET cerrada = 1 WHERE token_hash = ?", (_token_hash(token),))


def cerrar_sesiones_jugador(db, jugador_id) -> None:
    db.conexion().execute("UPDATE sesiones SET cerrada = 1 WHERE tipo = 'jugador' AND sujeto_id = ?",
                          (jugador_id,))


def detalle_json(**kw) -> str:
    return json.dumps(kw, ensure_ascii=False)
