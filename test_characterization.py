#!/usr/bin/env python3
"""Characterization pins for the pre-migration just-eat-mcp server (A.2 T02).

These tests pin the CURRENT server's tool behavior — plain markdown text
outputs and text-shaped errors — against canned urllib payloads. No network:
every HTTP path goes through a monkeypatched ``server.urllib.request.urlopen``.
They must stay green, byte-for-byte, after the era conversion (T04): tool
logic is byte-untouched, so the golden-era text IS the contract.
"""
import json
import urllib.error

import server


class FakeResponse:
    """Context-manager-shaped stand-in for urlopen's response object."""

    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def install_fake_urlopen(monkeypatch, payload):
    """Replace server.urllib.request.urlopen; returns a call counter."""
    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append(req)
        return FakeResponse(payload)

    monkeypatch.setattr(server.urllib.request, "urlopen", fake_urlopen)
    return calls


# ── Canned payloads (minimal, but shaped like the real API keys) ──

ENRICHED_SEARCH_PAYLOAD = {
    "deliveryArea": "Ayr Town Centre",
    "metaData": {"resultCount": 2},
    "restaurants": [
        {
            "id": 11111,
            "name": "The Golden Curry",
            "uniqueName": "the-golden-curry",
            "cuisines": [{"name": "Indian"}, {"name": "Halal"}],
            "rating": {"starRating": 4.5, "count": 320},
            "isOpenNow": True,
            "address": {"firstLine": "12 High Street"},
            "availability": {"delivery": {"isOpen": True}},
        },
        {
            "id": 22222,
            "name": "Pizza Pomodoro",
            "uniqueName": "pizza-pomodoro",
            "cuisines": [{"name": "Pizza"}],
            "rating": {"starRating": 4.0, "count": 118},
            "isOpenNow": False,
            "address": {"firstLine": "3 Wellington Square"},
        },
    ],
    "deliveryFees": {
        "restaurants": {
            "11111": {"bands": [{"fee": 0}, {"fee": 299}], "minimumOrderValue": 1500},
            "22222": {"bands": [{"fee": 0}], "minimumOrderValue": 0},
        }
    },
}

CUISINES_PAYLOAD = {
    "MetaData": {
        "Area": "Ayr Town Centre",
        "CuisineDetails": [
            {"Name": "Italian", "Total": 12},
            {"Name": "Chinese", "Total": 8},
        ],
    }
}


def test_search_restaurants_canned_text(monkeypatch):
    calls = install_fake_urlopen(monkeypatch, ENRICHED_SEARCH_PAYLOAD)
    out = server.handle_call("search_restaurants", {"postcode": "KA7 1AA"})
    assert len(calls) == 1
    assert "## 2 of 2 restaurants near" in out
    assert "The Golden Curry" in out
    assert "Pizza Pomodoro" in out
    assert "£" in out  # delivery-fee fragment from deliveryFees bands


def test_invalid_postcode_short_circuits_without_http(monkeypatch):
    calls = install_fake_urlopen(monkeypatch, {})
    out = server.handle_call("search_restaurants", {"postcode": "!!!"})
    assert out == "Error: invalid UK postcode format"
    assert len(calls) == 0


def test_unknown_tool_text(monkeypatch):
    calls = install_fake_urlopen(monkeypatch, {})
    out = server.handle_call("bogus", {})
    assert out == "Unknown tool: bogus"
    assert len(calls) == 0


def test_http_failure_text(monkeypatch):
    def raise_urerror(req, timeout=None):
        raise urllib.error.URLError("boom")

    monkeypatch.setattr(server.urllib.request, "urlopen", raise_urerror)
    # api_get retries MAX_RETRIES+1 times with time.sleep between — patch the
    # sleep out so the failure pin doesn't stall for ~6 s.
    sleeps = []
    monkeypatch.setattr(server.time, "sleep", lambda s: sleeps.append(s))

    out = server.handle_call("search_restaurants", {"postcode": "KA7 1AA"})
    assert out.startswith("Error: Request failed after 3 attempts:")
    assert "boom" in out
    assert len(sleeps) == 2  # two retry delays, no real waiting


def test_get_cuisines_canned_text(monkeypatch):
    calls = install_fake_urlopen(monkeypatch, CUISINES_PAYLOAD)
    out = server.handle_call("get_cuisines", {"postcode": "KA7 1AA"})
    assert len(calls) == 1
    assert "## Cuisines near" in out
    assert "- Italian: 12 restaurants" in out
