"""Individual identity and current corpus boundaries of the Assistant RH fork."""

import json
import re
import uuid
from importlib import import_module
from types import SimpleNamespace

from django.contrib import admin
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.test import Client, RequestFactory

import httpx
import jwt
import pytest
from asgiref.sync import async_to_sync
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat
from pydantic_ai import Agent
from pydantic_ai.exceptions import ModelHTTPError
from rest_framework.exceptions import PermissionDenied

from core.api.serializers import UserSerializer
from core.assistant_rh import configured_models, current_models, visible_conversations
from core.authentication.backends import OIDCAuthenticationBackend, OIDCRoleAccessDenied
from core.factories import UserFactory
from core.models import MinistryGrant, User

from chat.assistant_rh_delegation import DelegationAuth, delegated_model, sign_assertion
from chat.factories import ChatConversationAttachmentFactory
from chat.llm_configuration import LLModel, LLMProvider
from chat.models import ChatConversation, ChatProject

pytestmark = pytest.mark.django_db(transaction=True)
BASE = "/api/v1.0/"


@pytest.fixture(name="rh")
def assistant_rh_setup(settings, tmp_path):
    """Create an admitted user, corpus grants and isolated signing keys."""
    settings.ASSISTANT_RH_ENABLED = True
    settings.ASSISTANT_RH_API_URL = "https://rh.test/v1"
    settings.ASSISTANT_RH_DELEGATION_ISSUER = "conversations-test"
    settings.ASSISTANT_RH_API_AUDIENCE = "rh-test"
    settings.ASSISTANT_RH_SIGNING_KID = "test-20261012"
    key = Ed25519PrivateKey.generate()
    signing_file = tmp_path / "signing.pem"
    signing_file.write_bytes(key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
    audit_file = tmp_path / "audit.key"
    audit_file.write_bytes(b"a" * 32)
    settings.ASSISTANT_RH_SIGNING_KEY_FILE = str(signing_file)
    settings.ASSISTANT_RH_AUDIT_KEY_FILE = str(audit_file)
    provider = LLMProvider(
        hrid="rh", kind="assistant-rh", api_key="unused", base_url="https://rh.test/v1"
    )
    settings.LLM_CONFIGURATIONS = {
        name: LLModel(
            hrid=name,
            model_name=name,
            human_readable_name=name,
            provider=provider,
            is_active=True,
            tools=[],
            system_prompt="",
            supports_streaming=False,
        )
        for name in ("assistant-rh-matte", "assistant-rh-mso")
    }
    settings.LLM_DEFAULT_MODEL_HRID = "assistant-rh-matte"
    group = Group.objects.create(name="matte")
    MinistryGrant.objects.create(group=group, ministry="matte")
    user = UserFactory(assistant_rh_admitted=True)
    user.groups.add(group)
    return SimpleNamespace(user=user, group=group, key=key, settings=settings)


def login(client, user):
    """A server-created session, including the normal successful-login signal."""
    client.force_login(user)
    return client.session.session_key


def decode(rh, token):
    """Verify the assertion with the test public key, issuer and audience."""
    return jwt.decode(
        token,
        rh.key.public_key(),
        algorithms=["EdDSA"],
        audience="rh-test",
        issuer="conversations-test",
    )


def create_chat(client, hrid="assistant-rh-matte", **kwargs):
    """Create a conversation through the same API used by the browser."""
    return client.post(
        BASE + "chats/", {"title": "Question RH", "model_hrid": hrid, **kwargs}, format="json"
    )


def test_admission_is_independent_of_authentication_and_email(rh, api_client):
    """Admission is independent of authentication and email."""
    rh.user.assistant_rh_admitted = False
    rh.user.save()
    login(api_client, rh.user)
    assert api_client.get(BASE + "llm-configuration/").status_code == 403
    assert create_chat(api_client).status_code == 403
    assert current_models(rh.user) == []


def test_no_automatic_corpus_and_no_default(rh, api_client):
    """No automatic corpus and no default."""
    rh.user.groups.clear()
    login(api_client, rh.user)
    assert api_client.get(BASE + "llm-configuration/").json()["models"] == []
    assert create_chat(api_client).status_code == 400
    assert create_chat(api_client, hrid="").status_code == 400
    assert current_models(rh.user) == []


def test_multi_group_union_catalogue_and_no_cache_between_users(rh, api_client):
    """Multi group union catalogue and no cache between users."""
    extra = Group.objects.create(name="mso")
    MinistryGrant.objects.create(group=extra, ministry="mso")
    rh.user.groups.add(extra)
    login(api_client, rh.user)
    response = api_client.get(BASE + "llm-configuration/")
    assert {m["hrid"] for m in response.json()["models"]} == {
        "assistant-rh-matte",
        "assistant-rh-mso",
    }
    assert response["Cache-Control"] == "no-store"
    other = UserFactory(assistant_rh_admitted=True)
    login(api_client, other)
    assert api_client.get(BASE + "llm-configuration/").json()["models"] == []
    assert current_models(rh.user) == ["assistant-rh-matte", "assistant-rh-mso"]


def test_ministry_fixed_at_creation_and_client_cannot_change_it(rh, api_client):
    """Ministry fixed at creation and client cannot change it."""
    login(api_client, rh.user)
    response = create_chat(api_client, ministry="mso")
    assert response.status_code == 201
    chat = ChatConversation.objects.get(pk=response.json()["id"])
    assert chat.owner == rh.user and chat.ministry == "matte"
    assert chat.model_hrid == "assistant-rh-matte"
    for body in ({"model_hrid": "assistant-rh-mso"}, {"ministry": "mso"}):
        assert api_client.patch(BASE + f"chats/{chat.pk}/", body, format="json").status_code == 400
    assert create_chat(api_client, hrid="assistant-rh-mso").status_code == 400
    assert (
        api_client.post(
            BASE + f"chats/{chat.pk}/conversation/?model_hrid=assistant-rh-mso",
            {"messages": []},
            format="json",
        ).status_code
        == 403
    )


def test_history_private_hidden_and_restored_in_lists_projects_and_retrieve(rh, api_client):
    """History private hidden and restored in lists projects and retrieve."""
    login(api_client, rh.user)
    project = ChatProject.objects.create(owner=rh.user, title="RH", icon="folder", color="color_1")
    chat = ChatConversation.objects.create(
        owner=rh.user, ministry="matte", model_hrid="assistant-rh-matte", project=project
    )
    other = UserFactory(assistant_rh_admitted=True)
    other.groups.add(rh.group)
    private = ChatConversation.objects.create(owner=other, ministry="matte")
    assert api_client.get(BASE + f"chats/{private.pk}/").status_code == 404
    assert (
        api_client.post(BASE + f"chats/{private.pk}/score-message/", {}, format="json").status_code
        == 404
    )
    assert list(visible_conversations(rh.user)) == [chat]
    rh.user.groups.clear()
    assert api_client.get(BASE + f"chats/{chat.pk}/").status_code == 404
    assert api_client.get(BASE + "chats/").json()["results"] == []
    assert api_client.get(BASE + f"projects/{project.pk}/").json()["conversations"] == []
    assert ChatConversation.objects.filter(pk=chat.pk).exists()
    replacement = Group.objects.create(name="replacement")
    MinistryGrant.objects.create(group=replacement, ministry="matte")
    rh.user.groups.add(replacement)
    assert api_client.get(BASE + f"chats/{chat.pk}/").status_code == 200


def test_assertions_match_api_profile_and_session_audit_is_stable(rh, api_client):
    """Assertions match api profile and session audit is stable."""
    session_key = login(api_client, rh.user)
    first = sign_assertion(rh.user.pk, session_key, "matte")
    second = sign_assertion(rh.user.pk, session_key, "matte")
    a, b = decode(rh, first), decode(rh, second)
    assert a["sub"] == str(rh.user.pk)
    assert a["models"] == ["assistant-rh-matte"] and a["ministry"] == "matte"
    assert a["jti"] != b["jti"] and re.fullmatch(r"[A-Za-z0-9_-]{16,128}", a["jti"])
    # Integers only: bool is a subclass but is not a valid NumericDate.
    # pylint: disable-next=unidiomatic-typecheck
    assert type(a["iat"]) is int and a["exp"] - a["iat"] == 120
    assert re.fullmatch(r"[0-9a-f]{64}", a["audit_session"])
    assert a["audit_session"] == b["audit_session"]
    assert a["audit_session"] != session_key
    assert jwt.get_unverified_header(first) == {
        "alg": "EdDSA",
        "kid": "test-20261012",
        "typ": "JWT",
    }
    api_client.logout()
    new_session = login(api_client, rh.user)
    assert (
        decode(rh, sign_assertion(rh.user.pk, new_session, "matte"))["audit_session"]
        != a["audit_session"]
    )


@pytest.mark.parametrize(
    "revocation", ["logout", "disabled", "admission", "grant", "password", "expired"]
)
def test_signer_rechecks_each_attempt_and_rejects_revoked_sessions(rh, api_client, revocation):
    """Signer rechecks each attempt and rejects revoked sessions."""
    session_key = login(api_client, rh.user)
    sign_assertion(rh.user.pk, session_key, "matte")
    if revocation == "logout":
        api_client.logout()
    elif revocation == "disabled":
        User.objects.filter(pk=rh.user.pk).update(is_active=False)
    elif revocation == "admission":
        User.objects.filter(pk=rh.user.pk).update(assistant_rh_admitted=False)
    elif revocation == "grant":
        rh.user.groups.clear()
    elif revocation == "password":
        rh.user.set_password("changed-password")
        rh.user.save()
    else:
        store = import_module(rh.settings.SESSION_ENGINE).SessionStore(session_key=session_key)
        store.load()
        store.set_expiry(-1)
        store.save()
    with pytest.raises(PermissionDenied):
        sign_assertion(rh.user.pk, session_key, "matte")


def test_empty_grants_can_delegate_empty_catalogue(rh, api_client):
    """Empty grants can delegate empty catalogue."""
    session_key = login(api_client, rh.user)
    rh.user.groups.clear()
    assert decode(rh, sign_assertion(rh.user.pk, session_key))["models"] == []


def test_subject_identity_stable_and_never_merged_by_email(rh):
    """Subject identity stable and never merged by email."""
    backend = OIDCAuthenticationBackend()
    rh.user.sub = "proconnect-original"
    rh.user.save()
    old_pk, email = rh.user.pk, rh.user.email
    assert backend.get_existing_user("proconnect-original", email).pk == old_pk
    assert backend.get_existing_user("other-sub", email) is None
    rh.user.delete()
    replacement = UserFactory(sub="proconnect-original", email=email)
    assert replacement.pk != old_pk
    assert replacement.assistant_rh_admitted is False
    assert current_models(replacement) == []


def test_oidc_requires_central_approval(rh):
    """OIDC admission remains mandatory before Django creates the session."""
    backend = OIDCAuthenticationBackend()
    backend.request = SimpleNamespace(session={})
    backend.post_get_or_create_user(rh.user, {}, False)
    rh.user.assistant_rh_admitted = False
    with pytest.raises(OIDCRoleAccessDenied):
        backend.post_get_or_create_user(rh.user, {}, False)


@pytest.fixture(name="local_account")
def local_account_setup(rh):
    """Enable local login for a non-staff user with an actual hashed password."""
    rh.settings.ASSISTANT_RH_LOCAL_LOGIN_ENABLED = True
    rh.settings.LOGIN_REDIRECT_URL = "/"
    rh.settings.LOGOUT_REDIRECT_URL = "/"
    rh.user.admin_email = "local@example.invalid"
    rh.user.set_password("Synthetic-passphrase-596!")
    rh.user.save()
    cache.clear()
    yield {"username": rh.user.admin_email, "password": "Synthetic-passphrase-596!"}
    cache.clear()


def test_password_login_csrf_delegation_and_logout(rh, local_account):
    """The real form creates a signable session without staff or OIDC privileges."""
    client = Client(enforce_csrf_checks=True)
    assert not rh.user.is_staff and not rh.user.is_superuser
    response = client.get(BASE + "authenticate/")
    assert response.status_code == 200
    assert "Connexion à Assistant RH" in response.content.decode()
    assert client.post(BASE + "authenticate/", local_account).status_code == 403
    response = client.post(
        BASE + "authenticate/?next=https://evil.invalid/",
        local_account,
        HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value,
    )
    assert response.status_code == 302 and response["Location"] == "/"
    session_key = client.session.session_key
    claims = decode(rh, sign_assertion(rh.user.pk, session_key, "matte"))
    assert claims["sub"] == str(rh.user.pk)
    assert claims["models"] == ["assistant-rh-matte"]
    assert client.get(BASE + "llm-configuration/").status_code == 200
    assert client.get("/admin/").status_code == 302
    assert client.get(BASE + "logout/").status_code == 200
    assert client.post(BASE + "logout/").status_code == 403
    assert sign_assertion(rh.user.pk, session_key, "matte")
    assert (
        client.post(
            BASE + "logout/", HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value
        ).status_code
        == 302
    )
    with pytest.raises(PermissionDenied):
        sign_assertion(rh.user.pk, session_key, "matte")
    client.get(BASE + "authenticate/")
    assert (
        client.post(
            BASE + "authenticate/",
            local_account,
            HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value,
        ).status_code
        == 302
    )
    renewed = decode(rh, sign_assertion(rh.user.pk, client.session.session_key, "matte"))
    assert renewed["sub"] == claims["sub"]
    assert renewed["audit_session"] != claims["audit_session"]
    rh.user.set_password("Replacement-passphrase-596!")
    rh.user.save()
    with pytest.raises(PermissionDenied):
        sign_assertion(rh.user.pk, client.session.session_key, "matte")


@pytest.mark.parametrize("denied", ["wrong_password", "pending", "inactive"])
def test_local_login_denies_invalid_or_unadmitted_accounts(rh, local_account, client, denied):
    """No session or assertion can be created by a denied account."""
    if denied == "wrong_password":
        local_account["password"] = "wrong"
    elif denied == "pending":
        rh.user.assistant_rh_admitted = False
    else:
        rh.user.is_active = False
    rh.user.save()
    assert client.post(BASE + "authenticate/", local_account).status_code == 200
    assert "_auth_user_id" not in client.session
    assert "assistant_rh_audit_id" not in client.session


def test_local_login_throttled_and_configured_explicitly(rh, local_account, client):
    """Bound failed attempts and keep upstream login when either flag is off."""
    config = client.get(BASE + "config/").json()
    assert config["ASSISTANT_RH_LOCAL_LOGIN_ENABLED"] is True
    assert config["FRONTEND_SILENT_LOGIN_ENABLED"] is False
    local_account["password"] = "wrong"
    for _ in range(10):
        assert client.post(BASE + "authenticate/", local_account).status_code == 200
    response = client.post(BASE + "authenticate/", local_account)
    assert response.status_code == 429 and int(response["Retry-After"]) > 0
    rh.settings.ASSISTANT_RH_LOCAL_LOGIN_ENABLED = False
    assert client.get(BASE + "authenticate/")["Location"].startswith("http://oidc.test/")
    rh.settings.ASSISTANT_RH_LOCAL_LOGIN_ENABLED = True
    rh.settings.ASSISTANT_RH_ENABLED = False
    assert client.get(BASE + "authenticate/")["Location"].startswith("http://oidc.test/")
    assert client.get(BASE + "config/").json()["ASSISTANT_RH_LOCAL_LOGIN_ENABLED"] is False


def test_oidc_is_public_login_with_allowlisted_technical_fallback(rh, local_account):
    """ProConnect is public; the existing probe retains only its explicit password path."""
    rh.settings.ASSISTANT_RH_OIDC_ENABLED = True
    rh.settings.ASSISTANT_RH_LOCAL_LOGIN_ALLOWED_EMAILS = [local_account["username"]]
    client = Client(enforce_csrf_checks=True)
    assert client.get(BASE + "authenticate/")["Location"].startswith("http://oidc.test/")
    config = client.get(BASE + "config/").json()
    assert config["ASSISTANT_RH_LOCAL_LOGIN_ENABLED"] is False
    assert "ASSISTANT_RH_LOCAL_LOGIN_ALLOWED_EMAILS" not in config
    assert client.post(BASE + "local/authenticate/", local_account).status_code == 403
    assert client.get(BASE + "local/authenticate/").status_code == 200
    response = client.post(
        BASE + "local/authenticate/",
        local_account,
        HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value,
    )
    assert response.status_code == 302
    assert sign_assertion(rh.user.pk, client.session.session_key, "matte")
    assert client.get(BASE + "logout/").status_code == 200
    assert client.post(BASE + "logout/").status_code == 403
    response = client.post(BASE + "logout/", HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value)
    assert response.status_code == 302
    assert "_auth_user_id" not in client.session


@pytest.mark.parametrize("staff", [False, True])
def test_oidc_technical_login_does_not_admit_other_local_accounts(rh, local_account, client, staff):
    """Admission and a valid password do not bypass the technical account allowlist."""
    rh.settings.ASSISTANT_RH_OIDC_ENABLED = True
    rh.settings.ASSISTANT_RH_LOCAL_LOGIN_ALLOWED_EMAILS = ["probe@example.invalid"]
    rh.user.is_staff = staff
    rh.user.is_superuser = staff
    rh.user.save()
    assert client.post(BASE + "local/authenticate/", local_account).status_code == 200
    assert "_auth_user_id" not in client.session


@pytest.mark.usefixtures("local_account")
def test_explicit_local_login_is_disabled_without_the_rh_flag(rh, client):
    """The additional route cannot enable a password flow in the upstream mode."""
    rh.settings.ASSISTANT_RH_ENABLED = False
    assert client.get(BASE + "local/authenticate/").status_code == 404
    rh.settings.ASSISTANT_RH_ENABLED = True
    rh.settings.ASSISTANT_RH_LOCAL_LOGIN_ENABLED = False
    assert client.get(BASE + "local/authenticate/").status_code == 404


def test_oidc_session_logout_visits_provider_while_technical_login_remains_enabled(rh, client):
    """Logout follows the actual OIDC session even when the probe can still log in locally."""
    rh.settings.ASSISTANT_RH_OIDC_ENABLED = True
    rh.settings.ASSISTANT_RH_LOCAL_LOGIN_ENABLED = True
    rh.settings.OIDC_OP_LOGOUT_ENDPOINT = "https://oidc.test/session/end"
    client.force_login(rh.user, backend="core.authentication.backends.OIDCAuthenticationBackend")
    session = client.session
    session["oidc_id_token"] = "synthetic-id-token"
    session.save()
    response = client.get(BASE + "logout/")
    assert response.status_code == 302
    assert response["Location"].startswith("https://oidc.test/session/end?")
    assert "post_logout_redirect_uri=" in response["Location"]


@pytest.mark.usefixtures("rh")
def test_admin_can_provision_local_account_without_staff_rights():
    """The standard admin creation form must accept the actual login field."""
    request = RequestFactory().get("/admin/core/user/add/")
    request.user = UserFactory(is_staff=True, is_superuser=True)
    form_class = admin.site._registry[User].get_form(request)  # pylint: disable=protected-access
    form = form_class(
        data={
            "admin_email": "new@example.invalid",
            "password1": "Synthetic-passphrase-596!",
            "password2": "Synthetic-passphrase-596!",
        }
    )
    assert form.is_valid(), form.errors
    user = form.save()
    assert user.check_password("Synthetic-passphrase-596!")
    assert not user.is_staff and not user.is_superuser and not user.assistant_rh_admitted


@pytest.mark.usefixtures("rh")
def test_non_central_admin_cannot_edit_grants_or_admission():
    """Non central admin cannot edit grants or admission."""
    staff = UserFactory(is_staff=True)
    request = SimpleNamespace(user=staff)
    for model in (User, Group, MinistryGrant):
        handler = admin.site._registry[model]  # pylint: disable=protected-access
        assert not handler.has_add_permission(request)
        assert not handler.has_change_permission(request)
        assert not handler.has_delete_permission(request)


def test_disabled_config_and_foreign_provider_absent_from_catalogue(rh):
    """Disabled config and foreign provider absent from catalogue."""
    config = rh.settings.LLM_CONFIGURATIONS["assistant-rh-matte"]
    config.is_active = False
    assert configured_models(rh.user) == {}
    config.is_active = True
    config.provider.kind = "openai"
    assert configured_models(rh.user) == {}


def test_no_static_fallback_or_unsigned_background_calls(rh):
    """No static fallback or unsigned background calls."""
    config = rh.settings.LLM_CONFIGURATIONS["assistant-rh-matte"]
    with pytest.raises(PermissionDenied):
        delegated_model(config, None)
    with pytest.raises(PermissionDenied):
        delegated_model(config, (rh.user.pk, "session", "mso"))


def test_http_attempts_resign_and_use_safe_correlation_id(rh, api_client):
    """Http attempts resign and use safe correlation id."""
    session_key = login(api_client, rh.user)
    seen = []

    async def send():
        def handler(request):
            seen.append(request)
            return httpx.Response(200, json={})

        async with httpx.AsyncClient(
            auth=DelegationAuth(rh.user.pk, session_key, "matte"),
            transport=httpx.MockTransport(handler),
        ) as client:
            for _ in range(2):
                await client.post(
                    "https://rh.test/v1/chat/completions", json={"model": "assistant-rh-matte"}
                )
            for url, model in (
                ("https://other.test/v1/chat/completions", "assistant-rh-matte"),
                ("https://rh.test/v1/chat/completions", "assistant-rh-mso"),
            ):
                with pytest.raises(PermissionDenied):
                    await client.post(url, json={"model": model})

    async_to_sync(send)()
    assert len(seen) == 2
    claims = [
        decode(rh, request.headers["authorization"].removeprefix("Bearer ")) for request in seen
    ]
    assert claims[0]["jti"] != claims[1]["jti"]
    for request in seen:
        assert re.fullmatch(r"[0-9a-f]{32}", request.headers["X-Client-Request-Id"])


def test_legacy_attachments_disabled_and_no_presigned_url(rh, api_client):
    """Legacy attachments disabled and no presigned url."""
    login(api_client, rh.user)
    chat = ChatConversation.objects.create(owner=rh.user, ministry="matte")
    response = api_client.post(
        BASE + f"chats/{chat.pk}/attachments/", {"file_name": "file.pdf"}, format="json"
    )
    assert response.status_code == 403
    assert api_client.get(BASE + "file-stream/some-old-token/").status_code == 404


@pytest.mark.parametrize("stream", [False, True])
def test_openai_gateway_json_and_sse_use_individual_delegation(rh, api_client, respx_mock, stream):
    """Exercise the actual SDK transport, not only the standalone JWT encoder."""

    session_key = login(api_client, rh.user)
    completion_id = "chatcmpl-" + uuid.uuid4().hex
    seen = []

    def response(request):
        seen.append(request)
        assert json.loads(request.content)["model"] == "assistant-rh-matte"
        common = {"id": completion_id, "created": 1, "model": "assistant-rh-matte"}
        if stream:
            chunks = [
                {
                    **common,
                    "object": "chat.completion.chunk",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"role": "assistant", "content": "Réponse RH"},
                            "finish_reason": None,
                        }
                    ],
                },
                {
                    **common,
                    "object": "chat.completion.chunk",
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                },
            ]
            return httpx.Response(
                200,
                headers={"Content-Type": "text/event-stream"},
                text="".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks)
                + "data: [DONE]\n\n",
            )
        return httpx.Response(
            200,
            json={
                **common,
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Réponse RH"},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    respx_mock.post("https://rh.test/v1/chat/completions").mock(side_effect=response)

    async def run():
        model = delegated_model(
            rh.settings.LLM_CONFIGURATIONS["assistant-rh-matte"], (rh.user.pk, session_key, "matte")
        )
        agent = Agent(model=model)
        try:
            if stream:
                async with agent.run_stream("Question RH") as result:
                    return "".join([text async for text in result.stream_text(delta=True)])
            return (await agent.run("Question RH")).output
        finally:
            await model.client.close()

    assert async_to_sync(run)() == "Réponse RH"
    assert len(seen) == 1
    assert decode(rh, seen[0].headers["Authorization"].removeprefix("Bearer "))["sub"] == str(
        rh.user.pk
    )


def test_sdk_never_automatically_replays_bearer_on_provider_failure(rh, api_client, respx_mock):
    """Sdk never automatically replays bearer on provider failure."""
    session_key = login(api_client, rh.user)
    route = respx_mock.post("https://rh.test/v1/chat/completions").respond(
        503, json={"error": {"message": "Unavailable"}}
    )

    async def run():
        model = delegated_model(
            rh.settings.LLM_CONFIGURATIONS["assistant-rh-matte"], (rh.user.pk, session_key, "matte")
        )
        try:
            with pytest.raises(ModelHTTPError):
                await Agent(model=model).run("Question RH")
        finally:
            await model.client.close()

    async_to_sync(run)()
    assert route.call_count == 1


def test_chat_handler_persists_individual_history_and_stops_without_default_model(
    rh, api_client, respx_mock
):
    """Chat handler persists individual history and stops without default model."""
    login(api_client, rh.user)
    chat = ChatConversation.objects.get(pk=create_chat(api_client).json()["id"])
    route = respx_mock.post("https://rh.test/v1/chat/completions").respond(
        200,
        json={
            "id": "chatcmpl-" + uuid.uuid4().hex,
            "created": 1,
            "model": "assistant-rh-matte",
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "Réponse RH"},
                    "finish_reason": "stop",
                }
            ],
        },
    )
    response = api_client.post(
        BASE + f"chats/{chat.pk}/conversation/",
        {
            "messages": [
                {
                    "id": "question",
                    "role": "user",
                    "parts": [{"type": "text", "text": "Question RH"}],
                }
            ]
        },
        format="json",
    )
    assert response.status_code == 200
    body = b"".join(response.streaming_content).decode()
    assert "[DONE]" in body and route.call_count == 1
    deltas = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: {")]
    assert "".join(event.get("delta", "") for event in deltas) == "Réponse RH"
    chat.refresh_from_db()
    assert chat.owner == rh.user and chat.ministry == "matte"
    assert len(chat.messages) == 2
    assert api_client.post(BASE + f"chats/{chat.pk}/stop-streaming/").status_code == 200
    assert route.call_count == 1


@pytest.mark.parametrize("field", ["is_active", "assistant_rh_admitted"])
def test_reactivation_does_not_restore_revoked_sessions(rh, api_client, field):
    """Reactivation does not restore revoked sessions."""
    session_key = login(api_client, rh.user)
    setattr(rh.user, field, False)
    rh.user.save(update_fields=[field])
    setattr(rh.user, field, True)
    rh.user.save(update_fields=[field])
    with pytest.raises(PermissionDenied):
        sign_assertion(rh.user.pk, session_key, "matte")
    fresh = login(api_client, rh.user)
    assert decode(rh, sign_assertion(rh.user.pk, fresh, "matte"))["sub"] == str(rh.user.pk)


def test_profile_update_cannot_undo_concurrent_administrative_revocation(rh, api_client):
    """Profile update cannot undo concurrent administrative revocation."""
    session_key = login(api_client, rh.user)
    stale_user = User.objects.get(pk=rh.user.pk)
    rh.user.is_active = False
    rh.user.save()
    serializer = UserSerializer(stale_user, data={"language": "fr-fr"}, partial=True)
    serializer.is_valid(raise_exception=True)
    serializer.save()
    rh.user.refresh_from_db()
    assert rh.user.is_active is False
    assert rh.user.language == "fr-fr"
    with pytest.raises(PermissionDenied):
        sign_assertion(rh.user.pk, session_key, "matte")


def test_grant_withdrawn_before_the_http_attempt_reports_permission_denied(
    rh, api_client, respx_mock
):
    """The SDK wraps the signer's refusal; users must still see a rights error."""
    login(api_client, rh.user)
    chat_id = create_chat(api_client).json()["id"]
    route = respx_mock.post("https://rh.test/v1/chat/completions").respond(500)
    response = api_client.post(
        BASE + f"chats/{chat_id}/conversation/",
        {"messages": [{"id": "q", "role": "user", "parts": [{"type": "text", "text": "RH"}]}]},
        format="json",
    )
    assert response.status_code == 200
    rh.user.groups.clear()  # After the view checks, before the signed attempt.
    body = b"".join(response.streaming_content).decode()
    assert "permission_denied" in body and "model_connection_error" not in body
    assert route.call_count == 0


def test_local_login_throttle_is_per_account_not_per_proxy_address(local_account, client):
    """Every browser shares the proxy address: one account's failures lock only it."""
    attempt = {**local_account, "password": "wrong"}
    for _ in range(10):
        assert client.post(BASE + "authenticate/", attempt).status_code == 200
    assert client.post(BASE + "authenticate/", attempt).status_code == 429
    other = UserFactory(assistant_rh_admitted=True, admin_email="other@example.invalid")
    other.set_password("Synthetic-passphrase-596!")
    other.save()
    response = client.post(
        BASE + "authenticate/",
        {"username": other.admin_email, "password": "Synthetic-passphrase-596!"},
    )
    assert response.status_code == 302


def test_session_without_audit_nonce_is_signed_out(rh, api_client):
    """Sessions predating RH mode cannot be delegated, so they must sign in again."""
    session_key = login(api_client, rh.user)
    engine = import_module(rh.settings.SESSION_ENGINE)
    store = engine.SessionStore(session_key=session_key)
    store.pop("assistant_rh_audit_id")
    store.save()
    assert api_client.get(BASE + "llm-configuration/").status_code in (401, 403)
    assert not engine.SessionStore().exists(session_key)
    assert api_client.get(BASE + "users/me/").status_code in (401, 403)


def test_project_deletion_keeps_history_hidden_by_a_withdrawn_grant(rh, api_client, monkeypatch):
    """Only visible conversations and their files go with the project."""
    login(api_client, rh.user)
    project = ChatProject.objects.create(owner=rh.user, title="RH", icon="folder", color="color_1")
    visible = ChatConversation.objects.create(
        owner=rh.user, ministry="matte", model_hrid="assistant-rh-matte", project=project
    )
    hidden = ChatConversation.objects.create(
        owner=rh.user, ministry="mso", model_hrid="assistant-rh-mso", project=project
    )
    visible_key, hidden_key = (
        ChatConversationAttachmentFactory(conversation=conversation, uploaded_by=rh.user).key
        for conversation in (visible, hidden)
    )
    deleted_keys = set()
    monkeypatch.setattr("chat.views.projects._bulk_delete_s3_blobs", deleted_keys.update)
    assert api_client.delete(BASE + f"projects/{project.pk}/").status_code == 204
    assert not ChatConversation.objects.filter(pk=visible.pk).exists()
    hidden.refresh_from_db()
    assert hidden.project is None
    assert visible_key in deleted_keys and hidden_key not in deleted_keys
    assert hidden.attachments.get().key == hidden_key


def test_stale_full_save_cannot_undo_revocation_but_admins_can_reactivate(rh):
    """Any stale copy keeps the database flag; an explicit change still wins."""
    stale = User.objects.get(pk=rh.user.pk)
    admin_copy = User.objects.get(pk=rh.user.pk)
    admin_copy.is_active = False
    admin_copy.save()
    stale.language = "fr-fr"
    stale.save()
    current = User.objects.get(pk=rh.user.pk)
    assert current.is_active is False and current.language == "fr-fr"
    admin_copy.is_active = True
    admin_copy.save()
    assert User.objects.get(pk=rh.user.pk).is_active is True
    stale.refresh_from_db()
    stale.assistant_rh_admitted = False
    stale.save()
    assert User.objects.get(pk=rh.user.pk).assistant_rh_admitted is False


def test_signing_key_is_cached_until_rotated(rh, api_client, tmp_path):
    """Keys are not re-read per attempt, but an atomically replaced file is picked up."""
    session_key = login(api_client, rh.user)
    decode(rh, sign_assertion(rh.user.pk, session_key, "matte"))
    rotated = Ed25519PrivateKey.generate()
    staging = tmp_path / "rotated.pem"
    staging.write_bytes(rotated.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
    staging.replace(rh.settings.ASSISTANT_RH_SIGNING_KEY_FILE)
    rh.key = rotated
    assert decode(rh, sign_assertion(rh.user.pk, session_key, "matte"))["ministry"] == "matte"
