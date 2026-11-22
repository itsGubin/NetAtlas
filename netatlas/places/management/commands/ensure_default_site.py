from django.core.management import BaseCommand

from netatlas.places.services import ensure_default_site


class Command(BaseCommand):
    help = "Create the default Site. Does nothing once any Site exists."

    def handle(self, *args, **options):
        if ensure_default_site():
            self.stdout.write("Created the default Site.")
