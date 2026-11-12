"""Blueprint Flask de Winner: API JSON + pantallas (personal, jugador y pantalla pública de mesas)."""
from __future__ import annotations

import functools

from flask import Blueprint, current_app, g, jsonify, make_response, render_template, request

from . import caja, premios, reportes
from . import seguridad as seg
from . import torneos as T
from .db import BaseDatos, ErrorNegocio

bp = Blueprint("winner", __name__, url_prefix="/winner", template_folder="templates", static_folder="static")

COOKIE_PERSONAL, COOKIE_JUGADOR = "winner_p", "winner_j"


def db() -> BaseDatos:
    return current_app.extensions["winner_db"]


def iniciar(app, ruta_db=None) -> BaseDatos:
    base = BaseDatos(ruta_db)
    app.extensions["winner_db"] = base
    if seg.demo():
        from .demo import sembrar
        sembrar(base)
    app.register_blueprint(bp)
    return base


@bp.errorhandler(ErrorNegocio)
def _error_negocio(e: ErrorNegocio):
    return jsonify(error=e.mensaje), e.codigo


@bp.after_request
def _cabeceras(resp):
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "same-origin"
    resp.headers["X-Frame-Options"] = "DENY"
    return resp


def _cliente():
    return request.headers.get("User-Agent", "")[:200], request.headers.get("X-Forwarded-For", request.remote_addr)


def _datos() -> dict:
    return request.get_json(silent=True) or {}


def _cookie(resp, nombre, valor):
    resp.set_cookie(nombre, valor, httponly=True, samesite="Strict", secure=request.is_secure, path="/winner")
    return resp


def _anti_csrf():
    # Las cookies son SameSite=Strict y además toda escritura debe venir de fetch() con esta cabecera.
    if request.method != "GET" and request.headers.get("X-Winner") != "1":
        raise ErrorNegocio("Solicitud no permitida", 403)


def personal(permiso: str | None = None):
    def deco(f):
        @functools.wraps(f)
        def envoltura(*a, **kw):
            _anti_csrf()
            s = seg.sesion_valida(db(), request.cookies.get(COOKIE_PERSONAL), "personal")
            if not s:
                raise ErrorNegocio("Sesión vencida: ingrese de nuevo", 401)
            g.personal = db().uno("SELECT id, usuario, nombre, rol FROM personal WHERE id = ?", (s["sujeto_id"],))
            if permiso and g.personal["rol"] not in seg.PERMISOS[permiso]:
                raise ErrorNegocio("Su rol no tiene permiso para esta acción", 403)
            return f(*a, **kw)
        return envoltura
    return deco


def jugador_sesion(f):
    @functools.wraps(f)
    def envoltura(*a, **kw):
        _anti_csrf()
        s = seg.sesion_valida(db(), request.cookies.get(COOKIE_JUGADOR), "jugador")
        if not s:
            raise ErrorNegocio("Sesión cerrada por seguridad: ingrese de nuevo", 401)
        g.jugador_id = s["sujeto_id"]
        return f(*a, **kw)
    return envoltura


def _pid():
    return g.personal["id"]


# --- Pantallas ---------------------------------------------------------------------------------

@bp.get("/")
def inicio():
    return render_template("winner_inicio.html", demo=seg.demo())


@bp.get("/personal")
def pantalla_personal():
    return render_template("winner_personal.html", demo=seg.demo())


@bp.get("/app")
def pantalla_jugador():
    return render_template("winner_jugador.html", demo=seg.demo())


@bp.get("/pantalla/<int:torneo_id>")
def pantalla_publica(torneo_id):
    return render_template("winner_pantalla.html", torneo_id=torneo_id)


