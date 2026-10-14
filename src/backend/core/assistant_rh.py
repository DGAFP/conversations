"""Current author and corpus policy. Never infer a grant from an email or SIRET."""

import hmac
import secrets

from django.conf import settings
from django.contrib.auth import HASH_SESSION_KEY, SESSION_KEY, get_user_model
from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver

from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated

MINISTRIES = ("masa", "matte", "mi", "mso")
MODEL_PREFIX = "assistant-rh-"
AUDIT_SESSION_KEY = "assistant_rh_audit_id"


@receiver(user_logged_in, dispatch_uid="assistant_rh_session_audit")
def initialize_session_audit(request, **_kwargs):
    """Renew the audit nonce on every successful Django login, local or OIDC."""
    if settings.ASSISTANT_RH_ENABLED:
        request.session[AUDIT_SESSION_KEY] = secrets.token_urlsafe(32)


def _admission(user):
    """One query: (admitted, granted logical models), without any cache."""
    if not user.is_authenticated:
        return False, []
    rows = list(
        get_user_model()
        .objects.filter(pk=user.pk, is_active=True, assistant_rh_admitted=True)
        .values_list("groups__assistant_rh_grants__ministry", flat=True)
    )
    # The outer join yields a single NULL row for an admitted user without grants.
    granted = sorted({MODEL_PREFIX + ministry for ministry in rows if ministry in MINISTRIES})
    return bool(rows), granted


def _request_admission(request):
    """Admission and grants read once per HTTP request (middleware, permission, view)."""
    http_request = getattr(request, "_request", request)
    resolved = http_request.__dict__.setdefault("_assistant_rh_admission", {})
    user = request.user
    if user.pk not in resolved:
        resolved[user.pk] = _admission(user)
    return resolved[user.pk]


def current_models(user):
    """Reload admission, membership and configured logical models without a cache."""
    return _admission(user)[1]


def request_models(request):
    """Granted models of the request's user, resolved once per request; None outside RH."""
    if not settings.ASSISTANT_RH_ENABLED:
        return None
    return _request_admission(request)[1]


def configured_models(user, granted=None):
    """Only expose delegated API models which also exist in this deployment."""
    if granted is None:
        granted = current_models(user)
    return {
        hrid: config
        for hrid, config in settings.LLM_CONFIGURATIONS.items()
        if config.is_active
        and config.provider
        and config.provider.kind == "assistant-rh"
        and config.model_name in granted
        and hrid == config.model_name
    }


def require_admission(request):
    """Authentication alone, including a valid API token, grants no admission."""
    if not request.user.is_authenticated or not _request_admission(request)[0]:
        raise PermissionDenied("Accès en attente de validation DGAFP.")


def visible_conversations(user, granted=None):
    """Rights removal hides history, without deleting it or sharing it with a group."""
    # Avoid the core/chat model import cycle during Django initialization.
    # pylint: disable-next=import-outside-toplevel
    from chat.models import ChatConversation  # noqa: PLC0415

    qs = ChatConversation.objects.filter(owner=user)
    if settings.ASSISTANT_RH_ENABLED:
        if granted is None:
            granted = current_models(user)
        qs = qs.filter(ministry__in=[model.removeprefix(MODEL_PREFIX) for model in granted])
    return qs


class AssistantRHSessionPermission(IsAuthenticated):
    """Keep upstream behavior unless RH is enabled; then require an admitted session."""

    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        if not settings.ASSISTANT_RH_ENABLED:
            return True
        require_admission(request)
        # The session was already loaded for this request; a bearer-authenticated user
        # must also hold the matching browser session, which the signer requires too.
        session = getattr(request, "session", None)
        return bool(
            session is not None
            and session.session_key
            and session.get(SESSION_KEY) == str(request.user.pk)
            and hmac.compare_digest(
                session.get(HASH_SESSION_KEY, ""), request.user.get_session_auth_hash()
            )
            and session.get(AUDIT_SESSION_KEY)
        )
