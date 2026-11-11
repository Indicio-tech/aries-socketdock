from locust import events
from flask import Flask

import inspect
import os
import time

import gevent
from gevent.lock import RLock
from gevent.pywsgi import WSGIServer
import websocket

# URL SocketDock is reachable at from the locust container.
SOCKETDOCK_WS_URL = os.getenv("SOCKETDOCK_WS_URL", "ws://websocket-gateway:8765/ws")


def stopwatch(func):
    def wrapper(*args, **kwargs):
        # get task's function name
        previous_frame = inspect.currentframe().f_back
        file_name, _, task_name, _, _ = inspect.getframeinfo(previous_frame)
        name = file_name + "_" + task_name

        start = time.time()
        result = None
        exception = None
        try:
            result = func(*args, **kwargs)
        except Exception as e:
            exception = e
        events.request.fire(
            request_type="WS",
            name=name,
            response_time=int((time.time() - start) * 1000),
            response_length=0,
            exception=exception,
        )
        return result

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


class InboundHTTP(metaclass=Singleton):
    _self = None

    def __init__(self):
        app = Flask(__name__)

        @app.route("/")
        def hello_world():
            return "<p>Hello, World!</p>"

        @app.route("/connect", methods=["POST"])
        def connect():
            return "<p>Hello, World!</p>"

        @app.route("/disconnect", methods=["POST"])
        def disconnect():
            return "<p>Hello, World!</p>"

        @app.route("/message", methods=["POST"])
        def message():
            return "<p>Hello, World!</p>"

        WSGIServer(("0.0.0.0", 4242), app, log=None).start()

    def track(self):
        print(f"TODO track event at {self.api_url}")


class CustomClient:
    def __init__(self, host):
        self.host = host

    _locust_environment = None

    def startup(self):
        # print("startup", file=sys.stderr)

        InboundHTTP()

        # Give a sec for the inbound server to start
        time.sleep(5)  # Magic Number

        self.connected = False

        def on_message(wsapp, message):
            pass  # printing every reply would distort load results

        def on_open(wsapp):
            self.connected = True

        def on_close(wsapp, close_status_code=None, close_msg=None):
            self.connected = False

        # Debugging stuff
        # websocket.enableTrace(True)

        self.ws = websocket.WebSocketApp(
            SOCKETDOCK_WS_URL,
            on_message=on_message,
            on_open=on_open,
            on_close=on_close,
        )
        gevent.spawn(self.ws.run_forever)

        while not self.connected:
            time.sleep(1)

    def shutdown(self):
        self.ws.close()

    @stopwatch
    def msg_client(self):
        i = ""
        if self.connected is False:
            raise Exception("Not Connected!")
        self.ws.send(f"Ping! ({i})")
        pass
