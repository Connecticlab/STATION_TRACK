from django.db import models
from django.utils import timezone

from accounts.models import Employee
from stations.models import Pistolet, Station


class SessionCaisse(models.Model):
    """Regroupe TOUS les pistolets qu'un pompiste gère pour une journée donnée,
    quel que soit le nombre de pompes concernées. Créée automatiquement dès le premier
    relevé d'index de départ du pompiste (pas d'action "ouvrir la caisse" séparée)."""

    MANQUANT = "manquant"
    SURPLUS = "surplus"
    EXACT = "exact"
    RESULTAT_CHOICES = [
        (MANQUANT, "Manquant"),
        (SURPLUS, "Surplus"),
        (EXACT, "Exact"),
    ]

    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="sessions_caisse")
    date = models.DateField()
    montant_cash = models.DecimalField(
        max_digits=12, decimal_places=2, default=0,
        help_text="Cumul des paiements en espèces, incrémenté par le pompiste en cours de service.",
    )
    montant_wave = models.DecimalField(
        max_digits=12, decimal_places=2, default=0,
        help_text="Cumul des paiements Wave, incrémenté par le pompiste en cours de service.",
    )
    montant_orange_money = models.DecimalField(
        max_digits=12, decimal_places=2, default=0,
        help_text="Cumul des paiements Orange Money, incrémenté par le pompiste en cours de service.",
    )
    montant_encaisse = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        help_text="Somme automatique de montant_cash + montant_wave + montant_orange_money "
                   "à la clôture. Pas une saisie manuelle unique.",
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
        unique_together = ("employee_pompiste", "pistolet", "type_releve", "session_caisse")

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


class SoldePompiste(models.Model):
    """Solde roulant avoir/dette d'un pompiste. Positif = avoir, négatif = dette.
    Vit dans caisse (pas accounts) : c'est un concept de caisse, accounts ne connaît
    que l'identité de l'employé, jamais la logique métier caisse."""

    employee = models.OneToOneField(Employee, on_delete=models.CASCADE, related_name="solde_pompiste")
    solde_courant = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    date_maj = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Solde pompiste"
        verbose_name_plural = "Soldes pompistes"

    def __str__(self):
        nature = "avoir" if self.solde_courant >= 0 else "dette"
        return f"{self.employee.nom_complet} - {abs(self.solde_courant)} FCFA ({nature})"


class EcritureSolde(models.Model):
    """Trace immuable de chaque mouvement du solde d'un pompiste. Jamais modifiée après coup :
    toute correction passe par une nouvelle écriture (type AJUSTEMENT_MANUEL)."""

    SURPLUS_CONSTATE = "surplus_constate"
    MANQUANT_CONSTATE = "manquant_constate"
    AJUSTEMENT_MANUEL = "ajustement_manuel"
    TYPE_CHOICES = [
        (SURPLUS_CONSTATE, "Surplus constaté"),
        (MANQUANT_CONSTATE, "Manquant constaté"),
        (AJUSTEMENT_MANUEL, "Ajustement manuel"),
    ]

    solde_pompiste = models.ForeignKey(SoldePompiste, on_delete=models.CASCADE, related_name="ecritures")
    session_caisse = models.ForeignKey(
        SessionCaisse, on_delete=models.PROTECT, related_name="ecritures_solde",
        null=True, blank=True,
        help_text="Session de caisse à l'origine de cette écriture. Nulle pour un ajustement manuel.",
    )
    type_ecriture = models.CharField(max_length=20, choices=TYPE_CHOICES)
    montant = models.DecimalField(
        max_digits=12, decimal_places=2,
        help_text="Toujours positif. Le signe de l'effet se déduit du type_ecriture.",
    )
    solde_avant = models.DecimalField(max_digits=12, decimal_places=2)
    solde_apres = models.DecimalField(max_digits=12, decimal_places=2)
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Écriture de solde"
        verbose_name_plural = "Écritures de solde"
        ordering = ["-date_creation"]

    def __str__(self):
        return f"{self.solde_pompiste.employee.nom_complet} - {self.get_type_ecriture_display()} : {self.montant} FCFA"


class DepotBancaire(models.Model):
    """Envoi d'argent en banque par le Gérant (exclusif à ce rôle, jamais le Chef de piste).
    Volontairement indépendant du théorique : ne jamais traiter un montant qui ne correspond
    pas exactement au théorique/solde comme une anomalie automatique — le Gérant peut être
    autorisé à garder une partie de l'encaissement en caisse pour urgences."""

    station = models.ForeignKey(Station, on_delete=models.CASCADE, related_name="depots_bancaires")
    employee = models.ForeignKey(
        Employee, on_delete=models.PROTECT, related_name="depots_bancaires",
        help_text="Le Gérant qui a effectué le dépôt.",
    )
    montant = models.DecimalField(max_digits=12, decimal_places=2)
    banque = models.CharField(max_length=255, help_text="Nom de la banque/agence.")
    reference = models.CharField(max_length=100, blank=True, help_text="Numéro de reçu, optionnel.")
    date_heure = models.DateTimeField(
        help_text="Moment réel du dépôt (modifiable). À ne jamais confondre avec date_creation.",
    )
    date_creation = models.DateTimeField(
        auto_now_add=True,
        help_text="Horodatage technique de la saisie en base (audit uniquement).",
    )

    class Meta:
        verbose_name = "Dépôt bancaire"
        verbose_name_plural = "Dépôts bancaires"
        ordering = ["-date_heure"]

    def __str__(self):
        return f"{self.station.nom} - Dépôt {self.montant} FCFA ({self.banque}) le {self.date_heure:%d/%m/%Y}"


class DepenseCaisse(models.Model):
    """Dépense payée directement depuis la caisse de la station (Gérant OU Chef de piste,
    pas de restriction de rôle contrairement à DepotBancaire). Déductible du montant conservé
    en caisse."""

    station = models.ForeignKey(Station, on_delete=models.CASCADE, related_name="depenses_caisse")
    employee = models.ForeignKey(
        Employee, on_delete=models.PROTECT, related_name="depenses_caisse",
        help_text="Le Gérant ou Chef de piste qui a enregistré la dépense.",
    )
    montant = models.DecimalField(max_digits=12, decimal_places=2)
    motif = models.CharField(max_length=255, help_text="Texte libre — pas de catégories prédéfinies pour l'instant.")
    date_heure = models.DateTimeField(
        help_text="Moment réel de la dépense (modifiable). À ne jamais confondre avec date_creation.",
    )
    date_creation = models.DateTimeField(
        auto_now_add=True,
        help_text="Horodatage technique de la saisie en base (audit uniquement).",
    )

    class Meta:
        verbose_name = "Dépense de caisse"
        verbose_name_plural = "Dépenses de caisse"
        ordering = ["-date_heure"]

    def __str__(self):
        return f"{self.station.nom} - Dépense {self.montant} FCFA ({self.motif}) le {self.date_heure:%d/%m/%Y}"
