"""Real ASGI/TCP proof: session cookie, bounded large PDF, client abandonment."""

import asyncio
import hashlib
import re
import socket

from django.core.asgi import get_asgi_application

import httpx
import pytest
import uvicorn
from asgiref.sync import async_to_sync

# Imported fixture registers the RH setup with pytest.
# pylint: disable-next=unused-import,useless-import-alias
from core.tests.test_assistant_rh import assistant_rh_setup as assistant_rh_setup  # noqa: PLC0414
from core.tests.test_assistant_rh import login

from chat.tests.views.test_assistant_rh_documents import associated_chat, url
from chat.views.assistant_rh_documents import _slots

pytestmark = pytest.mark.django_db(transaction=True)
SIZE = 12 * 1024 * 1024


class LargePDF(httpx.AsyncByteStream):
    """Generate 12 MiB without allocating the whole test object."""

    def __init__(self):
        self.closed = False

    async def __aiter__(self):
        yield b"%PDF-" + b"x" * (65536 - 5)
        for _ in range(1, SIZE // 65536):
            await asyncio.sleep(0.001)
            yield b"x" * 65536

    async def aclose(self):
        self.closed = True


def test_large_pdf_through_asgi_socket_and_disconnect(rh, api_client, respx_mock):
    """Large pdf through asgi socket and disconnect."""
    chat = associated_chat(rh.user)
    key = login(api_client, rh.user)
    rh.settings.ALLOWED_HOSTS = ["127.0.0.1", "testserver"]
    bodies = []

    def backend(_):
        body = LargePDF()
        bodies.append(body)
        return httpx.Response(
            200,
            headers={"Content-Type": "application/pdf", "Content-Length": str(SIZE)},
            stream=body,
        )

    respx_mock.get("https://rh.test/v1/documents/private-doc/content").mock(side_effect=backend)
    respx_mock.get(re.compile(r"http://127\.0\.0\.1:\d+/.*")).pass_through()

    async def exercise():
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        server = uvicorn.Server(
            uvicorn.Config(
                get_asgi_application(),
                lifespan="off",
                log_config=None,
                ws="none",
                timeout_graceful_shutdown=3,
            )
        )
        task = asyncio.create_task(server.serve(sockets=[sock]))
        try:
            async with asyncio.timeout(5):
                while not server.started:
                    if task.done():
                        task.result()
                    await asyncio.sleep(0.01)
            base = "http://127.0.0.1:" + str(sock.getsockname()[1])
            async with httpx.AsyncClient(
                base_url=base, cookies={"sessionid": key}, trust_env=False
            ) as client:
                received = hashlib.sha256()
                async with client.stream("GET", url(chat)) as response:
                    assert response.status_code == 200
                    assert response.headers["cache-control"] == "private, no-store"
                    async for chunk in response.aiter_bytes(65536):
                        received.update(chunk)
                expected = hashlib.sha256(b"%PDF-" + b"x" * (65536 - 5))
                for _ in range(1, SIZE // 65536):
                    expected.update(b"x" * 65536)
                assert received.digest() == expected.digest()
                async with client.stream("GET", url(chat)) as response:
                    assert (await anext(response.aiter_bytes(1024))).startswith(b"%PDF-")
                async with asyncio.timeout(3):
                    while _slots._value != 8:  # pylint: disable=protected-access
                        await asyncio.sleep(0.01)
                assert all(body.closed for body in bodies)
        finally:
            server.should_exit = True
            await asyncio.wait_for(task, 5)
            sock.close()

    async_to_sync(exercise)()
