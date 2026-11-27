"""Session, author, source substitutions, rights withdrawal and streaming proxy."""

import asyncio
import json
import uuid
from unittest.mock import AsyncMock

from django.test import AsyncClient

import httpx
import pytest
from asgiref.sync import async_to_sync

from core.factories import UserFactory

# Imported fixture registers the RH setup with pytest.
# pylint: disable-next=unused-import,useless-import-alias
from core.tests.test_assistant_rh import assistant_rh_setup as assistant_rh_setup  # noqa: PLC0414
from core.tests.test_assistant_rh import decode, login

from chat.ai_sdk_types import UIMessage
from chat.models import AssistantRHMessageRun, ChatConversation
from chat.tests.test_assistant_rh_sources import COMPLETION, PAYLOAD, Fragments
from chat.views.assistant_rh_documents import PDFProxyResponse, ProxyResources, _slots

pytestmark = pytest.mark.django_db(transaction=True)
PDF = b"%PDF-1.7\n" + b"synthetic" * 100000


def associated_chat(user):
    """Create a persisted assistant reply and its authoritative source mapping."""
    chat = ChatConversation.objects.create(
        owner=user,
        ministry="matte",
        messages=[
            UIMessage(
                id="server-message", role="assistant", parts=[{"type": "text", "text": "Réponse"}]
            ),
        ],
    )
    AssistantRHMessageRun.objects.create(
        conversation=chat,
        message_id="server-message",
        completion_id=COMPLETION,
        sources=PAYLOAD["x_assistant_rh"]["sources"],
    )
    return chat


def url(chat, message="server-message", index=0):
    """Build the browser-facing source route without upstream identifiers."""
    return f"/api/v1.0/chats/{chat.pk}/messages/{message}/sources/{index}/"


def test_session_owner_admission_and_current_rights_before_upstream(rh, api_client):
    """Session owner admission and current rights before upstream."""
    chat = associated_chat(rh.user)
    endpoint = url(chat)
    assert api_client.get(endpoint).status_code == 401
    login(api_client, rh.user)
    assert api_client.get(url(chat, message="forged")).status_code == 404
    assert api_client.get(url(chat, index=99)).status_code == 404
    other = UserFactory(assistant_rh_admitted=True)
    other.groups.add(rh.group)
    login(api_client, other)
    assert api_client.get(endpoint).status_code == 404
    login(api_client, rh.user)
    rh.user.groups.clear()
    assert api_client.get(endpoint).status_code == 404
    rh.user.groups.add(rh.group)
    rh.user.assistant_rh_admitted = False
    rh.user.save()
    assert api_client.get(endpoint).status_code in (401, 403)
    rh.user.assistant_rh_admitted = True
    rh.user.is_active = False
    rh.user.save()
    assert api_client.get(endpoint).status_code == 401


def test_logout_and_old_responses_cannot_open_known_link(rh, api_client):
    """Logout and old responses cannot open known link."""
    chat = associated_chat(rh.user)
    login(api_client, rh.user)
    AssistantRHMessageRun.objects.all().delete()
    assert api_client.get(url(chat)).status_code == 404
    login(api_client, rh.user)
    api_client.logout()
    assert api_client.get(url(chat)).status_code == 401


@pytest.mark.parametrize("range_value", [None, "bytes=0-5", "bytes=999999999-"])
def test_pdf_proxy_uses_server_mapping_and_fresh_delegation(rh, respx_mock, range_value):
    """PDF proxy uses server mapping and fresh delegation."""
    chat = associated_chat(rh.user)
    client = AsyncClient()
    login(client, rh.user)
    bodies = []
    claims = []

    def backend(request):
        assert request.url.params["completion_id"] == COMPLETION
        assert request.url.path == "/v1/documents/private-doc/content"
        assert "range" not in request.headers and "cookie" not in request.headers
        claims.append(decode(rh, request.headers["authorization"].removeprefix("Bearer ")))
        body = Fragments(PDF, fragment_size=8192)
        bodies.append(body)
        return httpx.Response(
            200,
            headers={"Content-Type": "application/pdf", "Content-Length": str(len(PDF))},
            stream=body,
        )

    route = respx_mock.get("https://rh.test/v1/documents/private-doc/content").mock(
        side_effect=backend
    )

    async def check():
        for _ in range(2):
            response = await client.get(
                url(chat) + "?completion_id=forged&doc_ref=forged",
                headers={"Range": range_value} if range_value else {},
            )
            assert response.status_code == 200
            assert response["Cache-Control"] == "private, no-store"
            assert response["X-Content-Type-Options"] == "nosniff"
            assert response["Accept-Ranges"] == "none" and not response.has_header("Location")
            assert b"".join([chunk async for chunk in response]) == PDF
            assert "secret" not in str(response.headers) and "private-doc" not in str(
                response.headers
            )

    async_to_sync(check)()
    assert route.call_count == 2 and len({c["jti"] for c in claims}) == 2
    assert all(body.closed for body in bodies)


