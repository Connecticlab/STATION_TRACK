from functools import wraps

from django.shortcuts import redirect

from platform_admin.models import SuperAdmin


def require_super_admin_login(view_func):
    """Verifie la session SuperAdmin — equivalent de require_employee_login (accounts)
    mais pour la base MAITRE, sans notion de societe/tenant (un SuperAdmin gere toutes
    les societes, pas une seule). A combiner avec @require_admin_subdomain (deja
    existant) sur chaque vue, jamais l un sans l autre : le sous-domaine garantit qu on
    est bien sur admin.<BASE_DOMAIN>, ce decorateur garantit qu on est bien connecte."""

    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        super_admin_id = request.session.get("super_admin_id")
        if not super_admin_id:
            return redirect("platform_admin:login")

        try:
            super_admin = SuperAdmin.objects.get(pk=super_admin_id, actif=True)
        except SuperAdmin.DoesNotExist:
            request.session.pop("super_admin_id", None)
            return redirect("platform_admin:login")

        request.super_admin = super_admin
        return view_func(request, *args, **kwargs)

    return wrapper
