"""HTTP backend for SocketDock."""

import logging
from typing import Union

import aiohttp
from sanic import Request

from .backend import Backend


LOGGER = logging.getLogger(__name__)


class HTTPBackend(Backend):
    """HTTP backend for SocketDock."""

    def __init__(self, forward_uri: str, connect_uri: str, message_uri: str, disconnect_uri: str):
        """Initialize HTTP backend."""
        self._forward_uri = forward_uri
        self._connect_uri = connect_uri
        self._message_uri = message_uri
        self._disconnect_uri = disconnect_uri

    async def socket_connected(
        self, callback_uris: dict
    ):
        """Handle inbound socket message, with calback provided."""

        http_body = {
            "meta": callback_uris,
        }

        if self._connect_uri:
            async with aiohttp.ClientSession() as session:
                LOGGER.info("Posting message %s to %s", http_body, self._connect_uri)
                async with session.post(self._connect_uri, json=http_body) as resp:
                    response = await resp.text()
                    if resp.status != 200:
                        LOGGER.error("Error posting message: %s", response)
                    else:
                        LOGGER.debug("Response: %s", response)

    async def inbound_socket_message(
        self, callback_uris: dict, message: Union[str, bytes]
    ):
        """Handle inbound socket message, with calback provided."""

        http_body = {
            "meta": callback_uris,
            "message": message.decode("utf-8")
            if isinstance(message, bytes)
            else message,
        }

        async with aiohttp.ClientSession() as session:
            LOGGER.info("Posting message %s to %s", http_body, self._message_uri)
            async with session.post(self._message_uri, json=http_body) as resp:
                response = await resp.text()
                if resp.status != 200:
                    LOGGER.error("Error posting message: %s", response)
                else:
                    LOGGER.debug("Response: %s", response)

    async def socket_disconnected(self, bundle: dict):
        """Handle socket disconnected."""

        async with aiohttp.ClientSession() as session:
            LOGGER.info("Notifying of disconnect: %s %s", self._disconnect_uri, bundle)
            async with session.post(self._disconnect_uri, json=bundle) as resp:
                response = await resp.text()
                if resp.status != 200:
                    LOGGER.error("Error posting to disconnect uri: %s", response)
                else:
                    LOGGER.debug("Response: %s", response)

    async def forward_request(self, method: str, forward: Request, forward_url:str, callback_uris: dict):
        """Handle forwarding HTTP request."""
        
        url = '%s%s' % (self._forward_uri, forward_url)
        headers = {**dict(forward.headers), **callback_uris}
        headers = {k.lower(): v for k, v in headers.items()}

        if 'content-encoding' in headers:
            del headers['content-encoding']

        async with aiohttp.ClientSession() as session:
            LOGGER.info("Forwarding request: %s %s", url, forward)
            async with session.request(
                    method, 
                    url, 
                    headers=headers, 
                    params=forward.query_args, 
                    data=forward.body) as resp:
                if resp.status != 200:
                    response = await resp.text()
                    LOGGER.error("Error forwarding request: %s", response)

                headers = dict(resp.headers)
                headers = {k.lower(): v for k, v in headers.items()}

                if 'content-encoding' in headers:
                    del headers['content-encoding']
                if 'content-length' in headers:
                    del headers['content-length']
                if 'connection' in headers:
                    del headers['connection']
                if 'transfer-encoding' in headers:
                    del headers['transfer-encoding']

                return {
                    "headers": headers,
                    "body": await resp.content.read(),
                    "status": resp.status,
                }