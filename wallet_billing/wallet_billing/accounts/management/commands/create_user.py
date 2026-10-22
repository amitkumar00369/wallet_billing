from django.core.management.base import BaseCommand, CommandError

from accounts.models import User


class Command(BaseCommand):
    help = "Create a USER or PROVIDER account (a wallet is created automatically)."

    def add_arguments(self, parser):
        parser.add_argument("--email", required=True)
        parser.add_argument("--password", required=True)
        parser.add_argument("--role", choices=[User.Role.USER, User.Role.PROVIDER], default=User.Role.USER)

    def handle(self, *args, email, password, role, **options):
        if User.objects.filter(email__iexact=email).exists():
            raise CommandError(f"A user with email {email} already exists.")
        user = User.objects.create_user(email=email, password=password, role=role)
        self.stdout.write(self.style.SUCCESS(f"Created {user.role} id={user.id} email={user.email}"))