@bp.get("/api/publico/<int:torneo_id>")
def api_publico(torneo_id):
    """Pantalla de la sala: dónde está sentado cada jugador (solo nombre, mesa y asiento)."""
    tb = T.tablero(db(), torneo_id)
    t = tb["torneo"]
    sentados = sorted(({"nombre": a["nombre"], "mesa": m["numero"], "asiento": a["asiento"]}
                       for m in tb["mesas"] for a in m["asientos"] if a["estado"] == "sentado"),
                      key=lambda x: x["nombre"].lower())
    cambios = [{"nombre": c["nombre"], "de_mesa": c["de_mesa"], "a_mesa": c["a_mesa"], "a_asiento": c["a_asiento"],
                "motivo": c["motivo"], "fecha": c["fecha"]}
               for c in T.historial_asientos(db(), torneo_id, 12) if c["motivo"] in ("balanceo", "cierre_mesa")]
    return jsonify(torneo={"nombre": t["nombre"], "estado": t["estado"], "nivel": t["nivel"]},
                   vivos=tb["vivos"], entradas=tb["entradas_totales"], mesas=len(tb["mesas"]),
                   promedio=T.resumen_fichas(db(), torneo_id)["promedio"], sentados=sentados, cambios=cambios)


# --- Personal: sesión ----------------------------------------------------------------------------

@bp.post("/api/personal/ingresar")
def api_personal_ingresar():
    _anti_csrf()
    d = _datos()
    disp, ip = _cliente()
    p = seg.autenticar_personal(db(), d.get("usuario"), d.get("clave"), disp, ip)
    with db().transaccion():
        token = seg.crear_sesion(db(), "personal", p["id"], disp, ip)
        seg.registrar_acceso(db(), "personal", p["id"], "ingreso", None, disp, ip)
    return _cookie(make_response(jsonify(nombre=p["nombre"], rol=p["rol"])), COOKIE_PERSONAL, token)


@bp.post("/api/personal/salir")
def api_personal_salir():
    _anti_csrf()
    seg.cerrar_sesion(db(), request.cookies.get(COOKIE_PERSONAL))
    resp = make_response(jsonify(ok=True))
    resp.delete_cookie(COOKIE_PERSONAL, path="/winner")
    return resp


@bp.get("/api/personal/yo")
@personal()
def api_yo():
    permisos = [k for k, roles in seg.PERMISOS.items() if g.personal["rol"] in roles]
    return jsonify(**g.personal, permisos=permisos)


# --- Personal: jugadores y caja ------------------------------------------------------------------

@bp.get("/api/jugadores")
@personal()
def api_buscar_jugadores():
    return jsonify(caja.buscar_jugadores(db(), request.args.get("q")))


@bp.post("/api/jugadores")
@personal("caja")
def api_abrir_caja():
    d = _datos()
    r = caja.abrir_caja(db(), _pid(), d.get("nombre"), d.get("documento") or "", d.get("celular") or None,
                        bool(d.get("documento_verificado")))
    r.pop("_codigo", None)
    return jsonify(r)


@bp.get("/api/jugadores/<int:jid>")
@personal()
def api_jugador(jid):
    j = caja.jugador(db(), jid)
    return jsonify(jugador=caja.publico(j), saldo=caja.saldo(db(), jid),
                   movimientos=caja.movimientos(db(), jid, 50) if g.personal["rol"] in seg.PERMISOS["caja"] else [])


@bp.post("/api/jugadores/<int:jid>/celular")
@personal("caja")
def api_enviar_vinculacion(jid):
    caja.enviar_codigo_vinculacion(db(), _pid(), jid, _datos().get("celular") or None)
    return jsonify(ok=True)


@bp.post("/api/jugadores/<int:jid>/vincular")
@personal("caja")
def api_vincular(jid):
    caja.vincular_celular(db(), _pid(), jid, _datos().get("codigo"))
    return jsonify(ok=True)


@bp.post("/api/jugadores/<int:jid>/deposito")
@personal("caja")
def api_deposito(jid):
    return jsonify(movimiento_id=caja.depositar(db(), _pid(), jid, _datos().get("monto")), saldo=caja.saldo(db(), jid))


