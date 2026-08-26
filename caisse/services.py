from stations.constants import GASOIL, ESSENCE
from stations.models import PrixCarburant
from stations.services import litres_vendus_par_carburant

from caisse.models import (
    DepenseCaisse, DepotBancaire, EcritureSolde, ReleveIndexGerant,
    ReleveIndexPompiste, SessionCaisse, SoldePompiste,
)


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

    # Chaque role utilise SA PROPRE fenetre temporelle, jamais celle de l'autre — le
    # Gerant releve generalement APRES le depart du pompiste et AVANT sa cloture, donc
    # utiliser la fenetre du Gerant pour filtrer les releves du pompiste les exclurait
    # silencieusement (bug reel corrige : litres_pompiste tombait a 0).
    premiere_date_gerant = releves_gerant.order_by("date_heure").values_list("date_heure", flat=True).first()
    derniere_date_gerant = releves_gerant.order_by("-date_heure").values_list("date_heure", flat=True).first()
    premiere_date_pompiste = releves_pompiste.order_by("date_heure").values_list("date_heure", flat=True).first()
    derniere_date_pompiste = releves_pompiste.order_by("-date_heure").values_list("date_heure", flat=True).first()

    if premiere_date_gerant is None or derniere_date_gerant is None:
        return session_caisse

    litres_gerant = litres_vendus_par_carburant(
        pistolets_ids, releves_gerant, premiere_date_gerant, derniere_date_gerant
    )
    litres_pompiste = {}
    if premiere_date_pompiste is not None and derniere_date_pompiste is not None:
        litres_pompiste = litres_vendus_par_carburant(
            pistolets_ids, releves_pompiste, premiere_date_pompiste, derniere_date_pompiste
        )

    derniere_date = derniere_date_gerant

    ecart_gasoil = litres_pompiste.get(GASOIL, 0) - litres_gerant.get(GASOIL, 0)
    ecart_essence = litres_pompiste.get(ESSENCE, 0) - litres_gerant.get(ESSENCE, 0)

    session_caisse.ecart_gasoil = ecart_gasoil
    session_caisse.ecart_essence = ecart_essence
    session_caisse.divergence_signalee = (
        abs(ecart_gasoil) > marge_tolerance_litres or abs(ecart_essence) > marge_tolerance_litres
    )

    session_caisse.litres_gasoil_vendus = litres_gerant.get(GASOIL, 0)
    session_caisse.litres_essence_vendus = litres_gerant.get(ESSENCE, 0)

    montant_theorique_gasoil = 0
    montant_theorique_essence = 0
    for carburant, litres in litres_gerant.items():
        prix = PrixCarburant.objects.prix_en_vigueur(station, carburant, derniere_date)
        if prix is not None:
            montant = litres * prix.prix_au_litre
            if carburant == GASOIL:
                montant_theorique_gasoil = montant
            elif carburant == ESSENCE:
                montant_theorique_essence = montant

    montant_theorique_total = montant_theorique_gasoil + montant_theorique_essence
    session_caisse.montant_theorique_gasoil = montant_theorique_gasoil
    session_caisse.montant_theorique_essence = montant_theorique_essence
    session_caisse.montant_theorique_total = montant_theorique_total

    # ecart_montant_pompiste_gerant : signalement uniquement (divergence entre la
    # declaration du pompiste et la verification physique du Gerant), jamais un blocage,
    # jamais utilise pour le calcul MANQUANT/SURPLUS lui-meme.
    if session_caisse.montant_encaisse is not None and session_caisse.montant_verse_gerant is not None:
        session_caisse.ecart_montant_pompiste_gerant = (
            session_caisse.montant_encaisse - session_caisse.montant_verse_gerant
        )

    # montant_verse_gerant FAIT FOI pour le calcul MANQUANT/SURPLUS/EXACT — symetrique
    # a la regle deja etablie sur les index (le relevé du Gerant fait foi, celui du
    # pompiste sert de comparaison, pas de base de calcul).
    if session_caisse.montant_verse_gerant is not None:
        ecart_montant = session_caisse.montant_verse_gerant - montant_theorique_total
        session_caisse.montant_ecart = ecart_montant
        if ecart_montant > 0:
            session_caisse.resultat = SessionCaisse.SURPLUS
        elif ecart_montant < 0:
            session_caisse.resultat = SessionCaisse.MANQUANT
        else:
            session_caisse.resultat = SessionCaisse.EXACT

    return session_caisse


