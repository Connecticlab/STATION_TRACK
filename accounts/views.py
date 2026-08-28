from datetime import timedelta

from django.shortcuts import render, redirect
from django.utils import timezone

from accounts.decorators import require_employee_login
from accounts.models import Employee, LoginAttemptEmployee


def _get_client_ip(request):
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def _est_bloque(identifiant, adresse_ip):
    fenetre = timezone.now() - timedelta(minutes=15)

    echecs_identifiant = LoginAttemptEmployee.objects.filter(
        identifiant_tente=identifiant,
        reussie=False,
        date_tentative__gte=fenetre,
    ).count()
    if echecs_identifiant >= 5:
        return True

    echecs_ip = LoginAttemptEmployee.objects.filter(
        adresse_ip=adresse_ip,
        reussie=False,
        date_tentative__gte=fenetre,
    ).count()
    if echecs_ip >= 20:
        return True

    return False


def _url_apres_connexion(employee):
    """Redirection directe selon le role — pas de page d'accueil commune (philosophie MVP :
    chaque role a un usage tres different, pas de clic ajoute sans benefice reel)."""
    if employee.role == Employee.POMPISTE:
        return redirect("accounts:pompiste_accueil")
    if employee.role in (Employee.CHEF_DE_PISTE, Employee.GERANT):
        return redirect("accounts:gerant_accueil")
    return redirect("accounts:admin_accueil")


def employee_logout(request):
    """Deconnexion generique, reutilisable pour tous les roles Employee (pompiste,
    gerant, chef de piste)."""
    request.session.pop("employee_id", None)
    request.session.pop("employee_societe_slug", None)
    return redirect("accounts:login")


def employee_login(request):
    erreur = None

    if request.method == "POST":
        telephone = request.POST.get("telephone", "").strip()
        mot_de_passe = request.POST.get("password", "")
        adresse_ip = _get_client_ip(request)

        if _est_bloque(telephone, adresse_ip):
            erreur = "Trop de tentatives. Réessayez plus tard."
        else:
            employee = None
            try:
                candidat = Employee.objects.get(telephone=telephone, actif=True)
                if candidat.check_password(mot_de_passe):
                    # Station desactivee (Admin Siege exclu : station toujours nulle,
                    # role transversal jamais bloque par le statut d'une seule station).
                    if candidat.station is not None and not candidat.station.actif:
                        erreur = "Cette station est désactivée. Contactez votre administrateur."
                    else:
                        employee = candidat
            except Employee.DoesNotExist:
                employee = None

            LoginAttemptEmployee.objects.create(
                identifiant_tente=telephone,
                adresse_ip=adresse_ip,
                reussie=employee is not None,
            )

            if employee is not None:
                if not hasattr(request, "societe"):
                    # Ne devrait jamais arriver : TenantMiddleware retourne deja une 404
                    # avant d'atteindre cette vue si aucune societe n'est resolue. Garde
                    # defensive quand meme (meme discipline que require_employee_login).
                    erreur = "Erreur de configuration. Réessayez plus tard."
                else:
                    request.session["employee_id"] = employee.pk
                    request.session["employee_societe_slug"] = request.societe.sous_domaine
                    return _url_apres_connexion(employee)
            if erreur is None:
                erreur = "Identifiants invalides."

    return render(request, "accounts/login.html", {"erreur": erreur})


def _structurer_pistolets_par_pompe_face(pistolets_queryset):
    """Organise un queryset de Pistolet en une structure hierarchique
    [{"pompe": Pompe, "faces": [{"face": Face, "pistolets": [Pistolet, ...]}, ...]}, ...]
    pour un affichage groupe (jamais une liste plate) — le pompiste doit toujours voir
    de quelle Pompe/Face vient chaque pistolet, pas seulement son numero global."""
    pistolets = list(
        pistolets_queryset.select_related("face", "face__pompe").order_by(
            "face__pompe__numero", "face__numero", "numero"
        )
    )

    structure = []
    pompe_courante = None
    face_courante = None

    for pistolet in pistolets:
        pompe = pistolet.face.pompe
        face = pistolet.face

        if pompe_courante is None or pompe_courante["pompe"].pk != pompe.pk:
            pompe_courante = {"pompe": pompe, "faces": []}
            structure.append(pompe_courante)
            face_courante = None

        if face_courante is None or face_courante["face"].pk != face.pk:
            face_courante = {"face": face, "pistolets": []}
            pompe_courante["faces"].append(face_courante)

        face_courante["pistolets"].append(pistolet)

    return structure


@require_employee_login(roles=[Employee.POMPISTE])
def pompiste_accueil(request):
    """4 etats : pas demarre / demarre (paiements + index de fin) / index de fin saisi
    mais montant pas encore confirme (redirige vers pompiste_finaliser) / cloture.
    Le montant_encaisse est desormais une VRAIE saisie manuelle du pompiste (pas une
    somme automatique) — le pompiste peut oublier de cliquer un bouton de paiement en
    pleine activite, mais sait ce qu'il a reellement en main a la fin."""
    from django.db import transaction
    from django.utils import timezone

    from caisse.models import ReleveIndexPompiste, SessionCaisse
    from stations.services import pistolets_affectes_a

    from django.db.models import Q

    from stations.models import Pistolet, Pompe

    employee = request.employee
    pistolets = pistolets_affectes_a(employee)
    structure = _structurer_pistolets_par_pompe_face(pistolets)

    # Si aucun pistolet disponible, distinguer TROIS cas — jamais laisser un ecran
    # silencieusement vide avec un bouton inoperant : 1) station sans aucune pompe
    # configuree, 2) employe sans aucune affectation (nouvel employe, ou transfert
    # ayant desaffecte), 3) affectation existante mais pompe hors service.
    pompe_indisponible = None
    aucune_pompe_station = False
    aucune_affectation = False

    if not structure:
        if not Pompe.objects.filter(station=employee.station).exists():
            aucune_pompe_station = True
        else:
            pistolets_bruts_qs = Pistolet.objects.filter(
                Q(face__pompe__employee_affecte=employee) | Q(face__employee_affecte=employee)
            ).select_related("face__pompe")
            pistolet_brut = pistolets_bruts_qs.first()
            if pistolet_brut is None:
                aucune_affectation = True
            else:
                indisponible = pistolets_bruts_qs.exclude(face__pompe__statut=Pompe.STATUT_ACTIF).first()
                if indisponible:
                    pompe_indisponible = indisponible.face.pompe

    aujourdhui = timezone.localtime(timezone.now()).date()
    session = SessionCaisse.objects.filter(employee=employee, date=aujourdhui).first()

    a_depart = session is not None and ReleveIndexPompiste.objects.filter(
        session_caisse=session, type_releve=ReleveIndexPompiste.DEPART
    ).exists()
    a_index_fin = session is not None and ReleveIndexPompiste.objects.filter(
        session_caisse=session, type_releve=ReleveIndexPompiste.FIN
    ).exists()
    a_fin = session is not None and session.montant_encaisse is not None

    if a_index_fin and not a_fin:
        return redirect("accounts:pompiste_finaliser")

    erreurs = []

    if request.method == "POST" and not a_index_fin:
        type_releve = ReleveIndexPompiste.DEPART if not a_depart else ReleveIndexPompiste.FIN
        valeurs_validees = {}

        # Passe 1 : validation complete de tous les champs avant toute creation.
        for pistolet in pistolets:
            champ = f"index_{pistolet.pk}"
            valeur_brute = request.POST.get(champ, "").strip()

            if not valeur_brute:
                erreurs.append(f"Index manquant pour {pistolet}.")
                continue

            try:
                valeur = float(valeur_brute)
            except ValueError:
                erreurs.append(f"Index invalide pour {pistolet}.")
                continue

            if type_releve == ReleveIndexPompiste.FIN:
                releve_depart = ReleveIndexPompiste.objects.filter(
                    session_caisse=session, pistolet=pistolet, type_releve=ReleveIndexPompiste.DEPART
                ).first()
                if releve_depart and valeur < float(releve_depart.valeur_index):
                    erreurs.append(
                        f"L'index de fin de {pistolet} ne peut pas être inférieur à l'index de départ."
                    )
                    continue

            valeurs_validees[pistolet.pk] = valeur

        # Passe 2 : creation groupee, uniquement si aucune erreur.
        if not erreurs:
            maintenant = timezone.now()
            with transaction.atomic():
                for pistolet in pistolets:
                    ReleveIndexPompiste.objects.create(
                        employee=employee,
                        pistolet=pistolet,
                        type_releve=type_releve,
                        valeur_index=valeurs_validees[pistolet.pk],
                        date_heure=maintenant,
                    )

            if type_releve == ReleveIndexPompiste.FIN:
                return redirect("accounts:pompiste_finaliser")
            return redirect("accounts:pompiste_accueil")

    contexte = {
        "employee": employee,
        "structure": structure,
        "pompe_indisponible": pompe_indisponible,
        "aucune_pompe_station": aucune_pompe_station,
        "aucune_affectation": aucune_affectation,
        "a_depart": a_depart,
        "a_fin": a_fin,
        "session": session,
        "erreurs": erreurs,
    }
    return render(request, "accounts/pompiste_accueil.html", contexte)


@require_employee_login(roles=[Employee.POMPISTE])
def pompiste_finaliser(request):
    """Ecran dedie : affiche le calcul theorique (depuis les PROPRES relevés du pompiste,
    apercu, pas la reference officielle) + la somme deja collectee via les boutons de
    paiement, PUIS demande la saisie manuelle du montant reellement encaisse — jamais
    un simple affichage, le pompiste tape sa vraie somme en toute transparence."""
    from django.shortcuts import get_object_or_404, redirect
    from django.utils import timezone

    from caisse.models import ReleveIndexPompiste, SessionCaisse
    from caisse.services import calculer_apercu_theorique
    from stations.services import pistolets_affectes_a

    employee = request.employee
    aujourdhui = timezone.localtime(timezone.now()).date()
    session = get_object_or_404(SessionCaisse, employee=employee, date=aujourdhui)

    if session.montant_encaisse is not None:
        return redirect("accounts:pompiste_accueil")

    pistolets_ids = list(pistolets_affectes_a(employee).values_list("id", flat=True))
    releves = ReleveIndexPompiste.objects.filter(session_caisse=session)
    premiere_date = releves.order_by("date_heure").values_list("date_heure", flat=True).first()
    derniere_date = releves.order_by("-date_heure").values_list("date_heure", flat=True).first()

    apercu = calculer_apercu_theorique(
        employee.station, pistolets_ids, releves, premiere_date, derniere_date
    )

    # Detail par pistolet (index depart/fin, litres vendus) — structure par Pompe/Face,
    # jamais une liste plate, cohérent avec toutes nos regles d'affichage deja etablies.
    pistolets = pistolets_affectes_a(employee)
    structure = _structurer_pistolets_par_pompe_face(pistolets)
    for groupe_pompe in structure:
        for groupe_face in groupe_pompe["faces"]:
            for pistolet in groupe_face["pistolets"]:
                releve_depart = releves.filter(
                    pistolet=pistolet, type_releve=ReleveIndexPompiste.DEPART
                ).first()
                releve_fin = releves.filter(
                    pistolet=pistolet, type_releve=ReleveIndexPompiste.FIN
                ).first()
                pistolet.index_depart = releve_depart.valeur_index if releve_depart else None
                pistolet.index_fin = releve_fin.valeur_index if releve_fin else None
                if releve_depart and releve_fin:
                    pistolet.litres_vendus = releve_fin.valeur_index - releve_depart.valeur_index
                else:
                    pistolet.litres_vendus = None

    erreurs = []
    if apercu["prix_manquants"]:
        erreurs.append(
            "Prix non configuré pour : " + ", ".join(apercu["prix_manquants"])
            + ". Contactez l'Admin Siège avant de confirmer votre clôture."
        )

    if request.method == "POST":
        montant_brut = request.POST.get("montant_encaisse", "").strip()
        if not montant_brut:
            erreurs.append("Le montant encaissé est obligatoire.")
        else:
            try:
                montant = float(montant_brut)
                if montant < 0:
                    erreurs.append("Le montant ne peut pas être négatif.")
            except ValueError:
                erreurs.append("Montant invalide.")

        if not erreurs:
            session.montant_encaisse = montant
            session.save()
            return redirect("accounts:pompiste_accueil")

    contexte = {
        "employee": employee,
        "session": session,
        "apercu": apercu,
        "structure": structure,
        "erreurs": erreurs,
    }
    return render(request, "accounts/pompiste_finaliser.html", contexte)