@bp.post("/api/jugadores/<int:jid>/codigo-retiro")
@personal("caja")
def api_codigo_retiro(jid):
    caja.solicitar_codigo_retiro(db(), _pid(), jid)
    return jsonify(ok=True, tope=db().ajuste("tope_retiro_sin_supervisor"))


@bp.post("/api/jugadores/<int:jid>/retiro")
@personal("caja")
def api_retiro(jid):
    d = _datos()
    mid = caja.retirar(db(), _pid(), jid, d.get("monto"), d.get("documento") or "", d.get("codigo"),
                       d.get("supervisor_usuario") or None, d.get("supervisor_clave") or None)
    return jsonify(movimiento_id=mid, saldo=caja.saldo(db(), jid))


@bp.post("/api/jugadores/<int:jid>/bloquear")
@personal("caja")
def api_bloquear(jid):
    caja.bloquear(db(), _pid(), jid, _datos().get("motivo"))
    return jsonify(ok=True)


@bp.post("/api/jugadores/<int:jid>/desbloquear")
@personal("aprobar")
def api_desbloquear(jid):
    caja.desbloquear(db(), _pid(), jid)
    return jsonify(ok=True)


@bp.post("/api/movimientos/<int:mid>/corregir")
@personal("aprobar")
def api_corregir(mid):
    return jsonify(movimiento_id=caja.corregir(db(), _pid(), mid, _datos().get("motivo")))


# --- Personal: torneos ---------------------------------------------------------------------------

@bp.get("/api/torneos")
@personal()
def api_torneos():
    return jsonify(T.lista_torneos(db()))


@bp.post("/api/torneos")
@personal("director")
def api_crear_torneo():
    return jsonify(id=T.crear_torneo(db(), _pid(), _datos()))


@bp.patch("/api/torneos/<int:tid>")
@personal("floor")
def api_actualizar_torneo(tid):
    d = _datos()
    # El floor solo maneja el reloj de la operación; la configuración la hace el director.
    if g.personal["rol"] == "floor":
        d = {k: v for k, v in d.items() if k in ("nivel", "registro_abierto", "recompras_abiertas", "addon_abierto")}
    T.actualizar_torneo(db(), _pid(), tid, d)
    return jsonify(T.torneo(db(), tid))


@bp.post("/api/torneos/<int:tid>/iniciar")
@personal("floor")
def api_iniciar(tid):
    T.iniciar_torneo(db(), _pid(), tid)
    return jsonify(ok=True)


@bp.get("/api/torneos/<int:tid>/tablero")
@personal()
def api_tablero(tid):
    return jsonify(T.tablero(db(), tid))


@bp.post("/api/torneos/<int:tid>/mesas")
@personal("floor")
def api_abrir_mesas(tid):
    return jsonify(ids=T.abrir_mesas(db(), _pid(), tid, _datos().get("cantidad", 1)))


@bp.post("/api/torneos/<int:tid>/mesas/<int:mid>/cerrar")
@personal("floor")
def api_cerrar_mesa(tid, mid):
    return jsonify(movimientos=T.cerrar_mesa(db(), _pid(), tid, mid))


@bp.post("/api/torneos/<int:tid>/balancear")
@personal("floor")
def api_balancear(tid):
    return jsonify(movimientos=T.balancear(db(), _pid(), tid))


@bp.post("/api/torneos/<int:tid>/sentar-inscritos")
@personal("floor")
def api_sentar_inscritos(tid):
    return jsonify(movimientos=T.sentar_inscritos(db(), _pid(), tid))


@bp.post("/api/torneos/<int:tid>/inscribir")
@personal("caja")
def api_inscribir(tid):
    d = _datos()
    e = T.inscribir(db(), tid, int(d.get("jugador_id")), origen="caja", personal_id=_pid(),
                    pago=d.get("pago", "efectivo"), sentar_ya=bool(d.get("sentar_ya")))
    return jsonify(entrada=e, asiento=_asiento(e["id"]))


@bp.post("/api/qr")
@personal("floor")
def api_sentar_qr():
    return jsonify(T.sentar_por_qr(db(), _datos().get("codigo"), _pid()))


