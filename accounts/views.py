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
    # Admin Siege : redirection provisoire, sa vue dediee n'existe pas encore (priorite n°4).
    return redirect("accounts:login")


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

    employee = request.employee
    pistolets = pistolets_affectes_a(employee)
    structure = _structurer_pistolets_par_pompe_face(pistolets)

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

    erreurs = []

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

    contexte = {
        "employee": employee,
        "session": session,
        "releves": releves,
        "litres_vendus": litres_vendus,
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

        # Le montant verse (verification physique du Gerant) n'est demande QUE sur le
        # relevé de fin — pas de sens sur un relevé de départ.
        montant_verse_brut = request.POST.get("montant_verse_gerant", "").strip()
        montant_verse = None
        if type_releve == ReleveIndexGerant.FIN:
            if not montant_verse_brut:
                erreurs.append("Le montant versé (vérifié physiquement) est obligatoire.")
            else:
                try:
                    montant_verse = float(montant_verse_brut)
                    if montant_verse < 0:
                        erreurs.append("Le montant versé ne peut pas être négatif.")
                except ValueError:
                    erreurs.append("Montant versé invalide.")

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
                    if type_releve == ReleveIndexGerant.FIN and session_pompiste is not None:
                        session_pompiste.montant_verse_gerant = montant_verse
                        session_pompiste.save()
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

    from caisse.models import SessionCaisse
    from caisse.services import appliquer_resultat_au_solde, confronter_session_caisse

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

    contexte = {
        "employee": employee,
        "pompiste": pompiste,
        "session": session,
        "alerte_dette": alerte_dette,
    }
    return render(request, "accounts/gerant_confrontation_resultat.html", contexte)