@require_employee_login(roles=[Employee.POMPISTE])
def pompiste_ajouter_paiement(request):
    """Incremente un compteur de paiement (cash/wave/orange_money) sur la session du jour,
    de facon atomique (F() expression) pour eviter tout risque d'ecrasement en cas de
    double-clic ou de requetes concurrentes."""
    from django.db.models import F
    from django.utils import timezone

    from caisse.models import SessionCaisse

    if request.method != "POST":
        return redirect("accounts:pompiste_accueil")

    employee = request.employee
    aujourdhui = timezone.localtime(timezone.now()).date()
    methode = request.POST.get("methode")
    montant_brut = request.POST.get("montant", "").strip()

    champs_autorises = {
        "cash": "montant_cash",
        "wave": "montant_wave",
        "orange_money": "montant_orange_money",
    }

    if methode in champs_autorises:
        try:
            montant = float(montant_brut)
            if montant > 0:
                SessionCaisse.objects.filter(employee=employee, date=aujourdhui).update(
                    **{champs_autorises[methode]: F(champs_autorises[methode]) + montant}
                )
        except ValueError:
            pass

    return redirect("accounts:pompiste_accueil")


@require_employee_login(roles=[Employee.POMPISTE])
def pompiste_recu(request, session_id):
    """Recu de cloture (pas 'PV de caisse' — terme reserve au document officiel apres
    confrontation Gerant, qui n'existe pas encore a ce stade). Recapitule la propre
    declaration du pompiste : index, litres vendus (calcules depuis SES relevés), et
    ventilation par methode de paiement."""
    from django.shortcuts import get_object_or_404

    from caisse.models import ReleveIndexPompiste, SessionCaisse
    from stations.services import litres_vendus_par_carburant, pistolets_affectes_a

    employee = request.employee
    # Verification objet, pas seulement isolation par societe : un pompiste ne doit
    # jamais pouvoir consulter le recu d'un autre pompiste.
    session = get_object_or_404(SessionCaisse, pk=session_id, employee=employee)

    releves = ReleveIndexPompiste.objects.filter(session_caisse=session).select_related(
        "pistolet", "pistolet__face", "pistolet__face__pompe"
    ).order_by("date_heure")
    premiere_date = releves.first().date_heure if releves.exists() else None
    derniere_date = releves.last().date_heure if releves.exists() else None

    litres_vendus = {}
    if premiere_date and derniere_date:
        pistolets_ids = list(pistolets_affectes_a(employee).values_list("id", flat=True))
        litres_vendus = litres_vendus_par_carburant(
            pistolets_ids, releves, premiere_date, derniere_date
        )

    # Structure par Pompe/Face pour un vrai tableau (Carburant/Debut/Fin/Vendu), meme
    # composant que pompiste_finaliser — jamais une liste plate.
    pistolets = pistolets_affectes_a(employee)
    structure = _structurer_pistolets_par_pompe_face(pistolets)
    for groupe_pompe in structure:
        for groupe_face in groupe_pompe["faces"]:
            for pistolet in groupe_face["pistolets"]:
                releve_depart = releves.filter(
                    pistolet=pistolet, type_releve=ReleveIndexPompiste.DEPART
                ).first()
                releve_fin = releves.filter(
                    pistolet=pistolet, type_releve=ReleveIndexPompiste.FIN
                ).first()
                pistolet.index_depart = releve_depart.valeur_index if releve_depart else None
                pistolet.index_fin = releve_fin.valeur_index if releve_fin else None
                if releve_depart and releve_fin:
                    pistolet.litres_vendus = releve_fin.valeur_index - releve_depart.valeur_index
                else:
                    pistolet.litres_vendus = None

    contexte = {
        "employee": employee,
        "session": session,
        "structure": structure,
    }
    return render(request, "accounts/pompiste_recu.html", contexte)


@require_employee_login(roles=[Employee.POMPISTE])
def pompiste_historique(request):
    """Liste des sessions de caisse passees du pompiste, avec lien vers chaque recu."""
    from caisse.models import SessionCaisse

    employee = request.employee
    sessions = SessionCaisse.objects.filter(employee=employee).order_by("-date")

    contexte = {"employee": employee, "sessions": sessions}
    return render(request, "accounts/pompiste_historique.html", contexte)


@require_employee_login(roles=[Employee.GERANT, Employee.CHEF_DE_PISTE])
def gerant_accueil(request):
    """Tableau de bord de la station : TOUS les pompistes actifs de la station,
    avec leur statut du jour (pas demarre / en cours / cloture) — pas seulement ceux
    ayant deja une activite, pour que le Gerant/Chef de piste puisse voir qui n'a pas
    encore commence sa journee."""
    from django.utils import timezone

    from caisse.models import ReleveIndexPompiste, SessionCaisse

    employee = request.employee
    aujourdhui = timezone.localtime(timezone.now()).date()

    pompistes = Employee.objects.filter(
        role=Employee.POMPISTE, station=employee.station, actif=True
    ).order_by("nom_complet")

    lignes = []
    for pompiste in pompistes:
        session = SessionCaisse.objects.filter(employee=pompiste, date=aujourdhui).first()

        if session is None:
            statut = "pas_demarre"
        elif session.montant_encaisse is not None:
            statut = "cloture"
        else:
            statut = "en_cours"

        lignes.append({"pompiste": pompiste, "session": session, "statut": statut})

    nb_pas_demarre = sum(1 for l in lignes if l["statut"] == "pas_demarre")
    nb_en_cours = sum(1 for l in lignes if l["statut"] == "en_cours")
    nb_cloture = sum(1 for l in lignes if l["statut"] == "cloture")

    ca_jour = sum(
        l["session"].montant_encaisse
        for l in lignes
        if l["session"] and l["session"].montant_encaisse is not None
    ) or 0

    contexte = {
        "employee": employee,
        "lignes": lignes,
        "nb_pompistes": len(lignes),
        "nb_pas_demarre": nb_pas_demarre,
        "nb_en_cours": nb_en_cours,
        "nb_cloture": nb_cloture,
        "ca_jour": ca_jour,
        "aujourdhui": aujourdhui,
    }
    return render(request, "accounts/gerant_accueil.html", contexte)


@require_employee_login(roles=[Employee.GERANT, Employee.CHEF_DE_PISTE])
def gerant_jauge(request):
    """Saisie de la jauge du matin, par carburant. Le formulaire n'affiche QUE les
    carburants pour lesquels la station possede au moins une Cuve — reflete la realite
    physique reelle de chaque station, pas une liste theorique figee (ex: une station
    sans cuve d'essence ne doit jamais voir de champ Essence)."""
    from django.utils import timezone

    from cuves.models import Cuve, Jauge

    employee = request.employee
    station = employee.station

    carburants_station = list(
        Cuve.objects.filter(station=station).values_list("carburant", flat=True).distinct()
    )

    aujourdhui = timezone.localtime(timezone.now()).date()
    erreurs = []
    succes = False

    if request.method == "POST":
        maintenant = timezone.now()
        for carburant in carburants_station:
            champ = f"quantite_{carburant}"
            valeur_brute = request.POST.get(champ, "").strip()

            if not valeur_brute:
                erreurs.append(f"Quantité manquante pour {carburant}.")
                continue

            try:
                valeur = float(valeur_brute)
            except ValueError:
                erreurs.append(f"Quantité invalide pour {carburant}.")
                continue

            if Jauge.objects.filter(station=station, carburant=carburant, date_jauge=aujourdhui).exists():
                erreurs.append(f"La jauge {carburant} d'aujourd'hui a déjà été saisie.")
                continue

            Jauge.objects.create(
                station=station,
                carburant=carburant,
                quantite=valeur,
                date_jauge=aujourdhui,
                date_mesure=maintenant,
            )

        if not erreurs:
            succes = True

    jauges_du_jour = {
        j.carburant: j
        for j in Jauge.objects.filter(station=station, date_jauge=aujourdhui)
    }

    lignes_carburant = [
        {"carburant": carburant, "jauge_existante": jauges_du_jour.get(carburant)}
        for carburant in carburants_station
    ]
    reste_a_saisir = any(l["jauge_existante"] is None for l in lignes_carburant)

    contexte = {
        "employee": employee,
        "lignes_carburant": lignes_carburant,
        "reste_a_saisir": reste_a_saisir,
        "erreurs": erreurs,
        "succes": succes,
    }
    return render(request, "accounts/gerant_jauge.html", contexte)


@require_employee_login(roles=[Employee.GERANT, Employee.CHEF_DE_PISTE])
def gerant_depense(request):
    """Enregistrement d'une depense de caisse. Accessible aux DEUX roles (Gerant ET
    Chef de piste) — regle metier validee par l'entretien terrain, distincte du depot
    bancaire qui lui est restreint au Gerant uniquement."""
    from django.utils import timezone

    from caisse.models import DepenseCaisse

    employee = request.employee
    station = employee.station

    erreurs = []
    succes = False

    if request.method == "POST":
        montant_brut = request.POST.get("montant", "").strip()
        motif = request.POST.get("motif", "").strip()

        if not montant_brut:
            erreurs.append("Le montant est obligatoire.")
        else:
            try:
                montant = float(montant_brut)
                if montant <= 0:
                    erreurs.append("Le montant doit être supérieur à zéro.")
            except ValueError:
                erreurs.append("Montant invalide.")
                montant = None

        if not motif:
            erreurs.append("Le motif est obligatoire.")

        if not erreurs:
            DepenseCaisse.objects.create(
                station=station,
                employee=employee,
                montant=montant,
                motif=motif,
                date_heure=timezone.now(),
            )
            succes = True

    depenses_du_jour = DepenseCaisse.objects.filter(
        station=station, date_heure__date=timezone.localtime(timezone.now()).date()
    ).order_by("-date_heure")

    contexte = {
        "employee": employee,
        "erreurs": erreurs,
        "succes": succes,
        "depenses_du_jour": depenses_du_jour,
    }
    return render(request, "accounts/gerant_depense.html", contexte)