@bp.post("/api/entradas/<int:eid>/sentar")
@personal("floor")
def api_sentar(eid):
    return jsonify(T.sentar(db(), eid, _pid()))


@bp.post("/api/entradas/<int:eid>/eliminar")
@personal("mesas")
def api_eliminar(eid):
    return jsonify(T.eliminar(db(), _pid(), eid))


@bp.post("/api/entradas/<int:eid>/fichas")
@personal("mesas")
def api_fichas(eid):
    T.registrar_conteo(db(), _pid(), eid, _datos().get("fichas"))
    return jsonify(ok=True)


@bp.post("/api/entradas/<int:eid>/recompra")
@personal("caja")
def api_recompra_ventanilla(eid):
    return jsonify(T.recomprar(db(), eid, origen="caja", personal_id=_pid(), pago=_datos().get("pago", "efectivo")))


@bp.post("/api/entradas/<int:eid>/reentrada")
@personal("caja")
def api_reentrada_ventanilla(eid):
    e = T.reentrar(db(), eid, origen="caja", personal_id=_pid(), pago=_datos().get("pago", "efectivo"))
    return jsonify(entrada=e, asiento=_asiento(e["id"]))


@bp.post("/api/entradas/<int:eid>/addon")
@personal("caja")
def api_addon_ventanilla(eid):
    return jsonify(T.addon(db(), eid, origen="caja", personal_id=_pid(), pago=_datos().get("pago", "efectivo")))


@bp.get("/api/torneos/<int:tid>/fichas")
@personal()
def api_resumen_fichas(tid):
    return jsonify(resumen=T.resumen_fichas(db(), tid), historial=T.historial_conteos(db(), tid, 100))


@bp.get("/api/torneos/<int:tid>/historial")
@personal()
def api_historial(tid):
    return jsonify(T.historial_asientos(db(), tid, 300))


@bp.get("/api/torneos/<int:tid>/entradas")
@personal()
def api_entradas(tid):
    return jsonify(db().todos(
        "SELECT e.id, e.numero, e.estado, e.fichas, e.recompras, e.addons, e.asiento, m.numero AS mesa, "
        "j.nombre, j.numero_caja FROM entradas e JOIN jugadores j ON j.id = e.jugador_id "
        "LEFT JOIN mesas m ON m.id = e.mesa_id WHERE e.torneo_id = ? ORDER BY j.nombre, e.numero", (tid,)))


# --- Premios -------------------------------------------------------------------------------------

@bp.get("/api/torneos/<int:tid>/premios")
@personal()
def api_premios(tid):
    return jsonify(tabla=premios.tabla(db(), tid), premiacion=premios.premiacion(db(), tid),
                   posiciones=premios.posiciones(db(), tid)[:50])


@bp.put("/api/torneos/<int:tid>/premios")
@personal("director")
def api_tabla_premios(tid):
    premios.definir_tabla(db(), _pid(), tid, _datos().get("filas", []))
    return jsonify(premios.tabla(db(), tid))


@bp.post("/api/torneos/<int:tid>/finalizar")
@personal("director")
def api_finalizar(tid):
    return jsonify(premios.finalizar(db(), _pid(), tid))


@bp.post("/api/torneos/<int:tid>/premios/ajustar")
@personal("director")
def api_ajustar(tid):
    return jsonify(premios.ajustar(db(), _pid(), tid, _datos().get("filas", [])))


@bp.post("/api/torneos/<int:tid>/premios/confirmar")
@personal("director")
def api_confirmar(tid):
    return jsonify(premios.confirmar(db(), _pid(), tid))


@bp.post("/api/torneos/<int:tid>/premios/acreditar")
@personal("director")
def api_acreditar(tid):
    return jsonify(premios.acreditar(db(), _pid(), tid))


# --- Reportes y administración ------------------------------------------------------------------

@bp.get("/api/reportes/cuadre")
@personal("reportes")
def api_cuadre():
    return jsonify(reportes.cuadre_diario(db(), request.args.get("dia") or caja.dia_bogota()))


