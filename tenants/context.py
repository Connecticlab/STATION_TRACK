from contextvars import ContextVar

# Stocke le nom de la base de données de la société active pendant le traitement d'une requête.
current_tenant_db: ContextVar[str] = ContextVar("current_tenant_db", default="default")


def set_current_tenant_db(db_name: str) -> None:
    current_tenant_db.set(db_name)


def get_current_tenant_db() -> str:
    return current_tenant_db.get()
