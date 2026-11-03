"""Tests for `trueffelsau.http`.

No test touches the network (CLAUDE.md section 16). `FakeNet` answers from a
script of responses and records each request; `FakeClock` records sleeps
instead of sleeping. The courtesy tests matter most: a missing pause does not
fail anything here, it gets the tool blocked by a service run by one person.
"""

from __future__ import annotations

import io
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field

import pytest
import requests
from requests.adapters import BaseAdapter
from requests.structures import CaseInsensitiveDict

from trueffelsau import http
from trueffelsau.settings import Settings

URL = "https://example.com/api"


@dataclass
class FakeResponse:
    status_code: int = 200
    headers: Mapping[str, str] = field(default_factory=lambda: {"Content-Type": "application/json"})
    body: bytes = b"{}"
    closed: bool = False
    fail_while_reading: bool = False

    def iter_content(self, chunk_size: int) -> Iterator[bytes]:
        for start in range(0, len(self.body), chunk_size):
            if self.fail_while_reading and start > 0:
                raise http.TransportError("connection reset")
            yield self.body[start : start + chunk_size]

    def close(self) -> None:
        self.closed = True


@dataclass
class Call:
    url: str
    params: Mapping[str, str] | None
    headers: Mapping[str, str]
    timeout: float
    at: float


@dataclass
class FakeClock:
    now: float = 1000.0
    sleeps: list[float] = field(default_factory=list)

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@dataclass
class FakeNet:
    """Answers each request with the next scripted item. An exception is raised."""

    clock: FakeClock
    script: list[FakeResponse | Exception]
    calls: list[Call] = field(default_factory=list)
    responses: list[FakeResponse] = field(default_factory=list)

    def __call__(
        self, url: str, params: Mapping[str, str] | None, headers: Mapping[str, str], timeout: float
    ) -> FakeResponse:
        self.calls.append(Call(url, params, dict(headers), timeout, self.clock.now))
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        self.responses.append(item)
        return item


def fetcher(
    *script: FakeResponse | Exception, settings: Settings | None = None
) -> tuple[http.Fetcher, FakeNet, FakeClock]:
    clock = FakeClock()
    net = FakeNet(clock, list(script))
    return (
        http.Fetcher(settings or Settings(), send=net, clock=clock, sleep=clock.sleep),
        net,
        clock,
    )


def ok(body: bytes = b"{}", content_type: str = "application/json", **extra: str) -> FakeResponse:
    return FakeResponse(200, {"Content-Type": content_type, **extra}, body)


def status(code: int, **headers: str) -> FakeResponse:
    return FakeResponse(code, headers, b"")


# --- the happy path ----------------------------------------------------------------


def test_returns_the_page() -> None:
    f, _, _ = fetcher(ok(b'{"hits": []}'))
    page = f.get(URL, expect="json")
    assert page.json() == {"hits": []}
    assert page.url == URL
    assert page.status == 200


def test_sends_user_agent_accept_and_timeout() -> None:
    f, net, _ = fetcher(ok())
    f.get(URL, expect="json")
    call = net.calls[0]
    assert call.headers["User-Agent"] == Settings().user_agent
    assert call.headers["Accept"] == "application/json"
    assert call.timeout == Settings().http_timeout_seconds


def test_passes_the_query_parameters() -> None:
    f, net, _ = fetcher(ok())
    f.get(URL, params={"query": "price increase"}, expect="json")
    assert net.calls[0].params == {"query": "price increase"}


def test_closes_every_response() -> None:
    f, net, _ = fetcher(status(503), ok())
    f.get(URL, expect="json")
    assert all(response.closed for response in net.responses)


def test_closes_a_response_it_refuses() -> None:
    f, net, _ = fetcher(ok(content_type="text/html"))
    with pytest.raises(http.FetchError):
        f.get(URL, expect="json")
    assert net.responses[0].closed


# --- content type --------------------------------------------------------------------


def test_html_where_json_was_expected_is_an_error() -> None:
    # A login or error page served with 200. Parsed as an empty result it
    # would look like a quiet week.
    f, _, _ = fetcher(ok(b"<html>", content_type="text/html; charset=utf-8"))
    with pytest.raises(http.FetchError, match="expected json"):
        f.get(URL, expect="json")


