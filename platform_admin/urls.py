from django.urls import path

from platform_admin import views

app_name = "platform_admin"

urlpatterns = [
    path("login/", views.super_admin_login, name="login"),
    path("dashboard/", views.dashboard, name="dashboard"),
]
