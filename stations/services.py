from stations.constants import GASOIL, ESSENCE


def litres_vendus_par_carburant(pistolets_ids, releves_queryset, date_debut, date_fin):
    """Calcule les litres vendus par carburant, à partir d'un queryset de relevés d'index
    (ReleveIndexPompiste OU ReleveIndexGerant), pour un ensemble de pistolets, sur un intervalle.

    Pour chaque pistolet, on prend le dernier relevé de type "depart" et le dernier relevé
    de type "fin" dans l'intervalle [date_debut, date_fin], et on additionne (fin - depart)
    par carburant.

    Retourne un dict {carburant: litres_vendus (Decimal)}, avec toutes les clés de
    CARBURANT_CHOICES toujours présentes (0 si aucune vente sur la période).

    releves_queryset doit être un queryset de ReleveIndexPompiste ou ReleveIndexGerant,
    déjà filtré sur la période/l'employé pertinent par l'appelant si besoin en plus des
    paramètres date_debut/date_fin.
    """
    resultats = {GASOIL: 0, ESSENCE: 0}

    releves_periode = releves_queryset.filter(
        pistolet_id__in=pistolets_ids,
        date_heure__gte=date_debut,
        date_heure__lte=date_fin,
    ).select_related("pistolet")

    for pistolet_id in pistolets_ids:
        releves_pistolet = releves_periode.filter(pistolet_id=pistolet_id)

        releve_depart = releves_pistolet.filter(type_releve="depart").order_by("date_heure").first()
        releve_fin = releves_pistolet.filter(type_releve="fin").order_by("-date_heure").first()

        if releve_depart is None or releve_fin is None:
            continue

        carburant = releve_fin.pistolet.carburant
        litres = releve_fin.valeur_index - releve_depart.valeur_index
        resultats[carburant] = resultats.get(carburant, 0) + litres

    return resultats
