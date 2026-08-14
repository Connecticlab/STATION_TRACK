from stations.constants import GASOIL, ESSENCE
from stations.models import PrixCarburant
from stations.services import litres_vendus_par_carburant

from caisse.models import ReleveIndexGerant, ReleveIndexPompiste, SessionCaisse


def confronter_session_caisse(session_caisse, marge_tolerance_litres):
    """Calcule la confrontation complète d'une SessionCaisse : quantités vendues par carburant
    (relevé Gérant qui fait foi, relevé pompiste pour comparaison), écarts entre les deux relevés,
    montant théorique en FCFA (au prix en vigueur), et résultat final MANQUANT/SURPLUS.

    marge_tolerance_litres est fourni explicitement par l'appelant (typiquement
    societe.marge_tolerance_divergence_litres) — cette fonction ne dépend d'aucun contexte
    global (requête HTTP, session, etc.) et peut être appelée depuis n'importe où
    (vue, shell, tâche planifiée) de façon identique et testable isolément.

    Ne bloque jamais : toute divergence est enregistrée et signalée, jamais empêchée.
    Fonction pure : modifie session_caisse EN MÉMOIRE (attributs) et le retourne,
    mais ne le sauvegarde PAS. C'est à l'appelant de faire session_caisse.save()
    une fois le résultat validé.
    """
    employee = session_caisse.employee
    station = employee.station

    pistolets_ids = list(
        set(
            list(
                ReleveIndexGerant.objects.filter(session_caisse=session_caisse)
                .values_list("pistolet_id", flat=True)
            )
            + list(
                ReleveIndexPompiste.objects.filter(session_caisse=session_caisse)
                .values_list("pistolet_id", flat=True)
            )
        )
    )

    releves_gerant = ReleveIndexGerant.objects.filter(session_caisse=session_caisse)
    releves_pompiste = ReleveIndexPompiste.objects.filter(session_caisse=session_caisse)

    premiere_date = releves_gerant.order_by("date_heure").values_list("date_heure", flat=True).first()
    derniere_date = releves_gerant.order_by("-date_heure").values_list("date_heure", flat=True).first()

    if premiere_date is None or derniere_date is None:
        return session_caisse

    litres_gerant = litres_vendus_par_carburant(pistolets_ids, releves_gerant, premiere_date, derniere_date)
    litres_pompiste = litres_vendus_par_carburant(pistolets_ids, releves_pompiste, premiere_date, derniere_date)

    ecart_gasoil = litres_pompiste.get(GASOIL, 0) - litres_gerant.get(GASOIL, 0)
    ecart_essence = litres_pompiste.get(ESSENCE, 0) - litres_gerant.get(ESSENCE, 0)

    session_caisse.ecart_gasoil = ecart_gasoil
    session_caisse.ecart_essence = ecart_essence
    session_caisse.divergence_signalee = (
        abs(ecart_gasoil) > marge_tolerance_litres or abs(ecart_essence) > marge_tolerance_litres
    )

    montant_theorique = 0
    for carburant, litres in litres_gerant.items():
        prix = PrixCarburant.objects.prix_en_vigueur(station, carburant, derniere_date)
        if prix is not None:
            montant_theorique += litres * prix.prix_au_litre

    if session_caisse.montant_encaisse is not None:
        ecart_montant = session_caisse.montant_encaisse - montant_theorique
        session_caisse.montant_ecart = ecart_montant
        session_caisse.resultat = SessionCaisse.SURPLUS if ecart_montant > 0 else SessionCaisse.MANQUANT

    return session_caisse
