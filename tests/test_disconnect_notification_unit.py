"""In-process tests for the disconnect notification helpers.

The e2e tests rely on sanic cancelling the handler when a socket closes. These
tests do not: they cancel the awaiting task themselves, so they guard the
shielding regardless of how sanic behaves.
"""

import asyncio
import logging

import pytest

from socketdock import api
from socketdock.httpbackend import _redact_uri


class FakeBackend:
    """Records disconnect calls; optionally blocks or fails."""

    def __init__(self, delay: float = 0.0, error: Exception | None = None):
        self.delay = delay
        self.error = error
        self.calls: list[str] = []
        self.finished: list[str] = []

    async def socket_disconnected(self, socket_id: str):
        self.calls.append(socket_id)
        await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        self.finished.append(socket_id)


@pytest.mark.asyncio
async def test_notification_completes_when_the_caller_is_cancelled():
    backend = FakeBackend(delay=0.2)
    caller = asyncio.create_task(api._notify_disconnect_shielded(backend, "abc"))
    await asyncio.sleep(0.05)  # the notification is now in flight
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    await asyncio.sleep(0.4)
    assert backend.finished == ["abc"]


@pytest.mark.asyncio
async def test_notification_survives_repeated_cancellation():
    backend = FakeBackend(delay=0.2)
    caller = asyncio.create_task(api._notify_disconnect_shielded(backend, "abc"))
    await asyncio.sleep(0.05)
    caller.cancel()
    await asyncio.sleep(0.01)
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    await asyncio.sleep(0.4)
    assert backend.finished == ["abc"]


@pytest.mark.asyncio
async def test_notification_is_sent_exactly_once():
    backend = FakeBackend()
    await api._notify_disconnect_shielded(backend, "abc")
    await asyncio.sleep(0.05)
    assert backend.calls == ["abc"]


@pytest.mark.asyncio
async def test_failure_is_logged_not_raised(caplog):
    backend = FakeBackend(error=RuntimeError("boom"))
    with caplog.at_level(logging.ERROR, logger=api.LOGGER.name):
        await api._notify_disconnect_shielded(backend, "abc")
    assert "Failed to notify backend of disconnect: abc" in caplog.text


@pytest.mark.asyncio
async def test_hanging_backend_is_bounded_by_the_timeout(monkeypatch, caplog):
    monkeypatch.setattr(api, "DISCONNECT_NOTIFY_TIMEOUT", 0.1)
    backend = FakeBackend(delay=30)
    with caplog.at_level(logging.ERROR, logger=api.LOGGER.name):
        await asyncio.wait_for(api._notify_disconnect_shielded(backend, "abc"), 2)
    assert backend.finished == []
    assert "Failed to notify backend of disconnect: abc" in caplog.text
    assert not api._disconnect_tasks  # no task left behind


@pytest.mark.parametrize(
    "uri,expected",
    [
        ("http://example.com/hook", "http://example.com/hook"),
        (
            "https://user:secret@example.com:8443/hook?token=abc",
            "https://example.com:8443/hook",
        ),
        ("http://127.0.0.1:9/disconnect", "http://127.0.0.1:9/disconnect"),
    ],
)
def test_logged_uris_carry_no_credentials_or_query(uri, expected):
    assert _redact_uri(uri) == expected
