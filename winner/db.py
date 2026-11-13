"""Base de datos SQLite de Winner.

Reglas que se hacen cumplir en la propia base (no solo en el código):
- ``movimientos`` (libro de caja) no admite UPDATE ni DELETE: se corrige con otro movimiento.
- ``auditoria`` y ``accesos`` tampoco se pueden modificar ni borrar.
- Un jugador solo puede tener una entrada viva por torneo (índice único parcial).
- Un asiento de una mesa solo puede estar ocupado por una entrada viva.
"""
from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

RUTA_POR_DEFECTO = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "winner.db")

ESQUEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS ajustes (
    clave TEXT PRIMARY KEY,
    valor TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS personal (
    id INTEGER PRIMARY KEY,
    usuario TEXT NOT NULL UNIQUE,
    nombre TEXT NOT NULL,
    rol TEXT NOT NULL CHECK (rol IN ('cajero','crupier','floor','director','supervisor','admin')),
    clave_hash TEXT NOT NULL,
    activo INTEGER NOT NULL DEFAULT 1,
    intentos_fallidos INTEGER NOT NULL DEFAULT 0,
    bloqueado_hasta TEXT,
    creado_en TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jugadores (
    id INTEGER PRIMARY KEY,
    numero_caja TEXT NOT NULL UNIQUE,
    nombre TEXT NOT NULL,
    doc_hash TEXT NOT NULL UNIQUE,
    tipo_doc TEXT NOT NULL DEFAULT 'CC',
    doc_ultimos TEXT NOT NULL,
    celular TEXT UNIQUE,
    celular_verificado INTEGER NOT NULL DEFAULT 0,
    pin_hash TEXT,
    estado TEXT NOT NULL DEFAULT 'activo' CHECK (estado IN ('activo','bloqueado')),
    motivo_bloqueo TEXT,
    intentos_fallidos INTEGER NOT NULL DEFAULT 0,
    bloqueado_hasta TEXT,
    tope_diario INTEGER,
    creado_en TEXT NOT NULL,
    creado_por INTEGER REFERENCES personal(id)
);

CREATE TABLE IF NOT EXISTS sesiones (
    token_hash TEXT PRIMARY KEY,
    tipo TEXT NOT NULL CHECK (tipo IN ('jugador','personal')),
    sujeto_id INTEGER NOT NULL,
    dispositivo TEXT,
    ip TEXT,
    creada_en TEXT NOT NULL,
    ultima_actividad TEXT NOT NULL,
    cerrada INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS codigos (
    id INTEGER PRIMARY KEY,
    jugador_id INTEGER NOT NULL REFERENCES jugadores(id),
    proposito TEXT NOT NULL CHECK (proposito IN ('ingreso','retiro','vinculacion')),
    codigo_hash TEXT NOT NULL,
    vence_en TEXT NOT NULL,
    usado INTEGER NOT NULL DEFAULT 0,
    intentos INTEGER NOT NULL DEFAULT 0,
    creado_en TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS accesos (
    id INTEGER PRIMARY KEY,
    tipo TEXT NOT NULL,
    sujeto_id INTEGER,
    evento TEXT NOT NULL,
    detalle TEXT,
    dispositivo TEXT,
    ip TEXT,
    fecha TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS auditoria (
    id INTEGER PRIMARY KEY,
    personal_id INTEGER REFERENCES personal(id),
    accion TEXT NOT NULL,
    entidad TEXT,
    entidad_id INTEGER,
    detalle TEXT,
    fecha TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS torneos (
    id INTEGER PRIMARY KEY,
    nombre TEXT NOT NULL,
    fecha TEXT NOT NULL,
    estado TEXT NOT NULL DEFAULT 'programado'
        CHECK (estado IN ('programado','en_curso','finalizado','cancelado')),
    buyin INTEGER NOT NULL,
    stack_inicial INTEGER NOT NULL,
    asientos_por_mesa INTEGER NOT NULL DEFAULT 9,
    registro_abierto INTEGER NOT NULL DEFAULT 1,
    preinscripcion_movil INTEGER NOT NULL DEFAULT 1,
    reentrada_max INTEGER NOT NULL DEFAULT 0,
    recompra_max INTEGER NOT NULL DEFAULT 0,
    recompra_precio INTEGER NOT NULL DEFAULT 0,
    recompra_fichas INTEGER NOT NULL DEFAULT 0,
    recompras_abiertas INTEGER NOT NULL DEFAULT 1,
    addon_precio INTEGER NOT NULL DEFAULT 0,
    addon_fichas INTEGER NOT NULL DEFAULT 0,
    addon_abierto INTEGER NOT NULL DEFAULT 0,
    tope_compra INTEGER,
    nivel INTEGER NOT NULL DEFAULT 1,
    -- normal · dia1 (clasificatorio, pertenece a una final) · final (Día 2) · satelite (premia tickets)
    tipo TEXT NOT NULL DEFAULT 'normal',
    principal_id INTEGER REFERENCES torneos(id),
    satelite_de INTEGER REFERENCES torneos(id),
    -- Días clasificatorios: cómo se decide quién clasifica (lo define el administrador en cada torneo).
    -- nivel: el día termina al acabar el nivel N · porcentaje: hasta quedar el N % de las entradas ·
    -- jugadores: hasta quedar N jugadores
    modo_clasifica TEXT,
    clasifica_valor INTEGER,
    creado_en TEXT NOT NULL,
    finalizado_en TEXT
);

CREATE TABLE IF NOT EXISTS mesas (
    id INTEGER PRIMARY KEY,
    torneo_id INTEGER NOT NULL REFERENCES torneos(id),
    numero INTEGER NOT NULL,
    asientos INTEGER NOT NULL,
    estado TEXT NOT NULL DEFAULT 'abierta' CHECK (estado IN ('abierta','cerrada')),
    cerrada_en TEXT,
    UNIQUE (torneo_id, numero)
);

-- estado: inscrito (pagó, sin asiento) · sentado · reservado (eliminado, esperando recompra,
-- asiento guardado) · eliminado · clasificado (sobrevivió un día clasificatorio y guardó sus fichas)
CREATE TABLE IF NOT EXISTS entradas (
    id INTEGER PRIMARY KEY,
    torneo_id INTEGER NOT NULL REFERENCES torneos(id),
    jugador_id INTEGER NOT NULL REFERENCES jugadores(id),
    numero INTEGER NOT NULL,
    estado TEXT NOT NULL CHECK (estado IN ('inscrito','sentado','reservado','eliminado','clasificado')),
    mesa_id INTEGER REFERENCES mesas(id),
    asiento INTEGER,
    fichas INTEGER NOT NULL,
    fichas_iniciales INTEGER,
    pago TEXT,
    recompras INTEGER NOT NULL DEFAULT 0,
    addons INTEGER NOT NULL DEFAULT 0,
    codigo_qr TEXT NOT NULL UNIQUE,
    origen TEXT NOT NULL,
    eliminado_en TEXT,
    eliminado_por INTEGER REFERENCES personal(id),
    espera_desde TEXT,
    creado_en TEXT NOT NULL,
    UNIQUE (torneo_id, jugador_id, numero)
);
CREATE UNIQUE INDEX IF NOT EXISTS entrada_viva_unica
    ON entradas(torneo_id, jugador_id) WHERE estado != 'eliminado';
CREATE UNIQUE INDEX IF NOT EXISTS asiento_unico
    ON entradas(mesa_id, asiento) WHERE estado IN ('sentado','reservado') AND mesa_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS cambios_asiento (
    id INTEGER PRIMARY KEY,
    torneo_id INTEGER NOT NULL REFERENCES torneos(id),
    entrada_id INTEGER NOT NULL REFERENCES entradas(id),
    de_mesa INTEGER,
    de_asiento INTEGER,
    a_mesa INTEGER,
    a_asiento INTEGER,
    motivo TEXT NOT NULL,
    personal_id INTEGER REFERENCES personal(id),
    fecha TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conteos (
    id INTEGER PRIMARY KEY,
    torneo_id INTEGER NOT NULL REFERENCES torneos(id),
    entrada_id INTEGER NOT NULL REFERENCES entradas(id),
    fichas INTEGER NOT NULL,
    fichas_anteriores INTEGER NOT NULL,
    nivel INTEGER NOT NULL,
    personal_id INTEGER REFERENCES personal(id),
    fecha TEXT NOT NULL
);

-- Reserva temporal del asiento mientras el jugador confirma recompra o re-entrada.
CREATE TABLE IF NOT EXISTS reservas (
    id INTEGER PRIMARY KEY,
    entrada_id INTEGER NOT NULL REFERENCES entradas(id),
    jugador_id INTEGER NOT NULL REFERENCES jugadores(id),
    torneo_id INTEGER NOT NULL REFERENCES torneos(id),
    opciones TEXT NOT NULL,
    vence_en TEXT NOT NULL,
    estado TEXT NOT NULL DEFAULT 'activa'
        CHECK (estado IN ('activa','confirmada','vencida','rechazada')),
    resuelta_en TEXT,
    creada_en TEXT NOT NULL
);

-- Libro de caja: montos enteros en pesos; positivo entra al saldo del jugador, negativo sale.
CREATE TABLE IF NOT EXISTS movimientos (
    id INTEGER PRIMARY KEY,
    jugador_id INTEGER NOT NULL REFERENCES jugadores(id),
    tipo TEXT NOT NULL CHECK (tipo IN
        ('deposito','retiro','inscripcion','recompra','reentrada','addon','premio','correccion')),
    monto INTEGER NOT NULL CHECK (monto != 0),
    saldo_despues INTEGER NOT NULL,
    -- efectivo / datafono / transferencia: dinero que entra o sale por la ventanilla; ventanilla: compra pagada así
    medio TEXT NOT NULL DEFAULT 'saldo' CHECK (medio IN ('saldo','efectivo','ventanilla','datafono','transferencia')),
    torneo_id INTEGER REFERENCES torneos(id),
    corrige_id INTEGER REFERENCES movimientos(id),
    descripcion TEXT,
    origen TEXT NOT NULL CHECK (origen IN ('caja','app','sistema')),
    personal_id INTEGER REFERENCES personal(id),
    aprobado_por INTEGER REFERENCES personal(id),
    fecha TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS movimientos_jugador ON movimientos(jugador_id, id);
CREATE INDEX IF NOT EXISTS movimientos_fecha ON movimientos(fecha);

CREATE TRIGGER IF NOT EXISTS movimientos_sin_update BEFORE UPDATE ON movimientos
BEGIN SELECT RAISE(ABORT, 'El libro de movimientos no se puede modificar'); END;
CREATE TRIGGER IF NOT EXISTS movimientos_sin_delete BEFORE DELETE ON movimientos
BEGIN SELECT RAISE(ABORT, 'El libro de movimientos no se puede borrar'); END;
CREATE TRIGGER IF NOT EXISTS auditoria_sin_update BEFORE UPDATE ON auditoria
BEGIN SELECT RAISE(ABORT, 'La auditoría no se puede modificar'); END;
CREATE TRIGGER IF NOT EXISTS auditoria_sin_delete BEFORE DELETE ON auditoria
BEGIN SELECT RAISE(ABORT, 'La auditoría no se puede borrar'); END;
CREATE TRIGGER IF NOT EXISTS accesos_sin_update BEFORE UPDATE ON accesos
BEGIN SELECT RAISE(ABORT, 'El registro de accesos no se puede modificar'); END;
CREATE TRIGGER IF NOT EXISTS accesos_sin_delete BEFORE DELETE ON accesos
BEGIN SELECT RAISE(ABORT, 'El registro de accesos no se puede borrar'); END;

CREATE TABLE IF NOT EXISTS premios_tabla (
    torneo_id INTEGER NOT NULL REFERENCES torneos(id),
    posicion INTEGER NOT NULL,
    monto INTEGER NOT NULL CHECK (monto > 0),
    tipo TEXT NOT NULL DEFAULT 'dinero',
    PRIMARY KEY (torneo_id, posicion)
);

CREATE TABLE IF NOT EXISTS premiaciones (
    torneo_id INTEGER PRIMARY KEY REFERENCES torneos(id),
    estado TEXT NOT NULL CHECK (estado IN ('borrador','confirmada','acreditada')),
    preparada_en TEXT NOT NULL,
    confirmada_por INTEGER REFERENCES personal(id),
    confirmada_en TEXT,
    acreditada_por INTEGER REFERENCES personal(id),
    acreditada_en TEXT
);

CREATE TABLE IF NOT EXISTS premiacion_detalle (
    torneo_id INTEGER NOT NULL REFERENCES premiaciones(torneo_id),
    posicion INTEGER NOT NULL,
    jugador_id INTEGER NOT NULL REFERENCES jugadores(id),
    monto_tabla INTEGER NOT NULL,
    monto_final INTEGER NOT NULL,
    motivo_ajuste TEXT,
    movimiento_id INTEGER REFERENCES movimientos(id),
    tipo TEXT NOT NULL DEFAULT 'dinero',
    ticket_id INTEGER,
    PRIMARY KEY (torneo_id, posicion)
);

-- Fichas guardadas al cerrar un día clasificatorio. Si un jugador clasifica dos veces, vale el stack mayor.
CREATE TABLE IF NOT EXISTS clasificados (
    id INTEGER PRIMARY KEY,
    principal_id INTEGER NOT NULL REFERENCES torneos(id),
    jugador_id INTEGER NOT NULL REFERENCES jugadores(id),
    torneo_origen INTEGER NOT NULL REFERENCES torneos(id),
    entrada_id INTEGER NOT NULL REFERENCES entradas(id),
    fichas INTEGER NOT NULL,
    fecha TEXT NOT NULL
);

-- Cupos ganados en satélites: valen una entrada al torneo destino (o a cualquiera de sus días).
CREATE TABLE IF NOT EXISTS tickets (
    id INTEGER PRIMARY KEY,
    jugador_id INTEGER NOT NULL REFERENCES jugadores(id),
    destino_id INTEGER NOT NULL REFERENCES torneos(id),
    origen_torneo INTEGER REFERENCES torneos(id),
    valor INTEGER NOT NULL,
    estado TEXT NOT NULL DEFAULT 'disponible' CHECK (estado IN ('disponible','usado','anulado')),
    entrada_id INTEGER REFERENCES entradas(id),
    creado_en TEXT NOT NULL,
    usado_en TEXT,
    personal_id INTEGER REFERENCES personal(id)
);

-- Venta/traspaso de un ticket entre jugadores (el casino no devuelve dinero por un cupo).
CREATE TABLE IF NOT EXISTS traspasos_ticket (
    id INTEGER PRIMARY KEY,
    ticket_id INTEGER NOT NULL REFERENCES tickets(id),
    de_jugador INTEGER NOT NULL REFERENCES jugadores(id),
    a_jugador INTEGER NOT NULL REFERENCES jugadores(id),
    personal_id INTEGER NOT NULL REFERENCES personal(id),
    fecha TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mesas_cash (
    id INTEGER PRIMARY KEY,
    nombre TEXT NOT NULL,
    juego TEXT NOT NULL,
    asientos INTEGER NOT NULL DEFAULT 9,
    ocupados INTEGER NOT NULL DEFAULT 0,
    estado TEXT NOT NULL DEFAULT 'abierta' CHECK (estado IN ('abierta','cerrada')),
    actualizado_en TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS lista_espera (
    id INTEGER PRIMARY KEY,
    juego TEXT NOT NULL,
    jugador_id INTEGER REFERENCES jugadores(id),
    nombre TEXT NOT NULL,
    estado TEXT NOT NULL DEFAULT 'esperando'
        CHECK (estado IN ('esperando','llamado','sentado','retirado')),
    mesa_id INTEGER REFERENCES mesas_cash(id),
    creado_en TEXT NOT NULL,
    llamado_en TEXT,
    resuelto_en TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS espera_unica
    ON lista_espera(juego, jugador_id) WHERE estado IN ('esperando','llamado') AND jugador_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS notificaciones (
    id INTEGER PRIMARY KEY,
    jugador_id INTEGER NOT NULL REFERENCES jugadores(id),
    tipo TEXT NOT NULL,
    titulo TEXT NOT NULL,
    texto TEXT NOT NULL,
    leida INTEGER NOT NULL DEFAULT 0,
    fecha TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sms_enviados (
    id INTEGER PRIMARY KEY,
    celular TEXT NOT NULL,
    texto TEXT NOT NULL,
    fecha TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS arqueos (
    id INTEGER PRIMARY KEY,
    dia TEXT NOT NULL,
    personal_id INTEGER NOT NULL REFERENCES personal(id),
    efectivo_contado INTEGER NOT NULL,
    efectivo_esperado INTEGER NOT NULL,
    diferencia INTEGER NOT NULL,
    nota TEXT,
    fecha TEXT NOT NULL
);
"""

AJUSTES_POR_DEFECTO = {
    "tope_retiro_sin_supervisor": "2000000",
    "tope_compra_diario": "3000000",
    "minutos_inactividad_jugador": "5",
    "minutos_inactividad_personal": "30",
    "intentos_maximos": "5",
    "minutos_bloqueo": "15",
    "minutos_reserva_asiento": "3",
    "minutos_codigo": "5",
    # Fase 1: el celular solo consulta. En fase 2 se enciende la compra desde el celular.
    "compras_desde_celular": "0",
    # Capacidad física de la sala: 30 mesas (de 9 asientos por defecto = 270 jugadores sentados a la vez).
    "mesas_sala": "30",
}


COLUMNAS_NUEVAS = {
    "entradas": {"espera_desde": "TEXT", "fichas_iniciales": "INTEGER", "pago": "TEXT"},
    "torneos": {"tipo": "TEXT NOT NULL DEFAULT 'normal'", "principal_id": "INTEGER REFERENCES torneos(id)",
                "satelite_de": "INTEGER REFERENCES torneos(id)", "modo_clasifica": "TEXT",
                "clasifica_valor": "INTEGER"},
    "premios_tabla": {"tipo": "TEXT NOT NULL DEFAULT 'dinero'"},
    "premiacion_detalle": {"tipo": "TEXT NOT NULL DEFAULT 'dinero'", "ticket_id": "INTEGER"},
    "jugadores": {"tipo_doc": "TEXT NOT NULL DEFAULT 'CC'"},
}


def _reconstruir(c: sqlite3.Connection, tabla: str, viejo: str, nuevo: str) -> None:
    """SQLite no permite cambiar un CHECK: se reconstruye la tabla (procedimiento oficial).

    Los índices y disparadores (p. ej. los que impiden borrar el libro) se vuelven a crear con ESQUEMA.
    """
    sql = c.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (tabla,)).fetchone()[0]
    if nuevo in sql:
        return
    if viejo not in sql:
        raise RuntimeError(f"No se pudo migrar la tabla {tabla}")
    crear = sql.replace(viejo, nuevo).replace(f"CREATE TABLE {tabla}", f"CREATE TABLE {tabla}_nueva", 1)
    cols = ", ".join(r[1] for r in c.execute(f"PRAGMA table_info({tabla})"))
    c.execute("PRAGMA foreign_keys = OFF")
    c.execute("BEGIN")
    c.execute(crear)
    c.execute(f"INSERT INTO {tabla}_nueva ({cols}) SELECT {cols} FROM {tabla}")
    c.execute(f"DROP TABLE {tabla}")
    c.execute(f"ALTER TABLE {tabla}_nueva RENAME TO {tabla}")
    c.execute("COMMIT")
    c.execute("PRAGMA foreign_keys = ON")


def _migrar(c: sqlite3.Connection) -> None:
    """Pone al día bases creadas con versiones anteriores (columnas y estados nuevos)."""
    for tabla, cols in COLUMNAS_NUEVAS.items():
        existentes = {r[1] for r in c.execute(f"PRAGMA table_info({tabla})")}
        for col, ddl in cols.items():
            if col not in existentes:
                c.execute(f"ALTER TABLE {tabla} ADD COLUMN {col} {ddl}")
    if "porcentaje_clasifica" in {r[1] for r in c.execute("PRAGMA table_info(torneos)")}:
        c.execute("UPDATE torneos SET modo_clasifica = 'porcentaje', clasifica_valor = porcentaje_clasifica "
                  "WHERE porcentaje_clasifica IS NOT NULL AND modo_clasifica IS NULL")
    _reconstruir(c, "entradas", "'reservado','eliminado')", "'reservado','eliminado','clasificado')")
    _reconstruir(c, "movimientos", "('saldo','efectivo','ventanilla')",
                 "('saldo','efectivo','ventanilla','datafono','transferencia')")


class ErrorNegocio(Exception):
    """Error que se muestra tal cual al usuario (regla de negocio incumplida)."""

    def __init__(self, mensaje: str, codigo: int = 400, confirmar: bool = False):
        super().__init__(mensaje)
        self.mensaje = mensaje
        self.codigo = codigo
        # confirmar=True: lo escrito antes del error (intentos fallidos, registro de accesos) se guarda.
        self.confirmar = confirmar


def ahora() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def parse_fecha(texto: str) -> datetime:
    return datetime.strptime(texto, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)


class BaseDatos:
    """Conexión por hilo; las escrituras van en transacciones ``BEGIN IMMEDIATE``."""

    def __init__(self, ruta: str | None = None):
        self.ruta = ruta or os.environ.get("WINNER_DB") or RUTA_POR_DEFECTO
        if self.ruta != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(self.ruta)), exist_ok=True)
        self._local = threading.local()
        self._bloqueo = threading.RLock()
        self._memoria = None
        with self._bloqueo:
            self._crear_esquema(self.conexion())

    def _crear_esquema(self, c) -> None:
        c.executescript(ESQUEMA)
        _migrar(c)
        c.executescript(ESQUEMA)  # vuelve a crear índices de tablas reconstruidas
        for k, v in AJUSTES_POR_DEFECTO.items():
            c.execute("INSERT OR IGNORE INTO ajustes (clave, valor) VALUES (?, ?)", (k, v))
        c.commit()

    def borrar_todo(self) -> None:
        """Solo para la demo: deja la base vacía (borra también el libro y la auditoría)."""
        with self._bloqueo:
            c = self.conexion()
            c.execute("PRAGMA foreign_keys = OFF")
            for (tabla,) in c.execute("SELECT name FROM sqlite_master WHERE type = 'table' "
                                      "AND name NOT LIKE 'sqlite_%'").fetchall():
                c.execute(f"DROP TABLE IF EXISTS {tabla}")
            c.execute("PRAGMA foreign_keys = ON")
            self._crear_esquema(c)

    def conexion(self) -> sqlite3.Connection:
        if self.ruta == ":memory:":
            if self._memoria is None:
                self._memoria = self._abrir()
            return self._memoria
        c = getattr(self._local, "c", None)
        if c is None:
            c = self._local.c = self._abrir()
        return c

    def _abrir(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.ruta, check_same_thread=False, isolation_level=None, timeout=15)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys = ON")
        if self.ruta != ":memory:":
            c.execute("PRAGMA journal_mode = WAL")
        return c

    @contextmanager
    def transaccion(self):
        """Transacción exclusiva para escritura. Anidable (las internas se suman a la externa)."""
        with self._bloqueo:
            c = self.conexion()
            if c.in_transaction:
                yield c
                return
            c.execute("BEGIN IMMEDIATE")
            try:
                yield c
            except ErrorNegocio as e:
                c.execute("COMMIT" if e.confirmar else "ROLLBACK")
                raise
            except BaseException:
                c.execute("ROLLBACK")
                raise
            else:
                c.execute("COMMIT")

    def uno(self, sql: str, params=()):
        r = self.conexion().execute(sql, params).fetchone()
        return dict(r) if r else None

    def todos(self, sql: str, params=()) -> list[dict]:
        return [dict(r) for r in self.conexion().execute(sql, params).fetchall()]

    def valor(self, sql: str, params=()):
        r = self.conexion().execute(sql, params).fetchone()
        return r[0] if r else None

    def ajuste(self, clave: str) -> int:
        return int(self.valor("SELECT valor FROM ajustes WHERE clave = ?", (clave,)))

    def auditar(self, personal_id, accion, entidad=None, entidad_id=None, detalle=None):
        self.conexion().execute(
            "INSERT INTO auditoria (personal_id, accion, entidad, entidad_id, detalle, fecha) "
            "VALUES (?,?,?,?,?,?)", (personal_id, accion, entidad, entidad_id, detalle, ahora()))

    def notificar(self, jugador_id, tipo, titulo, texto):
        self.conexion().execute(
            "INSERT INTO notificaciones (jugador_id, tipo, titulo, texto, fecha) VALUES (?,?,?,?,?)",
            (jugador_id, tipo, titulo, texto, ahora()))
