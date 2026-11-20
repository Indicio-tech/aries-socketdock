"""End-to-end behavior of SocketDock with the HTTP backend.

The mock backend records what SocketDock posts to it; websocket clients and
backend callbacks exercise both directions of the relay.
"""

import asyncio

import pytest
from helpers import WSClosed

pytestmark = pytest.mark.asyncio

MAX_MESSAGE_BYTES = 2**22  # server-side WEBSOCKET_MAX_SIZE
TEXT = {"content-type": "text/plain"}

# Known bug (still present on sanic 25.12; fixed in a later PR in this stack):
# when a connection closes, sanic can cancel the handler task, so the
# `await backend.socket_disconnected(...)` in api.socket_handler's `finally`
# raises CancelledError before the HTTP request is sent and the backend
# is never told the client left. Whether it is lost depends on timing for every
# close path (clean client close, backend-initiated disconnect, abrupt drop): it
# is usually lost, but under CPU contention the notification can get through.
# Tests that assert the notification is delivered are marked below; remove the
# markers when fixed. The marker is deliberately non-strict, because an
# unexpected pass is a timing outcome here, not a sign the bug is fixed.
disconnect_lost_on_clean_close = pytest.mark.xfail(
    strict=False,
    reason="disconnect notification can be cancelled on a clean client close (still present on sanic 25.12; fixed in a later PR in this stack)",
)
disconnect_lost_sometimes = pytest.mark.xfail(
    strict=False,
    reason="disconnect notification can be cancelled depending on timing (still present on sanic 25.12; fixed in a later PR in this stack)",
)


@pytest.fixture
def connect(http_server, mock_backend, ws_connect):
    """Open a websocket and return (ws, connection_id) from the connect callback."""

    async def _connect(headers=None):
        before = len(mock_backend.requests["connect"])
        ws = await ws_connect(http_server.ws_url, headers=headers)
        connects = await mock_backend.wait_for("connect", before + 1)
        return ws, connects[before]["meta"]["connection_id"]

    return _connect


# --- client -> backend -------------------------------------------------------


async def test_connect_notifies_backend_with_callbacks_and_headers(
    http_server, mock_backend, connect
):
    _, conn_id = await connect(headers={"X-Test-Header": "abc123"})
    (notice,) = mock_backend.requests["connect"]

    meta = notice["meta"]
    assert meta["connection_id"] == conn_id and conn_id
    assert meta["send"] == f"{http_server.http_url}/socket/{conn_id}/send"
    assert meta["disconnect"] == f"{http_server.http_url}/socket/{conn_id}/disconnect"
    headers = {k.lower(): v for k, v in meta["headers"].items()}
    assert headers["x-test-header"] == "abc123"


async def test_text_message_is_forwarded_as_string(http_server, mock_backend, connect):
    ws, conn_id = await connect()
    await ws.send("hello backend")
    (msg,) = await mock_backend.wait_for("message")

    assert msg["message"] == "hello backend"
    assert msg["meta"]["connection_id"] == conn_id
    assert msg["meta"]["send"].endswith(f"/socket/{conn_id}/send")
    assert msg["meta"]["disconnect"].endswith(f"/socket/{conn_id}/disconnect")


async def test_binary_message_is_forwarded_as_utf8_string(mock_backend, connect):
    ws, _ = await connect()
    await ws.send("héllo wörld ✓".encode("utf-8"))
    (msg,) = await mock_backend.wait_for("message")

    assert msg["message"] == "héllo wörld ✓"


async def test_unicode_and_json_text_pass_through_unmodified(mock_backend, connect):
    payload = '{"a": [1, 2, {"b": "ünï"}], "emoji": "🔌"}'
    ws, _ = await connect()
    await ws.send(payload)
    (msg,) = await mock_backend.wait_for("message")

    assert msg["message"] == payload


async def test_message_order_is_preserved(mock_backend, connect):
    ws, _ = await connect()
    for i in range(20):
        await ws.send(str(i))
    msgs = await mock_backend.wait_for("message", 20)

    assert [m["message"] for m in msgs] == [str(i) for i in range(20)]


