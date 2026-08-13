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
