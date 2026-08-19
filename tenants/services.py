import psycopg2
from decouple import config
from django.core.management import call_command

from accounts.models import Employee
from tenants.context import set_current_tenant_db, reset_current_tenant_db
from tenants.db_utils import register_company_database
from tenants.models import Societe

APPS_METIER = ["stations", "accounts", "caisse", "cuves"]


def _creer_base_postgresql(nom_base):
    """Crée physiquement une base PostgreSQL via une connexion admin en autocommit
    (CREATE DATABASE ne peut pas s'exécuter dans une transaction)."""
    connexion = psycopg2.connect(
        dbname="postgres",
        user=config("DB_USER"),
        password=config("DB_PASSWORD"),
        host=config("DB_HOST"),
        port=config("DB_PORT"),
    )
    connexion.autocommit = True
    try:
        with connexion.cursor() as curseur:
            curseur.execute(f'CREATE DATABASE "{nom_base}" OWNER "{config("DB_USER")}";')
    finally:
        connexion.close()


def create_company(nom, sous_domaine, admin_prenom_nom, admin_telephone, admin_password):
    """Crée une nouvelle société de bout en bout : base PostgreSQL, fiche Societe,
    migrations des apps métier, et premier compte Admin Siège.

    Pas une fonction pure : effets de bord réels (base physique, fichiers de migration,
    contexte tenant global). Assumé, cohérent avec la nature de l'opération.

    Risque connu et accepté pour l'instant : si une étape après la création de la base
    PostgreSQL échoue, la base reste orpheline (aucune Societe ne la référence). Pas de
    rollback cross-système (PostgreSQL + Django) implémenté — nettoyage manuel possible
    si ça arrive. Un vrai rollback mériterait sa propre conception dédiée.

    admin_prenom_nom, admin_telephone, admin_password : le premier compte Admin Siège
    de la société, créé automatiquement (pas une étape manuelle séparée) — indispensable
    pour un onboarding client viable.
    """
    nom_base_donnees = f"stationtrack_{sous_domaine}"

    _creer_base_postgresql(nom_base_donnees)

    societe = Societe.objects.create(
        nom=nom,
        sous_domaine=sous_domaine,
        nom_base_donnees=nom_base_donnees,
    )

    register_company_database(societe)

    for app in APPS_METIER:
        call_command("migrate", app, database=nom_base_donnees)

    try:
        set_current_tenant_db(societe.nom_base_donnees)
        Employee.objects.create_user(
            telephone=admin_telephone,
            password=admin_password,
            nom_complet=admin_prenom_nom,
            role=Employee.ADMIN_SIEGE,
            station=None,
        )
    finally:
        # CRITIQUE (sécurité multitenant) : si cette réinitialisation n'a pas lieu, le
        # worker reste "coincé" sur la base de cette société pour toutes les requêtes
        # suivantes qu'il traitera — risque de fuite de données inter-société. Le
        # try/finally garantit l'exécution même si create_user échoue (ex. téléphone
        # déjà utilisé par un autre compte).
        reset_current_tenant_db()

    return societe
