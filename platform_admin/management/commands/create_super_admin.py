from django.core.management.base import BaseCommand, CommandError

from platform_admin.models import SuperAdmin


class Command(BaseCommand):
    help = (
        "Cree un compte Super Admin (equipe technique/plateforme). Bootstrap initial "
        "en deploiement (aucun autre moyen de creer le tout premier compte, l interface "
        "platform_admin necessitant deja une session Super Admin pour y acceder), ou "
        "ajout d un membre d equipe supplementaire."
    )

    def add_arguments(self, parser):
        parser.add_argument("--email", required=True, help="Email du Super Admin")
        parser.add_argument("--nom", required=True, help="Nom complet du Super Admin")
        parser.add_argument("--password", required=True, help="Mot de passe du Super Admin")

    def handle(self, *args, **options):
        email = options["email"].strip().lower()
        nom_complet = options["nom"].strip()
        password = options["password"]

        if SuperAdmin.objects.filter(email=email).exists():
            raise CommandError(f"Un Super Admin avec l'email '{email}' existe deja.")

        if len(password) < 8:
            raise CommandError("Le mot de passe doit contenir au moins 8 caracteres.")

        super_admin = SuperAdmin.objects.create_user(
            email=email,
            password=password,
            nom_complet=nom_complet,
        )
        self.stdout.write(self.style.SUCCESS(
            f"Super Admin '{super_admin.nom_complet}' ({super_admin.email}) cree avec succes."
        ))