@pytest.mark.parametrize(
    "status,expected", [(404, 404), (403, 404), (401, 503), (302, 503), (500, 503)]
)
def test_upstream_errors_never_redirect_or_leak(rh, respx_mock, status, expected):
    """Upstream errors never redirect or leak."""
    chat = associated_chat(rh.user)
    client = AsyncClient()
    login(client, rh.user)
    respx_mock.get("https://rh.test/v1/documents/private-doc/content").respond(
        status, headers={"Location": "https://storage.test/secret"}, text="secret"
    )
    response = async_to_sync(client.get)(url(chat))
    assert response.status_code == expected and not response.has_header("Location")
    assert b"secret" not in response.content


def test_wsgi_delivery_refuses_instead_of_buffering_the_pdf(rh, api_client):
    """WSGI delivery refuses instead of buffering the PDF."""
    chat = associated_chat(rh.user)
    login(api_client, rh.user)
    assert api_client.get(url(chat)).status_code == 503


@pytest.mark.asyncio
async def test_cancelled_pdf_transfer_closes_client_and_body():
    """Cancelled pdf transfer closes client and body."""

    class Stalled(Fragments):
        """Yield one PDF chunk, then wait until the reader is cancelled."""

        async def __aiter__(self):
            yield b"%PDF-" + b"x" * (65536 - 5)
            await asyncio.Event().wait()

    body = Stalled(b"")
    upstream = httpx.Response(200, headers={"Content-Length": "200000"}, stream=body)
    client = AsyncMock()
    assert _slots.acquire(blocking=False)  # pylint: disable=consider-using-with
    resources = ProxyResources(client)
    resources.upstream = upstream
    response = PDFProxyResponse(resources)
    begun = asyncio.Event()

    async def read():
        async for _ in response:
            begun.set()

    task = asyncio.create_task(read())
    await asyncio.wait_for(begun.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert body.closed and client.aclose.await_count == 1


@pytest.mark.asyncio
async def test_both_stalled_closers_have_independent_budgets(monkeypatch):
    """Both stalled closers have independent budgets."""

    async def stalled():
        await asyncio.Event().wait()

    monkeypatch.setattr("chat.views.assistant_rh_documents.CLEANUP_SECONDS", 0.02)
    client, upstream = AsyncMock(), AsyncMock()
    client.aclose.side_effect = stalled
    upstream.aclose.side_effect = stalled
    assert _slots.acquire(blocking=False)  # pylint: disable=consider-using-with
    resources = ProxyResources(client)
    resources.upstream = upstream
    async with asyncio.timeout(1):
        await resources.finish_cleanup()
    assert resources.cleanup_task.done()
    assert client.aclose.await_count == upstream.aclose.await_count == 1


@pytest.mark.parametrize("stream", [False, True])
def test_chat_stream_persists_server_message_completion_and_final_sources(
    rh, api_client, respx_mock, stream
):
    """Exercise the real SDK and chat finalization, then reload and forge UI input."""
    rh.settings.LLM_CONFIGURATIONS["assistant-rh-matte"].supports_streaming = stream
    login(api_client, rh.user)
    chat = ChatConversation.objects.create(
        owner=rh.user, ministry="matte", model_hrid="assistant-rh-matte"
    )
    completion = {**PAYLOAD, "created": 1, "model": "assistant-rh-matte"}
    if stream:
        frames = [
            {
                **completion,
                "x_assistant_rh": None,
                "object": "chat.completion.chunk",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"role": "assistant", "content": "Réponse RH"},
                        "finish_reason": None,
                    },
                ],
            },
            {
                **completion,
                "object": "chat.completion.chunk",
                "choices": [
                    {"index": 0, "delta": {}, "finish_reason": "stop"},
                ],
            },
        ]
        upstream = httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            stream=Fragments(
                b"".join(b"data: " + json.dumps(frame).encode() + b"\n\n" for frame in frames)
                + b"data: [DONE]\n\n"
            ),
        )
    else:
        completion.update(
            {
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Réponse RH"},
                        "finish_reason": "stop",
                    },
                ],
            }
        )
        upstream = httpx.Response(200, json=completion)
    route = respx_mock.post("https://rh.test/v1/chat/completions").mock(return_value=upstream)
    response = api_client.post(
        f"/api/v1.0/chats/{chat.pk}/conversation/",
        {
            "messages": [
                {
                    "id": "browser-forged-id",
                    "role": "user",
                    "parts": [{"type": "text", "text": "Question RH"}],
                    "metadata": {
                        "assistant_rh": {
                            "completion_id": "forged",
                            "sources": [{"doc_ref": "forged"}],
                        }
                    },
                }
            ],
        },
        format="json",
    )
    wire = b"".join(response.streaming_content).decode()
    assert route.call_count == 1 and "[DONE]" in wire
    assert route.calls[0].request.headers["accept-encoding"] == "identity"
    run = AssistantRHMessageRun.objects.get(conversation=chat)
    assert run.completion_id == COMPLETION and run.message_id != "browser-forged-id"
    assert run.sources[0]["doc_ref"] == "private-doc"
    reloaded = api_client.get(f"/api/v1.0/chats/{chat.pk}/").json()
    answer = reloaded["messages"][-1]
    assert answer["id"] == run.message_id
    assert answer["metadata"]["assistant_rh"]["sources"][0]["title"] == "Guide RH"
    assert run.completion_id not in json.dumps(reloaded)
    assert f"/messages/{run.message_id}/sources/0/" in wire


