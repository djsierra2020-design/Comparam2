"""Pruebas de la plataforma Winner (torneos, mesas, caja y seguridad)."""
import math
import random
import sqlite3

import pytest
from flask import Flask

from winner import caja, premios, reportes
from winner import seguridad as seg
from winner import torneos as T
from winner.db import BaseDatos, ErrorNegocio


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setenv("WINNER_DEMO", "1")  # los SMS se guardan en la base en vez de enviarse
    return BaseDatos(":memory:")


@pytest.fixture
def staff(db):
    return {r: seg.crear_personal(db, r, r.title(), r, "clave12345")
            for r in ("admin", "director", "floor", "crupier", "cajero", "supervisor")}


def nuevo_jugador(db, staff, i, celular=True, saldo=0):
    r = caja.abrir_caja(db, staff["cajero"], f"Jugador {i}", f"5000{i:05d}",
                        f"310{i:07d}" if celular else None, True)
    jid = r["jugador"]["id"]
    if celular:
        caja.vincular_celular(db, staff["cajero"], jid, r["_codigo"])
    if saldo:
        caja.depositar(db, staff["cajero"], jid, saldo)
    return jid


def nuevo_torneo(db, staff, **extra):
    datos = dict(nombre="Prueba", fecha="2026-12-01", buyin=100_000, stack_inicial=10_000, asientos_por_mesa=9)
    datos.update(extra)
    return T.crear_torneo(db, staff["director"], datos)


def conteo_mesas(db, tid):
    return [m["ocupados"] for m in T.tablero(db, tid)["mesas"]]


def vencer_todo(db):
    db.conexion().execute("UPDATE reservas SET vence_en = '2000-01-01T00:00:00.000000Z' WHERE estado = 'activa'")


# --- Inscripción ---------------------------------------------------------------------------------

def test_inscripcion_unica_por_jugador_y_torneo(db, staff):
    j = nuevo_jugador(db, staff, 1, saldo=500_000)
    t = nuevo_torneo(db, staff)
    T.inscribir(db, t, j, origen="caja", personal_id=staff["cajero"])
    with pytest.raises(ErrorNegocio, match="ya está inscrito"):
        T.inscribir(db, t, j, origen="caja", personal_id=staff["cajero"])
    assert caja.saldo(db, j) == 400_000  # el segundo intento no cobró
    # La base misma impide dos entradas vivas aunque se salte la lógica.
    with pytest.raises(sqlite3.IntegrityError):
        db.conexion().execute("INSERT INTO entradas (torneo_id, jugador_id, numero, estado, fichas, codigo_qr, origen, "
                              "creado_en) VALUES (?, ?, 2, 'inscrito', 1, 'X', 'caja', 'x')", (t, j))


def test_pago_en_efectivo_queda_en_el_libro(db, staff):
    j = nuevo_jugador(db, staff, 1)
    t = nuevo_torneo(db, staff)
    T.inscribir(db, t, j, origen="caja", personal_id=staff["cajero"], pago="efectivo")
    tipos = [m["tipo"] for m in caja.movimientos(db, j)]
    assert tipos == ["inscripcion", "deposito"] and caja.saldo(db, j) == 0


def test_saldo_insuficiente(db, staff):
    j = nuevo_jugador(db, staff, 1, saldo=50_000)
    t = nuevo_torneo(db, staff)
    with pytest.raises(ErrorNegocio, match="Saldo insuficiente"):
        T.inscribir(db, t, j, origen="caja", personal_id=staff["cajero"])
    assert not db.todos("SELECT * FROM entradas")


# --- Mesas ---------------------------------------------------------------------------------------

def test_sentado_reparte_parejo_y_sin_asientos_repetidos(db, staff):
    t = nuevo_torneo(db, staff)
    T.abrir_mesas(db, staff["floor"], t, 4)
    for i in range(30):
        T.inscribir(db, t, nuevo_jugador(db, staff, i, celular=False), origen="caja",
                    personal_id=staff["cajero"], pago="efectivo")
    T.iniciar_torneo(db, staff["floor"], t)
    T.sentar_inscritos(db, staff["floor"], t)
    c = conteo_mesas(db, t)
    assert sum(c) == 30 and max(c) - min(c) <= 1
    filas = db.todos("SELECT mesa_id, asiento FROM entradas WHERE estado = 'sentado'")
    assert len({(f["mesa_id"], f["asiento"]) for f in filas}) == 30


def test_sin_asientos_libres_pide_abrir_mesa(db, staff):
    t = nuevo_torneo(db, staff, asientos_por_mesa=2)
    T.abrir_mesas(db, staff["floor"], t, 1)
    ids = [T.inscribir(db, t, nuevo_jugador(db, staff, i, celular=False), origen="caja",
                       personal_id=staff["cajero"], pago="efectivo")["id"] for i in range(3)]
    T.sentar(db, ids[0]); T.sentar(db, ids[1])
    with pytest.raises(ErrorNegocio, match="abra una mesa"):
        T.sentar(db, ids[2])


def test_qr_sienta_una_sola_vez(db, staff):
    t = nuevo_torneo(db, staff)
    T.abrir_mesas(db, staff["floor"], t, 1)
    e = T.inscribir(db, t, nuevo_jugador(db, staff, 1, saldo=100_000), origen="caja", personal_id=staff["cajero"])
    r = T.sentar_por_qr(db, "WINNER:" + e["codigo_qr"], staff["floor"])
    assert r["a_mesa"] == 1
    with pytest.raises(ErrorNegocio, match="Ya está sentado"):
        T.sentar_por_qr(db, e["codigo_qr"], staff["floor"])
    with pytest.raises(ErrorNegocio, match="no válido"):
        T.sentar_por_qr(db, "NOEXISTE", staff["floor"])


def test_sala_de_30_mesas_con_300_jugadores_y_alternos(db, staff):
    """Sala de Winner: 30 mesas de 9 (270 asientos) y aforo de 300 → 30 alternos que entran en orden.

    Después de cada eliminación: alternos primero, nunca más de 1 de diferencia entre mesas y solo las
    mesas necesarias abiertas.
    """
    t = nuevo_torneo(db, staff, reentrada_max=1, recompra_max=1, recompra_precio=50_000, recompra_fichas=5_000)
    T.abrir_mesas(db, staff["floor"], t, 30)
    with pytest.raises(ErrorNegocio, match="La sala tiene 30 mesas"):
        T.abrir_mesas(db, staff["floor"], t, 1)
    entradas = [T.inscribir(db, t, nuevo_jugador(db, staff, i, celular=False), origen="caja",
                            personal_id=staff["cajero"], pago="efectivo")["id"] for i in range(300)]
    T.iniciar_torneo(db, staff["floor"], t)
    r = T.sentar_inscritos(db, staff["floor"], t)
    assert sum(1 for m in r if m.get("alterno")) == 30
    assert conteo_mesas(db, t) == [9] * 30
    orden = [a["id"] for a in T.alternos(db, t)]
    assert orden == entradas[270:]  # en orden de llegada
    # Un QR escaneado con la sala llena también va al final de la fila (no se adelanta).
    rnd = random.Random(7)
    sentados_alterno = []
    while True:
        sentados = db.todos("SELECT id FROM entradas WHERE torneo_id = ? AND estado = 'sentado'", (t,))
        if len(sentados) + len(T.alternos(db, t)) <= 1:
            break
        T.eliminar(db, staff["crupier"], rnd.choice(sentados)["id"])
        vencer_todo(db)
        tipos = [s["tipo"] for s in T.sugerencias(db, t)]
        if T.alternos(db, t):
            assert "alternos" in tipos and "cerrar_mesa" not in tipos
            sentados_alterno += [m["entrada_id"] for m in T.sentar_alternos(db, staff["floor"], t)]
        for s in T.sugerencias(db, t):
            if s["tipo"] == "cerrar_mesa":
                T.cerrar_mesa(db, staff["floor"], t, s["mesa_id"])
        T.balancear(db, staff["floor"], t)
        c = conteo_mesas(db, t)
        assert max(c) - min(c) <= 1
        assert len(c) == min(30, max(1, math.ceil((sum(c) + len(T.alternos(db, t))) / 9)))
    assert sentados_alterno == orden
    motivos = {h["motivo"] for h in T.historial_asientos(db, t, 100_000)}
    assert {"sentado", "alterno", "balanceo", "cierre_mesa"} <= motivos


