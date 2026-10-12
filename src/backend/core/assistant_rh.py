"""Current author and corpus policy. Never infer a grant from an email or SIRET."""

import hmac
import secrets
from importlib import import_module

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver

from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated

MINISTRIES = ("masa", "matte", "mi", "mso")
MODEL_PREFIX = "assistant-rh-"


@receiver(user_logged_in, dispatch_uid="assistant_rh_session_audit")
def initialize_session_audit(request, **_kwargs):
    """Renew the audit nonce on every successful Django login, local or OIDC."""
    if settings.ASSISTANT_RH_ENABLED:
        request.session["assistant_rh_audit_id"] = secrets.token_urlsafe(32)


def current_models(user):
    """Reload admission, membership and configured logical models without a cache."""
    if not user.is_authenticated:
        return []
    current = (
        get_user_model()
        .objects.filter(pk=user.pk, is_active=True, assistant_rh_admitted=True)
        .first()
    )
    if current is None:
        return []
    granted = current.groups.values_list("assistant_rh_grants__ministry", flat=True)
    return sorted({MODEL_PREFIX + ministry for ministry in granted if ministry in MINISTRIES})


def configured_models(user):
    """Only expose delegated API models which also exist in this deployment."""
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


def require_admission(user):
    """Authentication alone, including a valid API token, grants no admission."""
    if (
        not user.is_authenticated
        or not get_user_model()
        .objects.filter(pk=user.pk, is_active=True, assistant_rh_admitted=True)
        .exists()
    ):
        raise PermissionDenied("Accès en attente de validation DGAFP.")


def visible_conversations(user):
    """Rights removal hides history, without deleting it or sharing it with a group."""
    # Avoid the core/chat model import cycle during Django initialization.
    # pylint: disable-next=import-outside-toplevel
    from chat.models import ChatConversation  # noqa: PLC0415

    qs = ChatConversation.objects.filter(owner=user)
    if settings.ASSISTANT_RH_ENABLED:
        ministries = [model.removeprefix(MODEL_PREFIX) for model in current_models(user)]
        qs = qs.filter(ministry__in=ministries)
    return qs


class AssistantRHSessionPermission(IsAuthenticated):
    """Keep upstream behavior unless RH is enabled; then require an admitted session."""

    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        if settings.ASSISTANT_RH_ENABLED:
            require_admission(request.user)
            if not getattr(request, "session", None) or not request.session.session_key:
                return False
            store = import_module(settings.SESSION_ENGINE).SessionStore(
                session_key=request.session.session_key
            )
            session = store.load()
            if session.get("_auth_user_id") != str(request.user.pk):
                return False
            if not hmac.compare_digest(
                session.get("_auth_user_hash", ""), request.user.get_session_auth_hash()
            ):
                return False
        return True
