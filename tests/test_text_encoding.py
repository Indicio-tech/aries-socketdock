"""Unit tests for Content-Type charset parsing and the charset allow-list."""

import pytest

from socketdock.api import _resolve_charset, _text_encoding


@pytest.mark.parametrize(
    "content_type,expected",
    [
        ("text/plain", "utf-8"),
        ("TEXT/PLAIN", "utf-8"),
        ("text/plain; charset=utf-8", "utf-8"),
        ("text/plain;charset=UTF-8", "UTF-8"),
        ('text/plain; charset="iso-8859-1"', "iso-8859-1"),
        ("text/plain; format=flowed; charset=ascii", "ascii"),
        ("text/plain; CHARSET=ascii", "ascii"),
        ("text/plain; charset=", "utf-8"),
        ('text/plain; charset=""', "utf-8"),
        ("text/plain; charset=   ", "utf-8"),
        ("text/plain; charset=undefined", "undefined"),  # parsed, rejected later
        ("text/plainx", None),
        ("text/plain-foo", None),
        ("text/html", None),
        ("text/*", None),
        ("application/json; charset=utf-8", None),
        ("", None),
    ],
)
def test_text_encoding(content_type, expected):
    assert _text_encoding(content_type) == expected


@pytest.mark.parametrize(
    "name,expected",
    [
        ("utf-8", "utf-8"),
        ("UTF8", "utf-8"),
        ("us-ascii", "ascii"),
        ("latin-1", "iso8859-1"),
        ("iso-8859-1", "iso8859-1"),
        ("utf-16", "utf-16"),
        ("utf-16le", "utf-16-le"),
        ("utf-16be", "utf-16-be"),
    ],
)
def test_allowed_charsets_resolve(name, expected):
    assert _resolve_charset(name) == expected


@pytest.mark.parametrize(
    "name",
    [
        "punycode",
        "idna",
        "utf-7",
        "unicode_escape",
        "raw_unicode_escape",
        "rot13",
        "hex",
        "base64",
        "zlib",
        "undefined",
        "not-a-real-charset",
        "utf-8\x00",
        "",
    ],
)
def test_other_charsets_are_refused(name):
    assert _resolve_charset(name) is None
