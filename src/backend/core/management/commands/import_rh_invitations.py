"""Preview or import a CSV of Assistant RH ProConnect invitations."""

import csv
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from core.authentication.invitations import eligible_groups, has_account, normalize_email
from core.models import AssistantRHInvitation


def invitation_status(email, group, *, now, apply, renew_expired):
    """Report the row outcome and persist an authorized creation or renewal."""
    if has_account(email):
        return "existing account, manual review"

    expires_at = now + timedelta(days=30)
    if apply:
        invitation, created = AssistantRHInvitation.objects.select_for_update().get_or_create(
            email=email, defaults={"group": group, "expires_at": expires_at}
        )
    else:
        invitation = AssistantRHInvitation.objects.filter(email=email).first()
        created = invitation is None

    if created:
        return "imported" if apply else "would import"
    if invitation.accepted_at is not None:
        return "used invitation"
    if invitation.expires_at > now:
        return "existing invitation"
    if not renew_expired:
        return "expired invitation (use --renew-expired)"

    if apply:
        invitation.group = group
        invitation.expires_at = expires_at
        invitation.challenge_expires_at = None
        invitation.challenge_digest = ""
        invitation.attempts = 0
        invitation.save(
            update_fields=[
                "group",
                "expires_at",
                "challenge_expires_at",
                "challenge_digest",
                "attempts",
                "updated_at",
            ]
        )
    return "renewed expired invitation" if apply else "would renew expired invitation"


class Command(BaseCommand):
    """Import invitations for a single corpus group, with a read-only preview by default."""

    help = "Preview (default) or import email invitations for one Assistant RH corpus group."

    def add_arguments(self, parser):
        parser.add_argument("csv_path")
        parser.add_argument("--group", required=True, help="Exact name of an eligible Django group")
        parser.add_argument("--apply", action="store_true", help="Persist new invitations")
        parser.add_argument(
            "--renew-expired", action="store_true", help="Renew expired, unused invitations"
        )

    @staticmethod
    def read_emails(path):
        """Validate all CSV rows and collect their line numbers by normalized address."""
        emails = {}
        try:
            with open(path, encoding="utf-8-sig", newline="") as source:
                rows = csv.DictReader(source, strict=True)
                if rows.fieldnames != ["email"]:
                    raise CommandError("Expected a CSV with a single column named email.")
                for row in rows:
                    line = rows.line_num
                    if None in row:
                        raise CommandError(f"Unexpected column on line {line}.")
                    try:
                        email = normalize_email(row["email"])
                    except ValidationError as exc:
                        raise CommandError(f"Invalid email on line {line}.") from exc
                    emails.setdefault(email, []).append(line)
        except (OSError, UnicodeError, csv.Error) as exc:
            raise CommandError(f"Could not read CSV: {exc}") from exc
        return emails

    @transaction.atomic
    def handle(self, *args, **options):
        emails = self.read_emails(options["csv_path"])
        group = eligible_groups().filter(name=options["group"]).first()
        if group is None:
            raise CommandError("Select a group with RH corpus grants and no Django permissions.")

        now = timezone.now()
        for email, lines in emails.items():
            status = invitation_status(
                email,
                group,
                now=now,
                apply=options["apply"],
                renew_expired=options["renew_expired"],
            )
            self.stdout.write(f"Line {lines[0]}: {status}")
            for line in lines[1:]:
                self.stdout.write(f"Line {line}: duplicate of line {lines[0]}")