@require_employee_login(roles=[Employee.GERANT])
def gerant_depot_bancaire(request):
    """Enregistrement d'un depot bancaire. RESTREINT AU GERANT UNIQUEMENT — le Chef de
    piste ne peut jamais faire cette tache (regle metier stricte deja validee). Depot
    volontairement independant du theorique : ne jamais traiter comme une anomalie
    automatique (le Gerant peut garder une partie de l'encaissement pour urgences)."""
    from django.utils import timezone

    from caisse.models import DepotBancaire

    employee = request.employee
    station = employee.station

    erreurs = []
    succes = False

    if request.method == "POST":
        montant_brut = request.POST.get("montant", "").strip()
        banque = request.POST.get("banque", "").strip()
        reference = request.POST.get("reference", "").strip()

        if not montant_brut:
            erreurs.append("Le montant est obligatoire.")
        else:
            try:
                montant = float(montant_brut)
                if montant <= 0:
                    erreurs.append("Le montant doit être supérieur à zéro.")
            except ValueError:
                erreurs.append("Montant invalide.")
                montant = None

        if not banque:
            erreurs.append("Le nom de la banque/agence est obligatoire.")

        if not erreurs:
            DepotBancaire.objects.create(
                station=station,
                employee=employee,
                montant=montant,
                banque=banque,
                reference=reference,
                date_heure=timezone.now(),
            )
            succes = True

    depots_du_jour = DepotBancaire.objects.filter(
        station=station, date_heure__date=timezone.localtime(timezone.now()).date()
    ).order_by("-date_heure")

    contexte = {
        "employee": employee,
        "erreurs": erreurs,
        "succes": succes,
        "depots_du_jour": depots_du_jour,
    }
    return render(request, "accounts/gerant_depot_bancaire.html", contexte)


@require_employee_login(roles=[Employee.GERANT, Employee.CHEF_DE_PISTE])
def gerant_releve_pompiste(request, pompiste_id):
    """Relevé indépendant du Gérant/Chef de piste pour UN pompiste précis (double
    vérification croisée, jamais confiance aveugle envers le pompiste). Même logique de
    formulaire groupé qu'en cote Pompiste, mais avec capture explicite de l'IntegrityError
    (contrainte d'exclusivité sur ReleveIndexGerant) pour un message clair plutot qu'une
    erreur serveur brute."""
    from django.db import IntegrityError, transaction
    from django.shortcuts import get_object_or_404
    from django.utils import timezone

    from caisse.models import ReleveIndexGerant, SessionCaisse
    from stations.services import pistolets_affectes_a

    employee = request.employee
    pompiste = get_object_or_404(Employee, pk=pompiste_id, role=Employee.POMPISTE, station=employee.station)

    pistolets = pistolets_affectes_a(pompiste)
    structure = _structurer_pistolets_par_pompe_face(pistolets)

    aujourdhui = timezone.localtime(timezone.now()).date()
    session_pompiste = SessionCaisse.objects.filter(employee=pompiste, date=aujourdhui).first()

    a_depart_gerant = ReleveIndexGerant.objects.filter(
        employee_pompiste=pompiste, date_heure__date=aujourdhui, type_releve=ReleveIndexGerant.DEPART
    ).exists()
    a_fin_gerant = ReleveIndexGerant.objects.filter(
        employee_pompiste=pompiste, date_heure__date=aujourdhui, type_releve=ReleveIndexGerant.FIN
    ).exists()

    a_montant_verse = session_pompiste is not None and session_pompiste.montant_verse_gerant is not None

    if a_fin_gerant and not a_montant_verse:
        return redirect("accounts:gerant_finaliser", pompiste_id=pompiste.pk)

    erreurs = []

    if request.method == "POST" and not a_fin_gerant:
        type_releve = ReleveIndexGerant.DEPART if not a_depart_gerant else ReleveIndexGerant.FIN
        valeurs_validees = {}

        # Passe 1 : validation complete avant toute creation.
        for pistolet in pistolets:
            champ = f"index_{pistolet.pk}"
            valeur_brute = request.POST.get(champ, "").strip()

            if not valeur_brute:
                erreurs.append(f"Index manquant pour {pistolet}.")
                continue

            try:
                valeur = float(valeur_brute)
            except ValueError:
                erreurs.append(f"Index invalide pour {pistolet}.")
                continue

            if type_releve == ReleveIndexGerant.FIN:
                releve_depart = ReleveIndexGerant.objects.filter(
                    employee_pompiste=pompiste, pistolet=pistolet, type_releve=ReleveIndexGerant.DEPART
                ).first()
                if releve_depart and valeur < float(releve_depart.valeur_index):
                    erreurs.append(
                        f"L'index de fin de {pistolet} ne peut pas être inférieur à l'index de départ."
                    )
                    continue

            valeurs_validees[pistolet.pk] = valeur

        # Passe 2 : creation groupee, avec capture explicite de l'IntegrityError
        # (contrainte d'exclusivite) — jamais une erreur serveur brute.
        if not erreurs:
            maintenant = timezone.now()
            try:
                with transaction.atomic():
                    for pistolet in pistolets:
                        ReleveIndexGerant.objects.create(
                            employee=employee,
                            employee_pompiste=pompiste,
                            pistolet=pistolet,
                            type_releve=type_releve,
                            valeur_index=valeurs_validees[pistolet.pk],
                            date_heure=maintenant,
                        )
            except IntegrityError:
                releve_existant = ReleveIndexGerant.objects.filter(
                    employee_pompiste=pompiste, type_releve=type_releve, date_heure__date=aujourdhui
                ).select_related("employee").first()
                if releve_existant:
                    erreurs.append(
                        f"Ce relevé a déjà été saisi par {releve_existant.employee.nom_complet}, "
                        f"à {timezone.localtime(releve_existant.date_heure):%H:%M}."
                    )
                else:
                    erreurs.append("Ce relevé a déjà été saisi par un autre utilisateur.")
            else:
                return redirect("accounts:gerant_releve_pompiste", pompiste_id=pompiste.pk)

    peut_confronter = (
        a_fin_gerant
        and session_pompiste is not None
        and session_pompiste.montant_encaisse is not None
    )

    contexte = {
        "employee": employee,
        "pompiste": pompiste,
        "structure": structure,
        "a_depart_gerant": a_depart_gerant,
        "a_fin_gerant": a_fin_gerant,
        "session_pompiste": session_pompiste,
        "peut_confronter": peut_confronter,
        "erreurs": erreurs,
    }
    return render(request, "accounts/gerant_releve_pompiste.html", contexte)


@require_employee_login(roles=[Employee.GERANT, Employee.CHEF_DE_PISTE])
def gerant_confronter(request, pompiste_id):
    """Declenchement EXPLICITE de la confrontation (jamais automatique/silencieux, ce
    sont des fonctions a effet de bord). Verifie a nouveau peut_confronter cote serveur
    (pas seulement cache le bouton cote template) avant d'agir — defense en profondeur."""
    from django.shortcuts import get_object_or_404
    from django.utils import timezone

    from caisse.models import ReleveIndexGerant, ReleveIndexPompiste, SessionCaisse
    from caisse.services import appliquer_resultat_au_solde, confronter_session_caisse
    from stations.services import pistolets_affectes_a

    employee = request.employee
    pompiste = get_object_or_404(Employee, pk=pompiste_id, role=Employee.POMPISTE, station=employee.station)

    aujourdhui = timezone.localtime(timezone.now()).date()
    session = get_object_or_404(SessionCaisse, employee=pompiste, date=aujourdhui)

    if session.montant_encaisse is None:
        # Meme controle que peut_confronter dans gerant_releve_pompiste : jamais
        # confronter sans que le pompiste ait clos sa propre declaration.
        return redirect("accounts:gerant_releve_pompiste", pompiste_id=pompiste.pk)

    societe = request.societe

    session = confronter_session_caisse(session, societe.marge_tolerance_divergence_litres)
    session.save()

    alerte_dette = appliquer_resultat_au_solde(session, societe.seuil_alerte_dette_fcfa)

    # Detail par pistolet : les DEUX relevés cote a cote (Gerant, qui fait foi, ET
    # Pompiste, pour permettre de localiser precisement ou se situe une divergence —
    # jamais seulement un total agrege en litres qui ne dit pas quel pistolet diverge).
    releves_gerant = ReleveIndexGerant.objects.filter(
        employee_pompiste=pompiste, date_heure__date=aujourdhui
    )
    releves_pompiste = ReleveIndexPompiste.objects.filter(
        session_caisse=session
    )
    pistolets = pistolets_affectes_a(pompiste)
    structure = _structurer_pistolets_par_pompe_face(pistolets)
    for groupe_pompe in structure:
        for groupe_face in groupe_pompe["faces"]:
            for pistolet in groupe_face["pistolets"]:
                releve_depart = releves_gerant.filter(
                    pistolet=pistolet, type_releve=ReleveIndexGerant.DEPART
                ).first()
                releve_fin = releves_gerant.filter(
                    pistolet=pistolet, type_releve=ReleveIndexGerant.FIN
                ).first()
                pistolet.index_depart = releve_depart.valeur_index if releve_depart else None
                pistolet.index_fin = releve_fin.valeur_index if releve_fin else None
                if releve_depart and releve_fin:
                    pistolet.litres_vendus = releve_fin.valeur_index - releve_depart.valeur_index
                else:
                    pistolet.litres_vendus = None

                releve_depart_pompiste = releves_pompiste.filter(
                    pistolet=pistolet, type_releve="depart"
                ).first()
                releve_fin_pompiste = releves_pompiste.filter(
                    pistolet=pistolet, type_releve="fin"
                ).first()
                pistolet.index_depart_pompiste = releve_depart_pompiste.valeur_index if releve_depart_pompiste else None
                pistolet.index_fin_pompiste = releve_fin_pompiste.valeur_index if releve_fin_pompiste else None
                if releve_depart_pompiste and releve_fin_pompiste:
                    pistolet.litres_vendus_pompiste = releve_fin_pompiste.valeur_index - releve_depart_pompiste.valeur_index
                else:
                    pistolet.litres_vendus_pompiste = None

    contexte = {
        "employee": employee,
        "pompiste": pompiste,
        "session": session,
        "structure": structure,
        "alerte_dette": alerte_dette,
    }
    return render(request, "accounts/gerant_confrontation_resultat.html", contexte)


