"""Test helpers: a small websocket client built on aiohttp.

aiohttp is already a runtime dependency of SocketDock, and its websocket
client API is stable across versions, so tests do not depend on a separate
websocket library whose API changes between releases.
"""

import asyncio
from typing import Dict, Optional, Union

import aiohttp


class WSClosed(Exception):
    """Raised by ``WSClient.recv`` when the server closed the connection."""

    def __init__(self, code: Optional[int]):
        """Record the websocket close code (None if no code was received)."""
        super().__init__(f"websocket closed (code={code})")
        self.code = code


class WSClient:
    """Minimal websocket client returning ``str`` for text and ``bytes`` for binary."""

    def __init__(
        self, session: aiohttp.ClientSession, ws: aiohttp.ClientWebSocketResponse
    ):
        """Wrap an established aiohttp websocket."""
        self._session = session
        self._ws = ws

    @classmethod
    async def connect(
        cls, url: str, headers: Optional[Dict[str, str]] = None
    ) -> "WSClient":
        """Open a websocket (no client-side receive size limit)."""
        session = aiohttp.ClientSession()
        try:
            ws = await session.ws_connect(url, headers=headers, max_msg_size=0)
        except Exception:
            await session.close()
            raise
        return cls(session, ws)

    async def send(self, data: Union[str, bytes]):
        """Send a text frame for ``str`` or a binary frame for ``bytes``."""
        if isinstance(data, str):
            await self._ws.send_str(data)
        else:
            await self._ws.send_bytes(data)

    async def recv(self, timeout: float = 5.0) -> Union[str, bytes]:
        """Receive one data frame; raise WSClosed if the connection closes."""
        msg = await asyncio.wait_for(self._ws.receive(), timeout)
        if msg.type == aiohttp.WSMsgType.TEXT:
            return msg.data
        if msg.type == aiohttp.WSMsgType.BINARY:
            return msg.data
        if msg.type in (
            aiohttp.WSMsgType.CLOSE,
            aiohttp.WSMsgType.CLOSING,
            aiohttp.WSMsgType.CLOSED,
        ):
            raise WSClosed(self._ws.close_code)
        raise AssertionError(f"unexpected websocket message: {msg!r}")

    async def close(self):
        """Close politely and release the HTTP session."""
        await self._ws.close()
        await self._session.close()

    async def abort(self):
        """Drop the TCP connection without a websocket close handshake."""
        self._ws._response.close()  # no public API for an abrupt close
        await self._session.close()
