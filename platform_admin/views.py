from functools import wraps

from django.conf import settings
from django.http import Http404


def require_admin_subdomain(view_func):
    """N'autorise l'accès que via admin.<BASE_DOMAIN> exact.
    Renvoie une 404 (jamais une redirection ni un message explicite) sinon,
    pour ne pas révéler l'existence de l'interface Super Admin."""

    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        host = request.get_host().split(":")[0]
        attendu = f"admin.{settings.BASE_DOMAIN}"
        if host != attendu:
            raise Http404()
        return view_func(request, *args, **kwargs)

    return wrapper


from datetime import timedelta

from django.shortcuts import render, redirect
from django.utils import timezone

from platform_admin.decorators import require_super_admin_login
from platform_admin.models import LoginAttempt, SuperAdmin


def _get_client_ip(request):
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def _est_bloque(identifiant, adresse_ip):
    fenetre = timezone.now() - timedelta(minutes=15)

    echecs_identifiant = LoginAttempt.objects.filter(
        identifiant_tente=identifiant,
        reussie=False,
        date_tentative__gte=fenetre,
    ).count()
    if echecs_identifiant >= 5:
        return True

    echecs_ip = LoginAttempt.objects.filter(
        adresse_ip=adresse_ip,
        reussie=False,
        date_tentative__gte=fenetre,
    ).count()
    if echecs_ip >= 20:
        return True

    return False


@require_admin_subdomain
def super_admin_login(request):
    erreur = None

    if request.method == "POST":
        email = request.POST.get("email", "").strip()
        mot_de_passe = request.POST.get("password", "")
        adresse_ip = _get_client_ip(request)

        if _est_bloque(email, adresse_ip):
            erreur = "Trop de tentatives. Réessayez plus tard."
        else:
            super_admin = None
            try:
                candidat = SuperAdmin.objects.get(email=email, actif=True)
                if candidat.check_password(mot_de_passe):
                    super_admin = candidat
            except SuperAdmin.DoesNotExist:
                super_admin = None

            LoginAttempt.objects.create(
                identifiant_tente=email,
                adresse_ip=adresse_ip,
                reussie=super_admin is not None,
            )

            if super_admin is not None:
                request.session["super_admin_id"] = super_admin.pk
                return redirect("platform_admin:dashboard")
            erreur = "Identifiants invalides."

    return render(request, "platform_admin/login.html", {"erreur": erreur})


@require_admin_subdomain
def super_admin_logout(request):
    """Deconnexion Super Admin — vide uniquement la cle de session super_admin_id,
    jamais toute la session (au cas ou d autres donnees non liees y vivraient un jour)."""
    request.session.pop("super_admin_id", None)
    return redirect("platform_admin:login")


@require_admin_subdomain
@require_super_admin_login
def dashboard(request):
    """Tableau de bord Super Admin — UNIQUEMENT des chiffres reels (nombre de societes,
    actives/inactives). Jamais de CA, volume, ou autre metrique fictive tant que le
    systeme ne collecte pas reellement cette donnee — un chiffre invente affiche comme
    reel serait trompeur, inacceptable pour un produit serieux."""
    from tenants.models import Societe

    super_admin = request.super_admin
    societes = Societe.objects.all().order_by("-date_creation")
    nb_societes = societes.count()
    nb_societes_actives = societes.filter(actif=True).count()

    contexte = {
        "super_admin": super_admin,
        "nb_societes": nb_societes,
        "nb_societes_actives": nb_societes_actives,
        "societes_recentes": societes[:5],
        "vue_active": "dashboard",
    }
    return render(request, "platform_admin/dashboard.html", contexte)


@require_admin_subdomain
@require_super_admin_login
def admin_societes(request):
    """Liste de TOUTES les societes de la plateforme (tenants) — vue transversale
    reservee au Super Admin, jamais accessible a un Admin Siege (qui ne voit que sa
    propre societe, dans sa propre base)."""
    from tenants.models import Societe

    super_admin = request.super_admin
    societes = Societe.objects.all().order_by("nom")

    contexte = {
        "super_admin": super_admin,
        "societes": societes,
        "vue_active": "societes",
    }
    return render(request, "platform_admin/admin_societes.html", contexte)


@require_admin_subdomain
@require_super_admin_login
def admin_societe_creer(request):
    """Creation d une nouvelle societe cliente — remplace l usage manuel du shell
    (python manage.py create_company). Reutilise tenants.services.create_company,
    LA source de verite deja existante et testee (base PostgreSQL, migrations, premier
    compte Admin Siege) — jamais une reimplementation parallele qui pourrait diverger."""
    from psycopg2 import errors as psycopg2_errors

    from tenants.services import create_company

    super_admin = request.super_admin
    erreurs = []
    succes = False

    if request.method == "POST":
        nom = request.POST.get("nom", "").strip()
        sous_domaine = request.POST.get("sous_domaine", "").strip().lower()
        admin_nom = request.POST.get("admin_nom", "").strip()
        admin_telephone = request.POST.get("admin_telephone", "").strip()
        admin_password = request.POST.get("admin_password", "").strip()

        if not nom:
            erreurs.append("Le nom de la société est obligatoire.")
        if not sous_domaine:
            erreurs.append("Le sous-domaine est obligatoire.")
        elif not sous_domaine.replace("-", "").isalnum():
            erreurs.append("Le sous-domaine ne peut contenir que des lettres, chiffres et tirets.")
        if not admin_nom:
            erreurs.append("Le nom du premier Admin Siège est obligatoire.")
        if not admin_telephone:
            erreurs.append("Le téléphone du premier Admin Siège est obligatoire.")
        if not admin_password:
            erreurs.append("Le mot de passe du premier Admin Siège est obligatoire.")
        elif len(admin_password) < 8:
            erreurs.append("Le mot de passe doit contenir au moins 8 caractères.")

        if not erreurs:
            try:
                create_company(
                    nom=nom,
                    sous_domaine=sous_domaine,
                    admin_prenom_nom=admin_nom,
                    admin_telephone=admin_telephone,
                    admin_password=admin_password,
                )
                succes = True
            except psycopg2_errors.DuplicateDatabase:
                erreurs.append(f"Une base de données existe déjà pour le sous-domaine « {sous_domaine} ».")
            except Exception as exc:
                erreurs.append(f"Erreur lors de la création : {exc}")

        if succes:
            return redirect("platform_admin:admin_societes")

    contexte = {
        "super_admin": super_admin,
        "erreurs": erreurs,
        "vue_active": "societes",
    }
    return render(request, "platform_admin/admin_societe_creer.html", contexte)


@require_admin_subdomain
@require_super_admin_login
def admin_societe_toggle_actif(request, societe_id):
    """Active/desactive une societe — jamais une suppression. Coherent avec le principe
    deja etabli ailleurs dans ce projet (station, employe) : toggle reversible d abord,
    suppression definitive un chantier separe et explicite, jamais construite ici."""
    from django.shortcuts import get_object_or_404

    from tenants.models import Societe

    societe = get_object_or_404(Societe, pk=societe_id)
    societe.actif = not societe.actif
    societe.save()
    return redirect("platform_admin:admin_societes")