@require_employee_login(roles=[Employee.GERANT, Employee.CHEF_DE_PISTE])
def gerant_finaliser(request, pompiste_id):
    """Ecran dedie, symetrique a pompiste_finaliser : affiche le calcul theorique depuis
    les PROPRES relevés du Gerant (apercu, pas encore la confrontation officielle),
    detail par pistolet en tableau, PUIS demande la saisie manuelle du montant
    physiquement compte par le Gerant — jamais un simple affichage, en toute
    transparence pour que le Gerant puisse verifier son propre calcul."""
    from django.shortcuts import get_object_or_404, redirect
    from django.utils import timezone

    from caisse.models import ReleveIndexGerant, SessionCaisse
    from caisse.services import calculer_apercu_theorique
    from stations.services import pistolets_affectes_a

    employee = request.employee
    pompiste = get_object_or_404(Employee, pk=pompiste_id, role=Employee.POMPISTE, station=employee.station)

    aujourdhui = timezone.localtime(timezone.now()).date()
    session_pompiste = get_object_or_404(SessionCaisse, employee=pompiste, date=aujourdhui)

    if session_pompiste.montant_verse_gerant is not None:
        return redirect("accounts:gerant_releve_pompiste", pompiste_id=pompiste.pk)

    pistolets_ids = list(pistolets_affectes_a(pompiste).values_list("id", flat=True))
    releves = ReleveIndexGerant.objects.filter(
        employee_pompiste=pompiste, date_heure__date=aujourdhui
    )
    premiere_date = releves.order_by("date_heure").values_list("date_heure", flat=True).first()
    derniere_date = releves.order_by("-date_heure").values_list("date_heure", flat=True).first()

    apercu = calculer_apercu_theorique(
        employee.station, pistolets_ids, releves, premiere_date, derniere_date
    )

    # Detail par pistolet (index depart/fin, litres vendus) — structure par Pompe/Face,
    # meme composant que pompiste_finaliser.
    pistolets = pistolets_affectes_a(pompiste)
    structure = _structurer_pistolets_par_pompe_face(pistolets)
    for groupe_pompe in structure:
        for groupe_face in groupe_pompe["faces"]:
            for pistolet in groupe_face["pistolets"]:
                releve_depart = releves.filter(
                    pistolet=pistolet, type_releve=ReleveIndexGerant.DEPART
                ).first()
                releve_fin = releves.filter(
                    pistolet=pistolet, type_releve=ReleveIndexGerant.FIN
                ).first()
                pistolet.index_depart = releve_depart.valeur_index if releve_depart else None
                pistolet.index_fin = releve_fin.valeur_index if releve_fin else None
                if releve_depart and releve_fin:
                    pistolet.litres_vendus = releve_fin.valeur_index - releve_depart.valeur_index
                else:
                    pistolet.litres_vendus = None

    erreurs = []
    if apercu["prix_manquants"]:
        erreurs.append(
            "Prix non configuré pour : " + ", ".join(apercu["prix_manquants"])
            + ". Contactez l'Admin Siège avant de confirmer la vérification."
        )

    if request.method == "POST":
        montant_brut = request.POST.get("montant_verse_gerant", "").strip()
        if not montant_brut:
            erreurs.append("Le montant versé est obligatoire.")
        else:
            try:
                montant = float(montant_brut)
                if montant < 0:
                    erreurs.append("Le montant ne peut pas être négatif.")
            except ValueError:
                erreurs.append("Montant invalide.")

        if not erreurs:
            session_pompiste.montant_verse_gerant = montant
            session_pompiste.save()
            return redirect("accounts:gerant_releve_pompiste", pompiste_id=pompiste.pk)

    contexte = {
        "employee": employee,
        "pompiste": pompiste,
        "apercu": apercu,
        "structure": structure,
        "erreurs": erreurs,
    }
    return render(request, "accounts/gerant_finaliser.html", contexte)


@require_employee_login(roles=[Employee.ADMIN_SIEGE])
def admin_accueil(request):
    """Tableau de bord Admin Siege : vue consolidee sur TOUTES les stations de la
    societe (pas une seule, contrairement a gerant_accueil) — c'est le differenciateur
    principal de la plateforme, la visibilite que le siege n'avait jamais eu avant.
    Uniquement des donnees REELLES deja construites — aucune section fictive (pas de
    ventes unitaires, stocks, finance, abonnement : jamais construits)."""
    from django.utils import timezone

    from caisse.models import SessionCaisse, SoldePompiste
    from stations.models import Station

    employee = request.employee
    societe = request.societe
    aujourdhui = timezone.localtime(timezone.now()).date()

    stations = Station.objects.filter(actif=True)
    nb_stations_actives = stations.count()
    nb_stations_total = Station.objects.count()

    nb_employes = Employee.objects.filter(actif=True).count()

    sessions_jour = SessionCaisse.objects.filter(date=aujourdhui)
    ca_jour = sum(
        s.montant_encaisse for s in sessions_jour if s.montant_encaisse is not None
    ) or 0

    nb_manquant = sessions_jour.filter(resultat=SessionCaisse.MANQUANT).count()
    nb_surplus = sessions_jour.filter(resultat=SessionCaisse.SURPLUS).count()
    nb_exact = sessions_jour.filter(resultat=SessionCaisse.EXACT).count()

    nb_divergences_jour = sessions_jour.filter(divergence_signalee=True).count()

    # Dettes au-dela du seuil d'alerte de la societe — meme logique que
    # appliquer_resultat_au_solde, mais releve ici, pas declenche.
    pompistes_en_dette = []
    for solde in SoldePompiste.objects.select_related("employee").filter(solde_courant__lt=0):
        if abs(solde.solde_courant) > societe.seuil_alerte_dette_fcfa:
            pompistes_en_dette.append(solde)

    contexte = {
        "employee": employee,
        "societe": societe,
        "aujourdhui": aujourdhui,
        "stations": stations,
        "nb_stations_actives": nb_stations_actives,
        "nb_stations_total": nb_stations_total,
        "nb_employes": nb_employes,
        "ca_jour": ca_jour,
        "nb_manquant": nb_manquant,
        "nb_surplus": nb_surplus,
        "nb_exact": nb_exact,
        "nb_divergences_jour": nb_divergences_jour,
        "pompistes_en_dette": pompistes_en_dette,
        "vue_active": "dashboard",
    }
    return render(request, "accounts/admin_accueil.html", contexte)


@require_employee_login(roles=[Employee.ADMIN_SIEGE])
def admin_stations(request):
    """Liste de TOUTES les stations de la societe, avec le nombre reel de pompes et de
    pistolets pour chacune — pas de scalabilite illimitee dans cette premiere version
    (pas de pagination), a revoir si un client depasse une dizaine de stations."""
    from stations.models import Station

    employee = request.employee
    societe = request.societe

    stations = Station.objects.all().order_by("nom")
    from stations.models import Pistolet

    lignes = []
    for station in stations:
        nb_pompes = station.pompes.count()
        nb_pistolets = Pistolet.objects.filter(face__pompe__station=station).count()
        lignes.append({"station": station, "nb_pompes": nb_pompes, "nb_pistolets": nb_pistolets})

    contexte = {
        "employee": employee,
        "societe": societe,
        "lignes": lignes,
        "vue_active": "stations",
    }
    return render(request, "accounts/admin_stations.html", contexte)


@require_employee_login(roles=[Employee.ADMIN_SIEGE])
def admin_station_detail(request, station_id):
    """Detail d'UNE station : toutes ses pompes/faces/pistolets, structures (jamais une
    liste plate), reutilise le meme helper que les ecrans Pompiste/Gerant. Cuves
    deplacees vers l onglet dedie admin_station_cuves (chantier navigation)."""
    from django.shortcuts import get_object_or_404

    from stations.models import Pistolet, Station

    employee = request.employee
    societe = request.societe
    station = get_object_or_404(Station, pk=station_id)

    pistolets = Pistolet.objects.filter(face__pompe__station=station)
    structure = _structurer_pistolets_par_pompe_face(pistolets)

    contexte = {
        "employee": employee,
        "societe": societe,
        "station": station,
        "structure": structure,
        "onglet_actif": "pompes",
        "vue_active": "stations",
    }
    return render(request, "accounts/admin_station_detail.html", contexte)


@require_employee_login(roles=[Employee.ADMIN_SIEGE])
def admin_employes(request):
    """Liste de TOUS les employes de la societe, avec filtre optionnel par station via
    parametre GET — jamais une liste plate sans filtre pour une societe a plusieurs
    stations (priorite n°4, exigence de scalabilite deja validee)."""
    from stations.models import Station

    employee = request.employee
    societe = request.societe

    stations = Station.objects.all().order_by("nom")

    station_id_filtre = request.GET.get("station", "").strip()
    employes = Employee.objects.select_related("station").order_by("station__nom", "nom_complet")

    station_selectionnee = None
    if station_id_filtre:
        try:
            station_selectionnee = stations.get(pk=station_id_filtre)
            employes = employes.filter(station=station_selectionnee)
        except Station.DoesNotExist:
            pass

    contexte = {
        "employee": employee,
        "societe": societe,
        "stations": stations,
        "employes": employes,
        "station_selectionnee": station_selectionnee,
        "vue_active": "employes",
    }
    return render(request, "accounts/admin_employes.html", contexte)


@require_employee_login(roles=[Employee.ADMIN_SIEGE])
def admin_employe_toggle_actif(request, employee_id):
    """Active/desactive un employe (bascule). Jamais de suppression ici — desactiver
    coupe l'acces (actif=False deja utilise par employee_login), sans toucher a
    l'historique. Action POST uniquement (effet de bord), jamais un simple lien GET."""
    from django.shortcuts import get_object_or_404
    from django.utils.http import url_has_allowed_host_and_scheme

    if request.method != "POST":
        return redirect("accounts:admin_employes")

    cible = get_object_or_404(Employee, pk=employee_id)
    cible.actif = not cible.actif
    cible.save(update_fields=["actif"])

    next_url = request.POST.get("next", "")
    if next_url and url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return redirect(next_url)
    return redirect("accounts:admin_employes")


@require_employee_login(roles=[Employee.ADMIN_SIEGE])
def admin_employe_gerer(request, employee_id):
    """Modification complete d'un employe : nom, telephone, role, station, mot de passe
    (optionnel). Si l'employe a des affectations physiques actives (pompe/face) au moment
    d'un changement de role/station, elles sont desaffectees AUTOMATIQUEMENT (jamais
    silencieusement : le formulaire affiche un avertissement explicite avant validation,
    et le resultat est confirme apres coup) — plus pratique qu'un blocage dur, tout en
    evitant absolument qu'une Pompe/Face pointe vers un employe qui n'est plus sur
    cette station ou n'est plus pompiste."""
    from django.db import transaction
    from django.shortcuts import get_object_or_404

    from stations.models import Face, Pompe, Station

    employee = request.employee
    societe = request.societe
    cible = get_object_or_404(Employee, pk=employee_id)
    stations = Station.objects.all().order_by("nom")

    a_des_affectations = (
        Pompe.objects.filter(employee_affecte=cible).exists()
        or Face.objects.filter(employee_affecte=cible).exists()
    )

    from django.utils import timezone

    from caisse.models import SessionCaisse

    aujourdhui = timezone.localtime(timezone.now()).date()
    a_caisse_ouverte = SessionCaisse.objects.filter(
        employee=cible, date=aujourdhui, montant_encaisse__isnull=True
    ).exists()

    erreurs = []
    desaffectation_effectuee = False

    if request.method == "POST":
        nouveau_nom = request.POST.get("nom_complet", "").strip()
        nouveau_telephone = request.POST.get("telephone", "").strip()
        nouveau_role = request.POST.get("role", "").strip()
        nouveau_station_id = request.POST.get("station", "").strip()
        nouveau_mot_de_passe = request.POST.get("nouveau_mot_de_passe", "").strip()

        if not nouveau_nom:
            erreurs.append("Le nom complet est obligatoire.")

        if not nouveau_telephone:
            erreurs.append("Le téléphone est obligatoire.")
        elif Employee.objects.filter(telephone=nouveau_telephone).exclude(pk=cible.pk).exists():
            erreurs.append("Ce numéro de téléphone est déjà utilisé par un autre employé.")

        roles_valides = dict(Employee.ROLE_CHOICES)
        if nouveau_role not in roles_valides:
            erreurs.append("Rôle invalide.")

        nouvelle_station = None
        if nouveau_role != Employee.ADMIN_SIEGE:
            if not nouveau_station_id:
                erreurs.append("Une station est obligatoire pour ce rôle.")
            else:
                try:
                    nouvelle_station = stations.get(pk=nouveau_station_id)
                except Station.DoesNotExist:
                    erreurs.append("Station invalide.")

        role_ou_station_change = (
            nouveau_role != cible.role
            or (nouvelle_station.pk if nouvelle_station else None) != cible.station_id
        )
        if role_ou_station_change and a_caisse_ouverte:
            erreurs.append(
                "Impossible de changer le rôle ou la station : cet employé a une "
                "caisse ouverte non clôturée aujourd'hui. Demandez d'abord au "
                "Gérant de clôturer et de confronter sa caisse."
            )

        if nouveau_mot_de_passe and len(nouveau_mot_de_passe) < 8:
            erreurs.append("Le nouveau mot de passe doit contenir au moins 8 caractères.")

        if not erreurs:
            with transaction.atomic():
                if a_des_affectations:
                    Pompe.objects.filter(employee_affecte=cible).update(employee_affecte=None)
                    Face.objects.filter(employee_affecte=cible).update(employee_affecte=None)
                    desaffectation_effectuee = True

                cible.nom_complet = nouveau_nom
                cible.telephone = nouveau_telephone
                cible.role = nouveau_role
                cible.station = nouvelle_station
                if nouveau_mot_de_passe:
                    cible.set_password(nouveau_mot_de_passe)
                cible.save()

            url_confirmation = request.path + "?succes=1"
            if desaffectation_effectuee:
                url_confirmation += "&desaffecte=1"
            return redirect(url_confirmation)

    contexte = {
        "employee": employee,
        "societe": societe,
        "cible": cible,
        "stations": stations,
        "a_des_affectations": a_des_affectations,
        "desaffectation_effectuee": desaffectation_effectuee,
        "erreurs": erreurs,
        "vue_active": "employes",
    }
    return render(request, "accounts/admin_employe_gerer.html", contexte)


