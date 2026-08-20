from django import template

register = template.Library()


@register.filter(name="fcfa")
def fcfa(valeur):
    """Formate un nombre en montant FCFA lisible : separateur d'espace tous les 3
    chiffres, sans decimales inutiles (ex: 68000 -> '68 000 FCFA', 68000.00 -> idem,
    68000.50 -> '68 000,50 FCFA')."""
    if valeur is None:
        return ""

    try:
        nombre = float(valeur)
    except (TypeError, ValueError):
        return valeur

    est_entier = nombre == int(nombre)

    if est_entier:
        partie_entiere = f"{int(nombre):,}".replace(",", " ")
        return f"{partie_entiere} FCFA"

    partie_entiere, partie_decimale = f"{nombre:,.2f}".split(".")
    partie_entiere = partie_entiere.replace(",", " ")
    return f"{partie_entiere},{partie_decimale} FCFA"
