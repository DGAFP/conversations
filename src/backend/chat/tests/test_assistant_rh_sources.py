"""Authoritative metadata survives JSON/SSE and never trusts browser history."""

import json

import httpx
import pytest

from chat.assistant_rh_sources import CapturedStream, SourceCapture

COMPLETION = "chatcmpl-" + "a" * 32
PAYLOAD = {
    "id": COMPLETION,
    "choices": [{"finish_reason": "stop"}],
    "x_assistant_rh": {
        "sources": [
            {"doc_ref": "private-doc", "title": "Guide RH", "publisher": "DGAFP", "url": None},
            {
                "doc_ref": "public-doc",
                "title": "Service public",
                "url": "https://www.service-public.fr/a",
            },
        ]
    },
}


class Fragments(httpx.AsyncByteStream):
    """Fragment in the middle of JSON, UTF-8 and SSE delimiters."""

    def __init__(self, data, fragment_size=7):
        self.data = data
        self.fragment_size = fragment_size
        self.closed = False

    async def __aiter__(self):
        for offset in range(0, len(self.data), self.fragment_size):
            yield self.data[offset : offset + self.fragment_size]

    async def aclose(self):
        self.closed = True


@pytest.mark.asyncio
@pytest.mark.parametrize("sse", [False, True])
async def test_capture_json_and_sse_without_changing_wire_bytes(sse):
    """Capture json and sse without changing wire bytes."""
    raw = json.dumps(PAYLOAD).encode()
    wire = b"data: " + raw + b"\r\n\r\ndata: [DONE]\n\n" if sse else raw
    capture = SourceCapture()
    stream = Fragments(wire)
    tee = CapturedStream(stream, capture, sse)
    assert b"".join([part async for part in tee]) == wire
    assert capture.valid and capture.completion_id == COMPLETION
    info = capture.ui_metadata("11111111-1111-4111-8111-111111111111", "message-1")
    assert info["sources"][0]["url"].endswith("/messages/message-1/sources/0/")
    assert info["sources"][1]["url"] == "https://www.service-public.fr/a"
    assert COMPLETION not in json.dumps(info) and "private-doc" not in json.dumps(info)
    await tee.aclose()
    assert stream.closed


@pytest.mark.parametrize(
    "payload",
    [
        {
            "id": COMPLETION,
            "choices": [{"finish_reason": "error"}],
            "x_assistant_rh": {"sources": []},
        },
        {
            "id": "trace-ui-id",
            "choices": [{"finish_reason": "stop"}],
            "x_assistant_rh": {"sources": []},
        },
        {
            "id": COMPLETION,
            "choices": [{"finish_reason": "stop"}],
            "x_assistant_rh": {"sources": [{"doc_ref": "s3://key"}]},
        },
        {
            "id": COMPLETION,
            "choices": [{"finish_reason": "stop"}],
            "error": {},
            "x_assistant_rh": {"sources": []},
        },
        {"id": COMPLETION, "choices": [{"finish_reason": "stop"}]},
    ],
)
def test_failed_legacy_and_untrusted_references_grant_no_association(payload):
    """Failed legacy and untrusted references grant no association."""
    capture = SourceCapture()
    capture.accept(payload)
    assert not capture.valid
    assert capture.ui_metadata("id", "message") == {"status": "unavailable", "sources": []}


def test_storage_urls_and_unknown_metadata_are_not_exported():
    """Storage urls and unknown metadata are not exported."""
    payload = json.loads(json.dumps(PAYLOAD))
    payload["x_assistant_rh"]["sources"][0].update(
        {
            "url": "https://bucket.s3.fr-par.scw.cloud/private.pdf?signature=secret",
            "title": "Guide https://bucket.s3.fr-par.scw.cloud/private.pdf?signature=secret",
            "bearer": "secret",
            "storage_path": "secret",
        }
    )
    capture = SourceCapture()
    capture.accept(payload)
    assert capture.valid
    assert "secret" not in json.dumps(capture.sources)
    assert capture.sources[0]["title"] == "Guide"


@pytest.mark.asyncio
async def test_unfinished_stream_and_oversized_metadata_do_not_enable_documents():
    """Unfinished stream and oversized metadata do not enable documents."""
    for wire in (
        b'data: {"choices": [{"delta": {"content": "answer"}}]}\n\n',
        b"x" * (1024 * 1024 + 1),
    ):
        capture = SourceCapture()
        tee = CapturedStream(Fragments(wire), capture, True)
        async for _ in tee:
            pass
        assert not capture.valid