def _raisons_blocage_suppression_employe(cible):
    """Verification EXHAUSTIVE de toute donnee referencant cet employe, avant toute
    tentative de suppression — jamais une IntegrityError brute capturee apres coup.
    Couvre les 6 modeles avec PROTECT vers Employee (SessionCaisse, ReleveIndexPompiste,
    ReleveIndexGerant via ses deux FK, DepotBancaire, DepenseCaisse), PLUS EcritureSolde
    qui n'est techniquement PAS protegee (CASCADE via SoldePompiste) mais doit quand
    meme bloquer la suppression pour preserver l'immutabilite du livre de comptes —
    principe deja pose des la conception de ce modele. Retourne une liste de raisons
    lisibles, vide si la suppression est possible."""
    from caisse.models import (
        DepenseCaisse,
        DepotBancaire,
        EcritureSolde,
        ReleveIndexGerant,
        ReleveIndexPompiste,
        SessionCaisse,
        SoldePompiste,
    )
    from stations.models import Face, Pompe

    raisons = []

    if SessionCaisse.objects.filter(employee=cible).exists():
        raisons.append("des sessions de caisse enregistrées")
    if ReleveIndexPompiste.objects.filter(employee=cible).exists():
        raisons.append("des relevés d'index en tant que pompiste")
    if ReleveIndexGerant.objects.filter(employee=cible).exists():
        raisons.append("des relevés d'index en tant que Gérant/Chef de piste")
    if ReleveIndexGerant.objects.filter(employee_pompiste=cible).exists():
        raisons.append("des relevés de vérification le concernant en tant que pompiste vérifié")
    if DepotBancaire.objects.filter(employee=cible).exists():
        raisons.append("des dépôts bancaires enregistrés")
    if DepenseCaisse.objects.filter(employee=cible).exists():
        raisons.append("des dépenses de caisse enregistrées")

    solde = SoldePompiste.objects.filter(employee=cible).first()
    if solde is not None and EcritureSolde.objects.filter(solde_pompiste=solde).exists():
        raisons.append("des écritures dans son livre de solde (avoir/dette)")

    if Pompe.objects.filter(employee_affecte=cible).exists() or Face.objects.filter(employee_affecte=cible).exists():
        raisons.append("des affectations physiques actives (pompe ou face)")

    return raisons


@require_employee_login(roles=[Employee.ADMIN_SIEGE])
def admin_employe_supprimer(request, employee_id):
    """Suppression definitive d'un employe — reservee aux cas ou AUCUNE donnee ne le
    reference (verification exhaustive via _raisons_blocage_suppression_employe).
    Deux etapes : GET affiche la confirmation (avec les raisons de blocage le cas
    echeant), POST avec confirmation explicite effectue la suppression."""
    from django.shortcuts import get_object_or_404

    employee = request.employee
    societe = request.societe
    cible = get_object_or_404(Employee, pk=employee_id)

    raisons_blocage = _raisons_blocage_suppression_employe(cible)

    if request.method == "POST" and not raisons_blocage:
        if request.POST.get("confirmer") == "oui":
            cible.delete()
            return redirect("accounts:admin_employes")

    contexte = {
        "employee": employee,
        "societe": societe,
        "cible": cible,
        "raisons_blocage": raisons_blocage,
        "vue_active": "employes",
    }
    return render(request, "accounts/admin_employe_supprimer.html", contexte)


@require_employee_login(roles=[Employee.ADMIN_SIEGE])
def admin_station_creer(request):
    """Creation d'une nouvelle station de la societe. Premiere fondation de l'etape A
    (station -> pompe/face/pistolet -> utilisateur, dans cet ordre de dependances).
    Le prix Gasoil ET Essence sont OBLIGATOIRES a la creation — jamais une station
    sans prix configure, qui produirait silencieusement un calcul theorique a 0 FCFA
    (bug d'integrite financiere deja rencontre et corrige). Meme logique pour la
    Cuve + Jauge de depart de chaque carburant — sans ca, l ecran Jauge du matin du
    Gerant reste vide/inutilisable (filtre deja sur les carburants ayant une Cuve)."""
    from decimal import Decimal, InvalidOperation

    from django.utils import timezone

    from cuves.models import Cuve, Jauge
    from stations.constants import ESSENCE, GASOIL
    from stations.models import PrixCarburant, Station

    employee = request.employee
    societe = request.societe

    erreurs = []

    if request.method == "POST":
        nom = request.POST.get("nom", "").strip()
        adresse = request.POST.get("adresse", "").strip()
        prix_gasoil_brut = request.POST.get("prix_gasoil", "").strip()
        prix_essence_brut = request.POST.get("prix_essence", "").strip()
        capacite_gasoil_brut = request.POST.get("capacite_gasoil", "").strip()
        stock_gasoil_brut = request.POST.get("stock_gasoil", "").strip()
        capacite_essence_brut = request.POST.get("capacite_essence", "").strip()
        stock_essence_brut = request.POST.get("stock_essence", "").strip()

        if not nom:
            erreurs.append("Le nom de la station est obligatoire.")
        elif Station.objects.filter(nom__iexact=nom).exists():
            erreurs.append("Une station porte déjà ce nom.")

        prix_gasoil = None
        prix_essence = None

        if not prix_gasoil_brut:
            erreurs.append("Le prix du Gasoil est obligatoire.")
        else:
            try:
                prix_gasoil = Decimal(prix_gasoil_brut)
                if prix_gasoil <= 0:
                    erreurs.append("Le prix du Gasoil doit être supérieur à zéro.")
            except InvalidOperation:
                erreurs.append("Prix du Gasoil invalide.")

        if not prix_essence_brut:
            erreurs.append("Le prix de l'Essence est obligatoire.")
        else:
            try:
                prix_essence = Decimal(prix_essence_brut)
                if prix_essence <= 0:
                    erreurs.append("Le prix de l'Essence doit être supérieur à zéro.")
            except InvalidOperation:
                erreurs.append("Prix de l'Essence invalide.")

        capacite_gasoil = None
        stock_gasoil = None
        if not capacite_gasoil_brut:
            erreurs.append("La capacité de la cuve Gasoil est obligatoire.")
        else:
            try:
                capacite_gasoil = Decimal(capacite_gasoil_brut)
                if capacite_gasoil <= 0:
                    erreurs.append("La capacité de la cuve Gasoil doit être supérieure à zéro.")
            except InvalidOperation:
                erreurs.append("Capacité Gasoil invalide.")
        if not stock_gasoil_brut:
            erreurs.append("Le stock de départ Gasoil est obligatoire.")
        else:
            try:
                stock_gasoil = Decimal(stock_gasoil_brut)
                if stock_gasoil < 0:
                    erreurs.append("Le stock de départ Gasoil ne peut pas être négatif.")
            except InvalidOperation:
                erreurs.append("Stock de départ Gasoil invalide.")
        if capacite_gasoil is not None and stock_gasoil is not None and stock_gasoil > capacite_gasoil:
            erreurs.append("Le stock de départ Gasoil ne peut pas dépasser la capacité de la cuve.")

        capacite_essence = None
        stock_essence = None
        if not capacite_essence_brut:
            erreurs.append("La capacité de la cuve Essence est obligatoire.")
        else:
            try:
                capacite_essence = Decimal(capacite_essence_brut)
                if capacite_essence <= 0:
                    erreurs.append("La capacité de la cuve Essence doit être supérieure à zéro.")
            except InvalidOperation:
                erreurs.append("Capacité Essence invalide.")
        if not stock_essence_brut:
            erreurs.append("Le stock de départ Essence est obligatoire.")
        else:
            try:
                stock_essence = Decimal(stock_essence_brut)
                if stock_essence < 0:
                    erreurs.append("Le stock de départ Essence ne peut pas être négatif.")
            except InvalidOperation:
                erreurs.append("Stock de départ Essence invalide.")
        if capacite_essence is not None and stock_essence is not None and stock_essence > capacite_essence:
            erreurs.append("Le stock de départ Essence ne peut pas dépasser la capacité de la cuve.")

        if not erreurs:
            maintenant = timezone.now()
            station = Station.objects.create(nom=nom, adresse=adresse, actif=True)
            PrixCarburant.objects.create(
                station=station, carburant=GASOIL, prix_au_litre=prix_gasoil, date_debut=maintenant,
            )
            PrixCarburant.objects.create(
                station=station, carburant=ESSENCE, prix_au_litre=prix_essence, date_debut=maintenant,
            )
            aujourdhui = timezone.localtime(maintenant).date()
            Cuve.objects.create(station=station, carburant=GASOIL, capacite=capacite_gasoil, actif=True)
            Jauge.objects.create(
                station=station, carburant=GASOIL, quantite=stock_gasoil,
                date_jauge=aujourdhui, date_mesure=maintenant,
            )
            Cuve.objects.create(station=station, carburant=ESSENCE, capacite=capacite_essence, actif=True)
            Jauge.objects.create(
                station=station, carburant=ESSENCE, quantite=stock_essence,
                date_jauge=aujourdhui, date_mesure=maintenant,
            )
            return redirect("accounts:admin_station_detail", station_id=station.pk)

    contexte = {
        "employee": employee,
        "societe": societe,
        "erreurs": erreurs,
        "vue_active": "stations",
    }
    return render(request, "accounts/admin_station_creer.html", contexte)


