from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.db import models

from stations.models import Station


class EmployeeManager(BaseUserManager):
    def create_user(self, telephone, password=None, **extra_fields):
        if not telephone:
            raise ValueError("Le numéro de téléphone est obligatoire.")
        employee = self.model(telephone=telephone, **extra_fields)
        employee.set_password(password)
        employee.save(using=self._db)
        return employee


class Employee(AbstractBaseUser):
    """Un employé d'une société (Pompiste, Chef de piste, Gérant, Admin Siège), rattaché à une seule station.
    Vit dans la base de la société — jamais dans la base maître."""

    POMPISTE = "pompiste"
    CHEF_DE_PISTE = "chef_de_piste"
    GERANT = "gerant"
    ADMIN_SIEGE = "admin_siege"
    ROLE_CHOICES = [
        (POMPISTE, "Pompiste"),
        (CHEF_DE_PISTE, "Chef de piste"),
        (GERANT, "Gérant"),
        (ADMIN_SIEGE, "Admin Siège"),
    ]

    nom_complet = models.CharField(max_length=255)
    telephone = models.CharField(max_length=20, unique=True)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    station = models.ForeignKey(
        Station, on_delete=models.PROTECT, related_name="employes",
        null=True, blank=True,
        help_text="Nulle uniquement pour l'Admin Siège (rôle transversal à toutes les stations de la société).",
    )
    actif = models.BooleanField(default=True)
    date_creation = models.DateTimeField(auto_now_add=True)

    USERNAME_FIELD = "telephone"
    REQUIRED_FIELDS = ["nom_complet", "role"]

    objects = EmployeeManager()

    class Meta:
        verbose_name = "Employé"
        verbose_name_plural = "Employés"

    def __str__(self):
        return f"{self.nom_complet} ({self.get_role_display()})"


class LoginAttemptEmployee(models.Model):
    """Trace chaque tentative de connexion Employee (reussie ou echouee), pour l'anti brute-force.
    Vit dans la base de la societe (comme Employee), PAS dans platform_admin/base maitre :
    LoginAttempt (SuperAdmin) et LoginAttemptEmployee sont deux modeles distincts car ils
    vivent necessairement dans des bases differentes selon le routeur multitenant — pas
    une simple preference de style, une contrainte de l'architecture. Isole naturellement
    l'anti brute-force par societe."""

    identifiant_tente = models.CharField(
        max_length=255,
        help_text="Telephone tente au moment du login",
    )
    adresse_ip = models.GenericIPAddressField()
    reussie = models.BooleanField(default=False)
    date_tentative = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Tentative de connexion (Employe)"
        verbose_name_plural = "Tentatives de connexion (Employe)"
        indexes = [
            models.Index(fields=["identifiant_tente", "date_tentative"]),
            models.Index(fields=["adresse_ip", "date_tentative"]),
        ]
        ordering = ["-date_tentative"]

    def __str__(self):
        statut = "réussie" if self.reussie else "échouée"
        return f"{self.identifiant_tente} depuis {self.adresse_ip} - {statut}"
