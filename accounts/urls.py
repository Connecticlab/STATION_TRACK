from django.urls import path

from accounts import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.employee_login, name="login"),
    path("logout/", views.employee_logout, name="logout"),
    path("pompiste/", views.pompiste_accueil, name="pompiste_accueil"),
    path("pompiste/paiement/", views.pompiste_ajouter_paiement, name="pompiste_ajouter_paiement"),
    path("pompiste/finaliser/", views.pompiste_finaliser, name="pompiste_finaliser"),
    path("pompiste/recu/<int:session_id>/", views.pompiste_recu, name="pompiste_recu"),
    path("pompiste/historique/", views.pompiste_historique, name="pompiste_historique"),
    path("gerant/", views.gerant_accueil, name="gerant_accueil"),
    path("gerant/jauge/", views.gerant_jauge, name="gerant_jauge"),
    path("gerant/depense/", views.gerant_depense, name="gerant_depense"),
    path("gerant/depot-bancaire/", views.gerant_depot_bancaire, name="gerant_depot_bancaire"),
    path("gerant/releve/<int:pompiste_id>/", views.gerant_releve_pompiste, name="gerant_releve_pompiste"),
    path("gerant/finaliser/<int:pompiste_id>/", views.gerant_finaliser, name="gerant_finaliser"),
    path("gerant/confronter/<int:pompiste_id>/", views.gerant_confronter, name="gerant_confronter"),
    path("admin/", views.admin_accueil, name="admin_accueil"),
    path("admin/stations/", views.admin_stations, name="admin_stations"),
    path("admin/stations/<int:station_id>/", views.admin_station_detail, name="admin_station_detail"),
    path("admin/employes/", views.admin_employes, name="admin_employes"),
    path("admin/employes/<int:employee_id>/toggle-actif/", views.admin_employe_toggle_actif, name="admin_employe_toggle_actif"),
    path("admin/employes/<int:employee_id>/gerer/", views.admin_employe_gerer, name="admin_employe_gerer"),
    path("admin/employes/<int:employee_id>/supprimer/", views.admin_employe_supprimer, name="admin_employe_supprimer"),
]
