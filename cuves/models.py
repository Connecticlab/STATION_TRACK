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
