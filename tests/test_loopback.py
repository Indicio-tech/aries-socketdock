"""Behavior of SocketDock with the built-in loopback backend."""

import asyncio

import pytest

pytestmark = pytest.mark.asyncio


def as_text(data) -> str:
    """Normalize a received frame: the loopback reply may arrive as text or bytes."""
    return data.decode() if isinstance(data, bytes) else data


async def test_loopback_replies_to_a_message(loopback_server, ws_connect):
    ws = await ws_connect(loopback_server.ws_url)
    await ws.send("hello")
    assert as_text(await ws.recv()) == "Hello yourself"


async def test_loopback_handles_many_messages_on_one_connection(
    loopback_server, ws_connect
):
    ws = await ws_connect(loopback_server.ws_url)
    for i in range(10):
        await ws.send(f"msg {i}")
        assert as_text(await ws.recv()) == "Hello yourself"


async def test_loopback_serves_concurrent_clients(loopback_server, ws_connect):
    async def one_client(n: int) -> str:
        ws = await ws_connect(loopback_server.ws_url)
        await ws.send(f"client {n}")
        return as_text(await ws.recv())

    replies = await asyncio.gather(*(one_client(n) for n in range(5)))
    assert replies == ["Hello yourself"] * 5


async def test_loopback_reply_is_a_text_frame(loopback_server, ws_connect):
    ws = await ws_connect(loopback_server.ws_url)
    await ws.send("hello")
    assert isinstance(await ws.recv(), str)


async def test_status_endpoint_is_not_exposed(loopback_server):
    """/status is disabled until route authorization exists.

    If this starts failing, someone re-enabled an unauthenticated endpoint.
    """
    resp = await loopback_server.get("/status")
    assert resp.status == 404


async def test_unknown_routes_return_404(loopback_server):
    assert (await loopback_server.get("/nope")).status == 404
    assert (await loopback_server.post("/socket")).status == 404


async def test_send_to_unknown_connection_fails(loopback_server):
    resp = await loopback_server.post(
        "/socket/doesnotexist/send", data="hi", headers={"content-type": "text/plain"}
    )
    # Current behavior is 500 "FAIL". Assert only that it is an error so a future
    # switch to 404 does not break the suite.
    assert resp.status >= 400


async def test_disconnect_unknown_connection_fails(loopback_server):
    resp = await loopback_server.post("/socket/doesnotexist/disconnect")
    assert resp.status >= 400


async def test_server_survives_abrupt_client_disconnect(loopback_server, ws_connect):
    ws = await ws_connect(loopback_server.ws_url)
    await ws.send("hi")
    await ws.recv()
    await ws.abort()
    await asyncio.sleep(0.3)

    assert loopback_server.is_running()
    ws2 = await ws_connect(loopback_server.ws_url)
    await ws2.send("again")
    assert as_text(await ws2.recv()) == "Hello yourself"
