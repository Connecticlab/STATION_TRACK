from django.db import models
from django.utils import timezone

from accounts.models import Employee
from stations.models import Pistolet


class SessionCaisse(models.Model):
    """Regroupe TOUS les pistolets qu'un pompiste gère pour une journée donnée,
    quel que soit le nombre de pompes concernées. Créée automatiquement dès le premier
    relevé d'index de départ du pompiste (pas d'action "ouvrir la caisse" séparée)."""

    MANQUANT = "manquant"
    SURPLUS = "surplus"
    RESULTAT_CHOICES = [
        (MANQUANT, "Manquant"),
        (SURPLUS, "Surplus"),
    ]

    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="sessions_caisse")
    date = models.DateField()
    montant_encaisse = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        help_text="Montant réellement encaissé, déclaré par le pompiste à la clôture",
    )
    resultat = models.CharField(max_length=10, choices=RESULTAT_CHOICES, null=True, blank=True)
    montant_ecart = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    ecart_gasoil = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text="Écart en litres entre relevé pompiste et relevé Gérant/Chef de piste, pour le gasoil.",
    )
    ecart_essence = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text="Écart en litres entre relevé pompiste et relevé Gérant/Chef de piste, pour l'essence.",
    )
    divergence_signalee = models.BooleanField(
        default=False,
        help_text="Vrai si l'écart gasoil ou essence dépasse la marge de tolérance de la société. "
                   "Signalement uniquement, ne bloque jamais la clôture.",
    )

    class Meta:
        verbose_name = "Session de caisse"
        verbose_name_plural = "Sessions de caisse"
        unique_together = ("employee", "date")
        ordering = ["-date"]

    def __str__(self):
        return f"{self.employee.nom_complet} - Caisse du {self.date}"


class ReleveIndexPompiste(models.Model):
    """Index relevé par le pompiste lui-même sur un pistolet qui lui est affecté."""

    DEPART = "depart"
    FIN = "fin"
    TYPE_CHOICES = [
        (DEPART, "Départ"),
        (FIN, "Fin"),
    ]

    session_caisse = models.ForeignKey(
        SessionCaisse, on_delete=models.CASCADE, related_name="releves_pompiste",
        editable=False,
    )
    employee = models.ForeignKey(
        Employee, on_delete=models.PROTECT, related_name="releves_index_pompiste",
        help_text="Le pompiste qui a réalisé ce relevé (capturé au moment du relevé, jamais déduit).",
    )
    pistolet = models.ForeignKey(Pistolet, on_delete=models.PROTECT, related_name="releves_pompiste")
    type_releve = models.CharField(max_length=10, choices=TYPE_CHOICES)
    valeur_index = models.DecimalField(max_digits=10, decimal_places=2)
    date_heure = models.DateTimeField()

    class Meta:
        verbose_name = "Relevé d'index (pompiste)"
        verbose_name_plural = "Relevés d'index (pompiste)"
        ordering = ["date_heure"]

    def save(self, *args, **kwargs):
        if self.session_caisse_id is None:
            session, _ = SessionCaisse.objects.get_or_create(
                employee=self.employee,
                date=timezone.localtime(self.date_heure).date(),
            )
            self.session_caisse = session
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.employee.nom_complet} - {self.pistolet} - {self.get_type_releve_display()} : {self.valeur_index}"


class ReleveIndexGerant(models.Model):
    """Index relevé indépendamment par le Gérant ou le Chef de piste, pour double vérification croisée
    face au relevé du pompiste."""

    DEPART = "depart"
    FIN = "fin"
    TYPE_CHOICES = [
        (DEPART, "Départ"),
        (FIN, "Fin"),
    ]

    session_caisse = models.ForeignKey(
        SessionCaisse, on_delete=models.CASCADE, related_name="releves_gerant",
        editable=False,
    )
    employee = models.ForeignKey(
        Employee, on_delete=models.PROTECT, related_name="releves_index_gerant",
        help_text="Le Gérant ou Chef de piste qui a réalisé ce relevé (le vérificateur).",
    )
    employee_pompiste = models.ForeignKey(
        Employee, on_delete=models.PROTECT, related_name="releves_verifies_par_gerant",
        help_text="Le pompiste dont les index sont vérifiés par ce relevé (capturé explicitement, jamais déduit).",
    )
    pistolet = models.ForeignKey(Pistolet, on_delete=models.PROTECT, related_name="releves_gerant")
    type_releve = models.CharField(max_length=10, choices=TYPE_CHOICES)
    valeur_index = models.DecimalField(max_digits=10, decimal_places=2)
    date_heure = models.DateTimeField()

    class Meta:
        verbose_name = "Relevé d'index (Gérant/Chef de piste)"
        verbose_name_plural = "Relevés d'index (Gérant/Chef de piste)"
        ordering = ["date_heure"]

    def save(self, *args, **kwargs):
        if self.session_caisse_id is None:
            session, _ = SessionCaisse.objects.get_or_create(
                employee=self.employee_pompiste,
                date=timezone.localtime(self.date_heure).date(),
            )
            self.session_caisse = session
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.employee.nom_complet} vérifie {self.employee_pompiste.nom_complet} - {self.pistolet} - {self.get_type_releve_display()} : {self.valeur_index}"