def test_qr_con_sala_llena_va_a_alternos(db, staff):
    db.conexion().execute("UPDATE ajustes SET valor = '1' WHERE clave = 'mesas_sala'")
    t = nuevo_torneo(db, staff, asientos_por_mesa=2)
    T.abrir_mesas(db, staff["floor"], t, 1)
    es = [T.inscribir(db, t, nuevo_jugador(db, staff, i, celular=False), origen="caja",
                      personal_id=staff["cajero"], pago="efectivo") for i in range(4)]
    T.iniciar_torneo(db, staff["floor"], t)
    assert T.sentar_por_qr(db, es[0]["codigo_qr"], staff["floor"])["a_mesa"] == 1
    T.sentar_por_qr(db, es[1]["codigo_qr"], staff["floor"])
    r = T.sentar_por_qr(db, es[3]["codigo_qr"], staff["floor"])
    assert r["alterno"] and r["turno"] == 1
    assert T.sentar_por_qr(db, es[2]["codigo_qr"], staff["floor"])["turno"] == 2
    T.eliminar(db, staff["crupier"], es[0]["id"])
    assert [m["entrada_id"] for m in T.sentar_alternos(db, staff["floor"], t)] == [es[3]["id"]]


def test_cerrar_mesa_sin_espacio_no_mueve_a_nadie(db, staff):
    t = nuevo_torneo(db, staff, asientos_por_mesa=2)
    m1, m2 = T.abrir_mesas(db, staff["floor"], t, 2)
    for i in range(4):
        e = T.inscribir(db, t, nuevo_jugador(db, staff, i, celular=False), origen="caja",
                        personal_id=staff["cajero"], pago="efectivo")
        T.sentar(db, e["id"])
    with pytest.raises(ErrorNegocio, match="No hay asientos libres"):
        T.cerrar_mesa(db, staff["floor"], t, m1)
    assert conteo_mesas(db, t) == [2, 2]


# --- Eliminación, reserva y recompra ------------------------------------------------------------

@pytest.fixture
def en_juego(db, staff):
    t = nuevo_torneo(db, staff, reentrada_max=1, recompra_max=1, recompra_precio=60_000, recompra_fichas=8_000)
    T.abrir_mesas(db, staff["floor"], t, 1)
    j = nuevo_jugador(db, staff, 1, saldo=1_000_000)
    seg.ingresar_jugador(db, "3100000001", seg.solicitar_ingreso(db, "3100000001"), None, pin_nuevo="4826")
    e = T.inscribir(db, t, j, origen="caja", personal_id=staff["cajero"], sentar_ya=True)
    T.iniciar_torneo(db, staff["floor"], t)
    return t, j, e


def test_eliminacion_reserva_asiento_y_vence(db, staff, en_juego):
    t, j, e = en_juego
    r = T.eliminar(db, staff["crupier"], e["id"])
    assert r["estado"] == "reservado" and set(r["opciones"]) == {"recompra", "reentrada"}
    assert T.tablero(db, t)["mesas"][0]["ocupados"] == 1  # el asiento sigue guardado
    vencer_todo(db)
    assert T.tablero(db, t)["mesas"][0]["ocupados"] == 0
    assert T.entrada(db, e["id"])["estado"] == "eliminado"


def test_compras_desde_celular_apagadas_en_fase_1(db, staff, en_juego):
    t, j, e = en_juego
    T.eliminar(db, staff["crupier"], e["id"])
    with pytest.raises(ErrorNegocio, match="aún no están habilitadas"):
        T.recomprar(db, e["id"], origen="app", pin="4826")


def test_recompra_desde_celular_con_pin(db, staff, en_juego):
    t, j, e = en_juego
    db.conexion().execute("UPDATE ajustes SET valor = '1' WHERE clave = 'compras_desde_celular'")
    asiento = T.entrada(db, e["id"])["asiento"]
    T.eliminar(db, staff["crupier"], e["id"])
    with pytest.raises(ErrorNegocio, match="PIN incorrecto"):
        T.recomprar(db, e["id"], origen="app", pin="0000")
    despues = T.recomprar(db, e["id"], origen="app", pin="4826")
    assert despues["estado"] == "sentado" and despues["asiento"] == asiento and despues["fichas"] == 8_000
    assert caja.saldo(db, j) == 1_000_000 - 100_000 - 60_000
    T.eliminar(db, staff["crupier"], e["id"])  # ya no le quedan recompras: solo re-entrada
    nueva = T.reentrar(db, e["id"], origen="app", pin="4826")
    assert nueva["numero"] == 2 and nueva["estado"] == "sentado" and nueva["fichas"] == 10_000


def test_tope_diario_de_compras_por_celular(db, staff, en_juego):
    t, j, e = en_juego
    db.conexion().execute("UPDATE ajustes SET valor = '1' WHERE clave = 'compras_desde_celular'")
    caja.fijar_tope(db, staff["cajero"], j, 50_000)
    T.eliminar(db, staff["crupier"], e["id"])
    with pytest.raises(ErrorNegocio, match="tope diario"):
        T.recomprar(db, e["id"], origen="app", pin="4826")


# --- Caja ----------------------------------------------------------------------------------------

def test_libro_no_se_borra_ni_se_modifica(db, staff):
    j = nuevo_jugador(db, staff, 1, saldo=100_000)
    for sql in ("UPDATE movimientos SET monto = 1", "DELETE FROM movimientos"):
        with pytest.raises(sqlite3.IntegrityError):
            db.conexion().execute(sql)
    caja.corregir(db, staff["supervisor"], db.valor("SELECT id FROM movimientos"), "Depósito digitado dos veces")
    assert caja.saldo(db, j) == 0
    assert len(caja.movimientos(db, j)) == 2
    with pytest.raises(ErrorNegocio, match="ya fue corregido"):
        caja.corregir(db, staff["supervisor"], db.valor("SELECT MIN(id) FROM movimientos"), "otra vez")


