from contextvars import ContextVar

# Stocke le nom de la base de données de la société active pendant le traitement d'une requête.
current_tenant_db: ContextVar[str] = ContextVar("current_tenant_db", default="default")


def set_current_tenant_db(db_name: str) -> None:
    current_tenant_db.set(db_name)


def get_current_tenant_db() -> str:
    return current_tenant_db.get()


def reset_current_tenant_db() -> None:
    """Réinitialise le contexte vers la base par défaut ("default", la base maître).
    À appeler systématiquement (idéalement dans un finally) après toute bascule manuelle
    du contexte en dehors du cycle normal middleware/requête — par exemple à la fin de
    create_company, pour ne jamais laisser un worker "coincé" sur la base d'une société
    après une opération ponctuelle. Un oubli ici est un risque de fuite de données
    inter-société sur les requêtes suivantes traitées par le même worker."""
    current_tenant_db.set("default")
