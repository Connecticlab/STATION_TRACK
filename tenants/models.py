from django.core.exceptions import ValidationError
from django.db import models


def logo_upload_path(instance, filename):
    return f"logos/{instance.sous_domaine}/{filename}"


def validate_taille_logo(fichier):
    limite_mo = 2
    if fichier.size > limite_mo * 1024 * 1024:
        raise ValidationError(f"Le logo ne doit pas dépasser {limite_mo} Mo.")


class Societe(models.Model):
    """Une société cliente (tenant) — chaque société a sa propre base de données."""
    nom = models.CharField(max_length=255)
    sous_domaine = models.SlugField(max_length=100, unique=True)
    nom_base_donnees = models.CharField(max_length=100, unique=True)
    actif = models.BooleanField(default=True)
    date_creation = models.DateTimeField(auto_now_add=True)
    marge_tolerance_divergence_litres = models.DecimalField(
        max_digits=6, decimal_places=2, default=2,
        help_text="Marge de tolérance (en litres) pour la divergence entre relevé pompiste et "
                   "relevé Gérant/Chef de piste, au-delà de laquelle une divergence est signalée.",
    )
    seuil_alerte_dette_fcfa = models.DecimalField(
        max_digits=12, decimal_places=2, default=50000,
        help_text="Si le solde de dette cumulé d'un pompiste dépasse ce seuil (en FCFA), "
                   "une alerte est déclenchée pour le pompiste, le Gérant, et l'Admin Siège.",
    )
    marge_tolerance_ecart_carburant_mensuel_litres = models.DecimalField(
        max_digits=8, decimal_places=2, default=50,
        help_text="Marge de tolérance (en litres) pour le cumul mensuel de l'écart carburant "
                   "(consommé selon les cuves vs vendu selon les pompes). Au-delà, statut "
                   "Perte/Coulage plutôt que Normal. Distinct de la marge de divergence "
                   "pompiste/Gérant : nature d'écart différente (stock physique cumulé, pas "
                   "une erreur de lecture ponctuelle).",
    )
    logo = models.ImageField(
        upload_to=logo_upload_path, blank=True, null=True,
        validators=[validate_taille_logo],
        help_text="Logo de la société, affiché dans l'en-tête des PV de caisse générés. "
                   "Optionnel — le PV se génère normalement sans logo (nom en texte).",
    )

    class Meta:
        verbose_name = "Société"
        verbose_name_plural = "Sociétés"

    def __str__(self):
        return self.nom