def test_retiro_exige_documento_codigo_y_supervisor(db, staff):
    j = nuevo_jugador(db, staff, 1, saldo=5_000_000)
    codigo = caja.solicitar_codigo_retiro(db, staff["cajero"], j)
    with pytest.raises(ErrorNegocio, match="documento no corresponde"):
        caja.retirar(db, staff["cajero"], j, 100_000, "123456789", codigo)
    with pytest.raises(ErrorNegocio, match="Código incorrecto"):
        caja.retirar(db, staff["cajero"], j, 100_000, "500000001", "000000")
    with pytest.raises(ErrorNegocio, match="supervisor"):
        caja.retirar(db, staff["cajero"], j, 3_000_000, "500000001", codigo)
    with pytest.raises(ErrorNegocio, match="no es supervisor"):
        caja.retirar(db, staff["cajero"], j, 3_000_000, "500000001", codigo, "cajero", "clave12345")
    caja.retirar(db, staff["cajero"], j, 3_000_000, "500000001", codigo, "supervisor", "clave12345")
    assert caja.saldo(db, j) == 2_000_000
    with pytest.raises(ErrorNegocio, match="venció o no existe"):  # el código es de un solo uso
        caja.retirar(db, staff["cajero"], j, 1_000, "500000001", codigo)


def test_documento_no_se_guarda_en_claro(db, staff):
    nuevo_jugador(db, staff, 1)
    fila = db.uno("SELECT * FROM jugadores")
    assert "500000001" not in str(fila) and fila["doc_ultimos"] == "0001"
    assert caja.buscar_jugadores(db, "500000001")[0]["id"] == fila["id"]


def test_documento_duplicado(db, staff):
    nuevo_jugador(db, staff, 1)
    with pytest.raises(ErrorNegocio, match="Ya existe una caja"):
        caja.abrir_caja(db, staff["cajero"], "Otro", "500000001", None, True)
    with pytest.raises(ErrorNegocio, match="verificar el documento"):
        caja.abrir_caja(db, staff["cajero"], "Otro", "999999999", None, False)


# --- Seguridad -----------------------------------------------------------------------------------

def test_ingreso_requiere_codigo_y_pin_y_bloquea_tras_fallos(db, staff):
    nuevo_jugador(db, staff, 1)
    codigo = seg.solicitar_ingreso(db, "3100000001")
    with pytest.raises(ErrorNegocio, match="cree su PIN"):
        seg.ingresar_jugador(db, "3100000001", codigo, None)
    seg.ingresar_jugador(db, "3100000001", codigo, None, pin_nuevo="4826")
    for _ in range(5):
        with pytest.raises(ErrorNegocio):
            seg.ingresar_jugador(db, "3100000001", seg.solicitar_ingreso(db, "3100000001"), "1357")
    with pytest.raises(ErrorNegocio, match="Demasiados intentos"):
        seg.ingresar_jugador(db, "3100000001", "000000", "4826")
    assert db.valor("SELECT COUNT(*) FROM accesos WHERE evento = 'ingreso_fallido'") == 5


def test_celular_no_registrado_no_recibe_codigo(db):
    assert seg.solicitar_ingreso(db, "3209999999") is None


def test_bloqueo_por_cajero_cierra_sesiones(db, staff):
    j = nuevo_jugador(db, staff, 1)
    token, _ = seg.ingresar_jugador(db, "3100000001", seg.solicitar_ingreso(db, "3100000001"), None, pin_nuevo="4826")
    assert seg.sesion_valida(db, token, "jugador")
    caja.bloquear(db, staff["cajero"], j, "Perdió el celular")
    assert seg.sesion_valida(db, token, "jugador") is None
    assert seg.solicitar_ingreso(db, "3100000001") is None


def test_cierre_por_inactividad(db, staff):
    nuevo_jugador(db, staff, 1)
    token, _ = seg.ingresar_jugador(db, "3100000001", seg.solicitar_ingreso(db, "3100000001"), None, pin_nuevo="4826")
    db.conexion().execute("UPDATE sesiones SET ultima_actividad = '2000-01-01T00:00:00.000000Z'")
    assert seg.sesion_valida(db, token, "jugador") is None


def test_pin_facil_rechazado():
    for pin in ("1111", "1234", "12", "abcd"):
        with pytest.raises(ErrorNegocio):
            seg.validar_pin(pin)


# --- Premios -------------------------------------------------------------------------------------

def test_premios_confirmar_ajustar_y_acreditar(db, staff):
    t = nuevo_torneo(db, staff)
    T.abrir_mesas(db, staff["floor"], t, 1)
    js = [nuevo_jugador(db, staff, i, celular=False, saldo=100_000) for i in range(4)]
    es = [T.inscribir(db, t, j, origen="caja", personal_id=staff["cajero"], sentar_ya=True) for j in js]
    T.iniciar_torneo(db, staff["floor"], t)
    premios.definir_tabla(db, staff["director"], t, [{"posicion": 1, "monto": 250_000}, {"posicion": 2, "monto": 150_000}])
    with pytest.raises(ErrorNegocio, match="confirmar"):
        premios.acreditar(db, staff["director"], t)
    for e in es[:3]:  # eliminados en orden: 0 (4°), 1 (3°), 2 (2°); gana el 3
        T.eliminar(db, staff["crupier"], e["id"])
    p = premios.finalizar(db, staff["director"], t)
    assert [(d["posicion"], d["jugador_id"]) for d in p["detalle"]] == [(1, js[3]), (2, js[2])]
    with pytest.raises(ErrorNegocio, match="confirmar"):
        premios.acreditar(db, staff["director"], t)
    with pytest.raises(ErrorNegocio, match="total debe seguir"):
        premios.ajustar(db, staff["director"], t, [{"posicion": 1, "monto_final": 300_000, "motivo": "acuerdo"}])
    with pytest.raises(ErrorNegocio, match="motivo"):
        premios.ajustar(db, staff["director"], t, [{"posicion": 1, "monto_final": 200_000},
                                                   {"posicion": 2, "monto_final": 200_000}])
    premios.ajustar(db, staff["director"], t, [{"posicion": 1, "monto_final": 200_000, "motivo": "Acuerdo mesa final"},
                                               {"posicion": 2, "monto_final": 200_000, "motivo": "Acuerdo mesa final"}])
    premios.confirmar(db, staff["director"], t)
    with pytest.raises(ErrorNegocio, match="ya fue confirmada"):
        premios.ajustar(db, staff["director"], t, [])
    premios.acreditar(db, staff["director"], t)
    assert caja.saldo(db, js[3]) == 200_000 and caja.saldo(db, js[2]) == 200_000
    acciones = [a["accion"] for a in db.todos("SELECT accion FROM auditoria WHERE entidad = 'torneo' AND entidad_id = ?", (t,))]
    assert {"finalizar_torneo", "ajustar_premios", "confirmar_posiciones", "acreditar_premios"} <= set(acciones)


# --- API web -------------------------------------------------------------------------------------

@pytest.fixture
def cliente(monkeypatch, tmp_path):
    monkeypatch.setenv("WINNER_DEMO", "completo")
    from winner.web import iniciar
    app = Flask(__name__)
    iniciar(app, str(tmp_path / "w.db"))
    return app.test_client()