@bp.post("/api/reportes/arqueo")
@personal("caja")
def api_arqueo():
    d = _datos()
    return jsonify(reportes.registrar_arqueo(db(), _pid(), d.get("dia") or caja.dia_bogota(),
                                             d.get("efectivo_contado"), d.get("nota")))


@bp.get("/api/reportes/torneo/<int:tid>")
@personal("reportes")
def api_reporte_torneo(tid):
    return jsonify(reportes.resumen_torneo(db(), tid))


@bp.get("/api/auditoria")
@personal("auditoria")
def api_auditoria():
    return jsonify(db().todos("SELECT a.*, p.nombre FROM auditoria a LEFT JOIN personal p ON p.id = a.personal_id "
                              "ORDER BY a.id DESC LIMIT 300"))


@bp.get("/api/personal")
@personal("admin")
def api_lista_personal():
    return jsonify(db().todos("SELECT id, usuario, nombre, rol, activo FROM personal ORDER BY rol, nombre"))


@bp.post("/api/personal")
@personal("admin")
def api_crear_personal():
    d = _datos()
    return jsonify(id=seg.crear_personal(db(), d.get("usuario", ""), d.get("nombre", ""), d.get("rol"),
                                         d.get("clave", ""), _pid()))


@bp.post("/api/personal/<int:pid>/activo")
@personal("admin")
def api_activar_personal(pid):
    activo = 1 if _datos().get("activo") else 0
    with db().transaccion() as c:
        c.execute("UPDATE personal SET activo = ? WHERE id = ?", (activo, pid))
        if not activo:
            c.execute("UPDATE sesiones SET cerrada = 1 WHERE tipo = 'personal' AND sujeto_id = ?", (pid,))
        db().auditar(_pid(), "activar_personal" if activo else "desactivar_personal", "personal", pid)
    return jsonify(ok=True)


@bp.get("/api/ajustes")
@personal("admin")
def api_ajustes():
    return jsonify({r["clave"]: r["valor"] for r in db().todos("SELECT * FROM ajustes")})


@bp.put("/api/ajustes")
@personal("admin")
def api_guardar_ajustes():
    with db().transaccion() as c:
        for k, v in _datos().items():
            if c.execute("SELECT 1 FROM ajustes WHERE clave = ?", (k,)).fetchone():
                c.execute("UPDATE ajustes SET valor = ? WHERE clave = ?", (str(int(v)), k))
                db().auditar(_pid(), "ajuste", "ajustes", None, f"{k}={int(v)}")
    return api_ajustes.__wrapped__()


@bp.get("/api/demo/sms")
def api_demo_sms():
    """Solo en modo demo: muestra los SMS que se habrían enviado."""
    if not seg.demo():
        raise ErrorNegocio("No disponible", 404)
    return jsonify(db().todos("SELECT celular, texto, fecha FROM sms_enviados ORDER BY id DESC LIMIT 10"))


def _asiento(entrada_id):
    return db().uno("SELECT m.numero AS mesa, e.asiento FROM entradas e LEFT JOIN mesas m ON m.id = e.mesa_id "
                    "WHERE e.id = ?", (entrada_id,))


# --- App del jugador -----------------------------------------------------------------------------

@bp.post("/api/jugador/codigo")
def api_jugador_codigo():
    _anti_csrf()
    disp, ip = _cliente()
    seg.solicitar_ingreso(db(), _datos().get("celular", ""), disp, ip)
    # Misma respuesta exista o no la cuenta, para no revelar qué celulares están registrados.
    return jsonify(ok=True, mensaje="Si el celular está vinculado a una caja Winner, le llegará un código.")


@bp.post("/api/jugador/ingresar")
def api_jugador_ingresar():
    _anti_csrf()
    d = _datos()
    disp, ip = _cliente()
    token, j = seg.ingresar_jugador(db(), d.get("celular", ""), d.get("codigo"), d.get("pin"),
                                    d.get("pin_nuevo"), disp, ip)
    return _cookie(make_response(jsonify(nombre=j["nombre"])), COOKIE_JUGADOR, token)


