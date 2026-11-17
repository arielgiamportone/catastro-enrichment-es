import io
import urllib.error
import urllib.request
from email.message import Message

from catastro_enrich.transport import (
    HttpResponse,
    Pacer,
    UrllibTransport,
    build_url,
    default_user_agent,
)


def test_user_agent_identifies_project_and_contact(monkeypatch):
    monkeypatch.delenv("CATASTRO_ENRICH_CONTACT", raising=False)
    ua = default_user_agent()
    assert ua.startswith("catastro-enrich/") and "github.com" in ua
    assert default_user_agent("https://example.invalid/contact").endswith(
        "; https://example.invalid/contact)"
    )
    monkeypatch.setenv("CATASTRO_ENRICH_CONTACT", "env-contact")
    assert "env-contact" in default_user_agent()


def test_build_url_encodes_params():
    assert (
        build_url("https://example.invalid/x", {"q": "a b", "n": "1"})
        == "https://example.invalid/x?q=a%20b&n=1"
    )
    assert (
        build_url("https://example.invalid/x?y=1", {"q": "z"})
        == "https://example.invalid/x?y=1&q=z"
    )
    assert build_url("https://example.invalid/x", None) == "https://example.invalid/x"


class FakeResponse(io.BytesIO):
    status = 200
    headers = {"Content-Type": "text/xml"}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_urllib_transport_success_and_failures(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["ua"] = request.get_header("User-agent")
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        return FakeResponse(b"<ok/>")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    transport = UrllibTransport("agent/1")
    response = transport.get("https://example.invalid/svc", {"a": "1"}, timeout=7)
    assert response is not None and response.ok and response.text == "<ok/>"
    assert response.content_type == "text/xml" and captured == {
        "ua": "agent/1",
        "url": "https://example.invalid/svc?a=1",
        "timeout": 7,
    }

    headers = Message()
    headers["Content-Type"] = "text/plain"

    def http_error(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url, 429, "Too Many", headers, io.BytesIO(b"slow")
        )

    monkeypatch.setattr(urllib.request, "urlopen", http_error)
    rate_limited = transport.get("https://example.invalid/svc")
    assert rate_limited is not None and rate_limited.status == 429 and not rate_limited.ok

    def url_error(request, timeout):
        raise urllib.error.URLError("dns failure")

    monkeypatch.setattr(urllib.request, "urlopen", url_error)
    assert transport.get("https://example.invalid/svc") is None


def test_http_response_json_and_pacer_first_call_is_free():
    assert HttpResponse(200, b'{"a": 1}').json() == {"a": 1}
    waited = []
    pacer = Pacer(0.5, clock=lambda: 10.0, sleep=waited.append)
    assert pacer.wait() == 0.0
    assert pacer.wait() == 0.5 and waited == [0.5]
