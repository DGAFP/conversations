"""Both HTTP transports must refuse unmocked external calls during tests."""

from importlib import import_module

import pytest


@pytest.mark.parametrize("package", ["httpx", "httpx2"])
def test_sync_external_http_is_blocked(package):
    """No provider may escape the test network guard through a new transport."""
    client_module = import_module(package)
    with client_module.Client(trust_env=False) as client:
        with pytest.raises(RuntimeError, match="The test was about to connect"):
            client.get("https://unmocked.invalid/")


@pytest.mark.asyncio
@pytest.mark.parametrize("package", ["httpx", "httpx2"])
async def test_async_external_http_is_blocked(package):
    """The OpenAI 3 async transport is guarded as well as legacy HTTPX."""
    client_module = import_module(package)
    async with client_module.AsyncClient(trust_env=False) as client:
        with pytest.raises(RuntimeError, match="The test was about to connect"):
            await client.get("https://unmocked.invalid/")
