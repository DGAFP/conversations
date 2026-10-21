"""Capture only authoritative API extensions, never Markdown or client message IDs."""

import json
import re
from urllib.parse import urlsplit

from django.urls import reverse

import httpx

MAX_FRAME_BYTES = 1024 * 1024


def public_url(value):
    """Same public-source allowlist as the API; arbitrary links never reach the UI."""
    if not isinstance(value, str):
        return None
    try:
        url = urlsplit(value)
        if (
            url.scheme == "https"
            and url.hostname
            in {
                "www.service-public.fr",
                "www.service-public.gouv.fr",
                "www.legifrance.gouv.fr",
                "legifrance.gouv.fr",
            }
            and not (url.username or url.password or url.query or url.fragment)
            and url.port in (None, 443)
        ):
            return value
    except ValueError:
        pass
    return None


def source_text(value, limit):
    """Metadata is plain text. Do not accept URL-shaped titles as storage links."""
    if not isinstance(value, str):
        return ""
    return " ".join(re.sub(r"(?:[a-z][a-z0-9+.-]*://|//)\S+", "", value, flags=re.I).split())[
        :limit
    ]


class SourceCapture:
    """One capture per agent request; bounded, fail-closed, independent of SDK internals."""

    def __init__(self):
        self.completion_id = None
        self.sources = []
        self.valid = False

    def accept(self, payload):
        """The API commits source authority before its successful terminal frame."""
        if not isinstance(payload, dict) or "error" in payload:
            self.valid = False
            return
        extension = payload.get("x_assistant_rh")
        if extension is None:
            return
        choices = payload.get("choices")
        completion_id = payload.get("id")
        sources = extension.get("sources") if isinstance(extension, dict) else None
        terminal_choice = (
            isinstance(choices, list)
            and len(choices) == 1
            and isinstance(choices[0], dict)
            and choices[0].get("finish_reason") == "stop"
        )
        valid_completion = isinstance(completion_id, str) and re.fullmatch(
            r"chatcmpl-[0-9a-f]{32}", completion_id
        )
        if (
            self.completion_id is not None
            or not valid_completion
            or not terminal_choice
            or not isinstance(sources, list)
            or len(sources) > 100
        ):
            self.valid = False
            return
        normalized = []
        seen = set()
        for source in sources:
            if not isinstance(source, dict):
                return
            ref = source.get("doc_ref")
            if (
                not isinstance(ref, str)
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", ref)
                or ref in seen
            ):
                return
            seen.add(ref)
            normalized.append(
                {
                    "doc_ref": ref,
                    "title": source_text(source.get("title"), 1024) or "Document",
                    "publisher": source_text(source.get("publisher"), 512),
                    "url": public_url(source.get("url")),
                }
            )
        self.completion_id, self.sources, self.valid = completion_id, normalized, True

    def ui_metadata(self, conversation_id, message_id):
        """Only app paths/public URLs leave the backend; run IDs stay server-side."""
        if not self.valid:
            return {"status": "unavailable", "sources": []}
        sources = []
        for index, source in enumerate(self.sources):
            url = public_url(source["url"])
            publisher = source["publisher"]
            hostname = urlsplit(url).hostname if url else None
            if hostname in {"www.legifrance.gouv.fr", "legifrance.gouv.fr"}:
                publisher = "Légifrance"
            elif hostname in {"www.service-public.fr", "www.service-public.gouv.fr"}:
                publisher = "Service-Public"
            path = reverse(
                "assistant-rh-document",
                kwargs={
                    "conversation_id": conversation_id,
                    "message_id": message_id,
                    "source_index": index,
                },
            )
            sources.append(
                {
                    "title": source["title"],
                    "publisher": publisher,
                    "url": url or path,
                    "access": "public" if url else "authenticated",
                }
            )
        return {"status": "available", "sources": sources}

    def attach(self, response):
        """Inspect the same bytes consumed by OpenAI/Pydantic in JSON and SSE modes."""
        if response.headers.get("content-encoding", "identity") != "identity":
            self.valid = False
            return
        response.stream = CapturedStream(
            response.stream, self, "text/event-stream" in response.headers.get("content-type", "")
        )


class CapturedStream(httpx.AsyncByteStream):
    """Tee a bounded JSON body or SSE frame; never buffers the whole answer stream."""

    def __init__(self, stream, capture, sse):
        self.stream, self.capture, self.sse = stream, capture, sse

    async def __aiter__(self):
        pending = bytearray()
        failed = False
        scanned = 0
        async for chunk in self.stream:
            if not failed:
                pending.extend(chunk)
                if self.sse:
                    while True:
                        newline = pending.find(b"\n", scanned)
                        if newline < 0:
                            scanned = len(pending)
                            break
                        line = pending[:newline]
                        del pending[: newline + 1]
                        scanned = 0
                        if len(line) > MAX_FRAME_BYTES:
                            failed = True
                            self.capture.valid = False
                            break
                        if not self._inspect_line(line):
                            failed = True
                            self.capture.valid = False
                if len(pending) > MAX_FRAME_BYTES:
                    failed = True
                    pending.clear()
                    self.capture.valid = False
            yield chunk
        if not self.sse and not failed:
            try:
                self.capture.accept(json.loads(pending))
            except ValueError, TypeError, AttributeError:
                self.capture.valid = False

    def _inspect_line(self, line):
        if line.startswith(b"data:"):
            data = line[5:].strip()
            if data and data != b"[DONE]":
                try:
                    self.capture.accept(json.loads(data))
                except ValueError, TypeError, AttributeError:
                    return False
        return True

    async def aclose(self):
        await self.stream.aclose()
