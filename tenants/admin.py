from django.contrib import admin

from tenants.models import Societe


@admin.register(Societe)
class SocieteAdmin(admin.ModelAdmin):
    list_display = ("nom", "sous_domaine", "actif", "date_creation")
    fields = (
        "nom", "sous_domaine", "nom_base_donnees", "actif", "logo",
        "marge_tolerance_divergence_litres", "seuil_alerte_dette_fcfa",
    )
