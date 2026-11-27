"""Own asynchronous reads and bounded cleanup through repeated cancellation.

Adapted from the D2 API cleanup guard (assistant-rh #617), shared here by the proxy.
"""

import asyncio
from collections.abc import AsyncIterator

import anyio
import httpx


class CancellationSafeStream(httpx.AsyncByteStream):
    """Own reads and bounded cleanup, including HTTPX's automatic EOF close."""

    def __init__(self, stream: httpx.AsyncByteStream, timeout: float) -> None:
        self._stream = stream
        self._timeout = timeout
        self._reading: asyncio.Task[bytes] | None = None

    async def __aiter__(self) -> AsyncIterator[bytes]:
        iterator = self._stream.__aiter__()
        while True:
            # HTTPcore also closes internally on read errors/cancellation.
            # Own the read so caller cancellation cannot interrupt that cleanup.
            self._reading = asyncio.create_task(anext(iterator))
            try:
                chunk = await asyncio.shield(self._reading)
            except StopAsyncIteration:
                return
            except asyncio.CancelledError:
                try:
                    await self.aclose()
                except TimeoutError, httpx.HTTPError:
                    pass
                raise
            yield chunk

    async def _close(self) -> None:
        async with asyncio.timeout(self._timeout):
            try:
                # Close the connection before interrupting a pending read. Its
                # error handler then sees a fully closed stream, not half-close.
                await self._stream.aclose()
                if self._reading is not None:
                    await asyncio.gather(self._reading, return_exceptions=True)
            finally:
                if self._reading is not None and not self._reading.done():
                    self._reading.cancel()
                    await asyncio.gather(self._reading, return_exceptions=True)

    async def aclose(self) -> None:
        # AnyIO shielding alone cannot resist Task.cancel()/asyncio.timeout().
        # Keep ownership of a bounded child task until cleanup has finished.
        closing = asyncio.create_task(self._close())
        cancelled = None
        with anyio.CancelScope(shield=True):
            while not closing.done():
                try:
                    await asyncio.shield(closing)
                except asyncio.CancelledError as exc:
                    cancelled = exc
                except TimeoutError, httpx.HTTPError:
                    break
        try:
            if cancelled is not None:
                # Retrieve a possible cleanup error before propagating cancellation.
                if not closing.cancelled():
                    closing.exception()
                raise cancelled
            closing.result()
        finally:
            # A cancellation first received inside the AnyIO shield was deferred.
            await anyio.lowlevel.checkpoint_if_cancelled()
