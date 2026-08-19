"""URLconf pour les sous-domaines societe (<slug>.<BASE_DOMAIN>). Fixe dynamiquement
par TenantMiddleware via request.urlconf. Contient les vues employe (accounts) et, a
terme, toutes les vues metier (pompiste, gerant, chef de piste). Jamais l'admin Django
ni platform_admin — separation stricte avec urls_platform_admin.py."""
from django.urls import path, include

urlpatterns = [
    path('', include('accounts.urls')),
]
