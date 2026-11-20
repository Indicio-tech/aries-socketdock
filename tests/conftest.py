"""Shared fixtures for SocketDock tests.

These tests are deliberately black-box: they start the real server as a
subprocess (``python -m socketdock``) and talk to it over real websocket and
HTTP connections. That keeps them meaningful across dependency upgrades
(sanic, aiohttp, websockets) because nothing here imports the framework
internals.
"""

import asyncio
import os
import socket
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

import aiohttp
import pytest_asyncio
from aiohttp import web

from helpers import WSClient

REPO_ROOT = Path(__file__).resolve().parent.parent
LOG_CONFIG = REPO_ROOT / "socketdock" / "config" / "logging-config.yml"
STARTUP_TIMEOUT = 20.0


def free_port() -> int:
    """Ask the OS for an unused TCP port."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class MockBackend:
    """In-process HTTP backend that records what SocketDock sends it."""

    KINDS = ("connect", "message", "disconnect")

    def __init__(self):
        """Create an empty recorder; every endpoint answers 200 by default."""
        self.requests: Dict[str, List[dict]] = {kind: [] for kind in self.KINDS}
        self.status: Dict[str, int] = {kind: 200 for kind in self.KINDS}
        self._runner: Optional[web.AppRunner] = None
        self.base_url = ""

    def _handler(self, kind: str):
        async def handle(request: web.Request) -> web.Response:
            self.requests[kind].append(await request.json())
            return web.Response(status=self.status[kind], text="mock")

        return handle

    async def start(self):
        """Start listening on an ephemeral port."""
        # Default aiohttp limit is 1 MiB, below SocketDock's 4 MiB websocket limit.
        app = web.Application(client_max_size=2**26)
        for kind in self.KINDS:
            app.router.add_post(f"/{kind}", self._handler(kind))
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        port = self._runner.addresses[0][1]
        self.base_url = f"http://127.0.0.1:{port}"

    async def stop(self):
        """Stop the mock server."""
        if self._runner:
            await self._runner.cleanup()

    def uri(self, kind: str) -> str:
        """Return the URL SocketDock should be configured with for ``kind``."""
        return f"{self.base_url}/{kind}"

    async def wait_for(self, kind: str, count: int = 1, timeout: float = 5.0):
        """Wait until at least ``count`` requests of ``kind`` have arrived."""
        deadline = asyncio.get_running_loop().time() + timeout
        while len(self.requests[kind]) < count:
            if asyncio.get_running_loop().time() > deadline:
                raise AssertionError(
                    f"timed out waiting for {count} {kind} request(s); "
                    f"got {len(self.requests[kind])}: {self.requests[kind]}"
                )
            await asyncio.sleep(0.02)
        return self.requests[kind]

    async def assert_none_for(self, kind: str, duration: float = 0.5):
        """Assert that no request of ``kind`` arrives within ``duration``."""
        await asyncio.sleep(duration)
        assert self.requests[kind] == []


class Server:
    """Handle on a running SocketDock subprocess."""

    def __init__(self, port: int, process: subprocess.Popen, log_path: str):
        """Wrap a running process."""
        self.port = port
        self.process = process
        self.log_path = log_path
        self.http_url = f"http://127.0.0.1:{port}"
        self.ws_url = f"ws://127.0.0.1:{port}/ws"

    def logs(self) -> str:
        """Return everything the server has written so far."""
        return Path(self.log_path).read_text(errors="replace")

    def is_running(self) -> bool:
        """Return True while the subprocess is alive."""
        return self.process.poll() is None

    async def post(self, path: str, **kwargs) -> "_Response":
        """POST to the server and return a simple (status, body) object."""
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{self.http_url}{path}", **kwargs) as resp:
                body = await resp.text()
                return _Response(resp.status, body)

    async def get(self, path: str) -> "_Response":
        """GET from the server."""
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{self.http_url}{path}") as resp:
                return _Response(resp.status, await resp.text())


class _Response:
    def __init__(self, status: int, body: str):
        self.status = status
        self.body = body


async def _wait_until_listening(server: Server):
    deadline = asyncio.get_running_loop().time() + STARTUP_TIMEOUT
    while True:
        if not server.is_running():
            raise RuntimeError(
                f"SocketDock exited during startup (code {server.process.returncode}):\n"
                f"{server.logs()}"
            )
        try:
            _, writer = await asyncio.open_connection("127.0.0.1", server.port)
            writer.close()
            await writer.wait_closed()
            return
        except OSError:
            if asyncio.get_running_loop().time() > deadline:
                server.process.kill()
                server.process.wait()
                raise RuntimeError(
                    f"SocketDock did not start listening:\n{server.logs()}"
                )
            await asyncio.sleep(0.1)


@pytest_asyncio.fixture
async def mock_backend():
    """Run an in-process backend that records requests from SocketDock."""
    backend = MockBackend()
    await backend.start()
    yield backend
    await backend.stop()


@pytest_asyncio.fixture
async def start_server():
    """Return a factory that launches SocketDock; all servers stop at teardown."""
    servers: List[Server] = []

    async def factory(backend: str = "loopback", *extra_args: str) -> Server:
        port = free_port()
        log = tempfile.NamedTemporaryFile(
            prefix="socketdock-", suffix=".log", delete=False
        )
        cmd = [
            sys.executable,
            "-m",
            "socketdock",
            "--bindip",
            "127.0.0.1",
            "--bindport",
            str(port),
            "--endpoint",
            f"http://127.0.0.1:{port}",
            "--backend",
            backend,
            "--log-config",
            str(LOG_CONFIG),
            *extra_args,
        ]
        process = subprocess.Popen(
            cmd, cwd=REPO_ROOT, stdout=log, stderr=subprocess.STDOUT, env=os.environ
        )
        log.close()  # the child holds its own descriptor
        server = Server(port, process, log.name)
        servers.append(server)
        await _wait_until_listening(server)
        return server

    yield factory

    for server in servers:
        if server.is_running():
            server.process.terminate()
            try:
                server.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.process.kill()
                server.process.wait()
        try:
            os.unlink(server.log_path)
        except OSError:
            pass


@pytest_asyncio.fixture
async def loopback_server(start_server):
    """A SocketDock using the built-in loopback backend."""
    return await start_server("loopback")


@pytest_asyncio.fixture
async def http_server(start_server, mock_backend):
    """A SocketDock wired to the recording mock backend (all three URIs set)."""
    server = await start_server(
        "http",
        "--connect-uri",
        mock_backend.uri("connect"),
        "--message-uri",
        mock_backend.uri("message"),
        "--disconnect-uri",
        mock_backend.uri("disconnect"),
    )
    return server


@pytest_asyncio.fixture
async def ws_connect():
    """Return a factory for websocket clients; all are cleaned up at teardown."""
    clients: List[WSClient] = []

    async def factory(url: str, headers: Optional[Dict[str, str]] = None) -> WSClient:
        client = await WSClient.connect(url, headers=headers)
        clients.append(client)
        return client

    yield factory

    for client in clients:
        try:
            await client.close()
        except Exception:
            pass