H = {"X-Winner": "1"}


def test_api_roles_y_tablero(cliente):
    assert cliente.post("/winner/api/personal/ingresar", json={"usuario": "crupier", "clave": "x"}, headers=H).status_code == 401
    assert cliente.post("/winner/api/personal/ingresar", json={"usuario": "crupier", "clave": "winner2026"}).status_code == 403
    assert cliente.post("/winner/api/personal/ingresar", json={"usuario": "crupier", "clave": "winner2026"}, headers=H).status_code == 200
    # Demo: torneo sencillo (id 7) en las mesas 1-5, satélite en 6-9 y Día 1B (id 3) en las 21 restantes.
    sencillo = cliente.get("/winner/api/torneos/7/tablero", headers=H).get_json()
    assert sencillo["torneo"]["tipo"] == "normal" and sencillo["vivos"] == 45
    assert [m["numero"] for m in sencillo["mesas"]] == [1, 2, 3, 4, 5]
    tb = cliente.get("/winner/api/torneos/3/tablero", headers=H).get_json()
    assert tb["vivos"] == 200 and len(tb["mesas"]) == 21 and len(tb["alternos"]) == 11
    assert tb["mesas_sala_abiertas"] == 30
    assert cliente.post("/winner/api/torneos", json={"nombre": "x"}, headers=H).status_code == 403
    entrada = next(a for m in tb["mesas"] for a in m["asientos"] if a["entrada_id"])["entrada_id"]
    assert cliente.post(f"/winner/api/entradas/{entrada}/eliminar", json={}, headers=H).get_json()["estado"] == "reservado"


def test_api_jugador_ingreso_y_saldo(cliente):
    assert cliente.post("/winner/api/jugador/codigo", json={"celular": "3000000150"}, headers=H).status_code == 200
    sms = cliente.get("/winner/api/demo/sms").get_json()[0]["texto"]
    codigo = "".join(c for c in sms if c.isdigit())[:6]
    r = cliente.post("/winner/api/jugador/ingresar", json={"celular": "3000000150", "codigo": codigo}, headers=H)
    assert r.status_code == 409  # primer ingreso: debe crear PIN
    r = cliente.post("/winner/api/jugador/ingresar", json={"celular": "3000000150", "codigo": codigo, "pin_nuevo": "4826"}, headers=H)
    assert r.status_code == 200
    inicio = cliente.get("/winner/api/jugador/inicio", headers=H).get_json()
    assert inicio["saldo"] > 0 and inicio["torneos"][0]["estado"] == "sentado"
    assert cliente.get("/winner/api/torneos", headers=H).status_code == 401  # la sesión de jugador no sirve para el panel
    assert cliente.get("/winner/api/publico/3").get_json()["vivos"] == 200


def test_pantallas_cargan(cliente):
    for ruta in ("/winner/", "/winner/personal", "/winner/app", "/winner/pantalla/3"):
        assert cliente.get(ruta).status_code == 200
    assert "/winner/pantalla/7" in cliente.get("/winner/").get_data(as_text=True)  # inicio enlaza al torneo sencillo


def test_no_finaliza_con_mas_vivos_que_premios(db, staff):
    t = nuevo_torneo(db, staff)
    T.abrir_mesas(db, staff["floor"], t, 1)
    for i in range(3):
        T.inscribir(db, t, nuevo_jugador(db, staff, i, celular=False, saldo=100_000), origen="caja",
                    personal_id=staff["cajero"], sentar_ya=True)
    T.iniciar_torneo(db, staff["floor"], t)
    premios.definir_tabla(db, staff["director"], t, [{"posicion": 1, "monto": 300_000}])
    with pytest.raises(ErrorNegocio, match="Aún quedan 3"):
        premios.finalizar(db, staff["director"], t)


# --- Eventos de varios días, torneos simultáneos y satélites ----------------------------------------

from winner import eventos  # noqa: E402


def _jugar_hasta(db, staff, tid, quedan, rnd):
    """Elimina al azar hasta que queden ``quedan`` jugadores sentados (sin recompras)."""
    while db.valor("SELECT COUNT(*) FROM entradas WHERE torneo_id = ? AND estado = 'sentado'", (tid,)) > quedan:
        vivos = db.todos("SELECT id FROM entradas WHERE torneo_id = ? AND estado = 'sentado'", (tid,))
        T.eliminar(db, staff["crupier"], rnd.choice(vivos)["id"])
        vencer_todo(db)
        for s in T.sugerencias(db, tid):
            if s["tipo"] == "cerrar_mesa":
                T.cerrar_mesa(db, staff["floor"], tid, s["mesa_id"])
        T.balancear(db, staff["floor"], tid)


def test_evento_cinco_dias_clasificatorios_y_final(db, staff):
    rnd = random.Random(3)
    final = T.crear_torneo(db, staff["director"], dict(nombre="Gran Final", fecha="2026-12-10", buyin=500_000,
                                                        stack_inicial=40_000, tipo="final"))
    dias = [T.crear_torneo(db, staff["director"], dict(nombre=f"Día 1{letra}", fecha=f"2026-12-0{i + 1}", buyin=500_000,
                                                        stack_inicial=40_000, tipo="dia1", principal_id=final,
                                                        modo_clasifica="porcentaje", clasifica_valor=20))
            for i, letra in enumerate("ABCDE")]
    jugadores = [nuevo_jugador(db, staff, i, celular=False) for i in range(100)]
    doble = jugadores[0]  # juega dos días
    stacks_doble = []
    for n, dia in enumerate(dias):
        T.abrir_mesas(db, staff["floor"], dia, 3)
        participantes = jugadores[n * 20:(n + 1) * 20] + ([doble] if n == 1 else [])
        for j in participantes:
            T.inscribir(db, dia, j, origen="caja", personal_id=staff["cajero"], pago="efectivo")
        T.iniciar_torneo(db, staff["floor"], dia)
        T.sentar_inscritos(db, staff["floor"], dia)
        objetivo = eventos.objetivo_clasificados(db, T.torneo(db, dia))
        assert objetivo == math.ceil(len(participantes) * 0.2)
        # el jugador doble sobrevive siempre para probar la regla del stack mayor
        while db.valor("SELECT COUNT(*) FROM entradas WHERE torneo_id = ? AND estado = 'sentado'", (dia,)) > objetivo:
            vivos = db.todos("SELECT id FROM entradas WHERE torneo_id = ? AND estado = 'sentado' AND jugador_id != ?",
                             (dia, doble))
            T.eliminar(db, staff["crupier"], rnd.choice(vivos)["id"])
            for s in T.sugerencias(db, dia):
                if s["tipo"] == "cerrar_mesa":
                    T.cerrar_mesa(db, staff["floor"], dia, s["mesa_id"])
        assert "cerrar_dia" in [s["tipo"] for s in T.sugerencias(db, dia)]
        e_doble = db.uno("SELECT * FROM entradas WHERE torneo_id = ? AND jugador_id = ?", (dia, doble))
        if e_doble:
            nuevo = 90_000 if n == 0 else 55_000
            T.registrar_conteo(db, staff["floor"], e_doble["id"], nuevo)
            stacks_doble.append(nuevo)
        eventos.cerrar_dia(db, staff["director"], dia)
        if n < 4:
            with pytest.raises(ErrorNegocio, match="Faltan días por cerrar"):
                eventos.traer_clasificados(db, staff["director"], final)
    assert stacks_doble == [90_000, 55_000]
    vigentes = eventos.clasificados_vigentes(db, final)
    # 20 % de cada día (el Día 1B tuvo 21 entradas → 5): 21 clasificaciones, pero el jugador doble cuenta una vez.
    assert db.valor("SELECT COUNT(*) FROM clasificados WHERE principal_id = ?", (final,)) == 21
    assert len(vigentes) == 20
    assert next(v for v in vigentes if v["jugador_id"] == doble)["fichas"] == 90_000  # se queda con el mayor
    assert len({v["jugador_id"] for v in vigentes}) == len(vigentes)
    with pytest.raises(ErrorNegocio, match="se entra clasificando"):
        T.inscribir(db, final, jugadores[50], origen="caja", personal_id=staff["cajero"], pago="efectivo")
    assert eventos.traer_clasificados(db, staff["director"], final) == len(vigentes)
    assert eventos.traer_clasificados(db, staff["director"], final) == 0  # no duplica
    T.abrir_mesas(db, staff["floor"], final, 3)
    T.iniciar_torneo(db, staff["floor"], final)
    T.sentar_inscritos(db, staff["floor"], final)
    fichas = T.resumen_fichas(db, final)
    assert fichas["diferencia"] == 0 and fichas["esperado"] == sum(v["fichas"] for v in vigentes)
    resumen = eventos.resumen_evento(db, final)
    assert resumen["entradas_totales"] == 101 and resumen["recaudo_dinero"] == 101 * 500_000
    premios.definir_tabla(db, staff["director"], final, [{"posicion": 1, "monto": 30_000_000},
                                                         {"posicion": 2, "monto": 20_000_000}])
    _jugar_hasta(db, staff, final, 1, rnd)
    p = premios.finalizar(db, staff["director"], final)
    assert len(p["detalle"]) == 2
    with pytest.raises(ErrorNegocio, match="no pagan premios"):
        premios.definir_tabla(db, staff["director"], dias[0], [{"posicion": 1, "monto": 1}])