@require_employee_login(roles=[Employee.ADMIN_SIEGE])
def admin_station_toggle_actif(request, station_id):
    """Active/desactive une station (bascule). Jamais de suppression ici — meme
    logique que admin_employe_toggle_actif. Action POST uniquement."""
    from django.shortcuts import get_object_or_404
    from django.utils.http import url_has_allowed_host_and_scheme

    from stations.models import Station

    if request.method != "POST":
        return redirect("accounts:admin_stations")

    station = get_object_or_404(Station, pk=station_id)
    station.actif = not station.actif
    station.save(update_fields=["actif"])

    next_url = request.POST.get("next", "")
    if next_url and url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return redirect(next_url)
    return redirect("accounts:admin_stations")


def _raisons_blocage_suppression_station(station):
    """Verification EXHAUSTIVE de toute donnee referencant cette station, avant toute
    tentative de suppression. Toutes les FK vers Station sont en CASCADE (pas PROTECT) —
    une suppression silencieuse effacerait tout l'historique operationnel (pompes,
    prix, depots, depenses, cuves, jauges, depotages). On bloque quand meme
    explicitement, cohérent avec le principe d'immutabilite deja applique partout
    ailleurs dans ce projet (meme logique que pour la suppression d'un employe)."""
    from caisse.models import DepenseCaisse, DepotBancaire
    from cuves.models import Cuve, Depotage, Jauge
    from stations.models import Pompe, PrixCarburant

    raisons = []

    if Employee.objects.filter(station=station).exists():
        raisons.append("des employés rattachés")
    if Pompe.objects.filter(station=station).exists():
        raisons.append("des pompes enregistrées")
    if PrixCarburant.objects.filter(station=station).exists():
        raisons.append("des prix carburant configurés")
    if DepotBancaire.objects.filter(station=station).exists():
        raisons.append("des dépôts bancaires enregistrés")
    if DepenseCaisse.objects.filter(station=station).exists():
        raisons.append("des dépenses de caisse enregistrées")
    if Cuve.objects.filter(station=station).exists():
        raisons.append("des cuves enregistrées")
    if Jauge.objects.filter(station=station).exists():
        raisons.append("des relevés de jauge enregistrés")
    if Depotage.objects.filter(station=station).exists():
        raisons.append("des dépotages enregistrés")

    return raisons


@require_employee_login(roles=[Employee.ADMIN_SIEGE])
def admin_station_gerer(request, station_id):
    """Modification du nom/adresse d'une station, avec acces (lien) vers l'activation
    et la suppression — meme point d'entree unique que pour un employe."""
    from django.shortcuts import get_object_or_404

    from stations.models import Station

    employee = request.employee
    societe = request.societe
    cible = get_object_or_404(Station, pk=station_id)

    erreurs = []

    if request.method == "POST":
        nouveau_nom = request.POST.get("nom", "").strip()
        nouvelle_adresse = request.POST.get("adresse", "").strip()

        if not nouveau_nom:
            erreurs.append("Le nom de la station est obligatoire.")
        elif Station.objects.filter(nom__iexact=nouveau_nom).exclude(pk=cible.pk).exists():
            erreurs.append("Une autre station porte déjà ce nom.")

        if not erreurs:
            cible.nom = nouveau_nom
            cible.adresse = nouvelle_adresse
            cible.save(update_fields=["nom", "adresse"])
            return redirect(request.path + "?succes=1")

    contexte = {
        "employee": employee,
        "societe": societe,
        "cible": cible,
        "erreurs": erreurs,
        "vue_active": "stations",
    }
    return render(request, "accounts/admin_station_gerer.html", contexte)


@require_employee_login(roles=[Employee.ADMIN_SIEGE])
def admin_station_supprimer(request, station_id):
    """Suppression definitive d'une station — reservee aux cas ou AUCUNE donnee
    operationnelle ne la reference (verification exhaustive via
    _raisons_blocage_suppression_station). Meme flux en deux etapes que pour un
    employe."""
    from django.shortcuts import get_object_or_404

    from stations.models import Station

    employee = request.employee
    societe = request.societe
    cible = get_object_or_404(Station, pk=station_id)

    raisons_blocage = _raisons_blocage_suppression_station(cible)

    if request.method == "POST" and not raisons_blocage:
        if request.POST.get("confirmer") == "oui":
            cible.delete()
            return redirect("accounts:admin_stations")

    contexte = {
        "employee": employee,
        "societe": societe,
        "cible": cible,
        "raisons_blocage": raisons_blocage,
        "vue_active": "stations",
    }
    return render(request, "accounts/admin_station_supprimer.html", contexte)


@require_employee_login(roles=[Employee.ADMIN_SIEGE])
def admin_pompe_creer(request, station_id):
    """Creation d'une pompe pour une station, avec sa/ses face(s) et pistolets en une
    seule fois (Face 1 obligatoire, Face 2 optionnelle) — jamais de pompe vide sans
    aucun pistolet. Le numero de la pompe est auto-calcule (station, numero) etant
    unique_together ; le numero des pistolets est auto-calcule par leur propre save()
    (sequence par carburant, deja etablie ailleurs dans le projet)."""
    from django.shortcuts import get_object_or_404

    from stations.constants import ESSENCE, GASOIL
    from stations.models import Face, Pistolet, Pompe, Station

    employee = request.employee
    societe = request.societe
    station = get_object_or_404(Station, pk=station_id)

    erreurs = []

    if request.method == "POST":
        face1_gasoil = request.POST.get("face1_gasoil") == "on"
        face1_essence = request.POST.get("face1_essence") == "on"
        face2_gasoil = request.POST.get("face2_gasoil") == "on"
        face2_essence = request.POST.get("face2_essence") == "on"

        if not face1_gasoil and not face1_essence:
            erreurs.append("La Face 1 doit avoir au moins un carburant (Gasoil et/ou Essence).")

        if not erreurs:
            dernier_numero = Pompe.objects.filter(station=station).order_by("-numero").first()
            numero_pompe = (dernier_numero.numero + 1) if dernier_numero else 1

            pompe = Pompe.objects.create(station=station, numero=numero_pompe, statut=Pompe.STATUT_ACTIF)

            face1 = Face.objects.create(pompe=pompe, numero=1, actif=True)
            if face1_gasoil:
                Pistolet.objects.create(face=face1, carburant=GASOIL, actif=True)
            if face1_essence:
                Pistolet.objects.create(face=face1, carburant=ESSENCE, actif=True)

            if face2_gasoil or face2_essence:
                face2 = Face.objects.create(pompe=pompe, numero=2, actif=True)
                if face2_gasoil:
                    Pistolet.objects.create(face=face2, carburant=GASOIL, actif=True)
                if face2_essence:
                    Pistolet.objects.create(face=face2, carburant=ESSENCE, actif=True)

            return redirect("accounts:admin_station_detail", station_id=station.pk)

    contexte = {
        "employee": employee,
        "societe": societe,
        "station": station,
        "erreurs": erreurs,
        "vue_active": "stations",
    }
    return render(request, "accounts/admin_pompe_creer.html", contexte)


def _raisons_blocage_suppression_pompe(pompe):
    """Verification EXHAUSTIVE avant suppression d'une pompe : tout pistolet de cette
    pompe (via ses faces) ayant un releve (pompiste OU gerant, tous deux en PROTECT)
    bloque la suppression — sinon Face/Pistolet en CASCADE depuis Pompe effacerait
    silencieusement l'historique de relevés au moment ou Django tenterait de lever
    l'IntegrityError PROTECT (jamais laisser une erreur brute, toujours verifier avant)."""
    from caisse.models import ReleveIndexGerant, ReleveIndexPompiste
    from stations.models import Pistolet

    raisons = []

    pistolets_ids = Pistolet.objects.filter(face__pompe=pompe).values_list("pk", flat=True)

    if ReleveIndexPompiste.objects.filter(pistolet_id__in=pistolets_ids).exists():
        raisons.append("des relevés d'index pompiste enregistrés sur un ou plusieurs de ses pistolets")
    if ReleveIndexGerant.objects.filter(pistolet_id__in=pistolets_ids).exists():
        raisons.append("des relevés d'index Gérant enregistrés sur un ou plusieurs de ses pistolets")

    return raisons


@require_employee_login(roles=[Employee.ADMIN_SIEGE])
def admin_pompe_gerer(request, pompe_id):
    """Modification du statut et de l'affectation d'une pompe entiere. L'affectation
    par face individuelle reste geree ailleurs (pas encore construit — chantier
    distinct) ; ici, seule l'affectation au niveau de la pompe entiere."""
    from django.core.exceptions import ValidationError
    from django.shortcuts import get_object_or_404

    from accounts.models import Employee as EmployeeModel
    from stations.models import Pompe

    employee = request.employee
    societe = request.societe
    cible = get_object_or_404(Pompe, pk=pompe_id)

    employes_station = EmployeeModel.objects.filter(
        station=cible.station, role=EmployeeModel.POMPISTE, actif=True
    ).order_by("nom_complet")

    erreurs = []

    if request.method == "POST":
        nouveau_statut = request.POST.get("statut", "").strip()
        nouvel_employee_id = request.POST.get("employee_affecte", "").strip()

        statuts_valides = dict(Pompe.STATUT_CHOICES)
        if nouveau_statut not in statuts_valides:
            erreurs.append("Statut invalide.")

        nouvel_employee = None
        if nouvel_employee_id:
            try:
                nouvel_employee = employes_station.get(pk=nouvel_employee_id)
            except EmployeeModel.DoesNotExist:
                erreurs.append("Employé invalide.")

        if not erreurs:
            cible.statut = nouveau_statut
            cible.employee_affecte = nouvel_employee
            try:
                cible.save()
                return redirect(request.path + "?succes=1")
            except ValidationError as e:
                erreurs.extend(e.messages)

    contexte = {
        "employee": employee,
        "societe": societe,
        "cible": cible,
        "employes_station": employes_station,
        "erreurs": erreurs,
        "vue_active": "stations",
    }
    return render(request, "accounts/admin_pompe_gerer.html", contexte)


@require_employee_login(roles=[Employee.ADMIN_SIEGE])
def admin_pompe_supprimer(request, pompe_id):
    """Suppression definitive d'une pompe — reservee aux cas ou AUCUN releve n'existe
    sur aucun de ses pistolets (verification exhaustive). Meme flux en deux etapes."""
    from django.shortcuts import get_object_or_404

    from stations.models import Pompe

    employee = request.employee
    societe = request.societe
    cible = get_object_or_404(Pompe, pk=pompe_id)

    raisons_blocage = _raisons_blocage_suppression_pompe(cible)

    if request.method == "POST" and not raisons_blocage:
        if request.POST.get("confirmer") == "oui":
            station_id = cible.station_id
            cible.delete()
            return redirect("accounts:admin_station_detail", station_id=station_id)

    contexte = {
        "employee": employee,
        "societe": societe,
        "cible": cible,
        "raisons_blocage": raisons_blocage,
        "vue_active": "stations",
    }
    return render(request, "accounts/admin_pompe_supprimer.html", contexte)