async def test_empty_message_is_not_forwarded(mock_backend, connect):
    ws, _ = await connect()
    await ws.send("")
    await ws.send("real")
    await mock_backend.wait_for("message")
    await asyncio.sleep(0.3)

    assert [m["message"] for m in mock_backend.requests["message"]] == ["real"]


async def test_large_message_under_limit_is_forwarded(mock_backend, connect):
    big = "x" * (MAX_MESSAGE_BYTES // 2)
    ws, _ = await connect()
    await ws.send(big)
    (msg,) = await mock_backend.wait_for("message", timeout=15)

    assert msg["message"] == big


async def test_message_over_limit_is_rejected(http_server, mock_backend, connect):
    ws, _ = await connect()
    try:
        await ws.send("x" * (MAX_MESSAGE_BYTES + 1024))
    except ConnectionError:
        pass  # server may drop the connection while we are still sending
    with pytest.raises((WSClosed, ConnectionError)):
        await ws.recv(timeout=10)

    assert mock_backend.requests["message"] == []
    assert http_server.is_running()
    # The server keeps accepting new connections afterwards.
    ws2, _ = await connect()
    await ws2.send("ok")
    await mock_backend.wait_for("message")


# --- backend -> client -------------------------------------------------------


async def test_backend_can_send_text_to_client(http_server, connect):
    ws, conn_id = await connect()
    resp = await http_server.post(
        f"/socket/{conn_id}/send", data="from backend", headers=TEXT
    )
    assert (resp.status, resp.body) == (200, "OK")
    assert await ws.recv() == "from backend"


async def test_backend_can_send_binary_to_client(http_server, connect):
    ws, conn_id = await connect()
    resp = await http_server.post(
        f"/socket/{conn_id}/send",
        data=b"\x00\x01\x02\xff",
        headers={"content-type": "application/octet-stream"},
    )
    assert resp.status == 200
    assert await ws.recv() == b"\x00\x01\x02\xff"


async def test_send_is_routed_to_the_right_connection(http_server, connect):
    ws_a, id_a = await connect()
    ws_b, id_b = await connect()
    assert id_a != id_b

    await http_server.post(f"/socket/{id_b}/send", data="for b", headers=TEXT)
    assert await ws_b.recv() == "for b"
    with pytest.raises(asyncio.TimeoutError):
        await ws_a.recv(timeout=0.5)


async def test_messages_are_attributed_to_the_right_connection(mock_backend, connect):
    ws_a, id_a = await connect()
    ws_b, id_b = await connect()
    await ws_a.send("from a")
    await ws_b.send("from b")
    msgs = await mock_backend.wait_for("message", 2)

    by_conn = {m["meta"]["connection_id"]: m["message"] for m in msgs}
    assert by_conn == {id_a: "from a", id_b: "from b"}


async def test_send_with_other_content_type_is_delivered_as_binary(http_server, connect):
    ws, conn_id = await connect()
    await http_server.post(
        f"/socket/{conn_id}/send",
        data="json-ish",
        headers={"content-type": "application/json"},
    )
    assert await ws.recv() == b"json-ish"


async def test_send_without_content_type_is_delivered_as_binary(
    http_server, mock_backend, connect
):
    ws, conn_id = await connect()
    # No body and no content-type header.
    resp = await http_server.post(f"/socket/{conn_id}/send")
    assert resp.status == 200
    assert await ws.recv() == b""
    # The connection keeps working.
    await ws.send("still alive")
    await mock_backend.wait_for("message")


# --- disconnect handling -----------------------------------------------------


@disconnect_lost_on_clean_close
async def test_client_close_notifies_backend(mock_backend, connect):
    ws, conn_id = await connect()
    await ws.close()
    (disc,) = await mock_backend.wait_for("disconnect", timeout=2)
    assert disc == {"connection_id": conn_id}


@disconnect_lost_on_clean_close
async def test_connection_is_unregistered_after_close(http_server, mock_backend, connect):
    ws, conn_id = await connect()
    await ws.close()
    await mock_backend.wait_for("disconnect", timeout=2)

    resp = await http_server.post(f"/socket/{conn_id}/send", data="late", headers=TEXT)
    assert resp.status >= 400


async def test_closed_connection_cannot_receive_sends(http_server, mock_backend, connect):
    """Independent of notification delivery: a closed socket is unregistered."""
    ws, conn_id = await connect()
    await ws.close()
    for _ in range(50):
        resp = await http_server.post(
            f"/socket/{conn_id}/send", data="late", headers=TEXT
        )
        if resp.status >= 400:
            return
        await asyncio.sleep(0.1)
    pytest.fail("connection was still registered 5s after the client closed")


async def test_backend_can_disconnect_a_client(http_server, connect):
    ws, conn_id = await connect()
    resp = await http_server.post(f"/socket/{conn_id}/disconnect")
    assert (resp.status, resp.body) == (200, "OK")
    with pytest.raises(WSClosed):
        await ws.recv()


@disconnect_lost_sometimes
async def test_backend_initiated_disconnect_notifies_backend(
    http_server, mock_backend, connect
):
    ws, conn_id = await connect()
    await http_server.post(f"/socket/{conn_id}/disconnect")
    with pytest.raises(WSClosed):
        await ws.recv()

    (disc,) = await mock_backend.wait_for("disconnect", timeout=2)
    assert disc["connection_id"] == conn_id


@disconnect_lost_sometimes
async def test_abrupt_client_drop_still_notifies_backend(
    http_server, mock_backend, connect
):
    ws, conn_id = await connect()
    await ws.abort()
    (disc,) = await mock_backend.wait_for("disconnect", timeout=2)
    assert disc["connection_id"] == conn_id
    assert http_server.is_running()


# --- resilience --------------------------------------------------------------


async def test_backend_error_response_does_not_drop_the_socket(mock_backend, connect):
    mock_backend.status["message"] = 500
    ws, _ = await connect()
    await ws.send("first")
    await mock_backend.wait_for("message", 1)
    mock_backend.status["message"] = 200
    await ws.send("second")
    msgs = await mock_backend.wait_for("message", 2)

    assert [m["message"] for m in msgs] == ["first", "second"]


async def test_connect_error_response_does_not_drop_the_socket(mock_backend, connect):
    mock_backend.status["connect"] = 503
    ws, _ = await connect()
    await ws.send("hello")
    await mock_backend.wait_for("message")


async def test_non_utf8_binary_does_not_take_the_server_down(
    http_server, mock_backend, connect
):
    ws, _ = await connect()
    await ws.send(b"\xff\xfe\xfd")
    await asyncio.sleep(0.5)
    await ws.close()

    assert http_server.is_running()
    ws2, _ = await connect()
    await ws2.send("healthy")
    msgs = await mock_backend.wait_for("message")
    assert msgs[-1]["message"] == "healthy"


async def test_server_survives_unreachable_backend(start_server, ws_connect):
    dead = "http://127.0.0.1:9"  # nothing listens here
    server = await start_server(
        "http",
        "--connect-uri",
        f"{dead}/connect",
        "--message-uri",
        f"{dead}/message",
        "--disconnect-uri",
        f"{dead}/disconnect",
    )
    ws = await ws_connect(server.ws_url)
    await ws.send("anyone there?")
    await asyncio.sleep(0.5)
    await ws.close()
    await asyncio.sleep(0.3)

    assert server.is_running()
    ws2 = await ws_connect(server.ws_url)
    await ws2.close()


async def test_without_connect_uri_no_connect_notification_is_sent(
    start_server, mock_backend, ws_connect
):
    server = await start_server(
        "http",
        "--message-uri",
        mock_backend.uri("message"),
        "--disconnect-uri",
        mock_backend.uri("disconnect"),
    )
    ws = await ws_connect(server.ws_url)
    await ws.send("hi")
    await mock_backend.wait_for("message")
    await ws.close()

    await mock_backend.assert_none_for("connect")
