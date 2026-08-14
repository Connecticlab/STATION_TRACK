from django.db import models


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

    class Meta:
        verbose_name = "Société"
        verbose_name_plural = "Sociétés"

    def __str__(self):
        return self.nom
