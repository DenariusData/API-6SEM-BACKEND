from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core_api"

    def ready(self):
        # Liga a gravação automática do log de auditoria (AKA-31).
        from core_api import signals  # noqa: F401
