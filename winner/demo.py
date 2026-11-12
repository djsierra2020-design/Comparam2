"""Datos de demostración (solo con WINNER_DEMO=1). Usuarios y claves de prueba, nunca para producción."""
from __future__ import annotations

from . import caja
from . import seguridad as seg
from . import torneos as T

USUARIOS_DEMO = [
    ("admin", "Administrador demo", "admin"),
    ("director", "Director de torneo demo", "director"),
    ("floor", "Floor demo", "floor"),
    ("crupier", "Crupier demo", "crupier"),
    ("cajero", "Cajero demo", "cajero"),
    ("supervisor", "Supervisor demo", "supervisor"),
]
CLAVE_DEMO = "winner2026"
NOMBRES = ["Andrés Gómez", "Camila Rojas", "Juan Pérez", "Laura Martínez", "Santiago López", "Valentina Díaz",
           "Felipe Torres", "Daniela Herrera", "Carlos Ramírez", "Natalia Castro", "Diego Moreno", "Paula Vargas",
           "Sebastián Ruiz", "María Jiménez", "Alejandro Ortiz", "Juliana Navarro", "Mateo Silva", "Sofía Cárdenas",
           "Nicolás Ríos", "Isabella Mejía", "Tomás Acosta", "Mariana Peña", "David Rincón", "Gabriela Suárez",
           "Esteban Parra", "Catalina Rueda", "Julián Salazar", "Manuela Gil", "Ricardo Pineda", "Ana Lucía Prieto",
           "Hernán Duarte", "Lorena Cifuentes", "Óscar Beltrán", "Viviana Molina", "Jorge Quintero", "Carolina León",
           "Miguel Ángel Rozo", "Diana Sarmiento", "Pablo Fajardo", "Adriana Vega"]


APELLIDOS = ["Gómez", "Rojas", "Pérez", "Martínez", "López", "Díaz", "Torres", "Herrera", "Ramírez", "Castro",
             "Moreno", "Vargas", "Ruiz", "Jiménez", "Ortiz", "Navarro", "Silva", "Cárdenas", "Ríos", "Mejía"]
JUGADORES_DEMO = 300   # aforo máximo de Winner


def nombre_demo(i: int) -> str:
    if i < len(NOMBRES):
        return NOMBRES[i]
    nombre = NOMBRES[i % len(NOMBRES)].split()[0]
    return f"{nombre} {APELLIDOS[i % len(APELLIDOS)]} {APELLIDOS[(i // len(APELLIDOS)) % len(APELLIDOS)]}"


def sembrar(db) -> None:
    if db.valor("SELECT COUNT(*) FROM personal"):
        return
    ids = {u: seg.crear_personal(db, u, n, r, CLAVE_DEMO) for u, n, r in USUARIOS_DEMO}
    cajero, director = ids["cajero"], ids["director"]
    jugadores = []
    for i in range(JUGADORES_DEMO):
        celular = f"300000{i:04d}"
        r = caja.abrir_caja(db, cajero, nombre_demo(i), f"79{i:06d}", celular, True)
        caja.vincular_celular(db, cajero, r["jugador"]["id"], r["_codigo"])
        caja.depositar(db, cajero, r["jugador"]["id"], 2_000_000 + 100_000 * (i % 5))
        jugadores.append(r["jugador"]["id"])
    _evento_demo(db, director, cajero, jugadores)


