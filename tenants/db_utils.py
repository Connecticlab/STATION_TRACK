from decouple import config
from django.conf import settings
from django.db import connections


def register_company_database(societe):
    """Enregistre dynamiquement la base de données d'une société dans settings.DATABASES,
    pour que Django puisse s'y connecter dans le process courant.

    Idempotent : si la base est déjà enregistrée, ne fait rien.

    Doit être appelée à DEUX endroits distincts :
    1. Une fois pendant create_company, pour rendre la base immédiatement utilisable
       dans le même run (notamment pour y lancer les migrations).
    2. À CHAQUE requête HTTP, dans le middleware de résolution de tenant — pour garantir
       qu'un worker qui n'a jamais vu cette société avant (nouveau process, redémarrage)
       puisse quand même la servir dès sa première requête, sans redémarrage serveur.

    Réutilise DB_ENGINE/DB_USER/DB_PASSWORD/DB_HOST/DB_PORT du .env, avec le nom de base
    de la société — un seul rôle PostgreSQL pour toutes les bases.
    """
    nom_base = societe.nom_base_donnees

    if nom_base in settings.DATABASES:
        return

    settings.DATABASES[nom_base] = {
        "ENGINE": config("DB_ENGINE"),
        "NAME": nom_base,
        "USER": config("DB_USER"),
        "PASSWORD": config("DB_PASSWORD"),
        "HOST": config("DB_HOST"),
        "PORT": config("DB_PORT"),
    }

    # Django complète normalement chaque entrée de DATABASES avec des valeurs par défaut
    # (OPTIONS, CONN_MAX_AGE, etc.) une seule fois au démarrage, via une cached_property.
    # Un ajout après coup ne déclenche pas cette complétion — sans l'invalidation ci-dessous,
    # toute connexion à cette base échoue avec un KeyError sur 'OPTIONS'.
    if "settings" in connections.__dict__:
        del connections.__dict__["settings"]
