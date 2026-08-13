from django.db import models


class LoginAttempt(models.Model):
    """Trace chaque tentative de connexion Super Admin (réussie ou échouée), pour l'anti brute-force.
    Vit dans la base maître, comme le modèle SuperAdmin."""

    identifiant_tente = models.CharField(
        max_length=255,
        help_text="Téléphone ou email tenté au moment du login",
    )
    adresse_ip = models.GenericIPAddressField()
    reussie = models.BooleanField(default=False)
    date_tentative = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Tentative de connexion"
        verbose_name_plural = "Tentatives de connexion"
        indexes = [
            models.Index(fields=["identifiant_tente", "date_tentative"]),
            models.Index(fields=["adresse_ip", "date_tentative"]),
        ]
        ordering = ["-date_tentative"]

    def __str__(self):
        statut = "réussie" if self.reussie else "échouée"
        return f"{self.identifiant_tente} depuis {self.adresse_ip} - {statut}"