def test_successive_answers_and_reconnection_keep_their_own_associations(
    rh, api_client, respx_mock
):
    """Successive answers and reconnection keep their own associations."""
    rh.settings.AUTO_TITLE_AFTER_USER_MESSAGES = 99
    login(api_client, rh.user)
    chat = ChatConversation.objects.create(
        owner=rh.user, ministry="matte", model_hrid="assistant-rh-matte"
    )
    completions = []

    def backend(_):
        completion_id = "chatcmpl-" + uuid.uuid4().hex
        completions.append(completion_id)
        return httpx.Response(
            200,
            json={
                **PAYLOAD,
                "id": completion_id,
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

    respx_mock.post("https://rh.test/v1/chat/completions").mock(side_effect=backend)
    for index in range(2):
        response = api_client.post(
            f"/api/v1.0/chats/{chat.pk}/conversation/",
            {
                "messages": [
                    {
                        "id": f"browser-{index}",
                        "role": "user",
                        "parts": [{"type": "text", "text": f"Question {index}"}],
                    }
                ],
            },
            format="json",
        )
        assert "[DONE]" in b"".join(response.streaming_content).decode()
    runs = list(AssistantRHMessageRun.objects.filter(conversation=chat).order_by("created_at"))
    assert [run.completion_id for run in runs] == completions
    assert len({run.message_id for run in runs}) == 2
    api_client.logout()
    assert api_client.get(f"/api/v1.0/chats/{chat.pk}/").status_code in (401, 403)
    login(api_client, rh.user)
    messages = api_client.get(f"/api/v1.0/chats/{chat.pk}/").json()["messages"]
    answers = [message for message in messages if message["role"] == "assistant"]
    assert [message["id"] for message in answers] == [run.message_id for run in runs]
    for message, run in zip(answers, runs, strict=True):
        assert (
            f"/messages/{run.message_id}/sources/0/"
            in message["metadata"]["assistant_rh"]["sources"][0]["url"]
        )