def appliquer_resultat_au_solde(session_caisse, seuil_alerte_dette_fcfa):
    """Applique le résultat (déjà calculé par confronter_session_caisse et sauvegardé) d'une
    SessionCaisse au solde roulant du pompiste : crée une EcritureSolde, met à jour
    SoldePompiste.solde_courant, et retourne True si le nouveau solde de dette dépasse le
    seuil d'alerte fourni.

    seuil_alerte_dette_fcfa est fourni explicitement par l'appelant (typiquement
    societe.seuil_alerte_dette_fcfa), même principe que marge_tolerance_litres dans
    confronter_session_caisse : aucune dépendance à un contexte global.

    Ne fait rien si session_caisse.resultat n'est pas encore renseigné (confrontation
    pas encore effectuée). N'envoie AUCUNE notification elle-même — se contente de
    retourner le booléen d'alerte, à charge de l'appelant de déclencher l'envoi.

    Sauvegarde SoldePompiste et crée EcritureSolde (contrairement à confronter_session_caisse,
    qui reste pure) : appliquer un résultat au solde est par nature un effet de bord,
    pas un calcul de prévisualisation.
    """
    if session_caisse.resultat is None or session_caisse.montant_ecart is None:
        return False

    if session_caisse.resultat == SessionCaisse.EXACT:
        return False

    solde_pompiste, _ = SoldePompiste.objects.get_or_create(employee=session_caisse.employee)
    solde_avant = solde_pompiste.solde_courant

    if session_caisse.resultat == SessionCaisse.SURPLUS:
        type_ecriture = EcritureSolde.SURPLUS_CONSTATE
        montant = abs(session_caisse.montant_ecart)
        solde_apres = solde_avant + montant
    else:
        type_ecriture = EcritureSolde.MANQUANT_CONSTATE
        montant = abs(session_caisse.montant_ecart)
        solde_apres = solde_avant - montant

    EcritureSolde.objects.create(
        solde_pompiste=solde_pompiste,
        session_caisse=session_caisse,
        type_ecriture=type_ecriture,
        montant=montant,
        solde_avant=solde_avant,
        solde_apres=solde_apres,
    )

    solde_pompiste.solde_courant = solde_apres
    solde_pompiste.save()

    return solde_apres < 0 and abs(solde_apres) > seuil_alerte_dette_fcfa


def montant_conserve_caisse(station, date_debut, date_fin):
    """Calcule le montant conservé en caisse pour une station, sur un intervalle donné :
    montant_conserve = encaisse_total - depots_banque - depenses_caisse

    encaisse_total = somme des SessionCaisse.montant_encaisse des pompistes de la station
    (via employee__station=station) dont la date tombe dans l'intervalle. Un Employee sans
    station (ex. Admin Siège) est naturellement exclu par le filtre, sans erreur.

    Fonction pure : ne sauvegarde rien, retourne un dict détaillé. Le résultat est purement
    descriptif — un dépôt bancaire est volontairement indépendant du théorique (le Gérant
    peut garder une partie de l'encaissement pour urgences), donc ce montant ne doit JAMAIS
    être présenté comme une anomalie automatique dans l'interface.
    """
    sessions = SessionCaisse.objects.filter(
        employee__station=station, date__range=(date_debut, date_fin)
    )
    encaisse_total = sum(
        s.montant_encaisse for s in sessions if s.montant_encaisse is not None
    ) or 0

    depots = DepotBancaire.objects.filter(
        station=station, date_heure__range=(date_debut, date_fin)
    )
    depots_banque = sum(d.montant for d in depots) or 0

    depenses = DepenseCaisse.objects.filter(
        station=station, date_heure__range=(date_debut, date_fin)
    )
    depenses_caisse = sum(d.montant for d in depenses) or 0

    montant_conserve = encaisse_total - depots_banque - depenses_caisse

    return {
        "station": station,
        "date_debut": date_debut,
        "date_fin": date_fin,
        "encaisse_total": encaisse_total,
        "depots_banque": depots_banque,
        "depenses_caisse": depenses_caisse,
        "montant_conserve": montant_conserve,
    }


def calculer_apercu_theorique(station, pistolets_ids, releves_queryset, date_debut, date_fin):
    """Calcule un APERCU du theorique (litres vendus par carburant, montant theorique
    par carburant et total), a partir d'UN SEUL relevé (pompiste OU Gerant, au choix de
    l'appelant) — fonction pure, ne sauvegarde rien, distincte de confronter_session_caisse
    qui elle utilise toujours les relevés du Gerant comme reference officielle.

    Sert uniquement a AFFICHER le calcul theorique a chaque role sur son propre ecran
    intermediaire (transparence : le pompiste/Gerant doit pouvoir voir et verifier ce
    calcul avant de confirmer sa propre saisie), jamais a decider MANQUANT/SURPLUS
    officiellement (ca reste le role de confronter_session_caisse)."""
    litres = litres_vendus_par_carburant(pistolets_ids, releves_queryset, date_debut, date_fin)

    montant_gasoil = 0
    montant_essence = 0
    prix_gasoil = 0
    prix_essence = 0
    prix_manquants = []
    for carburant, quantite in litres.items():
        prix = PrixCarburant.objects.prix_en_vigueur(station, carburant, date_fin)
        if prix is not None:
            montant = quantite * prix.prix_au_litre
            if carburant == GASOIL:
                montant_gasoil = montant
                prix_gasoil = prix.prix_au_litre
            elif carburant == ESSENCE:
                montant_essence = montant
                prix_essence = prix.prix_au_litre
        else:
            prix_manquants.append(carburant)

    return {
        "litres_gasoil": litres.get(GASOIL, 0),
        "litres_essence": litres.get(ESSENCE, 0),
        "prix_gasoil": prix_gasoil,
        "prix_essence": prix_essence,
        "montant_theorique_gasoil": montant_gasoil,
        "montant_theorique_essence": montant_essence,
        "montant_theorique_total": montant_gasoil + montant_essence,
        "prix_manquants": prix_manquants,
    }
