from django.urls import path

from accounts import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.employee_login, name="login"),
    path("pompiste/", views.pompiste_accueil, name="pompiste_accueil"),
    path("pompiste/paiement/", views.pompiste_ajouter_paiement, name="pompiste_ajouter_paiement"),
]
