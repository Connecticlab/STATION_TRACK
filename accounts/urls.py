from django.urls import path

from accounts import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.employee_login, name="login"),
    path("logout/", views.employee_logout, name="logout"),
    path("pompiste/", views.pompiste_accueil, name="pompiste_accueil"),
    path("pompiste/paiement/", views.pompiste_ajouter_paiement, name="pompiste_ajouter_paiement"),
    path("pompiste/recu/<int:session_id>/", views.pompiste_recu, name="pompiste_recu"),
    path("pompiste/historique/", views.pompiste_historique, name="pompiste_historique"),
    path("gerant/", views.gerant_accueil, name="gerant_accueil"),
    path("gerant/jauge/", views.gerant_jauge, name="gerant_jauge"),
    path("gerant/depense/", views.gerant_depense, name="gerant_depense"),
]
