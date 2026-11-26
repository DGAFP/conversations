"""Assistant RH invitation import and confirmation."""

from datetime import timedelta
from io import StringIO

from django.contrib.auth.models import Group
from django.core import mail
from django.core.management import CommandError, call_command
from django.test import Client
from django.urls import reverse
from django.utils import timezone

import pytest
from lasuite.oidc_login.backends import OIDCAuthenticationBackend as SuiteBackend
from mozilla_django_oidc.auth import OIDCAuthenticationBackend as MozillaBackend

from core.authentication import invitations
from core.authentication.backends import OIDCAuthenticationBackend
from core.factories import UserFactory
from core.models import AssistantRHInvitation, MinistryGrant, User

pytestmark = pytest.mark.django_db


@pytest.fixture(name="corpus_group")
def fixture_corpus_group():
    """Create a group that grants access to the test corpus."""
    group = Group.objects.create(name="Corpus MASA")
    MinistryGrant.objects.create(group=group, ministry="masa")
    return group


@pytest.fixture(name="invite")
def fixture_invite(corpus_group, settings):
    """Enable invitations and prepare a pending invitation with in-memory email."""
    settings.ASSISTANT_RH_ENABLED = True
    settings.ASSISTANT_RH_OIDC_ENABLED = True
    settings.ASSISTANT_RH_INVITATIONS_ENABLED = True
    settings.OIDC_OP_URL = "https://issuer.test"
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    return AssistantRHInvitation.objects.create(
        email="agent@example.org",
        group=corpus_group,
        expires_at=timezone.now() + timedelta(days=1),
    )


def test_import_preview_apply_and_reimport(tmp_path, corpus_group):
    """Preview and import without duplicating invitations or enrolling existing accounts."""
    UserFactory(admin_email="existing@example.org")
    csv_path = tmp_path / "agents.csv"
    csv_path.write_text(
        "email\nAgent@Example.org\nagent@example.org\nexisting@example.org\n",
        encoding="utf-8",
    )
    report = StringIO()
    call_command("import_rh_invitations", csv_path, group=corpus_group.name, stdout=report)
    assert "would import" in report.getvalue()
    assert "duplicate" in report.getvalue()
    assert "manual review" in report.getvalue()
    assert not AssistantRHInvitation.objects.exists()

    for _ in range(2):
        call_command("import_rh_invitations", csv_path, group=corpus_group.name, apply=True)
    assert AssistantRHInvitation.objects.get().email == "agent@example.org"

    csv_path.write_text("email\nnew@example.org\ninvalid\n", encoding="utf-8")
    with pytest.raises(CommandError, match="line 3"):
        call_command("import_rh_invitations", csv_path, group=corpus_group.name, apply=True)
    assert AssistantRHInvitation.objects.count() == 1


@pytest.mark.parametrize("state", ["expired", "active", "used"])
def test_import_renew_expired_invitation(tmp_path, corpus_group, state):
    """Only an explicit apply can renew an expired, unused invitation."""
    now = timezone.now()
    former_group = Group.objects.create(name="Former corpus")
    invitation = AssistantRHInvitation.objects.create(
        email="agent@example.org",
        group=former_group,
        expires_at=now + timedelta(days=1 if state == "active" else -1),
        accepted_at=now - timedelta(days=2) if state == "used" else None,
        challenge_expires_at=now - timedelta(days=1),
        challenge_digest="old-digest",
        attempts=3,
    )
    original_expiry = invitation.expires_at
    csv_path = tmp_path / "agents.csv"
    csv_path.write_text(f"email\n{invitation.email}\n", encoding="utf-8")

    def run_import(*, apply=False, renew_expired=False):
        report = StringIO()
        call_command(
            "import_rh_invitations",
            csv_path,
            group=corpus_group.name,
            apply=apply,
            renew_expired=renew_expired,
            stdout=report,
        )
        invitation.refresh_from_db()
        return report.getvalue()

    if state == "expired":
        assert "expired invitation (use --renew-expired)" in run_import(apply=True)
        assert "would renew expired invitation" in run_import(renew_expired=True)
        assert invitation.expires_at == original_expiry
        assert invitation.group == former_group

        assert "renewed expired invitation" in run_import(apply=True, renew_expired=True)
        assert invitation.expires_at >= now + timedelta(days=30)
        assert invitation.group == corpus_group
        assert invitation.challenge_expires_at is None
        assert invitation.challenge_digest == ""
        assert invitation.attempts == 0
    else:
        expected = "used invitation" if state == "used" else "existing invitation"
        assert expected in run_import(apply=True, renew_expired=True)
        assert invitation.expires_at == original_expiry
        assert invitation.group == former_group


def start_invitation(invite, rf, client):
    """Start the confirmation flow and return the code captured by the mail backend."""
    request = rf.get("/")
    request.session = client.session
    backend = OIDCAuthenticationBackend()
    with pytest.raises(invitations.InvitationRequired) as exc:
        backend.create_user({"sub": "new-proconnect-sub", "email": invite.email.upper()})
    assert invitations.start(request, exc.value.claims)
    request.session.save()
    return mail.outbox[-1].body.split()[3].rstrip(".")