def test_missing_content_type_is_an_error_for_a_specific_kind() -> None:
    f, _, _ = fetcher(FakeResponse(200, {}, b"{}"))
    with pytest.raises(http.FetchError, match="content type"):
        f.get(URL, expect="json")


@pytest.mark.parametrize(
    ("kind", "content_type"),
    [
        ("json", "application/json; charset=utf-8"),
        ("html", "text/html"),
        ("html", "application/xhtml+xml"),
        ("feed", "application/atom+xml"),
        ("feed", "application/rss+xml"),
        ("feed", "text/xml"),
        ("any", "text/plain"),
        ("any", ""),
    ],
)
def test_accepted_content_types(kind: http.Kind, content_type: str) -> None:
    f, _, _ = fetcher(ok(content_type=content_type))
    assert f.get(URL, expect=kind).status == 200


def test_content_type_is_compared_case_insensitively() -> None:
    f, _, _ = fetcher(ok(content_type="Application/JSON"))
    assert f.get(URL, expect="json").status == 200


# --- size ceiling ----------------------------------------------------------------------


def test_declared_length_over_the_limit_is_refused_before_reading() -> None:
    settings = Settings(http_max_bytes=10)
    response = ok(b"x" * 5, **{"Content-Length": "11"})
    f, _, _ = fetcher(response, settings=settings)
    with pytest.raises(http.FetchError, match="limit"):
        f.get(URL, expect="json")


def test_body_over_the_limit_is_refused_while_reading() -> None:
    # The server says nothing, or says something small. Only counting the
    # bytes as they arrive keeps the ceiling.
    settings = Settings(http_max_bytes=100_000)
    response = ok(b"x" * 100_001, **{"Content-Length": "5"})
    f, _, _ = fetcher(response, settings=settings)
    with pytest.raises(http.FetchError, match="limit"):
        f.get(URL, expect="json")


def test_body_exactly_at_the_limit_is_accepted() -> None:
    settings = Settings(http_max_bytes=100_000)
    f, _, _ = fetcher(ok(b"x" * 100_000), settings=settings)
    assert len(f.get(URL, expect="json").body) == 100_000


# --- status codes and retries ------------------------------------------------------------


def test_client_error_is_not_retried() -> None:
    f, net, _ = fetcher(status(404))
    with pytest.raises(http.FetchError, match="HTTP 404"):
        f.get(URL, expect="json")
    assert len(net.calls) == 1


@pytest.mark.parametrize("code", sorted(http.RETRY_STATUSES))
def test_retryable_status_is_retried(code: int) -> None:
    f, net, _ = fetcher(status(code), ok())
    assert f.get(URL, expect="json").status == 200
    assert len(net.calls) == 2


def test_gives_up_after_the_last_retry() -> None:
    f, net, _ = fetcher(*[status(503) for _ in range(http.RETRIES + 1)])
    with pytest.raises(http.FetchError, match="HTTP 503"):
        f.get(URL, expect="json")
    assert len(net.calls) == http.RETRIES + 1


def test_backoff_doubles() -> None:
    f, _, clock = fetcher(status(503), status(503), ok())
    f.get(URL, expect="json")
    # Each retry also waits out the host interval, which the backoff already covers.
    assert clock.sleeps == [http.BACKOFF_SECONDS, http.BACKOFF_SECONDS * 2]


def test_retry_after_in_seconds_is_honoured() -> None:
    f, _, clock = fetcher(status(429, **{"Retry-After": "7"}), ok())
    f.get(URL, expect="json")
    assert clock.sleeps == [7.0]


