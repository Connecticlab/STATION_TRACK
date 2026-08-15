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
        jauge_debut.date_mesure, jauge_fin.date_mesure,
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


def calculer_statut_mensuel_carburant(station, carburant, mois, annee, marge_tolerance):
    """Calcule le statut mensuel (Normal ou Perte/Coulage) de l'écart carburant d'une station,
    pour un carburant donné, en sommant les écarts de calculer_ecart_jauge sur toutes les paires
    de jauges consécutives du mois.

    marge_tolerance est fourni explicitement par l'appelant (typiquement
    societe.marge_tolerance_ecart_carburant_mensuel_litres), même principe que
    confronter_session_caisse : aucune dépendance à un contexte global.

    Fonction pure : ne sauvegarde rien, retourne un dict avec le détail du calcul.

    Retourne cumul_ecart=None et statut=None si moins de 2 jauges existent pour ce mois
    (impossible de calculer un écart sans au moins une paire de jauges consécutives).
    """
    from cuves.models import Jauge

    jauges_du_mois = list(
        Jauge.objects.filter(
            station=station, carburant=carburant,
            date_jauge__year=annee, date_jauge__month=mois,
        ).order_by("date_mesure")
    )

    if len(jauges_du_mois) < 2:
        return {
            "carburant": carburant,
            "mois": mois,
            "annee": annee,
            "cumul_ecart": None,
            "statut": None,
            "detail_par_paire": [],
        }

    cumul_ecart = 0
    detail_par_paire = []
    for jauge_debut, jauge_fin in zip(jauges_du_mois, jauges_du_mois[1:]):
        resultat_paire = calculer_ecart_jauge(jauge_debut, jauge_fin)
        cumul_ecart += resultat_paire["ecart"]
        detail_par_paire.append(resultat_paire)

    statut = "normal" if abs(cumul_ecart) <= marge_tolerance else "perte"

    return {
        "carburant": carburant,
        "mois": mois,
        "annee": annee,
        "cumul_ecart": cumul_ecart,
        "statut": statut,
        "detail_par_paire": detail_par_paire,
    }