def test_cerrar_dia_exige_reservas_y_alternos_resueltos(db, staff):
    final = T.crear_torneo(db, staff["director"], dict(nombre="F", fecha="2026-12-10", buyin=1, stack_inicial=1, tipo="final"))
    dia = T.crear_torneo(db, staff["director"], dict(nombre="1A", fecha="2026-12-01", buyin=100_000, stack_inicial=10_000,
                                                      tipo="dia1", principal_id=final, reentrada_max=1,
                                                      modo_clasifica="nivel", clasifica_valor=10))
    T.abrir_mesas(db, staff["floor"], dia, 1)
    es = [T.inscribir(db, dia, nuevo_jugador(db, staff, i, celular=False), origen="caja", personal_id=staff["cajero"],
                      pago="efectivo", sentar_ya=True) for i in range(3)]
    T.iniciar_torneo(db, staff["floor"], dia)
    T.eliminar(db, staff["crupier"], es[0]["id"])  # queda en reserva (puede re-entrar)
    with pytest.raises(ErrorNegocio, match="decidiendo si recompran"):
        eventos.cerrar_dia(db, staff["director"], dia)
    vencer_todo(db)
    with pytest.raises(ErrorNegocio, match="Cerrar día"):
        premios.finalizar(db, staff["director"], dia)
    assert eventos.cerrar_dia(db, staff["director"], dia)["clasificados"] == 2
    assert T.torneo(db, dia)["estado"] == "finalizado"
    with pytest.raises(ErrorNegocio, match="ya clasificó|no está abierto|cerrada"):
        T.inscribir(db, dia, es[1]["jugador_id"], origen="caja", personal_id=staff["cajero"], pago="efectivo")


def test_torneos_simultaneos_comparten_las_30_mesas_fisicas(db, staff):
    a = nuevo_torneo(db, staff, nombre="Principal")
    b = nuevo_torneo(db, staff, nombre="Satélite nocturno")
    T.abrir_mesas(db, staff["floor"], a, 20)
    T.abrir_mesas(db, staff["floor"], b, 10)
    numeros = lambda t: sorted(m["numero"] for m in db.todos("SELECT numero FROM mesas WHERE torneo_id = ? AND estado = 'abierta'", (t,)))
    assert numeros(a) == list(range(1, 21)) and numeros(b) == list(range(21, 31))
    with pytest.raises(ErrorNegocio, match="La sala tiene 30 mesas"):
        T.abrir_mesas(db, staff["floor"], b, 1)
    mesa5 = db.valor("SELECT id FROM mesas WHERE torneo_id = ? AND numero = 5", (a,))
    T.cerrar_mesa(db, staff["floor"], a, mesa5)
    T.abrir_mesas(db, staff["floor"], b, 1)
    assert 5 in numeros(b) and 5 not in numeros(a)  # la mesa física 5 pasó al otro torneo
    mesa5b = db.valor("SELECT id FROM mesas WHERE torneo_id = ? AND numero = 5", (b,))
    T.cerrar_mesa(db, staff["floor"], b, mesa5b)
    T.abrir_mesas(db, staff["floor"], a, 1)  # el torneo A vuelve a abrir su mesa 5 (se reabre, no se duplica)
    assert 5 in numeros(a)
    mapa = T.mapa_sala(db)
    assert len(mapa) == 30 and all(m["torneo_id"] for m in mapa)
    # Un jugador puede estar inscrito en dos torneos simultáneos, uno por torneo.
    j = nuevo_jugador(db, staff, 1, saldo=500_000)
    T.inscribir(db, a, j, origen="caja", personal_id=staff["cajero"])
    T.inscribir(db, b, j, origen="caja", personal_id=staff["cajero"])


