from functools import wraps

from django.shortcuts import redirect

from accounts.models import Employee


def require_employee_login(roles=None):
    """Verifie la session Employee ET que la societe resolue par le middleware pour
    cette requete correspond bien a la societe ou l'employe s'est authentifie (compare
    a employee_societe_slug stocke en session au login) — protege contre une collision
    d'ID entre deux bases societe differentes : un meme employee_id pourrait exister par
    coincidence dans deux bases distinctes, chargeant silencieusement le mauvais employe
    sans cette verification. Une simple coherence get_current_tenant_db()/request.societe
    ne suffirait pas, puisque les deux derivent de la meme resolution de sous-domaine et
    ne detectent donc pas ce cas.

    roles : liste de roles autorises (ex. [Employee.POMPISTE]). None = tout role valide.
    """
    def decorateur(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            employee_id = request.session.get("employee_id")
            societe_slug_session = request.session.get("employee_societe_slug")

            if not employee_id or not societe_slug_session:
                return redirect("accounts:login")

            if not hasattr(request, "societe") or request.societe.sous_domaine != societe_slug_session:
                return redirect("accounts:login")

            try:
                employee = Employee.objects.get(pk=employee_id, actif=True)
            except Employee.DoesNotExist:
                return redirect("accounts:login")

            if roles is not None and employee.role not in roles:
                return redirect("accounts:login")

            # Station desactivee (par ex. entre-temps par l'Admin Siege) : deconnexion
            # immediate, jamais seulement bloque a la connexion — meme logique que
            # employee_login. Admin Siege exclu (station toujours nulle, role
            # transversal jamais bloque par le statut d'une seule station).
            if employee.station is not None and not employee.station.actif:
                request.session.pop("employee_id", None)
                request.session.pop("employee_societe_slug", None)
                return redirect("accounts:login")

            request.employee = employee
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorateur
