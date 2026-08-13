from django.db import models


class Societe(models.Model):
    """Une société cliente (tenant) — chaque société a sa propre base de données."""
    nom = models.CharField(max_length=255)
    sous_domaine = models.SlugField(max_length=100, unique=True)
    nom_base_donnees = models.CharField(max_length=100, unique=True)
    actif = models.BooleanField(default=True)
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Société"
        verbose_name_plural = "Sociétés"

    def __str__(self):
        return self.nom
