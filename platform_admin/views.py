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
def dashboard(request):
    if not request.session.get("super_admin_id"):
        return redirect("platform_admin:login")
    return render(request, "platform_admin/dashboard.html")
