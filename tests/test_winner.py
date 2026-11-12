"""Pruebas de la plataforma Winner (torneos, mesas, caja y seguridad)."""
import math
import random
import sqlite3

import pytest
from flask import Flask

from winner import caja, premios
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


def test_torneo_completo_balanceo_y_cierre_de_mesas(db, staff):
    """Aforo máximo de Winner (300) hasta el ganador: nunca más de 1 de diferencia entre mesas y mesas mínimas."""
    t = nuevo_torneo(db, staff, reentrada_max=1, recompra_max=1, recompra_precio=50_000, recompra_fichas=5_000)
    T.abrir_mesas(db, staff["floor"], t, 34)
    for i in range(300):
        T.inscribir(db, t, nuevo_jugador(db, staff, i, celular=False), origen="caja",
                    personal_id=staff["cajero"], pago="efectivo")
    T.iniciar_torneo(db, staff["floor"], t)
    T.sentar_inscritos(db, staff["floor"], t)
    rnd = random.Random(7)
    while True:
        sentados = db.todos("SELECT id FROM entradas WHERE torneo_id = ? AND estado = 'sentado'", (t,))
        if len(sentados) <= 1:
            break
        T.eliminar(db, staff["crupier"], rnd.choice(sentados)["id"])
        vencer_todo(db)
        for s in T.sugerencias(db, t):
            if s["tipo"] == "cerrar_mesa":
                T.cerrar_mesa(db, staff["floor"], t, s["mesa_id"])
        T.balancear(db, staff["floor"], t)
        c = conteo_mesas(db, t)
        assert max(c) - min(c) <= 1
        assert len(c) == max(1, math.ceil(sum(c) / 9))
    motivos = {h["motivo"] for h in T.historial_asientos(db, t, 100_000)}
    assert {"sentado", "balanceo", "cierre_mesa"} <= motivos


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
    monkeypatch.setenv("WINNER_DEMO", "1")
    from winner.web import iniciar
    app = Flask(__name__)
    iniciar(app, str(tmp_path / "w.db"))
    return app.test_client()


H = {"X-Winner": "1"}


def test_api_roles_y_tablero(cliente):
    assert cliente.post("/winner/api/personal/ingresar", json={"usuario": "crupier", "clave": "x"}, headers=H).status_code == 401
    assert cliente.post("/winner/api/personal/ingresar", json={"usuario": "crupier", "clave": "winner2026"}).status_code == 403
    assert cliente.post("/winner/api/personal/ingresar", json={"usuario": "crupier", "clave": "winner2026"}, headers=H).status_code == 200
    tb = cliente.get("/winner/api/torneos/1/tablero", headers=H).get_json()
    assert tb["vivos"] == 36 and len(tb["mesas"]) == 5
    assert cliente.post("/winner/api/torneos", json={"nombre": "x"}, headers=H).status_code == 403
    entrada = next(a for m in tb["mesas"] for a in m["asientos"] if a["entrada_id"])["entrada_id"]
    assert cliente.post(f"/winner/api/entradas/{entrada}/eliminar", json={}, headers=H).get_json()["estado"] == "reservado"


def test_api_jugador_ingreso_y_saldo(cliente):
    assert cliente.post("/winner/api/jugador/codigo", json={"celular": "3000000005"}, headers=H).status_code == 200
    sms = cliente.get("/winner/api/demo/sms").get_json()[0]["texto"]
    codigo = "".join(c for c in sms if c.isdigit())[:6]
    r = cliente.post("/winner/api/jugador/ingresar", json={"celular": "3000000005", "codigo": codigo}, headers=H)
    assert r.status_code == 409  # primer ingreso: debe crear PIN
    r = cliente.post("/winner/api/jugador/ingresar", json={"celular": "3000000005", "codigo": codigo, "pin_nuevo": "4826"}, headers=H)
    assert r.status_code == 200
    inicio = cliente.get("/winner/api/jugador/inicio", headers=H).get_json()
    assert inicio["saldo"] > 0 and inicio["torneos"][0]["estado"] == "sentado"
    assert cliente.get("/winner/api/torneos", headers=H).status_code == 401  # la sesión de jugador no sirve para el panel
    assert cliente.get("/winner/api/publico/1").get_json()["vivos"] == 36


def test_pantallas_cargan(cliente):
    for ruta in ("/winner/", "/winner/personal", "/winner/app", "/winner/pantalla/1"):
        assert cliente.get(ruta).status_code == 200


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
