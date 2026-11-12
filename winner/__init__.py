"""Winner Poker: plataforma de torneos, cash y caja con saldo del jugador.

Se monta como Blueprint en ``/winner`` (ver ``winner.web``). La lógica de negocio vive
en módulos sin Flask (``caja``, ``torneos``, ``cash``, ``premios``, ``reportes``) para
poder probarla directamente.
"""
