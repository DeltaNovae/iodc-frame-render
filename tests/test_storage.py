"""The storage client's retry ladder.

Writes were the last unretried network hop in the pipeline. These tests exist
because that path had no coverage at all, which is reliably where this project's
defects have lived.
"""

import io
import urllib.error
import urllib.request

import pytest

from iodc import storage


class FakeResponse:
    def __init__(self, body=b"ok"):
        self.body = body

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(code, body=b"boom"):
    return urllib.error.HTTPError(
        "https://example.invalid/x", code, "err", {}, io.BytesIO(body))


def client():
    return storage.S3Client("https://example.invalid", "bucket", "AKIA", "secret")


def responder(monkeypatch, outcomes):
    """Serve `outcomes` in order, recording the request sent on each attempt."""
    sent = []

    def fake_urlopen(req, timeout=None):
        sent.append(req)
        outcome = outcomes[len(sent) - 1]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return sent


def test_a_transient_server_error_is_retried_and_then_succeeds(monkeypatch):
    """The case this ladder exists for: R2 having a bad second. Publishing is
    ~49 calls per cycle, so an unretried blip rate is multiplied by fifty
    before it reaches the cycle."""
    sent = responder(monkeypatch, [http_error(503), FakeResponse(b"done")])

    result = client()._request("PUT", "k", b"data", sleep=lambda _: None)

    assert result == b"done"
    assert len(sent) == 2


def test_a_network_error_is_retried(monkeypatch):
    sent = responder(monkeypatch,
                     [urllib.error.URLError("connection reset"), FakeResponse()])

    client()._request("PUT", "k", b"data", sleep=lambda _: None)

    assert len(sent) == 2


def test_a_client_error_is_not_retried(monkeypatch):
    """A 4xx is a bad request, not a bad moment — signing errors, permission
    failures and malformed keys fail identically however often they repeat.
    Retrying them would triple the time to surface a real misconfiguration."""
    sent = responder(monkeypatch, [http_error(403, b"AccessDenied")])

    with pytest.raises(RuntimeError, match="403"):
        client()._request("PUT", "k", b"data", sleep=lambda _: None)

    assert len(sent) == 1


def test_a_missing_key_raises_filenotfound_without_retrying(monkeypatch):
    """404 is control flow here — `read_meta` uses it to detect a fresh bucket,
    so it must stay fast and must not become a RuntimeError."""
    sent = responder(monkeypatch, [http_error(404)])

    with pytest.raises(FileNotFoundError):
        client()._request("GET", "missing", sleep=lambda _: None)

    assert len(sent) == 1


def test_persistent_failure_gives_up_after_the_last_attempt(monkeypatch):
    """The cycle must fail rather than hang: the next one is 15 minutes away
    and the previous frames keep serving."""
    sent = responder(monkeypatch, [http_error(500)] * 3)

    with pytest.raises(RuntimeError, match="after 3 attempts"):
        client()._request("PUT", "k", b"data", sleep=lambda _: None)

    assert len(sent) == 3


def test_each_attempt_is_signed_afresh(monkeypatch):
    """SigV4 signs `x-amz-date`. Headers computed once and reused after a
    backoff would eventually be rejected as expired — a retry ladder that
    quietly guarantees its own failure.

    Hoisting the `_headers` call back out of the loop must fail this test."""
    c = client()
    stamps = iter(["20260811T000000Z", "20260811T000030Z"])
    real_headers = c._headers

    def stamped(method, key, payload, extra):
        url, headers = real_headers(method, key, payload, extra)
        headers["x-amz-date"] = next(stamps)
        return url, headers

    monkeypatch.setattr(c, "_headers", stamped)
    sent = responder(monkeypatch, [http_error(503), FakeResponse()])

    c._request("PUT", "k", b"data", sleep=lambda _: None)

    assert [r.get_header("X-amz-date") for r in sent] == [
        "20260811T000000Z", "20260811T000030Z"]


def test_the_public_verbs_inherit_the_ladder(monkeypatch):
    """`put` is what the pipeline actually calls; a ladder reachable only
    through the private method would protect nothing."""
    monkeypatch.setattr(storage._time, "sleep", lambda _: None)
    sent = responder(monkeypatch, [http_error(502), FakeResponse()])

    client().put("sat/x.jpg", b"bytes", "image/jpeg", "public, max-age=60")

    assert len(sent) == 2


def test_backoff_grows_between_attempts(monkeypatch):
    """Doubling, not a fixed pause: if R2 is briefly overloaded, three rapid
    retries are three more requests it does not need."""
    waits = []
    responder(monkeypatch, [http_error(500)] * 3)

    with pytest.raises(RuntimeError):
        client()._request("PUT", "k", b"d", backoff=2.0, sleep=waits.append)

    assert waits == [2.0, 4.0]
