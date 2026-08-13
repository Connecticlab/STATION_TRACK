from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
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


from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager


class SuperAdminManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("L'email est obligatoire.")
        email = self.normalize_email(email)
        super_admin = self.model(email=email, **extra_fields)
        super_admin.set_password(password)
        super_admin.save(using=self._db)
        return super_admin

    def create_superuser(self, email, password=None, **extra_fields):
        return self.create_user(email, password, **extra_fields)


class SuperAdmin(AbstractBaseUser):
    """Compte de l'équipe technique/plateforme CTL Group. Authentification totalement séparée
    des employés de société. Accessible uniquement via admin.<BASE_DOMAIN>."""

    nom_complet = models.CharField(max_length=255)
    email = models.EmailField(unique=True)
    actif = models.BooleanField(default=True)
    date_creation = models.DateTimeField(auto_now_add=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["nom_complet"]

    objects = SuperAdminManager()

    class Meta:
        verbose_name = "Super Admin"
        verbose_name_plural = "Super Admins"

    def __str__(self):
        return self.nom_complet
