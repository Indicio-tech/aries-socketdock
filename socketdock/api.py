"""API endpoints for the server."""

from contextvars import ContextVar
import logging
import time

import asyncio

from sanic import Blueprint, Request, Websocket, json, text

from .backend import Backend

backend_var: ContextVar[Backend] = ContextVar("backend")
endpoint_var: ContextVar[str] = ContextVar("endpoint")

api = Blueprint("api", url_prefix="/")

LOGGER = logging.getLogger(__name__)

LAUNCH_TIME = time.time_ns()
# TODO: track timestamp of connections when connected
active_connections = {}

post_connections = {}

lifetime_connections = 0


@api.get("/status")
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

@api.post("/post/<connectionid>/send")
async def post_send(request: Request, connectionid: str):
    await post_connections['abc'].put('bob')

    return text("OK")

@api.post("/post/<path:path>")
async def post(request: Request, path: str):
    LOGGER.info("Inbound message for %s", path)
    LOGGER.info("Inbound args: %s", request.args)
    LOGGER.info("Inbound body: %s", request.body)
    LOGGER.info("Inbound headers: %s", request.headers)

    queue = asyncio.Queue()

    post_connections['abc'] = queue

    try:
        result = await asyncio.wait_for(queue.get(), timeout=60.0)
        LOGGER.info(result)
    except:
        # Provide default response
        LOGGER.info("Timeout...")
        pass

    del post_connections['abc']

    return text("OK")


@api.post("/socket/<connectionid>/send")
async def socket_send(request: Request, connectionid: str):
    """Send a message to a connected socket."""
    LOGGER.info("Inbound message for %s", connectionid)
    LOGGER.info("Existing connections: %s", active_connections.keys())

    if connectionid not in active_connections:
        return text("FAIL", status=500)

    socket = active_connections[connectionid]
    await socket.send(request.body)
    return text("OK")

@api.post("/socket/<connectionid>/send-text")
async def socket_send_text(request: Request, connectionid: str):
    """Send a text message to a connected socket."""
    LOGGER.info("Inbound message for %s", connectionid)
    LOGGER.info("Existing connections: %s", active_connections.keys())

    if connectionid not in active_connections:
        return text("FAIL", status=500)

    socket = active_connections[connectionid]
    await socket.send(request.json["text"])
    return text("OK")


@api.post("/socket/<connectionid>/disconnect")
async def socket_disconnect(request: Request, connectionid: str):
    """Disconnect a socket."""
    LOGGER.info("Disconnect %s", connectionid)
    LOGGER.info("Existing connections: %s", active_connections.keys())

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
        socket_id = websocket.connection.id.hex
        active_connections[socket_id] = websocket
        lifetime_connections += 1
        LOGGER.info("Existing connections: %s", active_connections.keys())
        LOGGER.info("Added connection: %s", socket_id)
        LOGGER.info("Request headers: %s", dict(request.headers.items()))

        await backend.socket_connected(
                {
                "connection_id": socket_id,
                "headers": dict(request.headers.items()),
                "send": f"{endpoint_var.get()}/socket/{socket_id}/send",
                "send-text": f"{endpoint_var.get()}/socket/{socket_id}/send-text",
                "disconnect": f"{endpoint_var.get()}/socket/{socket_id}/disconnect",
                },
        )

        async for message in websocket:
            if message:
                await backend.inbound_socket_message(
                    {
                        "connection_id": socket_id,
                        "send": f"{endpoint_var.get()}/socket/{socket_id}/send",
                        "send-text": f"{endpoint_var.get()}/socket/{socket_id}/send-text", 
                        "disconnect": f"{endpoint_var.get()}/socket/{socket_id}/disconnect",                       
                    },
                    message,
                )
            else:
                LOGGER.warning("empty message received")

    finally:
        # unregister user
        if socket_id:
            del active_connections[socket_id]
            LOGGER.info("Removed connection: %s", socket_id)
            await backend.socket_disconnected({"connection_id": socket_id})
