from datetime import timedelta

from django.shortcuts import render, redirect
from django.utils import timezone

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
