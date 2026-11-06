"""Exercise the real feedback bridge, ownership, delegation and API contract."""

import json

import httpx
import pytest

from core.factories import UserFactory

# pylint: disable-next=unused-import,useless-import-alias
from core.tests.test_assistant_rh import assistant_rh_setup as assistant_rh_setup  # noqa: PLC0414
from core.tests.test_assistant_rh import decode, login

from chat.models import AssistantRHMessageRun
from chat.tests.views.test_assistant_rh_documents import associated_chat

pytestmark = pytest.mark.django_db(transaction=True)
VALUE = {
    "stars": 4,
    "reasons_positive": ["Clair"],
    "reasons_negative": ["Incomplet"],
    "comment": "Un exemple serait utile.",
}


def endpoint(chat, message="server-message"):
    """The browser only addresses a conversation and its assistant message."""
    return f"/api/v1.0/chats/{chat.pk}/messages/{message}/feedback/"


def test_save_read_and_update_use_exact_contract_and_fresh_individual_delegation(
    rh, api_client, respx_mock
):
    """The server chooses the completion ID and identity for every attempt."""
    chat = associated_chat(rh.user)
    run = AssistantRHMessageRun.objects.get(conversation=chat)
    login(api_client, rh.user)
    saved = {"completion_id": run.completion_id, **VALUE}
    post = respx_mock.post("https://rh.test/v1/feedback").respond(204)
    get = respx_mock.get(f"https://rh.test/v1/feedback/{run.completion_id}").respond(
        200, json={**saved, "helpful": True}
    )
    for _ in range(2):
        response = api_client.post(endpoint(chat), VALUE, format="json")
        assert response.status_code == 204
        assert response["Cache-Control"] == "private, no-store"
        assert json.loads(post.calls[-1].request.content) == saved
    response = api_client.get(endpoint(chat))
    assert response.status_code == 200 and response.json() == VALUE
    assert run.completion_id not in response.content.decode()
    requests = [call.request for call in post.calls] + [get.calls[0].request]
    claims = [
        decode(rh, request.headers["authorization"].removeprefix("Bearer ")) for request in requests
    ]
    assert all(claim["sub"] == str(rh.user.pk) and claim["ministry"] == "matte" for claim in claims)
    assert len({claim["jti"] for claim in claims}) == 3
    assert all("cookie" not in request.headers for request in requests)


def test_missing_evaluation_is_distinct_from_upstream_failure(rh, api_client, respx_mock):
    """Only the API's not-found on a known run means no previous evaluation."""
    chat = associated_chat(rh.user)
    login(api_client, rh.user)
    route = respx_mock.get(
        "https://rh.test/v1/feedback/" + chat.assistant_rh_runs.get().completion_id
    ).respond(404)
    assert api_client.get(endpoint(chat)).status_code == 204
    route.respond(503, text="secret")
    response = api_client.get(endpoint(chat))
    assert response.status_code == 503 and b"secret" not in response.content


@pytest.mark.parametrize("method", ["get", "post"])
def test_owner_current_grants_and_message_mapping_are_required(rh, api_client, respx_mock, method):
    """No upstream call for anonymous, forged, other-owner or revoked access."""
    chat = associated_chat(rh.user)
    call = getattr(api_client, method)
    kwargs = {"data": VALUE, "format": "json"} if method == "post" else {}
    assert call(endpoint(chat), **kwargs).status_code in (401, 403)
    login(api_client, rh.user)
    assert call(endpoint(chat, "forged"), **kwargs).status_code == 404
    other = UserFactory(assistant_rh_admitted=True)
    other.groups.add(rh.group)
    login(api_client, other)
    assert call(endpoint(chat), **kwargs).status_code == 404
    login(api_client, rh.user)
    rh.user.groups.clear()
    assert call(endpoint(chat), **kwargs).status_code == 404
    rh.user.groups.add(rh.group)
    AssistantRHMessageRun.objects.filter(conversation=chat).delete()
    assert call(endpoint(chat), **kwargs).status_code == 404
    assert not respx_mock.calls


@pytest.mark.parametrize(
    "change",
    [
        {"stars": 0},
        {"stars": 6},
        {"stars": True},
        {"completion_id": "forged"},
        {"user_id": "forged"},
        {"helpful": True},
        {"stars": 1},
        {"stars": 5},
        {"reasons_positive": ["Unknown"]},
        {"comment": "x" * 4001},
        {"comment": "   ", "reasons_positive": [], "reasons_negative": []},
    ],
)
def test_invalid_payload_never_reaches_the_api(rh, api_client, respx_mock, change):
    """Validate rating, vocabulary, compatible reasons, identity fields and content."""
    chat = associated_chat(rh.user)
    login(api_client, rh.user)
    assert api_client.post(endpoint(chat), {**VALUE, **change}, format="json").status_code == 400
    assert not respx_mock.calls


@pytest.mark.parametrize(
    "status,expected", [(403, 404), (404, 404), (422, 400), (401, 503), (302, 503), (500, 503)]
)
def test_write_failures_never_confirm_or_redirect(rh, api_client, respx_mock, status, expected):
    """A successful save requires the exact 204 response from the API."""
    chat = associated_chat(rh.user)
    login(api_client, rh.user)
    respx_mock.post("https://rh.test/v1/feedback").respond(
        status, text="secret", headers={"Location": "https://secret.test"}
    )
    response = api_client.post(endpoint(chat), VALUE, format="json")
    assert response.status_code == expected
    assert b"secret" not in response.content and "Location" not in response


def test_transport_error_is_retryable_without_leaking_details(rh, api_client, respx_mock):
    """A failed connection is a generic 503 and not a successful evaluation."""
    chat = associated_chat(rh.user)
    login(api_client, rh.user)
    respx_mock.post("https://rh.test/v1/feedback").mock(side_effect=httpx.ConnectError("secret"))
    response = api_client.post(endpoint(chat), VALUE, format="json")
    assert response.status_code == 503 and b"secret" not in response.content
