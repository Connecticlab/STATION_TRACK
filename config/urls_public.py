from django.urls import path

from public import views

app_name = "public"

urlpatterns = [
    path("", views.landing_page, name="landing"),
]
