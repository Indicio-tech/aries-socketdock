# SocketDock load test

A [Locust](https://locust.io) load test for SocketDock. It is test tooling: CI
does not build or publish it. The Locust container plays both sides: it opens
many websocket clients against SocketDock, and it runs the HTTP backend
(`/connect`, `/message`, `/disconnect` on port 4242) that SocketDock relays to.

Requires Docker with Compose v2 (the images use Python 3.12 and Poetry 2.5.1).
`docker compose` also builds the SocketDock image from the repository root
(`build: ../`).

```sh
cd locust
docker compose up --build
```

Open http://localhost:8089, choose a user count and spawn rate, and start. The
UI and the mock backend are bound to loopback / the compose network only; both
are unauthenticated, so do not expose them.

Headless run (prints the statistics and exits):

```sh
docker compose run --rm --service-ports locust-socketdock \
  --headless -u 20 -r 5 -t 30s --host http://unused
```

## What is measured

Each simulated user sends `ping <timestamp>` over its websocket. The mock backend
receives it from SocketDock and POSTs the same text back to the connection's
`send` callback, so SocketDock relays it to the client. Locust reports:

- `ws_send`: the local cost of writing to the socket (not a SocketDock latency).
- `round_trip`: client -> SocketDock -> backend -> SocketDock -> client. This is
  the figure to watch.
- failures: `ws_connect` (no websocket within `LOCUST_CONNECT_TIMEOUT`),
  `ws_closed` (socket closed mid-test; that user stops), `backend_reply` (the
  callback POST failed) and `reply_lost` (at test stop, messages that never came
  back; the stop waits `LOCUST_REPLY_GRACE` seconds for replies in flight).

At test stop a line is printed with the counts of messages sent, relayed to the
backend, and replied, plus connects and disconnects seen by the backend.

## Settings

| Variable | Default | Meaning |
|---|---|---|
| `SOCKETDOCK_WS_URL` | `ws://websocket-gateway:8765/ws` | websocket target |
| `LOCUST_MIN_WAIT` / `LOCUST_MAX_WAIT` | `0.1` / `1` | pause (seconds) between a user's messages |
| `LOCUST_CONNECT_TIMEOUT` | `10` | seconds to wait for a websocket to open |
| `LOCUST_REPLY_GRACE` | `2` | seconds to wait for in-flight replies at test stop |
| `LOCUST_CLOSE_GRACE` | `1` | seconds a stopping user waits before closing its socket |

Setting these in your shell is enough: `docker-compose.yaml` passes them through.

## Notes

- When Locust exits it stops the mock backend while SocketDock may still be
  posting `/disconnect`, so the gateway logs a few `Connection reset by peer`
  errors at shutdown. That is an artifact of the test, not a failure.
- Statistic names changed from earlier versions: the request type is now `WS`
  and the names are `ws_send` and `round_trip`.
