from django.db import models

from stations.constants import CARBURANT_CHOICES


class Station(models.Model):
    """Une station-service, rattachée à une seule société (implicite : contenue dans la base de la société)."""
    nom = models.CharField(max_length=255)
    adresse = models.CharField(max_length=255, blank=True)
    actif = models.BooleanField(default=True)
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Station"
        verbose_name_plural = "Stations"

    def __str__(self):
        return self.nom


class Pompe(models.Model):
    """Une pompe (volute compteur / distributeur) d'une station. Une station peut avoir jusqu'à 8 pompes."""
    station = models.ForeignKey(Station, on_delete=models.CASCADE, related_name="pompes")
    numero = models.PositiveIntegerField(help_text="Numéro de la pompe au sein de la station")
    actif = models.BooleanField(default=True)
    employee_affecte = models.ForeignKey(
        "accounts.Employee", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="pompes_affectees",
        help_text="Affectation au niveau de la pompe entière. Exclusif avec l'affectation par face.",
    )

    class Meta:
        verbose_name = "Pompe"
        verbose_name_plural = "Pompes"
        unique_together = ("station", "numero")
        ordering = ["station", "numero"]

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.employee_affecte_id and self.pk and self.faces.filter(employee_affecte__isnull=False).exists():
            raise ValidationError(
                "Impossible d'affecter un employé à la pompe entière : "
                "une ou plusieurs de ses faces sont déjà affectées individuellement."
            )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.station.nom} - Pompe {self.numero}"


class Face(models.Model):
    """Une face d'une pompe. Chaque pompe a 1 ou 2 faces."""
    pompe = models.ForeignKey(Pompe, on_delete=models.CASCADE, related_name="faces")
    numero = models.PositiveIntegerField(help_text="Numéro de la face au sein de la pompe (1 ou 2)")
    actif = models.BooleanField(default=True)
    employee_affecte = models.ForeignKey(
        "accounts.Employee", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="faces_affectees",
        help_text="Affectation au niveau de la face. Exclusif avec l'affectation de toute la pompe.",
    )

    class Meta:
        verbose_name = "Face"
        verbose_name_plural = "Faces"
        unique_together = ("pompe", "numero")
        ordering = ["pompe", "numero"]

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.employee_affecte_id and self.pompe_id and self.pompe.employee_affecte_id:
            raise ValidationError(
                "Impossible d'affecter un employé à cette face : "
                "la pompe entière est déjà affectée à un autre employé."
            )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.pompe} - Face {self.numero}"


class Pistolet(models.Model):
    """Un pistolet, rattaché à une face. L'INDEX est toujours rattaché au pistolet, jamais à la pompe."""

    face = models.ForeignKey(Face, on_delete=models.CASCADE, related_name="pistolets")
    numero = models.PositiveIntegerField(
        editable=False,
        help_text="Numéro séquentiel du pistolet sur toute la station (pas remis à zéro par pompe)",
    )
    carburant = models.CharField(max_length=10, choices=CARBURANT_CHOICES)
    actif = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Pistolet"
        verbose_name_plural = "Pistolets"
        ordering = ["numero"]

    def save(self, *args, **kwargs):
        if self.numero is None or self.pk is None:
            station = self.face.pompe.station
            dernier = Pistolet.objects.filter(
                face__pompe__station=station
            ).order_by("-numero").first()
            self.numero = (dernier.numero + 1) if dernier else 1
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Pistolet {self.numero} ({self.get_carburant_display()})"