def test_satelite_premia_tickets_y_el_ticket_sirve_en_un_dia_clasificatorio(db, staff):
    final = T.crear_torneo(db, staff["director"], dict(nombre="Main Event", fecha="2026-12-10", buyin=600_000,
                                                        stack_inicial=40_000, tipo="final"))
    dia = T.crear_torneo(db, staff["director"], dict(nombre="Día 1A", fecha="2026-12-01", buyin=600_000,
                                                      stack_inicial=40_000, tipo="dia1", principal_id=final,
                                                      modo_clasifica="jugadores", clasifica_valor=10))
    sat = T.crear_torneo(db, staff["director"], dict(nombre="Satélite", fecha="2026-11-20", buyin=150_000,
                                                      stack_inicial=10_000, tipo="satelite", satelite_de=final))
    with pytest.raises(ErrorNegocio, match="Solo los satélites"):
        premios.definir_tabla(db, staff["director"], final, [{"posicion": 1, "tipo": "ticket"}])
    T.abrir_mesas(db, staff["floor"], sat, 1)
    js = [nuevo_jugador(db, staff, i, celular=False, saldo=150_000) for i in range(6)]
    es = [T.inscribir(db, sat, j, origen="caja", personal_id=staff["cajero"], sentar_ya=True) for j in js]
    T.iniciar_torneo(db, staff["floor"], sat)
    premios.definir_tabla(db, staff["director"], sat, [{"posicion": 1, "tipo": "ticket"}, {"posicion": 2, "tipo": "ticket"},
                                                       {"posicion": 3, "monto": 300_000}])
    assert [f["monto"] for f in premios.tabla(db, sat)] == [600_000, 600_000, 300_000]
    for e in es[:4]:
        T.eliminar(db, staff["crupier"], e["id"])
    # El ganador ya tenía un ticket: el sistema lo advierte para pasar el cupo al siguiente si así es la regla.
    eventos.crear_ticket(db, staff["director"], js[5], final, None, 600_000)
    p = premios.finalizar(db, staff["director"], sat)
    assert any("ya tiene un ticket" in a for a in p["advertencias"])
    with pytest.raises(ErrorNegocio, match="es un ticket"):
        premios.ajustar(db, staff["director"], sat, [{"posicion": 1, "monto_final": 500_000, "motivo": "x"},
                                                     {"posicion": 3, "monto_final": 400_000, "motivo": "x"}])
    # Se pasa el cupo al siguiente: el que ya tenía ticket cobra el 3er puesto en dinero.
    pos_doble = next(d["posicion"] for d in p["detalle"] if d["jugador_id"] == js[5])
    tercero = next(d["jugador_id"] for d in p["detalle"] if d["posicion"] == 3)
    premios.ajustar(db, staff["director"], sat, [{"posicion": pos_doble, "jugador_id": tercero, "motivo": "Ya tenía ticket"},
                                                 {"posicion": 3, "jugador_id": js[5], "motivo": "Ya tenía ticket"}])
    premios.confirmar(db, staff["director"], sat)
    premios.acreditar(db, staff["director"], sat)
    assert tercero == js[3]
    assert caja.saldo(db, js[5]) == 300_000 and caja.saldo(db, js[3]) == 0
    assert len(eventos.tickets_jugador(db, js[3])) == 1 and len(eventos.tickets_jugador(db, js[4])) == 1
    # El ticket (destino: la final) sirve para entrar al Día 1A sin mover dinero.
    antes = db.valor("SELECT COUNT(*) FROM movimientos")
    e = T.inscribir(db, dia, js[3], origen="caja", personal_id=staff["cajero"], pago="ticket")
    assert e["pago"] == "ticket" and db.valor("SELECT COUNT(*) FROM movimientos") == antes
    assert not eventos.tickets_jugador(db, js[3])
    with pytest.raises(ErrorNegocio, match="no tiene un ticket"):
        T.inscribir(db, dia, js[0], origen="caja", personal_id=staff["cajero"], pago="ticket")
    assert eventos.resumen_evento(db, final)["entradas_con_ticket"] == 1


def test_forma_de_clasificar_la_decide_el_administrador(db, staff):
    final = T.crear_torneo(db, staff["director"], dict(nombre="F", fecha="2026-12-10", buyin=1, stack_inicial=1, tipo="final"))
    base = dict(fecha="2026-12-01", buyin=100_000, stack_inicial=10_000, tipo="dia1", principal_id=final)
    with pytest.raises(ErrorNegocio, match="cómo se decide"):
        T.crear_torneo(db, staff["director"], dict(nombre="1A", **base))
    with pytest.raises(ErrorNegocio, match="Valor inválido"):
        T.crear_torneo(db, staff["director"], dict(nombre="1A", modo_clasifica="porcentaje", clasifica_valor=150, **base))
    dia = T.crear_torneo(db, staff["director"], dict(nombre="1A", modo_clasifica="nivel", clasifica_valor=8, **base))
    T.abrir_mesas(db, staff["floor"], dia, 2)
    for i in range(12):
        T.inscribir(db, dia, nuevo_jugador(db, staff, i, celular=False), origen="caja", personal_id=staff["cajero"],
                    pago="efectivo", sentar_ya=True)
    T.iniciar_torneo(db, staff["floor"], dia)
    cierre = lambda: [s for s in T.sugerencias(db, dia) if s["tipo"] == "cerrar_dia"]  # noqa: E731
    T.actualizar_torneo(db, staff["floor"], dia, {"nivel": 7})
    assert not cierre()
    T.actualizar_torneo(db, staff["floor"], dia, {"nivel": 8})
    assert "último nivel" in cierre()[0]["texto"]
    # Se puede cambiar durante el día: ahora clasifican 10 jugadores.
    T.actualizar_torneo(db, staff["director"], dia, {"modo_clasifica": "jugadores", "clasifica_valor": 10})
    assert not cierre()
    vivos = db.todos("SELECT id FROM entradas WHERE torneo_id = ? AND estado = 'sentado'", (dia,))
    T.eliminar(db, staff["crupier"], vivos[0]["id"])
    T.eliminar(db, staff["crupier"], vivos[1]["id"])
    assert "Quedan 10" in cierre()[0]["texto"]


def test_ticket_repetido_no_se_devuelve_pero_se_puede_vender(db, staff):
    final = T.crear_torneo(db, staff["director"], dict(nombre="Main", fecha="2026-12-10", buyin=600_000,
                                                        stack_inicial=40_000, tipo="final"))
    dia = T.crear_torneo(db, staff["director"], dict(nombre="1A", fecha="2026-12-01", buyin=600_000, stack_inicial=40_000,
                                                      tipo="dia1", principal_id=final, modo_clasifica="nivel", clasifica_valor=10))
    vendedor = nuevo_jugador(db, staff, 1)
    comprador = nuevo_jugador(db, staff, 2)
    eventos.crear_ticket(db, staff["director"], vendedor, final, None, 600_000)
    kid = eventos.crear_ticket(db, staff["director"], vendedor, final, None, 600_000)  # ganó un segundo cupo
    saldo_antes = caja.saldo(db, vendedor)
    codigo = eventos.solicitar_codigo_traspaso(db, staff["cajero"], kid)
    numero_comprador = caja.jugador(db, comprador)["numero_caja"]
    with pytest.raises(ErrorNegocio, match="documento no corresponde"):
        eventos.traspasar_ticket(db, staff["cajero"], kid, numero_comprador, "123456789", codigo)
    with pytest.raises(ErrorNegocio, match="mismo dueño"):
        eventos.traspasar_ticket(db, staff["cajero"], kid, caja.jugador(db, vendedor)["numero_caja"], "500000001", codigo)
    with pytest.raises(ErrorNegocio, match="Código incorrecto"):
        eventos.traspasar_ticket(db, staff["cajero"], kid, numero_comprador, "500000001", "000000")
    r = eventos.traspasar_ticket(db, staff["cajero"], kid, numero_comprador.lower(), "500000001", codigo)
    assert r["a"] == numero_comprador
    assert caja.saldo(db, vendedor) == saldo_antes  # el casino no mueve dinero
    assert len(eventos.tickets_jugador(db, vendedor)) == 1 and len(eventos.tickets_jugador(db, comprador)) == 1
    e = T.inscribir(db, dia, comprador, origen="caja", personal_id=staff["cajero"], pago="ticket")
    assert e["pago"] == "ticket"
    with pytest.raises(ErrorNegocio, match="disponible"):  # ya usado: no se puede volver a traspasar
        eventos.solicitar_codigo_traspaso(db, staff["cajero"], kid)
    assert db.valor("SELECT COUNT(*) FROM auditoria WHERE accion = 'traspaso_ticket'") == 1


