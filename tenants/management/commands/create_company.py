from django.core.management.base import BaseCommand

from tenants.services import create_company


class Command(BaseCommand):
    help = "Cree une nouvelle societe cliente : base PostgreSQL, fiche Societe, migrations, et premier compte Admin Siege."

    def add_arguments(self, parser):
        parser.add_argument("--nom", required=True, help="Nom de la societe")
        parser.add_argument("--sous-domaine", required=True, help="Sous-domaine (slug), ex: eydon")
        parser.add_argument("--admin-nom", required=True, help="Nom complet du premier Admin Siege")
        parser.add_argument("--admin-telephone", required=True, help="Telephone du premier Admin Siege")
        parser.add_argument("--admin-password", required=True, help="Mot de passe du premier Admin Siege")

    def handle(self, *args, **options):
        societe = create_company(
            nom=options["nom"],
            sous_domaine=options["sous_domaine"],
            admin_prenom_nom=options["admin_nom"],
            admin_telephone=options["admin_telephone"],
            admin_password=options["admin_password"],
        )
        self.stdout.write(self.style.SUCCESS(
            f"Societe '{societe.nom}' creee avec succes (base: {societe.nom_base_donnees}, "
            f"sous-domaine: {societe.sous_domaine})."
        ))
