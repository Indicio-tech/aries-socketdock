from locust import events
from locust.exception import StopUser
from flask import Flask, request

import os
import threading
import time
import urllib.request

import gevent
from gevent.pywsgi import WSGIServer
from gevent.lock import RLock
import websocket

# URL SocketDock is reachable at from the locust container.
SOCKETDOCK_WS_URL = os.getenv("SOCKETDOCK_WS_URL", "ws://websocket-gateway:8765/ws")
# How long a simulated user waits for its websocket to open before giving up.
CONNECT_TIMEOUT = float(os.getenv("LOCUST_CONNECT_TIMEOUT", 10))
# How long to wait at test stop for replies still in flight.
REPLY_GRACE = float(os.getenv("LOCUST_REPLY_GRACE", 2))
# How long a stopping user waits before closing its socket, so a message sent in
# its last moments is relayed rather than cut off by the close.
CLOSE_GRACE = float(os.getenv("LOCUST_CLOSE_GRACE", 1))


class Stats:
    """Counters shared by every simulated user and the mock backend."""

    def __init__(self):
        self._lock = threading.Lock()
        self.reset()

    def reset(self):
        with self._lock:
            self.sent = 0  # websocket messages written by simulated users
            self.relayed = 0  # messages SocketDock POSTed to /message
            self.replied = 0  # replies that came back over the websocket
            self.connects = 0
            self.disconnects = 0

    def add(self, **counts):
        with self._lock:
            for key, value in counts.items():
                setattr(self, key, getattr(self, key) + value)


STATS = Stats()


def fire(name, response_time=0, response_length=0, exception=None):
    """Record one result in Locust's statistics."""
    events.request.fire(
        request_type="WS",
        name=name,
        response_time=response_time,
        response_length=response_length,
        exception=exception,
    )


def stopwatch(func):
    def wrapper(*args, **kwargs):
        start = time.time()
        exception = None
        try:
            func(*args, **kwargs)
        except Exception as e:
            exception = e
        fire(func.__name__, int((time.time() - start) * 1000), 0, exception)

    return wrapper


class Singleton(type):
    _instances = {}
    # Many simulated users start at once and server startup yields to other
    # greenlets, so creation must be serialized or the port gets bound twice.
    _lock = RLock()

    def __call__(cls, *args, **kwargs):
        with cls._lock:
            if cls not in cls._instances:
                cls._instances[cls] = super(Singleton, cls).__call__(*args, **kwargs)
        return cls._instances[cls]


def _reply(send_url, message):
    """POST ``message`` back to SocketDock so it is relayed to the client."""
    req = urllib.request.Request(
        send_url,
        data=message.encode(),
        headers={"Content-Type": "text/plain; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10):
            pass
    except Exception as e:
        fire("backend_reply", exception=e)


class InboundHTTP(metaclass=Singleton):
    """The backend SocketDock relays to: counts what it receives and replies."""

    def __init__(self):
        app = Flask(__name__)

        @app.route("/")
        def index():
            return "ok"

        @app.route("/connect", methods=["POST"])
        def connect():
            STATS.add(connects=1)
            return "ok"

        @app.route("/disconnect", methods=["POST"])
        def disconnect():
            STATS.add(disconnects=1)
            return "ok"

        @app.route("/message", methods=["POST"])
        def message():
            body = request.get_json(silent=True) or {}
            STATS.add(relayed=1)
            send_url = (body.get("meta") or {}).get("send")
            text = body.get("message")
            if send_url and isinstance(text, str):
                gevent.spawn(_reply, send_url, text)
            return "ok"

        WSGIServer(("0.0.0.0", 4242), app, log=None).start()


class CustomClient:
    def __init__(self, host):
        self.host = host
        self.connected = False
        self.ws = None

    def startup(self):
        InboundHTTP()  # binds the port before returning

        def on_message(wsapp, message):
            # The reply echoes "ping <send time>", so this is a true round trip
            # through SocketDock, the backend and SocketDock again.
            # Depending on the SocketDock version the reply arrives as a text or a
            # binary frame, so accept both.
            now = time.time()
            if isinstance(message, bytes):
                message = message.decode("utf-8", "replace")
            try:
                sent_at = float(message.split()[1])
            except (IndexError, ValueError):
                fire(
                    "reply_unparsed",
                    exception=Exception(f"unexpected reply: {message[:40]!r}"),
                )
                return
            STATS.add(replied=1)
            fire("round_trip", int((now - sent_at) * 1000), len(message))

        def on_open(wsapp):
            self.connected = True

        def on_close(wsapp, close_status_code=None, close_msg=None):
            self.connected = False

        def open_socket():
            self.ws = websocket.WebSocketApp(
                SOCKETDOCK_WS_URL,
                on_message=on_message,
                on_open=on_open,
                on_close=on_close,
            )
            return gevent.spawn(self.ws.run_forever)

        runner = open_socket()

        deadline = time.time() + CONNECT_TIMEOUT
        while not self.connected:
            if time.time() > deadline:
                fire(
                    "ws_connect",
                    exception=Exception(f"no websocket after {CONNECT_TIMEOUT}s"),
                )
                self.shutdown()
                raise StopUser()
            if runner.dead and not self.connected:
                # The connection attempt failed (for example SocketDock is still
                # starting); websocket-client does not retry, so do it here.
                gevent.sleep(0.5)
                runner = open_socket()
            gevent.sleep(0.05)

    def shutdown(self):
        ws = getattr(self, "ws", None)
        if ws is not None:
            if self.connected:
                gevent.sleep(CLOSE_GRACE)
            ws.close()

    def msg_client(self):
        if not self.connected:
            # Report once, then stop this user instead of failing every later task.
            fire("ws_closed", exception=Exception("websocket closed mid-test"))
            raise StopUser()
        self.ws_send()

    @stopwatch
    def ws_send(self):
        self.ws.send(f"ping {time.time()}")
        STATS.add(sent=1)


@events.test_start.add_listener
def _reset_stats(environment, **kwargs):
    STATS.reset()


@events.test_stop.add_listener
def _report(environment, **kwargs):
    gevent.sleep(REPLY_GRACE)  # let replies already in flight arrive
    print(
        f"[socketdock] sent={STATS.sent} relayed_to_backend={STATS.relayed} "
        f"replies_received={STATS.replied} connects={STATS.connects} "
        f"disconnects={STATS.disconnects}"
    )
    lost = STATS.sent - STATS.replied
    if lost > 0:
        fire(
            "reply_lost",
            exception=Exception(
                f"{lost} of {STATS.sent} messages did not come back "
                f"({STATS.relayed} reached the backend)"
            ),
        )