@require_employee_login(roles=[Employee.ADMIN_SIEGE])
def admin_employe_creer(request):
    """Creation d'un nouvel employe (n'importe quel role, y compris Admin Siege).
    Derniere fondation de l'etape A (station -> pompe/face/pistolet -> utilisateur,
    dans cet ordre de dependances)."""
    from stations.models import Station

    employee = request.employee
    societe = request.societe
    stations = Station.objects.all().order_by("nom")

    erreurs = []

    if request.method == "POST":
        nom = request.POST.get("nom_complet", "").strip()
        telephone = request.POST.get("telephone", "").strip()
        role = request.POST.get("role", "").strip()
        station_id = request.POST.get("station", "").strip()
        mot_de_passe = request.POST.get("mot_de_passe", "").strip()

        if not nom:
            erreurs.append("Le nom complet est obligatoire.")

        if not telephone:
            erreurs.append("Le téléphone est obligatoire.")
        elif Employee.objects.filter(telephone=telephone).exists():
            erreurs.append("Ce numéro de téléphone est déjà utilisé par un autre employé.")

        roles_valides = dict(Employee.ROLE_CHOICES)
        if role not in roles_valides:
            erreurs.append("Rôle invalide.")

        station = None
        if role != Employee.ADMIN_SIEGE:
            if not station_id:
                erreurs.append("Une station est obligatoire pour ce rôle.")
            else:
                try:
                    station = stations.get(pk=station_id)
                except Station.DoesNotExist:
                    erreurs.append("Station invalide.")

        if not mot_de_passe:
            erreurs.append("Le mot de passe est obligatoire.")
        elif len(mot_de_passe) < 8:
            erreurs.append("Le mot de passe doit contenir au moins 8 caractères.")

        if not erreurs:
            nouvel_employee = Employee(
                nom_complet=nom, telephone=telephone, role=role, station=station, actif=True,
            )
            nouvel_employee.set_password(mot_de_passe)
            nouvel_employee.save()
            return redirect("accounts:admin_employes")

    contexte = {
        "employee": employee,
        "societe": societe,
        "stations": stations,
        "erreurs": erreurs,
        "vue_active": "employes",
    }
    return render(request, "accounts/admin_employe_creer.html", contexte)


@require_employee_login(roles=[Employee.ADMIN_SIEGE])
def admin_employe_historique(request, employee_id):
    """Historique des relevés d'index d'un employe, regroupe par date puis par pistolet
    (jamais une liste plate). Bifurque selon le role : un Pompiste voit ses propres
    releves (ReleveIndexPompiste) ; un Gerant/Chef de piste voit ses releves de
    verification (ReleveIndexGerant), avec le nom du pompiste verifie a chaque fois
    puisqu'un seul Gerant peut verifier plusieurs pompistes le meme jour.
    Filtrable par mois/annee (et par pompiste verifie pour un Gerant/Chef de piste),
    meme logique de filtre que admin_station_historique."""
    from django.shortcuts import get_object_or_404
    from django.utils import timezone

    from caisse.models import ReleveIndexGerant, ReleveIndexPompiste, SessionCaisse

    employee = request.employee
    societe = request.societe
    cible = get_object_or_404(Employee, pk=employee_id)

    annee_filtre = request.GET.get("annee", "").strip()
    mois_filtre = request.GET.get("mois", "").strip()
    pompiste_filtre_id = request.GET.get("pompiste", "").strip()

    jours = []
    pompistes_historique = None

    if cible.role == Employee.POMPISTE:
        releves = ReleveIndexPompiste.objects.filter(employee=cible).select_related(
            "pistolet__face__pompe"
        )

        annees_disponibles = sorted(
            set(releves.values_list("date_heure__year", flat=True)), reverse=True
        )
        if annee_filtre:
            releves = releves.filter(date_heure__year=annee_filtre)
        if mois_filtre:
            releves = releves.filter(date_heure__month=mois_filtre)
        releves = releves.order_by("-date_heure")

        par_jour = {}
        for releve in releves:
            jour = timezone.localtime(releve.date_heure).date()
            par_jour.setdefault(jour, {}).setdefault(
                releve.pistolet_id, {"pistolet": releve.pistolet}
            )[releve.type_releve] = releve.valeur_index

        for jour in sorted(par_jour.keys(), reverse=True):
            lignes = []
            for entree in par_jour[jour].values():
                depart = entree.get(ReleveIndexPompiste.DEPART)
                fin = entree.get(ReleveIndexPompiste.FIN)
                lignes.append({
                    "pistolet": entree["pistolet"],
                    "depart": depart,
                    "fin": fin,
                    "litres": (fin - depart) if (depart is not None and fin is not None) else None,
                })
            # Tri par Pompe/Face/Pistolet — jamais l'ordre d'arrivee des relevés,
            # illisible pour une supervision serieuse.
            lignes.sort(key=lambda l: (
                l["pistolet"].face.pompe.numero, l["pistolet"].face.numero, l["pistolet"].numero
            ))

            totaux_carburant = {}
            for ligne in lignes:
                if ligne["litres"] is not None:
                    nom = ligne["pistolet"].get_carburant_display()
                    totaux_carburant[nom] = totaux_carburant.get(nom, 0) + ligne["litres"]

            session = SessionCaisse.objects.filter(employee=cible, date=jour).first()

            jours.append({
                "date": jour, "lignes": lignes, "totaux_carburant": totaux_carburant, "session": session,
            })

    else:
        releves_bruts = ReleveIndexGerant.objects.filter(employee=cible)

        pompistes_historique = Employee.objects.filter(
            pk__in=releves_bruts.values_list("employee_pompiste_id", flat=True).distinct()
        ).order_by("nom_complet")

        annees_disponibles = sorted(
            set(releves_bruts.values_list("date_heure__year", flat=True)), reverse=True
        )

        releves = releves_bruts.select_related("pistolet__face__pompe", "employee_pompiste")
        if pompiste_filtre_id:
            releves = releves.filter(employee_pompiste_id=pompiste_filtre_id)
        if annee_filtre:
            releves = releves.filter(date_heure__year=annee_filtre)
        if mois_filtre:
            releves = releves.filter(date_heure__month=mois_filtre)
        releves = releves.order_by("-date_heure")

        par_jour = {}
        for releve in releves:
            jour = timezone.localtime(releve.date_heure).date()
            cle = (releve.employee_pompiste_id, releve.pistolet_id)
            par_jour.setdefault(jour, {}).setdefault(
                cle, {"pistolet": releve.pistolet, "pompiste": releve.employee_pompiste}
            )[releve.type_releve] = releve.valeur_index

        for jour in sorted(par_jour.keys(), reverse=True):
            lignes = []
            for entree in par_jour[jour].values():
                depart = entree.get(ReleveIndexGerant.DEPART)
                fin = entree.get(ReleveIndexGerant.FIN)
                lignes.append({
                    "pistolet": entree["pistolet"],
                    "pompiste": entree["pompiste"],
                    "depart": depart,
                    "fin": fin,
                    "litres": (fin - depart) if (depart is not None and fin is not None) else None,
                })

            lignes.sort(key=lambda l: (
                l["pompiste"].nom_complet,
                l["pistolet"].face.pompe.numero, l["pistolet"].face.numero, l["pistolet"].numero
            ))

            totaux_carburant = {}
            for ligne in lignes:
                if ligne["litres"] is not None:
                    nom = ligne["pistolet"].get_carburant_display()
                    totaux_carburant[nom] = totaux_carburant.get(nom, 0) + ligne["litres"]

            jours.append({"date": jour, "lignes": lignes, "totaux_carburant": totaux_carburant, "session": None})

    contexte = {
        "employee": employee,
        "societe": societe,
        "cible": cible,
        "jours": jours,
        "pompistes_historique": pompistes_historique,
        "annees_disponibles": annees_disponibles,
        "pompiste_filtre_id": pompiste_filtre_id,
        "annee_filtre": annee_filtre,
        "mois_filtre": mois_filtre,
        "vue_active": "employes",
    }
    return render(request, "accounts/admin_employe_historique.html", contexte)


@require_employee_login(roles=[Employee.ADMIN_SIEGE])
def admin_station_historique(request, station_id):
    """Historique des relevés d'index de TOUTE une station, regroupe par date puis par
    pompiste, base sur ReleveIndexGerant (fait foi, source officielle) — jamais
    ReleveIndexPompiste ici, qui doublerait chaque ligne sans ajouter de valeur pour
    une vue de supervision au niveau station. Contrairement a l'historique par employe,
    la station n'a pas de FK directe vers SessionCaisse : on passe par les pistolets
    de la station pour retrouver tous les employes qui y ont travaille, quelle que
    soit leur station actuelle (un employe transfere garde son historique intact,
    attache au pistolet reel utilise, pas a sa station courante)."""
    from django.shortcuts import get_object_or_404
    from django.utils import timezone

    from caisse.models import ReleveIndexGerant, SessionCaisse
    from stations.models import Station

    employee = request.employee
    societe = request.societe
    station = get_object_or_404(Station, pk=station_id)

    releves_bruts = ReleveIndexGerant.objects.filter(pistolet__face__pompe__station=station)

    # Liste des pompistes ayant deja un historique sur cette station (pour le filtre),
    # toujours calculee AVANT filtrage pour que le menu deroulant reste complet.
    pompistes_historique = Employee.objects.filter(
        pk__in=releves_bruts.values_list("employee_pompiste_id", flat=True).distinct()
    ).order_by("nom_complet")

    annees_disponibles = sorted(
        set(releves_bruts.values_list("date_heure__year", flat=True)), reverse=True
    )

    pompiste_filtre_id = request.GET.get("pompiste", "").strip()
    annee_filtre = request.GET.get("annee", "").strip()
    mois_filtre = request.GET.get("mois", "").strip()

    releves = releves_bruts.select_related("pistolet__face__pompe", "employee_pompiste").order_by("-date_heure")

    if pompiste_filtre_id:
        releves = releves.filter(employee_pompiste_id=pompiste_filtre_id)
    if annee_filtre:
        releves = releves.filter(date_heure__year=annee_filtre)
    if mois_filtre:
        releves = releves.filter(date_heure__month=mois_filtre)

    par_jour = {}
    for releve in releves:
        jour = timezone.localtime(releve.date_heure).date()
        cle = (releve.employee_pompiste_id, releve.pistolet_id)
        par_jour.setdefault(jour, {}).setdefault(
            cle, {"pistolet": releve.pistolet, "pompiste": releve.employee_pompiste}
        )[releve.type_releve] = releve.valeur_index

    jours = []
    for jour in sorted(par_jour.keys(), reverse=True):
        lignes = []
        pompistes_ids = set()
        for entree in par_jour[jour].values():
            depart = entree.get(ReleveIndexGerant.DEPART)
            fin = entree.get(ReleveIndexGerant.FIN)
            lignes.append({
                "pistolet": entree["pistolet"],
                "pompiste": entree["pompiste"],
                "depart": depart,
                "fin": fin,
                "litres": (fin - depart) if (depart is not None and fin is not None) else None,
            })
            pompistes_ids.add(entree["pompiste"].pk)

        lignes.sort(key=lambda l: (
            l["pompiste"].nom_complet,
            l["pistolet"].face.pompe.numero, l["pistolet"].face.numero, l["pistolet"].numero
        ))

        totaux_carburant = {}
        for ligne in lignes:
            if ligne["litres"] is not None:
                nom = ligne["pistolet"].get_carburant_display()
                totaux_carburant[nom] = totaux_carburant.get(nom, 0) + ligne["litres"]

        sessions_jour = SessionCaisse.objects.filter(employee_id__in=pompistes_ids, date=jour)
        nb_exact = sessions_jour.filter(resultat=SessionCaisse.EXACT).count()
        nb_surplus = sessions_jour.filter(resultat=SessionCaisse.SURPLUS).count()
        nb_manquant = sessions_jour.filter(resultat=SessionCaisse.MANQUANT).count()

        jours.append({
            "date": jour, "lignes": lignes, "totaux_carburant": totaux_carburant,
            "nb_exact": nb_exact, "nb_surplus": nb_surplus, "nb_manquant": nb_manquant,
        })

    contexte = {
        "employee": employee,
        "societe": societe,
        "station": station,
        "jours": jours,
        "pompistes_historique": pompistes_historique,
        "annees_disponibles": annees_disponibles,
        "pompiste_filtre_id": pompiste_filtre_id,
        "annee_filtre": annee_filtre,
        "mois_filtre": mois_filtre,
        "onglet_actif": "historique",
        "vue_active": "stations",
    }
    return render(request, "accounts/admin_station_historique.html", contexte)


