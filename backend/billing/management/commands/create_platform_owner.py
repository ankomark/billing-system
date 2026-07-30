"""
Create the platform owner — the account that sees every operator.

Migration 0031 deliberately did not promote anyone. Every pre-existing account
belonged to the original operator, and quietly turning one into platform staff
would have granted it visibility over every operator onboarded later. So the
first platform account is created here, explicitly.
"""

import getpass

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from billing.models import User


class Command(BaseCommand):
    help = "Create a platform owner or platform staff account (sees all operators)"

    def add_arguments(self, parser):
        parser.add_argument("--username", required=True)
        parser.add_argument("--email", default="")
        parser.add_argument(
            "--staff",
            action="store_true",
            help="Create platform_staff instead of platform_owner",
        )
        parser.add_argument(
            "--password",
            help="Prompted for if omitted. Passing it here leaves it in shell history.",
        )

    def handle(self, *args, **options):
        username = options["username"]
        role = User.PLATFORM_STAFF if options["staff"] else User.PLATFORM_OWNER

        if User.objects.filter(username=username).exists():
            raise CommandError(f"A user named {username!r} already exists.")

        password = options.get("password")
        if not password:
            password = getpass.getpass("Password: ")
            if password != getpass.getpass("Password (again): "):
                raise CommandError("Passwords did not match.")

        try:
            validate_password(password)
        except ValidationError as exc:
            raise CommandError("Password rejected: " + "; ".join(exc.messages))

        user = User.objects.create_user(
            username=username,
            email=options["email"],
            password=password,
            role=role,
            # NULL tenant is what makes this a platform account: queries run
            # unscoped, so it sees every operator. The database constraint
            # user_role_matches_tenant_presence enforces the pairing.
            tenant=None,
            # Django admin access. Distinct from the role, which is what the
            # API authorises against.
            is_staff=True,
            is_superuser=options["staff"] is False,
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Created {user.username} as {user.get_role_display()} "
                f"(sees all operators)."
            )
        )
