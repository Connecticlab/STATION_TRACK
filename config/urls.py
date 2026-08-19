"""URLconf de secours (ROOT_URLCONF). NE DOIT JAMAIS contenir de route exploitable
(pas d'admin.site.urls, pas platform_admin, pas accounts) : c'est le filet de securite
ultime si TenantMiddleware ne parvient pas a fixer request.urlconf (bug futur, cas
non anticipe). Reste volontairement vide — toute requete qui l'atteint recoit une 404
standard, plutot que d'exposer accidentellement une interface sensible sur le mauvais
sous-domaine."""
urlpatterns = []
