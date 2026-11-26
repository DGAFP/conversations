"""Unit tests for the Scaleway Transactional Email (TEM) Django backend."""

import json
import logging

from django.core.mail import EmailMessage, EmailMultiAlternatives, get_connection, send_mail

import httpx
import pytest
import respx

from core.mail import ScalewayTEMEmailBackend, ScalewayTEMError

TEM_ENDPOINT = "https://api.scaleway.com/transactional-email/v1alpha1/regions/fr-par/emails"


@pytest.fixture(name="tem_settings")
def fixture_tem_settings(settings):
    """Configure Django to use the Scaleway TEM email backend."""
    settings.EMAIL_BACKEND = "core.mail.ScalewayTEMEmailBackend"
    settings.EMAIL_FROM = "Assistant RH <ne-pas-repondre@staging.assistant-rh.beta.gouv.fr>"
    settings.SCW_TEM_API_URL = "https://api.scaleway.com"
    settings.SCW_TEM_REGION = "fr-par"
    settings.SCW_TEM_PROJECT_ID = "c1d3f629-d08d-439d-ba76-f115ea06e579"
    settings.SCW_TEM_SECRET_KEY = "00000000-0000-0000-0000-000000000001"
    settings.SCW_TEM_TIMEOUT = 10.0
    return settings


@respx.mock
def test_send_plain_text_email_via_scaleway_tem(tem_settings):
    """Send a plain-text message through Django send_mail and verify TEM payload and headers."""
    route = respx.post(TEM_ENDPOINT).mock(
        return_value=httpx.Response(
            200,
            json={"emails": [{"id": "email-1", "status": "sending"}]},
        )
    )

    sent = send_mail(
        "Votre code d’activation Assistant RH",
        "Votre code est 123456abcdef. Il expire dans 10 minutes.",
        tem_settings.EMAIL_FROM,
        ["agent@example.org"],
        connection=get_connection(timeout=7),
    )

    assert sent == 1
    assert route.called
    request = route.calls.last.request
    assert request.headers["x-auth-token"] == tem_settings.SCW_TEM_SECRET_KEY
    assert request.extensions["timeout"] == {
        "connect": 7.0,
        "read": 7.0,
        "write": 7.0,
        "pool": 7.0,
    }
    body = json.loads(request.content)
    assert body == {
        "from": {
            "email": "ne-pas-repondre@staging.assistant-rh.beta.gouv.fr",
            "name": "Assistant RH",
        },
        "to": [{"email": "agent@example.org"}],
        "subject": "Votre code d’activation Assistant RH",
        "text": "Votre code est 123456abcdef. Il expire dans 10 minutes.",
        "project_id": tem_settings.SCW_TEM_PROJECT_ID,
    }


@respx.mock
@pytest.mark.usefixtures("tem_settings")
def test_send_multipart_email_with_optional_fields():
    """Forward HTML alternative, cc, bcc, reply-to, and extra headers."""
    route = respx.post(TEM_ENDPOINT).mock(
        return_value=httpx.Response(200, json={"emails": [{"id": "email-2", "status": "sent"}]})
    )
    message = EmailMultiAlternatives(
        subject="Sujet",
        body="Texte brut",
        from_email="ne-pas-repondre@staging.assistant-rh.beta.gouv.fr",
        to=["Destinataire <dest@example.org>"],
        cc=["cc@example.org"],
        bcc=["bcc@example.org"],
        reply_to=["support@example.org"],
        headers={"X-Custom-Header": "value"},
    )
    message.attach_alternative("<p>HTML</p>", "text/html")

    assert message.send() == 1
    body = json.loads(route.calls.last.request.content)
    assert body["from"] == {"email": "ne-pas-repondre@staging.assistant-rh.beta.gouv.fr"}
    assert body["to"] == [{"email": "dest@example.org", "name": "Destinataire"}]
    assert body["cc"] == [{"email": "cc@example.org"}]
    assert body["bcc"] == [{"email": "bcc@example.org"}]
    assert body["text"] == "Texte brut"
    assert body["html"] == "<p>HTML</p>"
    assert body["additional_headers"] == [
        {"key": "X-Custom-Header", "value": "value"},
        {"key": "Reply-To", "value": "support@example.org"},
    ]


@respx.mock
def test_http_error_raises_without_leaking_secret_or_code(tem_settings, caplog):
    """Raise ScalewayTEMError on HTTP failure without logging credentials or email bodies."""
    respx.post(TEM_ENDPOINT).mock(return_value=httpx.Response(403, json={"message": "denied"}))
    otp_code = "deadbeefcafe"
    with caplog.at_level(logging.DEBUG, logger="core.mail"):
        with pytest.raises(ScalewayTEMError, match="HTTP 403"):
            send_mail(
                "Code",
                f"Votre code est {otp_code}.",
                tem_settings.EMAIL_FROM,
                ["agent@example.org"],
            )

    assert tem_settings.SCW_TEM_SECRET_KEY not in caplog.text
    assert otp_code not in caplog.text


@respx.mock
def test_network_timeout_and_fail_silently(tem_settings):
    """Raise ScalewayTEMError on timeout, or return 0 when fail_silently=True."""
    respx.post(TEM_ENDPOINT).mock(side_effect=httpx.ReadTimeout("timed out"))

    with pytest.raises(ScalewayTEMError, match="HTTP request failed"):
        send_mail("Code", "Body", tem_settings.EMAIL_FROM, ["agent@example.org"])

    sent = send_mail(
        "Code",
        "Body",
        tem_settings.EMAIL_FROM,
        ["agent@example.org"],
        fail_silently=True,
    )
    assert sent == 0


@respx.mock
@pytest.mark.parametrize(
    "response_body",
    [
        {"emails": []},
        {"emails": [{"id": "email-3", "status": "failed"}]},
        {"emails": [{"id": "email-4", "status": "canceled"}]},
    ],
)
def test_immediate_failed_or_empty_tem_response_raises(tem_settings, response_body):
    """Treat missing or immediately failed/canceled TEM entries as delivery errors."""
    respx.post(TEM_ENDPOINT).mock(return_value=httpx.Response(200, json=response_body))

    with pytest.raises(ScalewayTEMError):
        send_mail("Code", "Body", tem_settings.EMAIL_FROM, ["agent@example.org"])


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"secret_key": ""}, "not configured"),
        ({"project_id": ""}, "not configured"),
        ({"region": "fr-par/../../evil"}, "invalid characters"),
        ({"api_url": "http://api.scaleway.com"}, "HTTPS"),
        ({"timeout": 0}, "strictly positive"),
    ],
)
def test_invalid_configuration_fails_closed(tem_settings, overrides, match):
    """Refuse to send when TEM configuration is missing or unsafe."""
    backend = ScalewayTEMEmailBackend(**overrides)
    message = EmailMessage("Code", "Body", tem_settings.EMAIL_FROM, ["agent@example.org"])

    with pytest.raises(ScalewayTEMError, match=match):
        backend.send_messages([message])

    silent_backend = ScalewayTEMEmailBackend(fail_silently=True, **overrides)
    assert silent_backend.send_messages([message]) == 0


@pytest.mark.usefixtures("tem_settings")
def test_empty_messages_returns_zero():
    """Return 0 immediately without opening an HTTP client when given no messages."""
    backend = ScalewayTEMEmailBackend()
    assert backend.send_messages([]) == 0
