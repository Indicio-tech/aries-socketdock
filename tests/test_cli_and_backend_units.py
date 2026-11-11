"""Unit tests for argument parsing and the HTTP backend's pure logic."""

import sys

import pytest

from socketdock import __main__ as cli
from socketdock.httpbackend import HTTPBackend


def parse(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["socketdock", *argv])
    return cli.config()


def test_cli_defaults(monkeypatch):
    args = parse(monkeypatch)
    assert args.bindip == "127.0.0.1"
    assert args.bindport == 8765
    assert args.endpoint == "http://127.0.0.1:8765"
    assert args.backend == "loopback"
    assert args.log_level == "INFO"
    assert args.message_uri is None
    assert args.connect_uri is None
    assert args.disconnect_uri is None


def test_cli_overrides(monkeypatch):
    args = parse(
        monkeypatch,
        "--bindip",
        "0.0.0.0",
        "--bindport",
        "9000",
        "--endpoint",
        "https://dock.example.com",
        "--backend",
        "http",
        "--message-uri",
        "http://b/m",
        "--connect-uri",
        "http://b/c",
        "--disconnect-uri",
        "http://b/d",
        "--log-level",
        "DEBUG",
    )
    assert args.bindip == "0.0.0.0"
    assert args.bindport == 9000  # parsed to int, not left as str
    assert args.endpoint == "https://dock.example.com"
    assert args.backend == "http"
    assert (args.message_uri, args.connect_uri, args.disconnect_uri) == (
        "http://b/m",
        "http://b/c",
        "http://b/d",
    )
    assert args.log_level == "DEBUG"


@pytest.mark.parametrize(
    "bad_args",
    [
        ["--backend", "carrier-pigeon"],
        ["--log-level", "LOUD"],
    ],
)
def test_cli_rejects_invalid_choices(monkeypatch, capsys, bad_args):
    with pytest.raises(SystemExit) as excinfo:
        parse(monkeypatch, *bad_args)
    assert excinfo.value.code == 2


def test_callback_uris_are_built_from_the_public_endpoint():
    backend = HTTPBackend("https://dock.example.com", "c", "m", "d")
    assert backend.send_callback("abc") == "https://dock.example.com/socket/abc/send"
    assert (
        backend.disconnect_callback("abc")
        == "https://dock.example.com/socket/abc/disconnect"
    )
    assert backend.callback_uris("abc") == {
        "send": "https://dock.example.com/socket/abc/send",
        "disconnect": "https://dock.example.com/socket/abc/disconnect",
    }
