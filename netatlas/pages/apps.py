from django.apps import AppConfig


class PagesConfig(AppConfig):
    name = "netatlas.pages"

    def ready(self):
        # Connects the receivers that keep Item Pages in step with their items.
        from netatlas.pages import attachments, services  # noqa: F401
