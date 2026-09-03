from django.conf import settings
from django.shortcuts import render

from tenants.context import set_current_tenant_db
from tenants.db_utils import register_company_database
from tenants.models import Societe


class TenantMiddleware:
    """Détermine la société active à partir du sous-domaine et pose le contexte de base de données."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        host = request.get_host().split(":")[0]

        # Domaine racine EXACT (carburant.sn, sans sous-domaine) : page vitrine
        # publique, jamais une resolution de societe — avant, host.split(".")[0]
        # produisait "carburant" pour ce cas, traite a tort comme un sous-domaine de
        # societe inexistant (404 trompeuse "Societe introuvable"), jamais une vraie
        # page d'accueil.
        if host == settings.BASE_DOMAIN:
            request.urlconf = "config.urls_public"
            return self.get_response(request)

        sous_domaine = host.split(".")[0]

        if sous_domaine == "admin":
            request.urlconf = "config.urls_platform_admin"
            return self.get_response(request)

        try:
            societe = Societe.objects.using("default").get(
                sous_domaine=sous_domaine, actif=True
            )
        except Societe.DoesNotExist:
            # Rendu via le template 404 standard (jamais un texte brut) — cette reponse
            # part AVANT la resolution normale des URLs Django, donc sans ce rendu
            # explicite elle contournerait completement notre page d erreur stylee.
            return render(request, "404.html", status=404)

        request.societe = societe
        request.urlconf = "config.urls_accounts"
        register_company_database(societe)
        set_current_tenant_db(societe.nom_base_donnees)

        response = self.get_response(request)
        return response