def test_demo_practica_desde_cero_y_reinicio(monkeypatch, tmp_path):
    monkeypatch.setenv("WINNER_DEMO", "1")  # «1» = práctica desde cero
    from winner.web import iniciar
    app = Flask(__name__)
    base = iniciar(app, str(tmp_path / "p.db"))
    c = app.test_client()
    assert base.valor("SELECT COUNT(*) FROM jugadores") == 30
    t = base.uno("SELECT * FROM torneos")
    assert t["estado"] == "programado" and not base.valor("SELECT COUNT(*) FROM mesas")
    assert not base.valor("SELECT COUNT(*) FROM entradas")
    assert "Práctica desde cero" in c.get("/winner/").get_data(as_text=True)
    c.post("/winner/api/personal/ingresar", json={"usuario": "floor", "clave": "winner2026"}, headers=H)
    assert c.post(f"/winner/api/torneos/{t['id']}/mesas", json={"cantidad": 3}, headers=H).status_code == 200
    assert c.post("/winner/api/demo/reiniciar", json={}, headers=H).status_code == 403  # solo el admin
    c.post("/winner/api/personal/ingresar", json={"usuario": "admin", "clave": "winner2026"}, headers=H)
    assert c.post("/winner/api/demo/reiniciar", json={"escenario": "practica"}, headers=H).status_code == 200
    assert not base.valor("SELECT COUNT(*) FROM mesas") and base.valor("SELECT COUNT(*) FROM jugadores") == 30
    assert c.get("/winner/api/torneos", headers=H).status_code == 401  # el reinicio cierra las sesiones
    c.post("/winner/api/personal/ingresar", json={"usuario": "admin", "clave": "winner2026"}, headers=H)
    assert c.post("/winner/api/demo/reiniciar", json={"escenario": "completo"}, headers=H).status_code == 200
    assert base.valor("SELECT COUNT(*) FROM jugadores") == 300


def test_recorrido_completo_de_practica(db, staff):
    """El recorrido de la guía: 3 mesas, 30 inscritos (3 alternos), eliminaciones, cierre de mesa y premios."""
    from winner.demo import _practica
    db2 = BaseDatos(":memory:")
    _practica(db2)
    t = db2.valor("SELECT id FROM torneos")
    floor = db2.valor("SELECT id FROM personal WHERE usuario = 'floor'")
    director = db2.valor("SELECT id FROM personal WHERE usuario = 'director'")
    cajero = db2.valor("SELECT id FROM personal WHERE usuario = 'cajero'")
    T.abrir_mesas(db2, floor, t, 3)
    jugadores = [r["id"] for r in db2.todos("SELECT id FROM jugadores ORDER BY id")]
    e = T.inscribir(db2, t, jugadores[0], origen="caja", personal_id=cajero, pago="saldo")
    assert T.sentar_por_qr(db2, e["codigo_qr"], floor)["a_mesa"] in (1, 2, 3)
    for j in jugadores[1:]:
        T.inscribir(db2, t, j, origen="caja", personal_id=cajero, pago="saldo")
    r = T.sentar_inscritos(db2, floor, t)
    assert sum(1 for m in r if m.get("alterno")) == 3
    T.iniciar_torneo(db2, floor, t)
    rnd = random.Random(5)
    while True:
        vivos = db2.todos("SELECT id FROM entradas WHERE torneo_id = ? AND estado = 'sentado'", (t,))
        if len(vivos) + len(T.alternos(db2, t)) <= 1:
            break
        T.eliminar(db2, floor, rnd.choice(vivos)["id"])
        vencer_todo(db2)
        T.sentar_alternos(db2, floor, t)
        for s in T.sugerencias(db2, t):
            if s["tipo"] == "cerrar_mesa":
                T.cerrar_mesa(db2, floor, t, s["mesa_id"])
        T.balancear(db2, floor, t)
    p = premios.finalizar(db2, director, t)
    premios.confirmar(db2, director, t)
    premios.acreditar(db2, director, t)
    ganador = p["detalle"][0]["jugador_id"]
    assert caja.saldo(db2, ganador) == 1_000_000 - 100_000 + 1_200_000


def test_no_sugiere_cerrar_mesas_antes_de_empezar(db, staff):
    t = nuevo_torneo(db, staff)
    T.abrir_mesas(db, staff["floor"], t, 3)
    assert not [s for s in T.sugerencias(db, t) if s["tipo"] == "cerrar_mesa"]
    for i in range(5):
        T.inscribir(db, t, nuevo_jugador(db, staff, i, celular=False), origen="caja", personal_id=staff["cajero"],
                    pago="efectivo", sentar_ya=True)
    assert not [s for s in T.sugerencias(db, t) if s["tipo"] == "cerrar_mesa"]
    T.iniciar_torneo(db, staff["floor"], t)
    assert [s for s in T.sugerencias(db, t) if s["tipo"] == "cerrar_mesa"]  # en curso, 5 jugadores caben en 1 mesa


def test_inscripcion_manual_con_documento_exige_confirmar_pago(db, staff):
    t = nuevo_torneo(db, staff)
    T.abrir_mesas(db, staff["floor"], t, 1)
    base = dict(tipo_doc="CC", documento="1.020.304.050", documento_verificado=True)
    # Sin confirmar el pago no se registra nada (ni la caja nueva).
    with pytest.raises(ErrorNegocio, match="Confirme que recibió el pago"):
        T.inscripcion_manual(db, staff["cajero"], t, nombre="Pedro Gómez", pago="efectivo", **base)
    with pytest.raises(ErrorNegocio, match="comprobante"):
        T.inscripcion_manual(db, staff["cajero"], t, nombre="Pedro Gómez", pago="datafono", pago_confirmado=True,
                             referencia="", **base)
    with pytest.raises(ErrorNegocio, match="documento original"):
        T.inscripcion_manual(db, staff["cajero"], t, nombre="Pedro Gómez", pago="efectivo", pago_confirmado=True,
                             tipo_doc="CC", documento="1020304050")
    with pytest.raises(ErrorNegocio, match="nombre completo"):
        T.inscripcion_manual(db, staff["cajero"], t, pago="efectivo", pago_confirmado=True, **base)
    assert not db.valor("SELECT COUNT(*) FROM jugadores") and not db.valor("SELECT COUNT(*) FROM entradas")
    # Jugador nuevo, paga con datáfono: se abre su caja y queda inscrito y sentado.
    r = T.inscripcion_manual(db, staff["cajero"], t, nombre="Pedro Gómez", pago="datafono", pago_confirmado=True,
                             referencia="AUT-123456", sentar_ya=True, **base)
    assert r["nuevo"] and r["asiento"]["mesa"] == 1 and r["jugador"]["documento"] == "****4050"
    jid = r["jugador"]["id"]
    movs = caja.movimientos(db, jid)
    assert [(m["tipo"], m["medio"]) for m in movs] == [("inscripcion", "ventanilla"), ("deposito", "datafono")]
    assert "AUT-123456" in movs[1]["descripcion"] and caja.saldo(db, jid) == 0
    cuadre = reportes.cuadre_diario(db, caja.dia_bogota())
    assert cuadre["datafono"] == 100_000 and cuadre["efectivo_esperado"] == 0  # el datáfono no es efectivo en caja
    # Segunda vez con el mismo documento: lo encuentra y no lo deja inscribirse dos veces.
    info = T.consultar_documento(db, t, "CC", "1020304050")
    assert info["existe"] and info["ya_inscrito"]
    with pytest.raises(ErrorNegocio, match="ya está inscrito"):
        T.inscripcion_manual(db, staff["cajero"], t, pago="efectivo", pago_confirmado=True, **base)
    # Pasaporte con letras.
    r2 = T.inscripcion_manual(db, staff["cajero"], t, tipo_doc="PA", documento="ab-123456", nombre="John Smith",
                              pago="efectivo", pago_confirmado=True, documento_verificado=True)
    assert r2["jugador"]["tipo_doc"] == "PA" and T.consultar_documento(db, t, "PA", "AB123456")["existe"]
    # Con saldo de su caja no hace falta confirmar efectivo, pero el saldo debe alcanzar.
    caja.depositar(db, staff["cajero"], jid, 50_000)
    otro = nuevo_torneo(db, staff, nombre="Otro")
    with pytest.raises(ErrorNegocio, match="Saldo insuficiente"):
        T.inscripcion_manual(db, staff["cajero"], otro, pago="saldo", **base)


