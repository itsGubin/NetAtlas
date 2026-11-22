import os

from django.core.exceptions import ValidationError
from django.core.management import BaseCommand, CommandError

from netatlas.accounts.services import ensure_initial_admin, has_admin


class Command(BaseCommand):
    help = "Create the first Admin from the environment. Does nothing once an Admin exists."

    def handle(self, *args, **options):
        if has_admin():
            return

        names = ("NETATLAS_ADMIN_USERNAME", "NETATLAS_ADMIN_PASSWORD")
        missing = [name for name in names if not os.environ.get(name)]
        if missing:
            raise CommandError(f"No Admin exists yet. Set {' and '.join(missing)} in .env.")

        try:
            ensure_initial_admin(
                username=os.environ["NETATLAS_ADMIN_USERNAME"],
                password=os.environ["NETATLAS_ADMIN_PASSWORD"],
            )
        except ValidationError as error:
            raise CommandError(f"Invalid initial Admin: {' '.join(error.messages)}") from error
        self.stdout.write("Created the initial Admin.")
