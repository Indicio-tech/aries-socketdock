# SocketDock load test

A [Locust](https://locust.io) load test for SocketDock. The Locust container plays
both sides: it opens many websocket clients against SocketDock and it runs the
HTTP backend (`/connect`, `/message`, `/disconnect` on port 4242) that SocketDock
relays to.

```sh
cd locust
docker compose up --build
```

Open http://localhost:8089, choose a user count and spawn rate, and start.
The websocket target defaults to `ws://websocket-gateway:8765/ws`; override it
with `SOCKETDOCK_WS_URL`. `LOCUST_MIN_WAIT` / `LOCUST_MAX_WAIT` control the
pause (seconds) between each simulated user's messages.
