from django.http import HttpResponseNotFound

from tenants.context import set_current_tenant_db
from tenants.models import Societe


class TenantMiddleware:
    """Détermine la société active à partir du sous-domaine et pose le contexte de base de données."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        host = request.get_host().split(":")[0]
        sous_domaine = host.split(".")[0]

        if sous_domaine == "admin":
            return self.get_response(request)

        try:
            societe = Societe.objects.using("default").get(
                sous_domaine=sous_domaine, actif=True
            )
        except Societe.DoesNotExist:
            return HttpResponseNotFound("Société introuvable pour ce sous-domaine.")

        request.societe = societe
        set_current_tenant_db(societe.nom_base_donnees)

        response = self.get_response(request)
        return response
