import aiohttp
import asyncio
from mythic_container.logging import logger
from .constants import LLM_MAX_RETRIES, LLM_RETRY_BACKOFF


async def retry_post(session, url, headers, payload, *, timeout=180,
                     sock_read=60):
    for attempt in range(LLM_MAX_RETRIES):
        resp = await session.post(
            url, headers=headers, json=payload,
            timeout=aiohttp.ClientTimeout(total=timeout, sock_read=sock_read),
        )
        if resp.status == 429 and attempt < LLM_MAX_RETRIES - 1:
            resp.close()
            wait = LLM_RETRY_BACKOFF[attempt]
            logger.warning(f"[echidna] 429 from LLM, retrying in {wait}s")
            await asyncio.sleep(wait)
            continue
        return resp
    return resp
