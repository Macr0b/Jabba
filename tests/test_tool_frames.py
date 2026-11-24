"""Un outil s'ouvre dans Jabba (cadre + barre de retour) seulement s'il ne l'interdit pas."""

import httpx
import pytest

from host import catalog
from services import frame_check, tools


@pytest.mark.parametrize(
    "headers, in_jabba",
    [
        ({}, True),
        ({"x-frame-options": "DENY"}, False),
        ({"x-frame-options": "SAMEORIGIN"}, False),  # Jabba est sur un autre port : autre origine
        ({"content-security-policy": "default-src 'self'; frame-ancestors 'self'"}, False),
        ({"content-security-policy": "frame-ancestors 'none'"}, False),
        ({"content-security-policy": "frame-ancestors *"}, True),
        ({"content-security-policy": "default-src 'self'; img-src *"}, True),  # pas de règle de cadre
    ],
)
def test_header_rule(headers, in_jabba):
    assert frame_check.frameable(httpx.Headers(headers)) is in_jabba


def test_answer_kept_ten_minutes(monkeypatch):
    frame_check._cache.clear()
    calls = []
    monkeypatch.setattr(frame_check, "_probe", lambda port: calls.append(port) or True)
    assert frame_check.allows_framing(7745) and frame_check.allows_framing(7745)
    assert calls == [7745]
    assert not frame_check.allows_framing(None)  # pas de page web (Time Machine)


def test_tool_that_does_not_answer(monkeypatch):
    def refused(*args, **kwargs):
        raise httpx.ConnectError("refusé")

    monkeypatch.setattr(frame_check.httpx, "get", refused)
    assert frame_check._probe(1) is False  # dans le doute, il s'ouvre à part


def test_choice_per_tool(monkeypatch):
    monkeypatch.setattr(
        frame_check, "allows_framing", lambda port: port == catalog.OPTIONAL_TOOLS["homebox"]["port_local"]
    )
    assert tools._in_app("homebox") and not tools._in_app("vaultwarden")
    assert tools._in_app("jellyfin") and not tools._in_app("nextcloud")