@bp.post("/api/jugador/salir")
def api_jugador_salir():
    _anti_csrf()
    seg.cerrar_sesion(db(), request.cookies.get(COOKIE_JUGADOR))
    resp = make_response(jsonify(ok=True))
    resp.delete_cookie(COOKIE_JUGADOR, path="/winner")
    return resp


@bp.get("/api/jugador/inicio")
@jugador_sesion
def api_jugador_inicio():
    jid = g.jugador_id
    if request.args.get("abrir") == "1":
        # Se registra cada vez que el jugador abre o actualiza la app, no en cada sondeo automático.
        disp, ip = _cliente()
        with db().transaccion():
            seg.registrar_acceso(db(), "jugador", jid, "consulta_saldo", None, disp, ip)
    j = caja.jugador(db(), jid)
    mis = db().todos(
        "SELECT e.id, e.torneo_id, e.estado, e.codigo_qr, e.asiento, e.fichas, e.numero, m.numero AS mesa, "
        "t.nombre AS torneo, t.fecha, t.estado AS estado_torneo FROM entradas e JOIN torneos t ON t.id = e.torneo_id "
        "LEFT JOIN mesas m ON m.id = e.mesa_id WHERE e.jugador_id = ? AND t.estado IN ('programado','en_curso') "
        "AND e.estado != 'eliminado' ORDER BY t.fecha", (jid,))
    compras = bool(db().ajuste("compras_desde_celular"))
    disponibles = []
    for t in T.lista_torneos(db(), solo_activos=True):
        op = T.opciones_jugador(db(), t, jid)
        disponibles.append({"id": t["id"], "nombre": t["nombre"], "fecha": t["fecha"], "buyin": t["buyin"],
                            "estado": t["estado"], "inscritos": t["vivos"],
                            "puede_inscribirse": compras and t["preinscripcion_movil"] and op["puede_inscribirse"],
                            "inscrito": op["entrada_viva"] is not None})
    return jsonify(jugador=caja.publico(j), saldo=caja.saldo(db(), jid), torneos=mis, disponibles=disponibles,
                   reservas=T.reserva_activa(db(), jid) if compras else [], compras_habilitadas=compras,
                   movimientos=caja.movimientos(db(), jid, 30),
                   notificaciones=db().todos("SELECT * FROM notificaciones WHERE jugador_id = ? ORDER BY id DESC LIMIT 30",
                                             (jid,)))


@bp.post("/api/jugador/notificaciones/leidas")
@jugador_sesion
def api_jugador_leidas():
    with db().transaccion() as c:
        c.execute("UPDATE notificaciones SET leida = 1 WHERE jugador_id = ?", (g.jugador_id,))
    return jsonify(ok=True)


@bp.post("/api/jugador/torneos/<int:tid>/inscribir")
@jugador_sesion
def api_jugador_inscribir(tid):
    e = T.inscribir(db(), tid, g.jugador_id, origen="app", pin=_datos().get("pin"))
    return jsonify(entrada=e)


@bp.post("/api/jugador/entradas/<int:eid>/<accion>")
@jugador_sesion
def api_jugador_compra(eid, accion):
    e = T.entrada(db(), eid)
    if e["jugador_id"] != g.jugador_id:
        raise ErrorNegocio("No encontrado", 404)
    pin = _datos().get("pin")
    if accion == "recompra":
        return jsonify(entrada=T.recomprar(db(), eid, origen="app", pin=pin))
    if accion == "reentrada":
        nueva = T.reentrar(db(), eid, origen="app", pin=pin)
        return jsonify(entrada=nueva, asiento=_asiento(nueva["id"]))
    if accion == "rechazar":
        T.rechazar_reserva(db(), g.jugador_id, eid)
        return jsonify(ok=True)
    raise ErrorNegocio("Acción inválida", 404)
