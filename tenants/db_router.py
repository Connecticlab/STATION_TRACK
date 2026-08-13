from tenants.context import get_current_tenant_db

APPS_PARTAGEES = {"auth", "admin", "sessions", "contenttypes", "tenants"}


class TenantDatabaseRouter:
    """Dirige chaque requête vers la base maître (apps partagées) ou la base de la société active (apps métier)."""

    def db_for_read(self, model, **hints):
        return self._route(model)

    def db_for_write(self, model, **hints):
        return self._route(model)

    def allow_relation(self, obj1, obj2, **hints):
        return True

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        if app_label in APPS_PARTAGEES:
            return db == "default"
        return db != "default"

    def _route(self, model):
        if model._meta.app_label in APPS_PARTAGEES:
            return "default"
        return get_current_tenant_db()
