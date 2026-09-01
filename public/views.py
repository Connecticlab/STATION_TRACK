from django.conf import settings
from django.shortcuts import render
from django.utils import timezone


def landing_page(request):
    """Page vitrine publique de StationTrack, accessible uniquement sur le domaine
    racine exact (carburant.sn, sans sous-domaine) — jamais confondue avec un espace
    societe. Aucune logique metier : contenu marketing statique. base_domaine transmis
    au template pour le widget "Acceder a mon espace" (construit le lien de
    redirection vers <sous_domaine>.<base_domaine> cote client, sans jamais coder en
    dur le domaine — fonctionne pareil en dev/test et en production). annee_courante
    transmise explicitement pour le copyright du pied de page — jamais une valeur en
    dur qui deviendrait fausse l annee suivante."""
    contexte = {
        "base_domaine": settings.BASE_DOMAIN,
        "annee_courante": timezone.now().year,
    }
    return render(request, "public/landing.html", contexte)
