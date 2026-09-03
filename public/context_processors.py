from django.conf import settings


def est_domaine_racine(request):
    """Expose un booleen indiquant si la requete provient du domaine racine exact
    (carburant.sn, sans sous-domaine) — utile pour les pages d'erreur (404/500), qui
    n'ont pas de contexte personnalise (vues par defaut de Django) et doivent pourtant
    savoir si /login/ existe (societe/admin) ou non (domaine racine, page vitrine
    uniquement). host.split(':')[0] ignore le port, pour un comportement identique en
    dev (carburan.sn:8000) et en production (carburant.sn, sans port)."""
    host = request.get_host().split(":")[0]
    return {"est_domaine_racine": host == settings.BASE_DOMAIN}
