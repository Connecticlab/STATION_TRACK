from caisse.models import ReleveIndexGerant
from cuves.models import Depotage
from stations.models import Pistolet
from stations.services import litres_vendus_par_carburant


def calculer_ecart_jauge(jauge_debut, jauge_fin):
    """Calcule l'écart carburant entre deux jauges consécutives (même carburant, même station) :
    quantité consommée selon les cuves (corrigée des dépotages intermédiaires) vs quantité vendue
    selon les pompes (relevé Gérant qui fait foi).

    Formule (bilan physique du stock) :
        quantite_consommee = (jauge_debut.quantite + total_depotage_intermediaire) - jauge_fin.quantite
    où total_depotage_intermediaire est la somme des Depotage dont jauge_completee == jauge_debut,
    pour ce carburant (lien explicite, pas une recherche par intervalle de dates).

        ecart = quantite_consommee - quantite_vendue

    Fonction pure : ne sauvegarde rien, retourne un dict avec le détail du calcul.

    Lève ValueError si jauge_debut.carburant != jauge_fin.carburant (garde-fou : un appel
    accidentel avec des jauges de carburants différents produirait un résultat incohérent
    sans erreur visible autrement).
    """
    if jauge_debut.carburant != jauge_fin.carburant:
        raise ValueError(
            f"jauge_debut ({jauge_debut.carburant}) et jauge_fin ({jauge_fin.carburant}) "
            "doivent porter sur le même carburant."
        )

    carburant = jauge_debut.carburant
    station = jauge_debut.station

    depotages_intermediaires = Depotage.objects.filter(
        jauge_completee=jauge_debut, carburant=carburant
    )
    total_depotage = sum(d.quantite_citerne for d in depotages_intermediaires) or 0

    quantite_consommee = (jauge_debut.quantite + total_depotage) - jauge_fin.quantite

    pistolets_ids = list(
        Pistolet.objects.filter(
            face__pompe__station=station, carburant=carburant
        ).values_list("id", flat=True)
    )

    from caisse.models import ReleveIndexGerant

    litres_vendus = litres_vendus_par_carburant(
        pistolets_ids, ReleveIndexGerant.objects.all(),
        jauge_debut.date_creation, jauge_fin.date_creation,
    )
    quantite_vendue = litres_vendus.get(carburant, 0)

    ecart = quantite_consommee - quantite_vendue

    return {
        "carburant": carburant,
        "quantite_consommee": quantite_consommee,
        "quantite_vendue": quantite_vendue,
        "total_depotage": total_depotage,
        "ecart": ecart,
    }
