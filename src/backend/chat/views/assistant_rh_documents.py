"""Session-protected asynchronous PDF proxy. No RAG DB or storage URL access."""

import asyncio
import logging
import threading
import uuid
from urllib.parse import quote, urlsplit

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpResponse, StreamingHttpResponse
from django.views.decorators.http import require_GET

import anyio
import httpx
from asgiref.sync import sync_to_async
from rest_framework.exceptions import PermissionDenied

from core.assistant_rh import AssistantRHSessionPermission, visible_conversations

from chat.assistant_rh_delegation import sign_assertion
from chat.assistant_rh_http import CancellationSafeStream
from chat.models import AssistantRHMessageRun, ChatConversation

logger = logging.getLogger(__name__)
_slots = threading.BoundedSemaphore(8)
CHUNK_BYTES = 64 * 1024
CLEANUP_SECONDS = 5


class AccessFailure(Exception):
    """Uniform document failure without backend details."""

    def __init__(self, status):
        super().__init__("document_unavailable")
        self.status = status


def authorize(request, conversation_id, message_id, source_index):
    """Always resolve message → completion → source on the server, with current rights."""
    if not settings.ASSISTANT_RH_ENABLED:
        raise AccessFailure(404)
    if not request.user.is_authenticated:
        raise AccessFailure(401)
    if not AssistantRHSessionPermission().has_permission(request, None):
        raise AccessFailure(403)
    try:
        conversation = visible_conversations(request.user).get(pk=conversation_id)
        run = AssistantRHMessageRun.objects.get(conversation=conversation, message_id=message_id)
    except ChatConversation.DoesNotExist, AssistantRHMessageRun.DoesNotExist:
        raise AccessFailure(404) from None
    if not any(
        message.id == message_id and message.role == "assistant"
        for message in conversation.messages
    ):
        raise AccessFailure(404)
    if not 0 <= source_index < len(run.sources) or run.sources[source_index].get("url"):
        raise AccessFailure(404)
    return (
        request.user.pk,
        request.session.session_key,
        conversation.ministry,
        run.completion_id,
        run.sources[source_index]["doc_ref"],
    )


def failure(status):
    """Visible in a PDF frame/new tab; no redirect or upstream error detail."""
    messages = {
        401: "Votre session a expiré. Reconnectez-vous à Conversations, puis réessayez.",
        403: "Votre accès n’est plus autorisé. Revenez à Conversations.",
        404: "Document indisponible ou accès non autorisé.",
        503: (
            "Le service documentaire est momentanément indisponible. "
            "Réessayez depuis la conversation."
        ),
    }
    response = HttpResponse(
        messages[status], status=status, content_type="text/plain; charset=utf-8"
    )
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


class ProxyResources:
    """One transfer owns the client, response, admission slot and cleanup task."""

    def __init__(self, client):
        self.client = client
        self.upstream = None
        self.loop = asyncio.get_running_loop()
        self.cleanup_task = None
        self.deadline = self.loop.time() + 120

    def start_cleanup(self):
        """One bounded task, retained across repeated caller cancellation."""
        if self.cleanup_task is None:
            self.cleanup_task = self.loop.create_task(self.cleanup())
        return self.cleanup_task

    async def cleanup(self):
        """Give body and client independent budgets; a stuck close cannot hold a slot."""
        try:
            try:
                async with asyncio.timeout(CLEANUP_SECONDS):
                    if self.upstream is not None:
                        await self.upstream.aclose()
            except TimeoutError, httpx.HTTPError:
                logger.warning("assistant_rh_document_body_cleanup_failed")
            try:
                async with asyncio.timeout(CLEANUP_SECONDS):
                    await self.client.aclose()
            except TimeoutError, httpx.HTTPError:
                logger.warning("assistant_rh_document_client_cleanup_failed")
        finally:
            _slots.release()

    async def finish_cleanup(self):
        """Shield the cleanup owner from cancellation, then propagate cancellation."""
        task = self.start_cleanup()
        cancelled = None
        with anyio.CancelScope(shield=True):
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError as exc:
                    cancelled = exc
        task.result()
        await anyio.lowlevel.checkpoint_if_cancelled()
        if cancelled is not None:
            raise cancelled


