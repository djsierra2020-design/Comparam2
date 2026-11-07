"""Localidades de Bogotá y a cuál pertenecen los barrios más comunes en los anuncios.

Se usa en la búsqueda de toda Bogotá: si el barrio de un anuncio tiene muy pocos anuncios
para calcular su precio del m², se compara con su localidad.
"""
from __future__ import annotations

from .parsing import slugify

LOCALIDADES = [
    "Usaquén", "Chapinero", "Santa Fe", "San Cristóbal", "Usme", "Tunjuelito", "Bosa", "Kennedy",
    "Fontibón", "Engativá", "Suba", "Barrios Unidos", "Teusaquillo", "Los Mártires", "Antonio Nariño",
    "Puente Aranda", "La Candelaria", "Rafael Uribe Uribe", "Ciudad Bolívar",
]

_BARRIOS = {
    "Usaquén": "Cedritos, Cedro Golf, Cedro Bolívar, Cedro Salazar, Cedro Narváez, Santa Bárbara, "
               "Santa Bárbara Central, Santa Bárbara Occidental, Santa Bárbara Alta, Santa Paula, Usaquén, "
               "Country Club, Bella Suiza, Ginebra, Lisboa, Toberín, Verbenal, San Antonio Norte, "
               "Contador, Babilonia, Los Cedros, El Contador, Santa Ana, Santa Ana Oriental, Molinos Norte, "
               "Multicentro, Prado Veraniego, La Calleja, Belmira, El Codito, Barrancas, Servitá, "
               "San Cristóbal Norte, Las Orquídeas, Altos de Serrezuela, Navarra, Unicentro, Puente Largo, "
               "La Carolina, Bosque de Pinos, Granada Norte, Estrella del Norte, Canaima, Mirandela, "
               "Cantagallo, Los Naranjos, Nueva Autopista, Pasadena, Rincón del Chicó, Spring",
    "Chapinero": "Chicó, Chicó Navarra, Chicó Norte, Chicó Reservado, Chicó Alto, El Chicó, Chicó Museo, "
                 "Rosales, Los Rosales, El Nogal, Nogal, La Cabrera, El Retiro, Chapinero Alto, "
                 "Chapinero Central, Chapinero Norte, Pardo Rubio, Bosque Calderón, Marly, La Salle, "
                 "Porciúncula, La Porciúncula, Quinta Camacho, Antiguo Country, Emaús, Lago Gaitán, "
                 "El Refugio, Seminario, Granada, Bellavista, Villa del Cerro, Sucre, Cataluña, "
                 "San Martín de Porres, Siberia Central, El Paraíso, Ingemar, Juan XXIII, Chicó Lago",
    "Santa Fe": "La Macarena, Macarena, Las Aguas, Las Nieves, La Perseverancia, Bosque Izquierdo, "
                "San Martín, Germania, Samper Mendoza, Las Cruces, Lourdes, Centro Internacional",
    "San Cristóbal": "20 de Julio, Veinte de Julio, San Blas, La Victoria, Los Libertadores, Altamira",
    "Usme": "Santa Librada, Alfonso López, Danubio, La Aurora",
    "Tunjuelito": "Tunal, Ciudad Tunal, Tunjuelito, Venecia, San Benito, Fátima, Isla del Sol",
    "Bosa": "Bosa Centro, Bosa Nova, Bosa La Paz, El Recreo, Bosa El Recreo, Nuevo Recreo, "
            "San Bernardino, San Bernardino XXII, Islandia, Bosa Islandia, Campo Verde, Madelena, "
            "Hacienda la Estancia, La Estancia, Bosa Porvenir, El Porvenir, Brasil, La Amistad, Laureles, "
            "Andalucía, Primavera, Gualoche, Los Sauces, La Estación",
    "Kennedy": "Castilla, Kennedy, Kennedy Central, Timiza, Marsella, Américas, Las Américas, "
               "Mandalay, Tintal, Patio Bonito, Banderas, Bavaria, Ciudad Kennedy, Techo, Alsacia, "
               "Nueva Castilla, Villa Alsacia, Valladolid, Carvajal, El Vergel, Calandaima, Pío XII, "
               "Ciudad Techo, Gran Britalia, Britalia, Corabastos",
    "Fontibón": "Fontibón, Fontibón Centro, Modelia, Hayuelos, Ciudad Salitre Occidental, "
                "Salitre Occidental, Capellanía, Ciudad Hayuelos, Versalles, Zona Franca, El Tintal, "
                "Villemar, Puerto Amor, La Cabaña, El Refugio de Fontibón, Aeropuerto",
    "Engativá": "Normandía, Villa Luz, Bochica, Minuto de Dios, Engativá, Álamos, Los Álamos, Boyacá, "
                "Boyacá Real, Tabora, Santa Helenita, Garcés Navas, Villas de Granada, Quirigua, "
                "Ciudad Bachué, Bachué, La Granja, Las Ferias, El Encanto, Florencia, Marandú, "
                "Santa Cecilia, Granada Norte Engativá, Normandía Occidental, El Cortijo",
    "Suba": "Suba, Colina Campestre, Niza, Niza Norte, Niza Sur, Pontevedra, Mazurén, San José de Bavaria, "
            "Prado, Prado Pinzón, Casa Blanca, Casablanca, Casa Blanca Suba, Lombardía, Gratamira, "
            "Alhambra, La Alhambra, Batán, El Batán, Villa del Prado, Iberia, Córdoba, Las Villas, "
            "Puente Largo, Rincón, El Rincón, Tibabuyes, Lisboa Suba, Mirandela, Ciudad Jardín Norte, "
            "Altos de Suba, Victoria Norte, Club de los Lagartos, Morato, Andes Norte, Monaco, "
            "La Campiña, Villa Elisa, Pinar de Suba, Ilarco, Calatayud, Potosí, Bosque Reservado",
    "Barrios Unidos": "Barrios Unidos, Rionegro, Polo Club, El Polo, La Castellana, Castellana, "
                      "Doce de Octubre, 12 de Octubre, Siete de Agosto, 7 de Agosto, Benjamín Herrera, "
                      "Alcázares, Los Alcázares, San Felipe, Entre Ríos, Andes, Los Andes, Juan XXIII, "
                      "Santa Sofía, La Esperanza, Metrópolis, Simón Bolívar",
    "Teusaquillo": "Teusaquillo, La Soledad, Palermo, Galerías, Park Way, Parkway, Quinta Paredes, "
                   "Ciudad Salitre, Ciudad Salitre Oriental, Salitre, Salitre El Greco, El Salitre, "
                   "Nicolás de Federmán, Campín, El Campín, Pablo VI, Armenia, La Esmeralda, Rafael Núñez, "
                   "Acevedo Tejada, Ciudad Universitaria, Banco Central, Santa Teresita, Belalcázar, "
                   "Alfonso López Pumarejo, Gran América, El Recuerdo, Estrella",
    "Los Mártires": "Santa Isabel, Ricaurte, Paloquemao, Eduardo Santos, La Sabana, Voto Nacional",
    "Antonio Nariño": "Restrepo, Ciudad Jardín, Ciudad Jardín Sur, Santander, Policarpa, San Antonio",
    "Puente Aranda": "Puente Aranda, Ciudad Montes, Galán, Pensilvania, Primavera Occidental, "
                     "Villa del Rosario, La Alquería, Alquería, Autopista Sur, Ospina Pérez, Tejar, "
                     "El Tejar, Santa Matilde, Los Ejidos, Gorgonzola, Comuneros, Remanso",
    "La Candelaria": "La Candelaria, Candelaria, Egipto, Belén, La Concordia, Centro Administrativo",
    "Rafael Uribe Uribe": "Quiroga, Olaya, Marco Fidel Suárez, Diana Turbay, Libertador, Centenario, "
                          "Santa Lucía, Bravo Páez, San Jorge, Gustavo Restrepo",
    "Ciudad Bolívar": "Ciudad Bolívar, Arborizadora, Arborizadora Alta, Arborizadora Baja, Candelaria La Nueva, "
                      "Madelena Ciudad Bolívar, Sierra Morena, Perdomo, Lucero, Jerusalén",
}

# slug de barrio/localidad -> localidad (los más largos primero, para que «chapinero-alto» gane a «chapinero»)
_MAPA: dict[str, str] = {}
for _loc, _lista in _BARRIOS.items():
    for _b in _lista.split(","):
        if (_s := slugify(_b)):
            _MAPA.setdefault(_s, _loc)
for _loc in LOCALIDADES:
    _MAPA.setdefault(slugify(_loc), _loc)
_ORDEN = sorted(_MAPA, key=len, reverse=True)


def localidad_de(*textos: str) -> str:
    """Localidad que se deduce del barrio, título o ubicación del anuncio («» si no se sabe)."""
    t = f"-{slugify(' '.join(x for x in textos if x))}-"
    for s in _ORDEN:
        if f"-{s}-" in t:
            return _MAPA[s]
    return ""
