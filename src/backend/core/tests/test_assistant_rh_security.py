"""Upstream security hardening must preserve RH password authentication."""

import re

from django.urls import reverse

import pytest

from core.factories import UserFactory
from core.tests.test_assistant_rh import (  # pylint: disable=unused-import,useless-import-alias
    assistant_rh_setup as assistant_rh_setup,  # noqa: PLC0414
)
from core.tests.test_assistant_rh import (  # pylint: disable=unused-import,useless-import-alias
    local_account_setup as local_account_setup,  # noqa: PLC0414
)

from conversations.assistant_rh_settings import AssistantRHSettings

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("path", ["authenticate/", "local/authenticate/", "logout/"])
def test_rh_forms_allow_same_origin_posts_and_only_nonce_styles(local_account, client, path):
    """HTML remains usable without relaxing script, framing or API restrictions."""
    del local_account
    response = client.get("/api/v1.0/" + path)
    assert response.status_code == 200
    directives = dict(
        directive.strip().split(" ", 1)
        for directive in response.headers["Content-Security-Policy"].split(";")
    )
    nonce = re.search(r'<style nonce="([^" ]+)">', response.content.decode())[1]
    assert directives["form-action"] == "'self'"
    assert directives["style-src"] == f"'nonce-{nonce}'"
    for directive in ["default-src", "script-src", "frame-ancestors"]:
        assert directives[directive] == "'none'"


def test_password_only_setup_keeps_admin_lockout(local_account, client, settings):
    """Real RH settings keep rejecting correct admin credentials after lockout."""

    class ParentSettings:
        """Isolate mixin setup from django-configurations' global installation."""

        @classmethod
        def setup(cls):
            """No other configuration hooks are needed for this mixin."""

    class PasswordOnlySettings(AssistantRHSettings, ParentSettings):
        """Exercise the same override used when staging has no OIDC provider."""

        ASSISTANT_RH_ENABLED = True
        ASSISTANT_RH_LOCAL_LOGIN_ENABLED = True
        ASSISTANT_RH_OIDC_ENABLED = False
        REST_FRAMEWORK = settings.REST_FRAMEWORK

    PasswordOnlySettings.setup()
    settings.AUTHENTICATION_BACKENDS = PasswordOnlySettings.AUTHENTICATION_BACKENDS
    settings.AXES_FAILURE_LIMIT = 1
    admin = UserFactory(admin_email="locked@example.invalid", is_staff=True, is_superuser=True)
    payload = {"username": admin.admin_email, "next": reverse("admin:index")}
    assert client.post(reverse("admin:login"), {**payload, "password": "wrong"}).status_code == 429
    assert (
        client.post(reverse("admin:login"), {**payload, "password": "password"}).status_code == 429
    )
    assert "_auth_user_id" not in client.session
    # Ordinary RH users still authenticate through ModelBackend.
    response = client.post("/api/v1.0/authenticate/", local_account)
    assert response.status_code == 302
