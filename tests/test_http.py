"""Tests for core/http.py — retry_post."""
import asyncio
import pytest
import aiohttp
from unittest.mock import AsyncMock, MagicMock, patch
from echidna.mythic.agent_functions.core.http import retry_post


class FakeResponse:
    def __init__(self, status):
        self.status = status

    def close(self):
        pass


@pytest.mark.asyncio
async def test_retry_post_success_first_try():
    session = AsyncMock()
    resp = FakeResponse(200)
    session.post.return_value = resp

    result = await retry_post(session, "http://test", {}, {})
    assert result.status == 200
    assert session.post.call_count == 1


@pytest.mark.asyncio
async def test_retry_post_retries_on_429():
    session = AsyncMock()
    resp_429 = FakeResponse(429)
    resp_200 = FakeResponse(200)
    session.post.side_effect = [resp_429, resp_200]

    with patch("echidna.mythic.agent_functions.core.http.asyncio.sleep",
               new_callable=AsyncMock):
        result = await retry_post(session, "http://test", {}, {})

    assert result.status == 200
    assert session.post.call_count == 2


@pytest.mark.asyncio
async def test_retry_post_gives_up_after_max():
    session = AsyncMock()
    resp_429 = FakeResponse(429)
    session.post.return_value = resp_429

    with patch("echidna.mythic.agent_functions.core.http.asyncio.sleep",
               new_callable=AsyncMock):
        result = await retry_post(session, "http://test", {}, {})

    assert result.status == 429
    assert session.post.call_count == 5
