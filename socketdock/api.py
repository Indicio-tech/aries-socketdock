"""API endpoints for the server."""

from contextvars import ContextVar
import logging
import time
import uuid

import aiohttp
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
    LOGGER.info("Post connections: %s", post_connections.keys())

    await post_connections[connectionid].put(request.body)

    return text("OK")

@api.post("/post/<path:path>")
async def post(request: Request, path: str):
    post_id = str(uuid.uuid4())
    backend = backend_var.get()

    LOGGER.info("Inbound message for %s", path)
    LOGGER.info("Inbound message id %s", post_id)
    LOGGER.info("Inbound args: %s", request.args)
    LOGGER.info("Inbound headers: %s", request.headers)

    timeout = 60
    if 'timeout' in request.args:
        timeout = int(request.args['timeout'][0])

    if timeout < 0:
        timeout = 0
    if timeout > 600:
        timeout = 600

    LOGGER.info("Timeout: %s", timeout)

    # We use a queue to pass messages between us and incoming requests
    queue = asyncio.Queue()
    post_connections[post_id] = queue

    LOGGER.info("Post connections: %s", post_connections.keys())

    callback_uris = {
        "postdock-send": f"{endpoint_var.get()}/post/{post_id}/send",
    }

    forward_url = request.path.replace('/post','',1)

    forward_result = await backend.forward_request('POST', request, forward_url, callback_uris)

    # only wait if we get a 200 status
    if forward_result['status'] == 200:
        try:
            result_updated = await asyncio.wait_for(queue.get(), timeout=timeout)
            LOGGER.info(result_updated)
            forward_result['body'] = result_updated
        except:
            # Provide default response
            LOGGER.info("No update to post, timeout reached, returning default values...")
            pass

    del post_connections[post_id]

    # Set content_type
    content_type = 'text/html; charset=utf-8'
    if 'content-type' in forward_result['headers']:
        content_type = forward_result['headers']['content-type']

    response = await request.respond(headers=forward_result['headers'], status=forward_result['status'], content_type=content_type)
    await response.send(forward_result['body'])
    await response.eof()


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
