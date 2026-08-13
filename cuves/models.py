from django.db import models

from stations.constants import CARBURANT_CHOICES
from stations.models import Station


class Cuve(models.Model):
    """Un réservoir de stockage carburant. Une station a plusieurs cuves par carburant.
    Le stock réel à un instant T est donné par Jauge, pas par ce modèle (infrastructure uniquement)."""

    station = models.ForeignKey(Station, on_delete=models.CASCADE, related_name="cuves")
    carburant = models.CharField(max_length=10, choices=CARBURANT_CHOICES)
    capacite = models.DecimalField(
        max_digits=10, decimal_places=2,
        help_text="Capacité de stockage de la cuve, en litres",
    )
    actif = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Cuve"
        verbose_name_plural = "Cuves"

    def __str__(self):
        return f"{self.station.nom} - Cuve {self.get_carburant_display()} ({self.capacite} L)"


class Jauge(models.Model):
    """Mesure agrégée du stock d'un carburant (somme de toutes les cuves de ce carburant), pour une station.
    Cycle calendaire fixe le matin, indépendant des rotations d'équipes de pompistes."""

    station = models.ForeignKey(Station, on_delete=models.CASCADE, related_name="jauges")
    carburant = models.CharField(max_length=10, choices=CARBURANT_CHOICES)
    quantite = models.DecimalField(
        max_digits=10, decimal_places=2,
        help_text="Quantité mesurée agrégée (somme de toutes les cuves de ce carburant), en litres",
    )
    date_jauge = models.DateField()
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Jauge"
        verbose_name_plural = "Jauges"
        unique_together = ("station", "carburant", "date_jauge")
        ordering = ["-date_jauge"]

    def __str__(self):
        return f"{self.station.nom} - Jauge {self.get_carburant_display()} du {self.date_jauge}"


class Depotage(models.Model):
    """Ravitaillement des cuves par camion-citerne, à heure variable.
    Avant le dépotage, on jauge le contenu de la citerne elle-même (quantite_citerne)."""

    station = models.ForeignKey(Station, on_delete=models.CASCADE, related_name="depotages")
    carburant = models.CharField(max_length=10, choices=CARBURANT_CHOICES)
    quantite_citerne = models.DecimalField(
        max_digits=10, decimal_places=2,
        help_text="Quantité mesurée dans la citerne avant dépotage, en litres",
    )
    date_heure = models.DateTimeField(help_text="Horodatage du dépotage, heure variable")
    jauge_completee = models.ForeignKey(
        Jauge, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="depotages",
        help_text="Dernière jauge du même carburant/station juste avant ce dépotage, "
                   "fixée à la création (jamais recalculée dynamiquement).",
    )

    class Meta:
        verbose_name = "Dépotage"
        verbose_name_plural = "Dépotages"
        ordering = ["-date_heure"]

    def __str__(self):
        return f"{self.station.nom} - Dépotage {self.get_carburant_display()} du {self.date_heure:%d/%m/%Y %H:%M}"