@require_employee_login(roles=[Employee.ADMIN_SIEGE])
def admin_station_depenses(request, station_id):
    """Historique des depenses de caisse d une station, filtrable par mois/annee,
    meme pattern deja etabli pour les autres ecrans d historique."""
    from django.shortcuts import get_object_or_404

    from caisse.models import DepenseCaisse
    from stations.models import Station

    employee = request.employee
    societe = request.societe
    station = get_object_or_404(Station, pk=station_id)

    depenses_brutes = DepenseCaisse.objects.filter(station=station)

    annees_disponibles = sorted(
        set(depenses_brutes.values_list("date_heure__year", flat=True)), reverse=True
    )

    annee_filtre = request.GET.get("annee", "").strip()
    mois_filtre = request.GET.get("mois", "").strip()

    depenses = depenses_brutes.select_related("employee").order_by("-date_heure")
    if annee_filtre:
        depenses = depenses.filter(date_heure__year=annee_filtre)
    if mois_filtre:
        depenses = depenses.filter(date_heure__month=mois_filtre)

    total = sum((d.montant for d in depenses), 0)

    contexte = {
        "employee": employee,
        "societe": societe,
        "station": station,
        "depenses": depenses,
        "total": total,
        "annees_disponibles": annees_disponibles,
        "annee_filtre": annee_filtre,
        "mois_filtre": mois_filtre,
        "onglet_actif": "depenses",
        "vue_active": "stations",
    }
    return render(request, "accounts/admin_station_depenses.html", contexte)


@require_employee_login(roles=[Employee.ADMIN_SIEGE])
def admin_station_jauges(request, station_id):
    """Historique des jauges (releves de stock carburant) d une station, filtrable
    par mois/annee, meme pattern deja etabli."""
    from django.shortcuts import get_object_or_404

    from cuves.models import Jauge
    from stations.models import Station

    employee = request.employee
    societe = request.societe
    station = get_object_or_404(Station, pk=station_id)

    jauges_brutes = Jauge.objects.filter(station=station)

    annees_disponibles = sorted(
        set(jauges_brutes.values_list("date_jauge__year", flat=True)), reverse=True
    )

    annee_filtre = request.GET.get("annee", "").strip()
    mois_filtre = request.GET.get("mois", "").strip()

    jauges = jauges_brutes.order_by("-date_jauge", "carburant")
    if annee_filtre:
        jauges = jauges.filter(date_jauge__year=annee_filtre)
    if mois_filtre:
        jauges = jauges.filter(date_jauge__month=mois_filtre)

    contexte = {
        "employee": employee,
        "societe": societe,
        "station": station,
        "jauges": jauges,
        "annees_disponibles": annees_disponibles,
        "annee_filtre": annee_filtre,
        "mois_filtre": mois_filtre,
        "onglet_actif": "jauges",
        "vue_active": "stations",
    }
    return render(request, "accounts/admin_station_jauges.html", contexte)


@require_employee_login(roles=[Employee.ADMIN_SIEGE])
def admin_cuve_creer(request, station_id):
    """Creation d une cuve supplementaire pour une station — jamais un stock cree
    sans une premiere Jauge de depart correspondante, meme logique que la creation
    de station (chantier Cuves etape 2/5)."""
    from decimal import Decimal, InvalidOperation

    from django.shortcuts import get_object_or_404
    from django.utils import timezone

    from cuves.models import Cuve, Jauge
    from stations.models import Station

    employee = request.employee
    societe = request.societe
    station = get_object_or_404(Station, pk=station_id)

    erreurs = []

    if request.method == "POST":
        carburant = request.POST.get("carburant", "").strip()
        capacite_brute = request.POST.get("capacite", "").strip()
        stock_brut = request.POST.get("stock", "").strip()

        carburants_valides = dict(dict(Cuve._meta.get_field("carburant").choices))
        if carburant not in carburants_valides:
            erreurs.append("Carburant invalide.")

        capacite = None
        stock = None
        if not capacite_brute:
            erreurs.append("La capacité est obligatoire.")
        else:
            try:
                capacite = Decimal(capacite_brute)
                if capacite <= 0:
                    erreurs.append("La capacité doit être supérieure à zéro.")
            except InvalidOperation:
                erreurs.append("Capacité invalide.")
        if not stock_brut:
            erreurs.append("Le stock de départ est obligatoire.")
        else:
            try:
                stock = Decimal(stock_brut)
                if stock < 0:
                    erreurs.append("Le stock de départ ne peut pas être négatif.")
            except InvalidOperation:
                erreurs.append("Stock de départ invalide.")
        if capacite is not None and stock is not None and stock > capacite:
            erreurs.append("Le stock de départ ne peut pas dépasser la capacité de la cuve.")

        if not erreurs:
            maintenant = timezone.now()
            aujourdhui = timezone.localtime(maintenant).date()
            Cuve.objects.create(station=station, carburant=carburant, capacite=capacite, actif=True)
            # Jauge = stock AGREGE de toutes les cuves d un meme carburant (jamais par
            # cuve individuelle, cf. docstring du modele) — le stock de depart de cette
            # nouvelle cuve S AJOUTE a la derniere jauge existante, jamais une jauge
            # concurrente independante (bug d integrite deja rencontre et corrige).
            derniere_jauge = Jauge.objects.filter(
                station=station, carburant=carburant
            ).order_by("-date_mesure").first()
            stock_total = (derniere_jauge.quantite if derniere_jauge else 0) + stock
            # update_or_create car Jauge a une contrainte unique_together sur
            # (station, carburant, date_jauge) — une jauge du jour peut deja exister
            # (ex: celle creee a l instant meme de la creation de la station), il faut
            # alors la METTRE A JOUR plutot que d en creer une seconde, sous peine de
            # IntegrityError (bug deja rencontre et corrige).
            Jauge.objects.update_or_create(
                station=station, carburant=carburant, date_jauge=aujourdhui,
                defaults={"quantite": stock_total, "date_mesure": maintenant},
            )
            return redirect("accounts:admin_station_detail", station_id=station.pk)

    contexte = {
        "employee": employee,
        "societe": societe,
        "station": station,
        "erreurs": erreurs,
        "vue_active": "stations",
    }
    return render(request, "accounts/admin_cuve_creer.html", contexte)


@require_employee_login(roles=[Employee.ADMIN_SIEGE])
def admin_cuve_gerer(request, cuve_id):
    """Gestion d une cuve individuelle : capacite, statut actif/inactif (chantier
    Cuves etape 3/5). Toute modification qui reduirait la capacite ACTIVE totale du
    carburant en dessous du stock actuellement mesure (derniere Jauge) est bloquee —
    jamais un stock affiche qui ne tiendrait plus physiquement dans les cuves
    restantes disponibles."""
    from decimal import Decimal, InvalidOperation

    from django.db.models import Sum
    from django.shortcuts import get_object_or_404

    from cuves.models import Cuve, Jauge

    employee = request.employee
    societe = request.societe
    cuve = get_object_or_404(Cuve, pk=cuve_id)
    station = cuve.station

    derniere_jauge = Jauge.objects.filter(
        station=station, carburant=cuve.carburant
    ).order_by("-date_mesure").first()
    stock_actuel = derniere_jauge.quantite if derniere_jauge else 0

    erreurs = []

    if request.method == "POST":
        capacite_brute = request.POST.get("capacite", "").strip()
        nouveau_actif = request.POST.get("actif") == "1"

        capacite = None
        if not capacite_brute:
            erreurs.append("La capacité est obligatoire.")
        else:
            try:
                capacite = Decimal(capacite_brute)
                if capacite <= 0:
                    erreurs.append("La capacité doit être supérieure à zéro.")
            except InvalidOperation:
                erreurs.append("Capacité invalide.")

        if capacite is not None:
            autres_cuves_actives = Cuve.objects.filter(
                station=station, carburant=cuve.carburant, actif=True
            ).exclude(pk=cuve.pk)
            capacite_autres = autres_cuves_actives.aggregate(total=Sum("capacite"))["total"] or 0
            capacite_totale_projetee = capacite_autres + (capacite if nouveau_actif else 0)
            if capacite_totale_projetee < stock_actuel:
                erreurs.append(
                    f"Impossible : la capacité active totale du {cuve.get_carburant_display()} "
                    f"passerait à {capacite_totale_projetee} L, en dessous du stock actuellement "
                    f"mesuré ({stock_actuel} L)."
                )

        if not erreurs:
            cuve.capacite = capacite
            cuve.actif = nouveau_actif
            cuve.save()
            return redirect("accounts:admin_station_detail", station_id=station.pk)

    contexte = {
        "employee": employee,
        "societe": societe,
        "cuve": cuve,
        "station": station,
        "stock_actuel": stock_actuel,
        "erreurs": erreurs,
        "vue_active": "stations",
    }
    return render(request, "accounts/admin_cuve_gerer.html", contexte)


@require_employee_login(roles=[Employee.ADMIN_SIEGE])
def admin_station_cuves(request, station_id):
    """Onglet dedie Cuves de la fiche station (deplace hors de admin_station_detail
    pour desencombrer l ecran Pompes — chantier navigation par onglets). Meme logique
    de resume agrege par carburant deja etablie."""
    from django.db.models import Sum
    from django.shortcuts import get_object_or_404

    from cuves.models import Cuve, Jauge
    from stations.models import Station

    employee = request.employee
    societe = request.societe
    station = get_object_or_404(Station, pk=station_id)

    cuves = Cuve.objects.filter(station=station).order_by("carburant")
    resume_carburants = []
    for carburant, libelle in Cuve._meta.get_field("carburant").choices:
        cuves_carburant = cuves.filter(carburant=carburant)
        if not cuves_carburant.exists():
            continue
        derniere_jauge = Jauge.objects.filter(
            station=station, carburant=carburant
        ).order_by("-date_mesure").first()
        resume_carburants.append({
            "carburant": libelle,
            "nb_cuves": cuves_carburant.count(),
            "capacite_totale": cuves_carburant.filter(actif=True).aggregate(total=Sum("capacite"))["total"] or 0,
            "derniere_jauge": derniere_jauge,
        })

    contexte = {
        "employee": employee,
        "societe": societe,
        "station": station,
        "cuves": cuves,
        "resume_carburants": resume_carburants,
        "onglet_actif": "cuves",
        "vue_active": "stations",
    }
    return render(request, "accounts/admin_station_cuves.html", contexte)