def test_retry_after_as_a_date_falls_back_to_backoff() -> None:
    f, _, clock = fetcher(status(429, **{"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}), ok())
    f.get(URL, expect="json")
    assert clock.sleeps == [http.BACKOFF_SECONDS]


def test_a_long_retry_after_is_not_waited_for() -> None:
    # Waiting an hour would stall every other source in the weekly run.
    f, net, clock = fetcher(status(429, **{"Retry-After": "3600"}))
    with pytest.raises(http.FetchError, match="wait 3600"):
        f.get(URL, expect="json")
    assert clock.sleeps == []
    assert len(net.calls) == 1


def test_connection_error_is_retried() -> None:
    f, net, _ = fetcher(http.TransportError("refused"), ok())
    assert f.get(URL, expect="json").status == 200
    assert len(net.calls) == 2


def test_connection_error_on_every_try_names_the_cause() -> None:
    f, _, _ = fetcher(*[http.TransportError("refused") for _ in range(http.RETRIES + 1)])
    with pytest.raises(http.FetchError, match="connection failed: refused"):
        f.get(URL, expect="json")


def test_a_reset_while_reading_the_body_is_retried() -> None:
    broken = ok(b"x" * (http.CHUNK_BYTES + 1))
    broken.fail_while_reading = True
    f, net, _ = fetcher(broken, ok())
    assert f.get(URL, expect="json").body == b"{}"
    assert len(net.calls) == 2


# --- redirects ------------------------------------------------------------------------------


def test_same_host_redirect_is_followed() -> None:
    f, net, _ = fetcher(status(301, Location="/api/v2"), ok())
    page = f.get(URL, expect="json")
    assert page.url == "https://example.com/api/v2"
    assert [call.url for call in net.calls] == [URL, "https://example.com/api/v2"]


def test_redirect_does_not_resend_the_query_parameters() -> None:
    # The Location already holds the full query; sending params again
    # would duplicate them.
    f, net, _ = fetcher(status(302, Location="/api?q=x&page=2"), ok())
    f.get(URL, params={"q": "x"}, expect="json")
    assert net.calls[1].params is None


def test_redirect_to_another_host_is_refused() -> None:
    f, net, _ = fetcher(status(302, Location="https://login.example.org/"))
    with pytest.raises(http.FetchError, match="another host"):
        f.get(URL, expect="json")
    assert len(net.calls) == 1


def test_redirect_to_http_is_refused() -> None:
    f, _, _ = fetcher(status(301, Location="http://example.com/api"))
    with pytest.raises(http.FetchError, match="only https"):
        f.get(URL, expect="json")


def test_redirect_host_is_compared_case_insensitively() -> None:
    f, _, _ = fetcher(status(301, Location="https://EXAMPLE.com/api/v2"), ok())
    assert f.get(URL, expect="json").status == 200


def test_redirect_without_location_is_an_error() -> None:
    f, _, _ = fetcher(status(302))
    with pytest.raises(http.FetchError, match="Location"):
        f.get(URL, expect="json")


def test_redirect_loop_ends() -> None:
    script = [status(302, Location="/a") for _ in range(http.MAX_REDIRECTS + 1)]
    f, net, _ = fetcher(*script)
    with pytest.raises(http.FetchError, match="redirects"):
        f.get(URL, expect="json")
    assert len(net.calls) == http.MAX_REDIRECTS + 1


# --- URLs -----------------------------------------------------------------------------------


def test_http_url_is_refused_without_a_request() -> None:
    f, net, _ = fetcher()
    with pytest.raises(http.FetchError, match="only https"):
        f.get("http://example.com/", expect="any")
    assert net.calls == []


def test_url_with_credentials_is_refused() -> None:
    f, net, _ = fetcher()
    with pytest.raises(http.FetchError, match="credentials"):
        f.get("https://user:secret@example.com/", expect="any")
    assert net.calls == []


# --- pauses between requests to one host ------------------------------------------------------


def test_first_request_does_not_wait() -> None:
    f, _, clock = fetcher(ok())
    f.get(URL, expect="json")
    assert clock.sleeps == []


def test_second_request_to_a_host_waits_for_the_interval() -> None:
    f, net, _ = fetcher(ok(), ok())
    f.get(URL, expect="json")
    f.get(URL, expect="json")
    assert net.calls[1].at - net.calls[0].at == pytest.approx(Settings().http_min_interval_seconds)


def test_time_already_passed_counts_toward_the_interval() -> None:
    f, _, clock = fetcher(ok(), ok(), settings=Settings(http_min_interval_seconds=5.0))
    f.get(URL, expect="json")
    clock.now += 3.0
    f.get(URL, expect="json")
    assert clock.sleeps == [pytest.approx(2.0)]


def test_requests_to_different_hosts_do_not_wait() -> None:
    f, _, clock = fetcher(ok(), ok())
    f.get("https://a.example.com/", expect="json")
    f.get("https://b.example.com/", expect="json")
    assert clock.sleeps == []


def test_host_specific_interval_is_used() -> None:
    f, net, _ = fetcher(ok(), ok())
    f.get("https://api.github.com/search", expect="json")
    f.get("https://api.github.com/search", expect="json")
    assert net.calls[1].at - net.calls[0].at == pytest.approx(2.0)


def test_redirect_within_a_host_also_waits() -> None:
    f, net, _ = fetcher(status(301, Location="/b"), ok())
    f.get(URL, expect="json")
    assert net.calls[1].at - net.calls[0].at >= Settings().http_min_interval_seconds


# --- Page --------------------------------------------------------------------------------------


def test_text_uses_the_declared_charset() -> None:
    page = http.Page(URL, 200, "text/html; charset=iso-8859-1", "Grüß".encode("latin-1"))
    assert page.text() == "Grüß"


def test_text_defaults_to_utf8() -> None:
    page = http.Page(URL, 200, "text/html", "Grüß".encode())
    assert page.text() == "Grüß"


def test_text_with_an_unknown_charset_falls_back_to_utf8() -> None:
    page = http.Page(URL, 200, "text/html; charset=nonsense-9", "Grüß".encode())
    assert page.text() == "Grüß"


def test_text_replaces_undecodable_bytes() -> None:
    page = http.Page(URL, 200, "text/plain", b"ok \xff ok")
    assert page.text() == "ok � ok"


def test_invalid_json_names_the_url() -> None:
    page = http.Page(URL, 200, "application/json", b"<html>")
    with pytest.raises(http.FetchError, match=r"example\.com"):
        page.json()


# --- requests_sender: the real transport, through a fake adapter --------------------------------


class RecordingAdapter(BaseAdapter):
    def __init__(self, status_code: int = 200, headers: dict[str, str] | None = None) -> None:
        super().__init__()
        self.status_code = status_code
        self.response_headers = headers or {"Content-Type": "application/json"}
        self.sent: list[tuple[requests.PreparedRequest, dict[str, object]]] = []

    def send(  # type: ignore[override]
        self, request: requests.PreparedRequest, **kwargs: object
    ) -> requests.Response:
        self.sent.append((request, kwargs))
        response = requests.Response()
        response.status_code = self.status_code
        response.headers = CaseInsensitiveDict(self.response_headers)
        response.raw = io.BytesIO(b'{"ok": true}')
        response.url = request.url or ""
        response.request = request
        return response

    def close(self) -> None:
        pass


def _session(adapter: RecordingAdapter) -> requests.Session:
    session = requests.Session()
    session.mount("https://", adapter)
    return session


def test_requests_sender_streams_and_does_not_follow_redirects() -> None:
    adapter = RecordingAdapter()
    send = http.requests_sender(_session(adapter))
    send(URL, {"q": "a b"}, {"User-Agent": "t"}, 3.0)
    request, kwargs = adapter.sent[0]
    assert request.url == "https://example.com/api?q=a+b"
    assert request.headers["User-Agent"] == "t"
    assert kwargs["stream"] is True
    assert kwargs["timeout"] == 3.0


def test_requests_sender_returns_a_redirect_instead_of_following_it() -> None:
    adapter = RecordingAdapter(302, {"Location": "https://elsewhere.example/"})
    response = http.requests_sender(_session(adapter))(URL, None, {}, 3.0)
    assert response.status_code == 302
    assert len(adapter.sent) == 1


def test_requests_sender_turns_connection_errors_into_transport_errors() -> None:
    class Failing(RecordingAdapter):
        def send(  # type: ignore[override]
            self, request: requests.PreparedRequest, **kwargs: object
        ) -> requests.Response:
            raise requests.ConnectionError("refused")

    with pytest.raises(http.TransportError, match="refused"):
        http.requests_sender(_session(Failing()))(URL, None, {}, 3.0)


def test_fetcher_works_end_to_end_over_requests() -> None:
    adapter = RecordingAdapter()
    f = http.Fetcher(Settings(), send=http.requests_sender(_session(adapter)))
    assert f.get(URL, expect="json").json() == {"ok": True}