def test_confirmation_checks_browser_csrf_and_replay(invite, settings, monkeypatch):
    """Open an admitted session only after confirmation in the original browser."""
    settings.LOGIN_REDIRECT_URL = "/"
    settings.LOGIN_REDIRECT_URL_FAILURE = "/"
    client = Client(enforce_csrf_checks=True)
    monkeypatch.setattr(
        MozillaBackend,
        "get_token",
        lambda *_: {"id_token": "test-id-token", "access_token": "test-access-token"},
    )
    monkeypatch.setattr(
        OIDCAuthenticationBackend, "verify_token", lambda *_, **__: {"sub": "new-proconnect-sub"}
    )
    monkeypatch.setattr(
        SuiteBackend,
        "get_userinfo",
        lambda *_: {"sub": "new-proconnect-sub", "email": invite.email},
    )

    def callback(state):
        session = client.session
        session["oidc_states"] = {state: {"nonce": "test-nonce", "code_verifier": "verifier"}}
        session.save()
        return client.get(
            reverse("oidc_authentication_callback"), {"code": "oidc-code", "state": state}
        )

    url = reverse("assistant-rh-proconnect-confirm")
    assert callback("first-state").url == url
    assert "oidc_id_token" not in client.session
    assert "_auth_user_id" not in client.session
    code = mail.outbox[-1].body.split()[3].rstrip(".")
    assert mail.outbox[-1].to == [invite.email]
    assert not User.objects.filter(sub="new-proconnect-sub").exists()
    resume = reverse("assistant-rh-proconnect-login")
    assert Client().post(url, {"code": code}).url == resume
    assert client.post(url, {"code": code}).status_code == 403

    response = client.get(url)
    assert "Saisissez le code" in response.content.decode()
    assert "form-action 'self'" in response["Content-Security-Policy"]
    csrf = client.cookies["csrftoken"].value
    response = client.post(url, {"code": code}, HTTP_X_CSRFTOKEN=csrf)
    assert response.status_code == 200
    assert f'content="0;url={resume}"' in response.content.decode()

    invite.refresh_from_db()
    assert invite.accepted_at
    user = User.objects.get(sub="new-proconnect-sub")
    assert user.assistant_rh_admitted and not user.has_usable_password()
    assert list(user.groups.all()) == [invite.group]
    assert client.post(url, {"code": code}, HTTP_X_CSRFTOKEN=csrf).url == resume
    assert User.objects.filter(email=invite.email).count() == 1
    assert callback("second-state").url == "/"
    assert client.session["_auth_user_id"] == str(user.pk)
    assert client.session["oidc_id_token"] == "test-id-token"


@pytest.mark.parametrize("blocker", ["attempts", "expiry", "existing_local", "group"])
def test_confirmation_rejects_invalid_activation(invite, rf, client, blocker):
    """Keep the invitation pending when a redemption precondition fails."""
    code = start_invitation(invite, rf, client)
    pending = client.session[invitations.SESSION_KEY]
    create_user = OIDCAuthenticationBackend().create_oidc_user
    if blocker == "attempts":
        for _ in range(invitations.MAX_ATTEMPTS):
            assert not invitations.confirm(pending, "incorrect", create_user)
    elif blocker == "expiry":
        AssistantRHInvitation.objects.filter(pk=invite.pk).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
    elif blocker == "existing_local":
        UserFactory(admin_email=invite.email.upper())
    else:
        MinistryGrant.objects.filter(group=invite.group).delete()

    assert not invitations.confirm(pending, code, create_user)
    assert not User.objects.filter(sub="new-proconnect-sub").exists()
    invite.refresh_from_db()
    assert invite.accepted_at is None


def test_one_email_per_challenge_window(invite, rf, client):
    """Keep one active code per invitation even across browser sessions."""
    start_invitation(invite, rf, client)
    other_request = rf.get("/")
    other_request.session = Client().session
    assert not invitations.start(other_request, {"sub": "other-sub", "email": invite.email})
    assert len(mail.outbox) == 1


def test_mail_failure_allows_retry_after_short_cooldown(invite, rf, client, monkeypatch):
    """Permit a fresh code after the mail failure cooldown elapses."""
    request = rf.get("/")
    request.session = client.session
    claims = {"sub": "new-proconnect-sub", "email": invite.email}
    with monkeypatch.context() as patch:
        patch.setattr(invitations, "send_mail", lambda *args, **kwargs: 0)
        assert not invitations.start(request, claims)
    invite.refresh_from_db()
    assert not invite.challenge_digest
    assert invitations.SESSION_KEY not in request.session
    assert not invitations.start(request, claims)

    AssistantRHInvitation.objects.filter(pk=invite.pk).update(
        challenge_expires_at=timezone.now() - timedelta(seconds=1)
    )
    assert invitations.start(request, claims)
    assert mail.outbox[-1].to == [invite.email]
