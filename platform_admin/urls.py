from django.urls import path

from platform_admin import views

app_name = "platform_admin"

urlpatterns = [
    path("login/", views.super_admin_login, name="login"),
    path("logout/", views.super_admin_logout, name="logout"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("societes/", views.admin_societes, name="admin_societes"),
    path("societes/creer/", views.admin_societe_creer, name="admin_societe_creer"),
    path("societes/<int:societe_id>/toggle-actif/", views.admin_societe_toggle_actif, name="admin_societe_toggle_actif"),
]
