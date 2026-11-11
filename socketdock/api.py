"""API endpoints for the server."""

import asyncio
from contextvars import ContextVar
import logging
import time

from sanic import Blueprint, Request, Websocket, json, text

from .backend import Backend

backend_var: ContextVar[Backend] = ContextVar("backend")

api = Blueprint("api", url_prefix="/")

LOGGER = logging.getLogger(__name__)

LAUNCH_TIME = time.time_ns()
# TODO: track timestamp of connections when connected
active_connections = {}
lifetime_connections = 0

# Strong references to in-flight disconnect notifications; asyncio only keeps
# weak references to tasks, so an unreferenced task could be collected mid-flight.
_disconnect_tasks: set = set()


async def _notify_disconnect(backend: Backend, socket_id: str):
    """Tell the backend a socket is gone, logging (not raising) any failure."""
    try:
        await backend.socket_disconnected(socket_id)
    except Exception:
        LOGGER.exception("Failed to notify backend of disconnect: %s", socket_id)


async def _notify_disconnect_shielded(backend: Backend, socket_id: str):
    """Send the disconnect notification even if this handler is cancelled.

    When a websocket closes, sanic may cancel the handler task. A plain
    ``await`` in the handler's ``finally`` would then be cancelled before the
    notification is sent, and the backend would never learn the client left.
    Running it as its own task, shielded from the handler's cancellation, lets
    it finish regardless.
    """
    task = asyncio.ensure_future(_notify_disconnect(backend, socket_id))
    _disconnect_tasks.add(task)
    task.add_done_callback(_disconnect_tasks.discard)
    await asyncio.shield(task)


# Temporarily disabled due to lack of authorization
# TODO: Add route authorization controls
# @api.get("/status")
async def status_handler(request: Request):
    """Return status information about the server."""
    uptime = time.time_ns() - LAUNCH_TIME
    return json(
        {
            "uptime": {
                "ns": uptime,
                "seconds": int(uptime / 1000000000),  # ns -> second conversion
            },
            "connections": {
                "active": len(active_connections),
                "lifetime": lifetime_connections,
            },
        }
    )


@api.post("/socket/<connectionid>/send")
async def socket_send(request: Request, connectionid: str):
    """Send a message to a connected socket."""
    LOGGER.info("Inbound message for %s", connectionid)
    LOGGER.debug("Existing connections: %s", active_connections.keys())

    if connectionid not in active_connections:
        return text("FAIL", status=500)

    socket = active_connections[connectionid]
    if request.headers["content-type"] == "text/plain":
        await socket.send(request.body.decode())
    else:
        await socket.send(request.body)
    return text("OK")


@api.post("/socket/<connectionid>/disconnect")
async def socket_disconnect(request: Request, connectionid: str):
    """Disconnect a socket."""
    LOGGER.info("Disconnect %s", connectionid)
    LOGGER.debug("Existing connections: %s", active_connections.keys())

    if connectionid not in active_connections:
        return text("FAIL", status=500)

    socket = active_connections[connectionid]
    await socket.close()
    return text("OK")


@api.websocket("/ws")
async def socket_handler(request: Request, websocket: Websocket):
    """Handle a new websocket connection."""
    global lifetime_connections
    backend = backend_var.get()
    socket_id = None
    try:
        # register user
        LOGGER.info("new client connected")
        socket_id = websocket.ws_proto.id.hex
        active_connections[socket_id] = websocket
        lifetime_connections += 1
        LOGGER.debug("Existing connections: %s", active_connections.keys())
        LOGGER.debug("Added connection: %s", socket_id)
        LOGGER.debug("Request headers: %s", dict(request.headers.items()))

        await backend.socket_connected(
            connection_id=socket_id,
            headers=dict(request.headers.items()),
        )

        async for message in websocket:
            if message:
                await backend.inbound_socket_message(
                    connection_id=socket_id,
                    message=message,
                )
            else:
                LOGGER.warning("empty message received")

    finally:
        # unregister user
        if socket_id:
            del active_connections[socket_id]
            LOGGER.info("Removed connection: %s", socket_id)
            await _notify_disconnect_shielded(backend, socket_id)
