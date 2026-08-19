from django.db import models


class PricingTier(models.Model):
    """Grille tarifaire : palier de nombre de stations x duree d'engagement.
    Modele en base (pas des constantes Python) pour permettre au Super Admin d'ajuster
    la tarification sans redeploiement."""

    DUREE_CHOICES = [
        (1, "1 mois"),
        (3, "3 mois"),
        (6, "6 mois"),
        (12, "12 mois"),
    ]

    nombre_stations_min = models.PositiveIntegerField(
        help_text="Borne basse du palier (inclusive).",
    )
    nombre_stations_max = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Borne haute du palier (inclusive). Null = illimite vers le haut "
                   "(dernier palier).",
    )
    duree_mois = models.PositiveSmallIntegerField(choices=DUREE_CHOICES)
    prix_par_station_mensuel = models.DecimalField(
        max_digits=10, decimal_places=2,
        help_text="Prix unitaire mensuel par station, en FCFA.",
    )

    class Meta:
        verbose_name = "Palier tarifaire"
        verbose_name_plural = "Paliers tarifaires"
        unique_together = ("nombre_stations_min", "nombre_stations_max", "duree_mois")
        ordering = ["nombre_stations_min", "duree_mois"]

    def __str__(self):
        borne_haute = self.nombre_stations_max if self.nombre_stations_max else "+"
        return f"{self.nombre_stations_min}-{borne_haute} stations, {self.get_duree_mois_display()} : {self.prix_par_station_mensuel} FCFA/station/mois"


class Subscription(models.Model):
    """Abonnement d'une societe. UN SEUL abonnement continu par societe (OneToOneField) :
    ce n'est jamais recree. nombre_stations/montant_mensuel/pricing_tier sont MUTABLES,
    mis a jour en place a chaque changement de nombre de stations (l'immutabilite se
    reporte sur Invoice, pas ici : chaque changement declenche une facture qui capture
    un instantane fige du montant a ce moment precis).

    date_debut/date_fin representent le CYCLE D'ENGAGEMENT EN COURS (se renouvelle,
    date_fin se prolonge a l'echeance), pas la duree de vie totale de l'abonnement.
    duree_mois reste fige jusqu'au renouvellement — un changement de duree serait une
    decision prise explicitement au moment du renouvellement, pas en cours de cycle."""

    TRIAL = "trial"
    ACTIVE = "active"
    LATE = "late"
    SUSPENDED = "suspended"
    TERMINATED = "terminated"
    STATUT_CHOICES = [
        (TRIAL, "Essai"),
        (ACTIVE, "Actif"),
        (LATE, "En retard"),
        (SUSPENDED, "Suspendu"),
        (TERMINATED, "Resilie"),
    ]

    societe = models.OneToOneField("tenants.Societe", on_delete=models.PROTECT, related_name="subscription")
    pricing_tier = models.ForeignKey(PricingTier, on_delete=models.PROTECT, related_name="subscriptions")
    nombre_stations = models.PositiveIntegerField(
        help_text="Nombre de stations actuel de la societe. Mutable : mis a jour a "
                   "chaque changement (ajout/retrait de station).",
    )
    duree_mois = models.PositiveSmallIntegerField(
        choices=PricingTier.DUREE_CHOICES,
        help_text="Duree d'engagement du cycle en cours. Fige jusqu'au renouvellement.",
    )
    montant_mensuel = models.DecimalField(
        max_digits=12, decimal_places=2,
        help_text="nombre_stations x prix_par_station_mensuel du tier. Mutable, recalcule "
                   "a chaque changement de nombre_stations.",
    )
    statut = models.CharField(max_length=20, choices=STATUT_CHOICES, default=TRIAL)
    date_debut = models.DateTimeField(help_text="Debut du cycle d'engagement en cours.")
    date_fin = models.DateTimeField(help_text="Fin du cycle d'engagement en cours (se prolonge au renouvellement).")
    date_prochaine_echeance = models.DateTimeField()
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Abonnement"
        verbose_name_plural = "Abonnements"

    def __str__(self):
        return f"{self.societe.nom} - {self.get_statut_display()} ({self.nombre_stations} stations)"


