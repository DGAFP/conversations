"""Email confirmation for CSV invitations, after verified RH OIDC authentication."""

import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.core.mail import get_connection, send_mail
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac

from core.mail import ScalewayTEMError
from core.models import AssistantRHInvitation, User

logger = logging.getLogger(__name__)

SESSION_KEY = "assistant_rh_invitation"
CHALLENGE_LIFETIME = timedelta(minutes=10)
SEND_RETRY_DELAY = timedelta(minutes=1)
MAX_ATTEMPTS = 5


class InvitationRequired(Exception):
    """Carry verified profile claims to the callback without starting a login."""

    def __init__(self, claims):
        super().__init__()
        self.claims = claims


def enabled():
    """Require RH OIDC, invitations, and a configured issuer."""
    return (
        settings.ASSISTANT_RH_ENABLED
        and settings.ASSISTANT_RH_OIDC_ENABLED
        and settings.ASSISTANT_RH_INVITATIONS_ENABLED
        and bool(settings.OIDC_OP_URL)
    )


def normalize_email(value):
    """Validate and normalize the same way at import and redemption."""
    if not isinstance(value, str):
        raise ValidationError("Invalid email.")
    email = value.strip().lower()
    if len(email) > 254:
        raise ValidationError("Invalid email.")
    validate_email(email)
    return email


def has_account(email, sub=None):
    """Keep existing local and OIDC accounts on the manual admission path."""
    query = Q(email__iexact=email) | Q(admin_email__iexact=email)
    if sub:
        query |= Q(sub=sub)
    return User.objects.filter(query).exists()


def eligible_groups():
    """Use corpus groups that grant no Django model permissions."""
    return Group.objects.filter(assistant_rh_grants__isnull=False, permissions__isnull=True)


def _digest(challenge_id, code):
    return salted_hmac(
        "assistant-rh.invitation", f"{challenge_id}:{code}", algorithm="sha256"
    ).hexdigest()


def _available(invitation, claims, now):
    return (
        invitation is not None
        and invitation.accepted_at is None
        and invitation.expires_at > now
        and eligible_groups().filter(pk=invitation.group_id).exists()
        and not has_account(invitation.email, claims["sub"])
    )


def start(request, claims):
    """Reserve one challenge per address per lifetime, then send its code."""
    now = timezone.now()
    pending = request.session.pop(SESSION_KEY, None)
    with transaction.atomic():
        invitation = (
            AssistantRHInvitation.objects.select_for_update().filter(email=claims["email"]).first()
        )
        if not _available(invitation, claims, now):
            return False
        if invitation.challenge_expires_at and invitation.challenge_expires_at > now:
            if (
                pending
                and pending["invitation"] == str(invitation.pk)
                and pending["issuer"] == settings.OIDC_OP_URL
                and pending["expires_at"] == invitation.challenge_expires_at.isoformat()
                and pending["claims"]["sub"] == claims["sub"]
            ):
                request.session[SESSION_KEY] = pending
                return True
            return False

        code = secrets.token_hex(6)
        challenge_id = secrets.token_hex(32)
        invitation.challenge_expires_at = now + CHALLENGE_LIFETIME
        invitation.challenge_digest = _digest(challenge_id, code)
        invitation.attempts = 0
        invitation.save(update_fields=["challenge_expires_at", "challenge_digest", "attempts"])

    try:
        sent = send_mail(
            "Votre code d’activation Assistant RH",
            f"Votre code est {code}. Il expire dans 10 minutes.",
            settings.EMAIL_FROM,
            [invitation.email],
            connection=get_connection(timeout=10),
        )
    except OSError, ScalewayTEMError:
        sent = 0
    if sent != 1:
        logger.warning("Assistant RH invitation email could not be sent (%s)", invitation.pk)
        AssistantRHInvitation.objects.filter(
            pk=invitation.pk, challenge_digest=invitation.challenge_digest
        ).update(challenge_digest="", challenge_expires_at=timezone.now() + SEND_RETRY_DELAY)
        return False

    request.session[SESSION_KEY] = {
        "invitation": str(invitation.pk),
        "issuer": settings.OIDC_OP_URL,
        "challenge_id": challenge_id,
        "expires_at": invitation.challenge_expires_at.isoformat(),
        "claims": claims,
    }
    return True


def confirm(pending, code, create_user):
    """Consume the invitation exactly once, and only together with account creation."""
    if pending["issuer"] != settings.OIDC_OP_URL:
        return False

    claims = pending["claims"]
    try:
        with transaction.atomic():
            invitation = (
                AssistantRHInvitation.objects.select_for_update()
                .filter(pk=pending["invitation"], email=claims["email"])
                .first()
            )
            now = timezone.now()
            if not _available(invitation, claims, now):
                return False
            if (
                not invitation.challenge_expires_at
                or invitation.challenge_expires_at <= now
                or pending["expires_at"] != invitation.challenge_expires_at.isoformat()
                or invitation.attempts >= MAX_ATTEMPTS
                or not invitation.challenge_digest
            ):
                return False
            if not constant_time_compare(
                invitation.challenge_digest, _digest(pending["challenge_id"], code)
            ):
                invitation.attempts += 1
                invitation.save(update_fields=["attempts"])
                return False

            user = create_user({**claims, "assistant_rh_admitted": True})
            user.groups.add(invitation.group_id)
            invitation.accepted_at = now
            invitation.challenge_digest = ""
            invitation.save(update_fields=["accepted_at", "challenge_digest"])
    except IntegrityError:
        if has_account(claims["email"], claims["sub"]):
            return False
        raise
    return True