def _evento_demo(db, director, cajero, jugadores) -> None:
    """Una semana de evento en la sala de 30 mesas, con torneos simultáneos.

    - Final «Main Event Día 2» con 5 días clasificatorios (1A a 1E), clasifica el 15 %.
    - Día 1A ya jugado y cerrado (sus sobrevivientes ya están clasificados).
    - Día 1B en curso en 24 mesas, con lista de alternos.
    - Satélite al Main Event en curso, al mismo tiempo, en 4 mesas (premia 3 tickets).
    """
    import random
    from datetime import date, timedelta

    from .eventos import cerrar_dia
    from .premios import definir_tabla
    rnd = random.Random(2026)
    hoy = date.fromisoformat(caja.dia_bogota())
    dia = lambda n: (hoy + timedelta(days=n)).isoformat()  # noqa: E731
    base = {"buyin": 600_000, "stack_inicial": 40_000, "asientos_por_mesa": 9, "reentrada_max": 1}
    final = T.crear_torneo(db, director, {**base, "nombre": "Main Event Winner · Día 2", "fecha": dia(4), "tipo": "final"})
    dias = [T.crear_torneo(db, director, {**base, "nombre": f"Main Event · Día 1{letra}", "fecha": dia(n - 1),
                                          "tipo": "dia1", "principal_id": final, "porcentaje_clasifica": 15})
            for n, letra in enumerate("ABCDE")]
    definir_tabla(db, director, final, [{"posicion": p, "monto": m} for p, m in enumerate(
        [40_000_000, 25_000_000, 16_000_000, 11_000_000, 8_000_000, 6_000_000, 4_500_000, 3_500_000, 3_000_000], 1)])

    # Día 1A (ayer): 120 jugadores, se juega hasta el 15 % y se cierra.
    d1a = dias[0]
    T.abrir_mesas(db, director, d1a, 14)
    for j in jugadores[:120]:
        T.inscribir(db, d1a, j, origen="caja", personal_id=cajero, pago="saldo")
    T.iniciar_torneo(db, director, d1a)
    T.sentar_inscritos(db, director, d1a)
    objetivo = 18  # 15 % de 120
    while db.valor("SELECT COUNT(*) FROM entradas WHERE torneo_id = ? AND estado = 'sentado'", (d1a,)) > objetivo:
        vivos = db.todos("SELECT id FROM entradas WHERE torneo_id = ? AND estado = 'sentado'", (d1a,))
        T.eliminar(db, director, rnd.choice(vivos)["id"])
        db.conexion().execute("UPDATE reservas SET vence_en = '2000-01-01T00:00:00.000000Z' WHERE estado = 'activa'")
        for sug in T.sugerencias(db, d1a):
            if sug["tipo"] == "cerrar_mesa":
                T.cerrar_mesa(db, director, d1a, sug["mesa_id"])
        T.balancear(db, director, d1a)
    for e in db.todos("SELECT id FROM entradas WHERE torneo_id = ? AND estado = 'sentado'", (d1a,)):
        T.registrar_conteo(db, director, e["id"], rnd.randrange(80, 600) * 1_000)
    cerrar_dia(db, director, d1a)
    eliminados_1a = [r["jugador_id"] for r in db.todos(
        "SELECT DISTINCT jugador_id FROM entradas WHERE torneo_id = ? AND estado = 'eliminado'", (d1a,))]

    # Satélite (hoy, simultáneo): 30 jugadores en 4 mesas; 3 tickets al Main Event y el 4° cobra.
    sat = T.crear_torneo(db, director, {"nombre": "Satélite al Main Event", "fecha": dia(0), "buyin": 150_000,
                                        "stack_inicial": 15_000, "tipo": "satelite", "satelite_de": final})
    T.abrir_mesas(db, director, sat, 4)
    for j in eliminados_1a[:30]:
        T.inscribir(db, sat, j, origen="caja", personal_id=cajero, pago="saldo")
    T.iniciar_torneo(db, director, sat)
    T.sentar_inscritos(db, director, sat)
    definir_tabla(db, director, sat, [{"posicion": 1, "tipo": "ticket"}, {"posicion": 2, "tipo": "ticket"},
                                      {"posicion": 3, "tipo": "ticket"}, {"posicion": 4, "monto": 300_000}])

    # Día 1B (hoy, en curso): 180 jugadores nuevos + 50 que re-entran tras caer en el 1A, en 24 mesas → alternos.
    d1b = dias[1]
    T.abrir_mesas(db, director, d1b, 24)
    for j in jugadores[120:] + eliminados_1a[30:80]:
        T.inscribir(db, d1b, j, origen="caja", personal_id=cajero, pago="saldo")
    T.iniciar_torneo(db, director, d1b)
    T.sentar_inscritos(db, director, d1b)  # 216 sentados, el resto queda en la lista de alternos
