"""Per-attempt delegation. Private keys and assertions never leave the backend."""

import hashlib
import hmac
import json
import logging
import secrets
import time
import uuid
from importlib import import_module
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured

import httpx
import jwt
from asgiref.sync import sync_to_async
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from openai import AsyncOpenAI
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from rest_framework.exceptions import PermissionDenied

from core.assistant_rh import AUDIT_SESSION_KEY, MODEL_PREFIX, configured_models, current_models

logger = logging.getLogger(__name__)

_loaded_keys = {}


def _file_version(path):
    stat = path.stat()
    return str(path), stat.st_ino, stat.st_size, stat.st_mtime_ns


def _delegation_keys():
    """Load and validate both keys once per file version; a rotated file is reloaded."""
    signing_path = Path(settings.ASSISTANT_RH_SIGNING_KEY_FILE)
    audit_path = Path(settings.ASSISTANT_RH_AUDIT_KEY_FILE)
    version = (_file_version(signing_path), _file_version(audit_path))
    loaded = _loaded_keys.get("current")
    if loaded is None or loaded[0] != version:
        signing_pem = signing_path.read_bytes()
        key = load_pem_private_key(signing_pem, password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise ImproperlyConfigured("Assistant RH requires an Ed25519 signing key.")
        audit_key = audit_path.read_bytes()
        if len(audit_key) < 32 or audit_key in (signing_pem, key.private_bytes_raw()):
            raise ImproperlyConfigured("Assistant RH requires a separate audit key (>=32 bytes).")
        loaded = _loaded_keys["current"] = (version, key, audit_key)
    return loaded[1], loaded[2]


def delegation_refused(exc):
    """The OpenAI SDK wraps auth-flow errors as connection errors; find our refusal."""
    seen = set()
    while exc is not None and id(exc) not in seen:
        if isinstance(exc, PermissionDenied):
            return True
        seen.add(id(exc))
        exc = exc.__cause__ or exc.__context__
    return False


def sign_assertion(user_id, session_key, ministry=None):
    """Re-read the session and grants immediately before signing each HTTP attempt."""
    if not settings.ASSISTANT_RH_ENABLED or not session_key:
        raise PermissionDenied("Session invalide.")
    store = import_module(settings.SESSION_ENGINE).SessionStore(session_key=session_key)
    session = store.load()
    user = (
        get_user_model()
        .objects.filter(pk=user_id, is_active=True, assistant_rh_admitted=True)
        .first()
    )
    if (
        user is None
        or session.get("_auth_user_id") != str(user.pk)
        or not hmac.compare_digest(session.get("_auth_user_hash", ""), user.get_session_auth_hash())
        or not session.get(AUDIT_SESSION_KEY)
    ):
        raise PermissionDenied("Session invalide.")
    models = current_models(user)
    if ministry is not None and MODEL_PREFIX + ministry not in configured_models(user, models):
        raise PermissionDenied("Ministère non autorisé.")
    key, audit_key = _delegation_keys()
    if not settings.ASSISTANT_RH_DELEGATION_ISSUER or not settings.ASSISTANT_RH_SIGNING_KID:
        raise ImproperlyConfigured("Assistant RH delegation issuer and kid are required.")
    now = int(time.time())
    claims = {
        "iss": settings.ASSISTANT_RH_DELEGATION_ISSUER,
        "aud": settings.ASSISTANT_RH_API_AUDIENCE,
        "sub": str(user.pk),
        "models": models,
        "iat": now,
        "exp": now + 120,
        "jti": secrets.token_urlsafe(24),
        "audit_session": hmac.new(
            audit_key, session[AUDIT_SESSION_KEY].encode(), hashlib.sha256
        ).hexdigest(),
    }
    if ministry is not None:
        claims["ministry"] = ministry
    return jwt.encode(
        claims, key, algorithm="EdDSA", headers={"kid": settings.ASSISTANT_RH_SIGNING_KID}
    )


class DelegationAuth(httpx.Auth):
    """httpx invokes auth again on each send; OpenAI automatic retries are disabled."""

    requires_request_body = True

    def __init__(self, user_id, session_key, ministry, capture=None):
        self.user_id, self.session_key, self.ministry = user_id, session_key, ministry
        self.capture = capture

    async def async_auth_flow(self, request):
        # A modified client/config cannot use this signer to change corpus or host.
        base = httpx.URL(settings.ASSISTANT_RH_API_URL)
        if (request.url.scheme, request.url.host, request.url.port) != (
            base.scheme,
            base.host,
            base.port,
        ):
            raise PermissionDenied("Destination API non autorisée.")
        expected_path = base.path.rstrip("/") + "/chat/completions"
        if request.method != "POST" or request.url.path != expected_path:
            raise PermissionDenied("Route API non autorisée.")
        if json.loads(request.content).get("model") != MODEL_PREFIX + self.ministry:
            raise PermissionDenied("Modèle différent du ministère de la conversation.")
        assertion = await sync_to_async(sign_assertion)(
            self.user_id, self.session_key, self.ministry
        )
        # The metadata tee observes wire bytes before HTTPX decoding.
        request.headers["Accept-Encoding"] = "identity"
        request.headers["Authorization"] = "Bearer " + assertion
        request.headers["X-Client-Request-Id"] = uuid.uuid4().hex
        logger.info("assistant_rh_request request_id=%s", request.headers["X-Client-Request-Id"])
        response = yield request
        if self.capture is not None and response.status_code == 200:
            self.capture.attach(response)


def delegated_model(configuration, context):
    """No static bearer, client cache, provider fallback or browser-supplied identity."""
    if context is None:
        raise PermissionDenied("Session de délégation requise.")
    user_id, session_key, ministry = context[:3]
    capture = context[3] if len(context) > 3 else None
    if configuration.model_name != MODEL_PREFIX + ministry:
        raise PermissionDenied("Modèle différent du ministère de la conversation.")
    if not settings.ASSISTANT_RH_API_URL:
        raise ImproperlyConfigured("Assistant RH API URL is required.")
    # Disable HTTP redirects and SDK retries: a retry must recheck session and rights.
    client = AsyncOpenAI(
        api_key="delegated-at-send-time",
        base_url=settings.ASSISTANT_RH_API_URL,
        max_retries=0,
        http_client=httpx.AsyncClient(
            auth=DelegationAuth(user_id, session_key, ministry, capture),
            follow_redirects=False,
            timeout=httpx.Timeout(600, connect=5),
        ),
    )
    return OpenAIChatModel(
        model_name=configuration.model_name, provider=OpenAIProvider(openai_client=client)
    )