class Invoice(models.Model):
    """Facture immuable, numerotee sequentiellement. Le taux de TVA est stocke sur la
    facture elle-meme (pas une constante globale) : si le taux legal change un jour, les
    anciennes factures doivent rester prouvables avec le taux reellement en vigueur a
    leur emission — meme principe deja applique a PrixCarburant.

    Rattachee directement a la Societe (pas seulement a la Subscription) : une facture
    reste comprehensible seule, meme si l'abonnement sous-jacent change de statut apres."""

    IMPAYEE = "impayee"
    PARTIELLEMENT_PAYEE = "partiellement_payee"
    PAYEE = "payee"
    VOID = "void"
    STATUT_CHOICES = [
        (IMPAYEE, "Impayee"),
        (PARTIELLEMENT_PAYEE, "Partiellement payee"),
        (PAYEE, "Payee"),
        (VOID, "Annulee (void)"),
    ]

    societe = models.ForeignKey("tenants.Societe", on_delete=models.PROTECT, related_name="invoices")
    subscription = models.ForeignKey(
        Subscription, on_delete=models.PROTECT, related_name="invoices",
        help_text="Abonnement ayant genere cette facture (tracabilite).",
    )
    numero = models.CharField(max_length=50, unique=True, help_text="Ex: INV-2026-000123")
    montant_ht = models.DecimalField(max_digits=12, decimal_places=2)
    taux_tva = models.DecimalField(
        max_digits=5, decimal_places=2, default=18,
        help_text="Taux de TVA applique a cette facture (%), fige a l'emission.",
    )
    montant_tva = models.DecimalField(max_digits=12, decimal_places=2)
    montant_ttc = models.DecimalField(max_digits=12, decimal_places=2)
    statut = models.CharField(max_length=20, choices=STATUT_CHOICES, default=IMPAYEE)
    date_emission = models.DateTimeField()
    date_echeance = models.DateTimeField()
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Facture"
        verbose_name_plural = "Factures"
        ordering = ["-date_emission"]

    def __str__(self):
        return f"{self.numero} - {self.societe.nom} - {self.montant_ttc} FCFA ({self.get_statut_display()})"


class Payment(models.Model):
    """Paiement lie a une facture, peut etre partiel. Enregistre manuellement par le
    Super Admin pour l'instant (integration PayDunya prevue plus tard)."""

    VIREMENT = "virement"
    WAVE = "wave"
    ORANGE_MONEY = "orange_money"
    PAYDUNYA = "paydunya"
    METHODE_CHOICES = [
        (VIREMENT, "Virement bancaire"),
        (WAVE, "Wave"),
        (ORANGE_MONEY, "Orange Money"),
        (PAYDUNYA, "PayDunya"),
    ]

    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="payments")
    montant = models.DecimalField(
        max_digits=12, decimal_places=2,
        help_text="Peut etre inferieur au montant_ttc de la facture (paiement partiel).",
    )
    methode = models.CharField(max_length=20, choices=METHODE_CHOICES)
    reference = models.CharField(max_length=100, blank=True, help_text="Reference libre, optionnelle.")
    enregistre_par = models.ForeignKey(
        "platform_admin.SuperAdmin", on_delete=models.PROTECT, related_name="paiements_enregistres",
        help_text="Le Super Admin qui a enregistre ce paiement manuellement.",
    )
    date_paiement = models.DateTimeField(
        help_text="Moment reel du paiement (modifiable). A ne jamais confondre avec date_creation.",
    )
    date_creation = models.DateTimeField(
        auto_now_add=True,
        help_text="Horodatage technique de la saisie en base (audit uniquement).",
    )

    class Meta:
        verbose_name = "Paiement"
        verbose_name_plural = "Paiements"
        ordering = ["-date_paiement"]

    def __str__(self):
        return f"{self.invoice.numero} - {self.montant} FCFA ({self.get_methode_display()})"