class PDFProxyResponse(StreamingHttpResponse):
    """Own upstream cleanup even if ASGI closes before the first body iteration."""

    def __init__(self, resources, *, download=False):
        self.resources = resources
        self.upstream = upstream = resources.upstream
        self.deadline = resources.deadline
        super().__init__(self.body(), content_type="application/pdf")
        self["Content-Length"] = upstream.headers["content-length"]
        disposition = "attachment" if download else "inline"
        self["Content-Disposition"] = f'{disposition}; filename="document.pdf"'
        self["Cache-Control"] = "private, no-store"
        self["X-Content-Type-Options"] = "nosniff"
        self["Accept-Ranges"] = "none"
        # Only the application origins configured by the operator can embed PDFs.
        origins = []
        for value in settings.CORS_ALLOWED_ORIGINS:
            origin = urlsplit(value)
            if origin.scheme in ("http", "https") and origin.netloc and not origin.username:
                origins.append(f"{origin.scheme}://{origin.netloc}")
        self["Content-Security-Policy"] = " ".join(["frame-ancestors", "'self'", *origins])
        self.xframe_options_exempt = True

    async def body(self):
        """Bound reads; no eager list, bytes/base64 buffer, or sync ASGI iterator."""
        received = 0
        try:
            async with asyncio.timeout_at(self.deadline):
                async for part in self.upstream.aiter_raw(CHUNK_BYTES):
                    received += len(part)
                    if received > int(self["Content-Length"]):
                        raise AccessFailure(503)
                    yield part
                if received != int(self["Content-Length"]):
                    raise AccessFailure(503)
        except httpx.HTTPError, TimeoutError, AccessFailure:
            # Once headers are committed, terminate the PDF instead of mixing JSON.
            raise RuntimeError("Document transfer interrupted") from None
        finally:
            await self.resources.finish_cleanup()

    async def __aiter__(self):
        try:
            async for part in self._iterator:
                yield self.make_bytes(part)
        finally:
            await self.resources.finish_cleanup()

    def close(self):
        if not self.closed and self.resources.loop.is_running():
            self.resources.loop.call_soon_threadsafe(self.resources.start_cleanup)
        super().close()


@require_GET
async def document(request, conversation_id, message_id, source_index):
    """Every GET (Range included) rechecks the session and signs exactly once."""
    try:
        user_id, session_key, ministry, completion_id, doc_ref = await sync_to_async(authorize)(
            request, conversation_id, message_id, source_index
        )
        # Django would materialize an async iterator under WSGI, violating the
        # bounded transfer contract. The PDF route requires the ASGI deployment.
        if not hasattr(request, "scope"):
            raise AccessFailure(503)
        assertion = await sync_to_async(sign_assertion)(user_id, session_key, ministry)
    except AccessFailure as exc:
        return failure(exc.status)
    except PermissionDenied:
        return failure(403)
    except ImproperlyConfigured, OSError:
        return failure(503)
    if not _slots.acquire(blocking=False):  # pylint: disable=consider-using-with
        return failure(503)
    # The streaming response owns the client until disconnect or EOF.
    client = httpx.AsyncClient(  # pylint: disable=consider-using-with
        trust_env=False,
        follow_redirects=False,
        timeout=httpx.Timeout(15, connect=5, pool=5, write=5),
    )
    resources = ProxyResources(client)
    try:
        url = (
            settings.ASSISTANT_RH_API_URL.rstrip("/")
            + "/documents/"
            + quote(doc_ref, safe="")
            + "/content"
        )
        request_id = uuid.uuid4().hex
        outbound = client.build_request(
            "GET",
            url,
            params={"completion_id": completion_id},
            headers={
                "Authorization": "Bearer " + assertion,
                "X-Client-Request-Id": request_id,
                "Accept-Encoding": "identity",
            },
        )
        async with asyncio.timeout(20):
            response = await client.send(outbound, stream=True)
        resources.upstream = response
        response.stream = CancellationSafeStream(response.stream, 5)
        if response.status_code != 200:
            raise AccessFailure(404 if response.status_code in (403, 404) else 503)
        if (
            response.headers.get("content-type", "").split(";")[0] != "application/pdf"
            or not response.headers.get("content-length", "").isdigit()
            or int(response.headers["content-length"]) < 5
            or response.headers.get("content-encoding", "identity") != "identity"
        ):
            raise AccessFailure(503)
        logger.info("assistant_rh_document_request request_id=%s status=200", request_id)
        return PDFProxyResponse(resources, download=request.GET.get("download") == "1")
    except (httpx.HTTPError, TimeoutError, AccessFailure) as exc:
        await resources.finish_cleanup()
        return failure(exc.status if isinstance(exc, AccessFailure) else 503)
    except BaseException:
        await resources.finish_cleanup()
        raise
