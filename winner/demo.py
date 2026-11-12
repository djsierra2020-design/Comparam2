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
INSCRITOS_DEMO = 296   # 4 cajas quedan libres para probar la inscripción
MESAS_DEMO = 30        # sala de 30 mesas de 9 = 270 asientos → 26 alternos


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
        caja.depositar(db, cajero, r["jugador"]["id"], 1_000_000 + 100_000 * (i % 5))
        jugadores.append(r["jugador"]["id"])
    t = T.crear_torneo(db, director, {
        "nombre": "Torneo demo Winner", "fecha": caja.dia_bogota(), "buyin": 300_000, "stack_inicial": 30_000,
        "asientos_por_mesa": 9, "reentrada_max": 1, "recompra_max": 2, "recompra_precio": 200_000,
        "recompra_fichas": 20_000, "addon_precio": 150_000, "addon_fichas": 15_000})
    T.abrir_mesas(db, director, t, MESAS_DEMO)
    for j in jugadores[:INSCRITOS_DEMO]:
        T.inscribir(db, t, j, origen="caja", personal_id=cajero, pago="saldo")
    T.iniciar_torneo(db, director, t)
    T.sentar_inscritos(db, director, t)  # 270 sentados y el resto en la lista de alternos
    from .premios import definir_tabla
    definir_tabla(db, director, t, [{"posicion": 1, "monto": 4_500_000}, {"posicion": 2, "monto": 2_700_000},
                                    {"posicion": 3, "monto": 1_600_000}, {"posicion": 4, "monto": 1_000_000}])
    T.crear_torneo(db, director, {"nombre": "Deepstack del sábado", "fecha": "2026-12-05", "buyin": 500_000,
                                  "stack_inicial": 50_000, "reentrada_max": 1})