def test_api_cobro_en_ventanilla_exige_confirmacion(cliente):
    cliente.post("/winner/api/personal/ingresar", json={"usuario": "cajero", "clave": "winner2026"}, headers=H)
    jugador = cliente.get("/winner/api/jugadores?q=Andr", headers=H).get_json()[0]["id"]
    r = cliente.post("/winner/api/torneos/4/inscribir", json={"jugador_id": jugador, "pago": "efectivo"}, headers=H)
    assert r.status_code == 400 and "Confirme" in r.get_json()["error"]
    info = cliente.get("/winner/api/torneos/7/documento?tipo=CC&numero=99999999", headers=H).get_json()
    assert info["existe"] is False and info["buyin"] == 200_000


# --- Reloj del torneo y estructura de ciegas -------------------------------------------------------

from winner import reloj  # noqa: E402


def test_estructura_de_ciegas_en_texto():
    filas = reloj.leer_texto("100/200 20\n150/300 20\n# comentario\n\n200/400/400 25 min\ndescanso 15\n1.000/2.000/2.000 30")
    assert [f["tipo"] for f in filas] == ["nivel", "nivel", "nivel", "descanso", "nivel"]
    assert filas[2] == {"tipo": "nivel", "ciega_chica": 200, "ciega_grande": 400, "ante": 400, "minutos": 25}
    assert filas[4]["ciega_grande"] == 2000
    assert reloj.leer_texto(reloj.a_texto(filas)) == filas
    with pytest.raises(ErrorNegocio, match="Línea 2"):
        reloj.leer_texto("100/200 20\nciegas raras")


def test_calculo_del_reloj():
    filas = reloj.leer_texto("100/200 20\n200/400 20\ndescanso 10\n300/600/600 20")
    p = reloj.calcular(filas, 0)
    assert p["nivel"] == 1 and p["restante"] == 1200 and p["hasta_descanso"] == 2400
    p = reloj.calcular(filas, 25 * 60)
    assert p["nivel"] == 2 and p["restante"] == 15 * 60 and p["proximo_nivel"]["ciega_grande"] == 600
    p = reloj.calcular(filas, 45 * 60)
    assert p["actual"]["tipo"] == "descanso" and p["nivel"] == 2 and p["restante"] == 5 * 60 and p["hasta_descanso"] is None
    p = reloj.calcular(filas, 99 * 60)
    assert p["nivel"] == 3 and p["restante"] == 0 and p["terminado"]


def test_controles_del_reloj(db, staff):
    t = nuevo_torneo(db, staff)
    with pytest.raises(ErrorNegocio, match="estructura"):
        reloj.controlar(db, staff["floor"], t, "iniciar")
    reloj.definir_estructura(db, staff["director"], t, reloj.plantilla_estandar(minutos=20, descanso_cada=2))
    with pytest.raises(ErrorNegocio, match="Inicie el torneo"):
        reloj.controlar(db, staff["floor"], t, "iniciar")
    T.abrir_mesas(db, staff["floor"], t, 1)
    T.iniciar_torneo(db, staff["floor"], t)
    e = reloj.controlar(db, staff["floor"], t, "iniciar")
    assert e["corriendo"] and e["nivel"] == 1
    # Simula 25 minutos jugados: va en el nivel 2 y el torneo lo sabe (para la regla de nivel fijo).
    db.conexion().execute("UPDATE torneos SET reloj_desde = NULL, reloj_acumulado = 1500 WHERE id = ?", (t,))
    e = reloj.estado(db, t)
    assert e["nivel"] == 2 and not e["corriendo"] and T.torneo(db, t)["nivel"] == 2
    e = reloj.controlar(db, staff["floor"], t, "siguiente")  # pasa al descanso (cada 2 niveles)
    assert e["actual"]["tipo"] == "descanso" and e["restante"] == 600
    e = reloj.controlar(db, staff["floor"], t, "siguiente")
    assert e["nivel"] == 3 and e["restante"] == 1200
    e = reloj.controlar(db, staff["floor"], t, "ajustar", 60)  # al empezar el nivel no puede pasarse del tiempo completo
    assert e["nivel"] == 3 and e["restante"] == 1200
    e = reloj.controlar(db, staff["floor"], t, "ajustar", -180)  # −3 min
    assert e["restante"] == 1020
    e = reloj.controlar(db, staff["floor"], t, "ajustar", 60)  # +1 min
    assert e["restante"] == 1080
    e = reloj.controlar(db, staff["floor"], t, "anterior")
    assert e["actual"]["tipo"] == "descanso"
    e = reloj.controlar(db, staff["floor"], t, "pausar")
    assert not e["corriendo"]
    assert db.valor("SELECT COUNT(*) FROM auditoria WHERE accion = 'reloj'") == 8


def test_pantalla_publica_con_reloj_y_premios(cliente):
    r = cliente.get("/winner/api/publico/7").get_json()  # torneo diario de la demo completa
    assert r["reloj"]["corriendo"] and r["reloj"]["nivel"] == 3
    assert r["bolsa"] == 7_200_000 and len(r["premios"]) == 5
    assert r["fichas_totales"] == 45 * 20_000 and r["promedio"] == 20_000 and r["vivos"] == 45
    assert "saldo" not in str(r) and "documento" not in str(r)  # la pantalla no muestra datos privados
    cliente.post("/winner/api/personal/ingresar", json={"usuario": "floor", "clave": "winner2026"}, headers=H)
    assert cliente.post("/winner/api/torneos/7/reloj", json={"accion": "pausar"}, headers=H).get_json()["corriendo"] is False
    assert cliente.put("/winner/api/torneos/7/estructura", json={"texto": "100/200 20"}, headers=H).status_code == 403
