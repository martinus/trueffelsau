"""Every HTTP request trueffelsau makes goes through `Fetcher.get`.

One place for the courtesy and safety rules, so a new collector cannot forget
them:

- A pause between two requests to the same host (CLAUDE.md section 12). Arctic
  Shift and HN Algolia are free services run by other people.
- A timeout, and a byte ceiling checked while the body is read, not only from
  `Content-Length`, which a server may leave out or get wrong.
- The response must have the expected content type. A login page or an error
  page served with status 200 is HTML where JSON was expected; parsing it would
  give zero events and look like a quiet week.
- Redirects are followed only within the same host and only to HTTPS. A
  listing page that redirects to a login on another domain is an error, not a
  page to parse.
- 429 and 5xx are retried a few times with backoff, honouring `Retry-After`.
  A server that asks for a long wait gets no request, not a run that hangs.

The rules for limits, redirects and content type follow `isas1/daily-team`'s
feed reader (see docs/implementation-plan.md). The code is new.

The network itself is behind `Send`, a plain function. Tests pass a fake one
and a fake clock; `requests_sender` is the real one.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from typing import Literal, Protocol
from urllib.parse import urljoin, urlsplit

import requests

from trueffelsau.settings import Settings

#: What a caller expects back. Each kind maps to the content types it accepts
#: and to the `Accept` header sent with the request.
Kind = Literal["json", "html", "feed", "any"]

_ACCEPT: dict[str, str] = {
    "json": "application/json",
    "html": "text/html, application/xhtml+xml",
    "feed": "application/rss+xml, application/atom+xml, application/xml, text/xml",
    "any": "*/*",
}

#: Lowercase substrings of the media type that a kind accepts.
_ACCEPTED_TYPES: dict[str, tuple[str, ...]] = {
    "json": ("json",),
    "html": ("text/html", "application/xhtml"),
    "feed": ("xml", "rss", "atom"),
    "any": ("",),
}

#: Statuses worth asking again for. 408 and 429 are the server saying "later";
#: the 5xx set is the one where a second try often works. 501 is not in it: a
#: method the server does not implement stays unimplemented.
RETRY_STATUSES = frozenset({408, 429, 500, 502, 503, 504})

#: Redirect statuses. 304 is not one: it answers a conditional request, and
#: trueffelsau sends none.
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})

#: Tries after the first. Four requests in total is enough for a brief outage
#: and short enough that a dead source does not hold up the others for long.
RETRIES = 3

#: Seconds before the first retry; doubled for each next one.
BACKOFF_SECONDS = 2.0

#: The longest wait the fetcher accepts, from backoff or `Retry-After`. A server
#: that asks for longer is treated as down for this run.
MAX_WAIT_SECONDS = 120.0

#: Redirects followed for one request. Real sites need one or two.
MAX_REDIRECTS = 5

#: Bytes read per step while checking the ceiling.
CHUNK_BYTES = 64 * 1024


class FetchError(Exception):
    """A request that did not give a usable page. The message names the URL."""

    def __init__(self, url: str, reason: str) -> None:
        super().__init__(f"{url}: {reason}")
        self.url = url
        self.reason = reason


class TransportError(Exception):
    """The connection failed: DNS, refused, reset, or timed out. Retried."""


class RawResponse(Protocol):
    """The parts of a response the fetcher reads. Body bytes are streamed."""

    @property
    def status_code(self) -> int: ...

    @property
    def headers(self) -> Mapping[str, str]: ...

    def iter_content(self, chunk_size: int) -> Iterator[bytes]: ...

    def close(self) -> None: ...


#: Sends one GET without following redirects and without reading the body.
#: Arguments: url, query parameters, headers, timeout in seconds.
Send = Callable[[str, Mapping[str, str] | None, Mapping[str, str], float], RawResponse]


@dataclass(frozen=True)
class Page:
    """A successful response, read in full."""

    url: str
    status: int
    content_type: str
    body: bytes

    def text(self) -> str:
        """The body decoded with the charset the server named, or UTF-8.

        Undecodable bytes become U+FFFD rather than an error: one bad byte in a
        forum post should cost that character, not the whole page.
        """
        return self.body.decode(_charset(self.content_type), errors="replace")

    def json(self) -> object:
        try:
            return json.loads(self.body)
        except ValueError as error:
            raise FetchError(self.url, f"body is not valid JSON: {error}") from error


def _charset(content_type: str) -> str:
    for part in content_type.split(";")[1:]:
        name, _, value = part.strip().partition("=")
        if name.strip().lower() == "charset" and value.strip():
            charset = value.strip().strip("\"'")
            try:
                "".encode(charset)
            except LookupError:
                break
            return charset
    return "utf-8"


class Fetcher:
    """GET requests with the rules in the module docstring.

    One fetcher per run. It remembers when it last called each host, so all
    collectors in a run share the pauses.
    """

    def __init__(
        self,
        settings: Settings,
        send: Send | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._settings = settings
        self._send = send if send is not None else requests_sender()
        self._clock = clock
        self._sleep = sleep
        self._last_call: dict[str, float] = {}

    def get(self, url: str, *, params: Mapping[str, str] | None = None, expect: Kind) -> Page:
        """Fetch `url` and return it in full, or raise `FetchError`."""
        _check_url(url, url)
        current = url
        query = params

        for _ in range(MAX_REDIRECTS + 1):
            outcome = self._with_retries(current, query, expect)
            if isinstance(outcome, Page):
                return outcome
            target = urljoin(current, outcome)
            _check_redirect(current, target)
            current = target
            # The `Location` is a whole URL, query included. Sending the
            # original parameters again would add them a second time.
            query = None

        raise FetchError(url, f"more than {MAX_REDIRECTS} redirects")

    def _with_retries(self, url: str, params: Mapping[str, str] | None, expect: Kind) -> Page | str:
        """One request with retries. Returns the page, or a redirect location."""
        for attempt in range(RETRIES + 1):
            last = attempt == RETRIES
            try:
                outcome = self._attempt(url, params, expect)
            except TransportError as error:
                if last:
                    raise FetchError(url, f"connection failed: {error}") from error
                self._wait(url, BACKOFF_SECONDS * 2**attempt)
                continue

            if isinstance(outcome, _Retry):
                if last:
                    raise FetchError(url, f"HTTP {outcome.status} after {RETRIES + 1} tries")
                self._wait(url, outcome.retry_after or BACKOFF_SECONDS * 2**attempt)
                continue

            return outcome

        raise AssertionError("unreachable: the last attempt returns or raises")

    def _wait(self, url: str, seconds: float) -> None:
        if seconds > MAX_WAIT_SECONDS:
            raise FetchError(
                url, f"server asks to wait {seconds:.0f} s, more than {MAX_WAIT_SECONDS:.0f} s"
            )
        self._sleep(seconds)

    def _attempt(
        self, url: str, params: Mapping[str, str] | None, expect: Kind
    ) -> Page | str | _Retry:
        self._pause_for(url)
        headers = {"User-Agent": self._settings.user_agent, "Accept": _ACCEPT[expect]}
        response = self._send(url, params, headers, self._settings.http_timeout_seconds)
        try:
            return self._read(url, response, expect)
        finally:
            response.close()

    def _pause_for(self, url: str) -> None:
        """Sleep until the host's minimum interval has passed, then note the call."""
        host = _host(url)
        interval = self._settings.min_interval(host)
        previous = self._last_call.get(host)
        if previous is not None:
            remaining = interval - (self._clock() - previous)
            if remaining > 0:
                self._sleep(remaining)
        self._last_call[host] = self._clock()

    def _read(self, url: str, response: RawResponse, expect: Kind) -> Page | str | _Retry:
        status = response.status_code
        headers = {name.lower(): value for name, value in response.headers.items()}

        if status in RETRY_STATUSES:
            return _Retry(status, _retry_after(headers.get("retry-after")))

        if status in REDIRECT_STATUSES:
            location = headers.get("location")
            if not location:
                raise FetchError(url, f"HTTP {status} without a Location header")
            return location

        if not 200 <= status < 300:
            raise FetchError(url, f"HTTP {status}")

        content_type = headers.get("content-type", "")
        if not any(accepted in content_type.lower() for accepted in _ACCEPTED_TYPES[expect]):
            raise FetchError(url, f"expected {expect}, got content type {content_type!r}")

        limit = self._settings.http_max_bytes
        declared = headers.get("content-length", "")
        if declared.isdigit() and int(declared) > limit:
            raise FetchError(url, f"response is {declared} bytes, the limit is {limit}")

        return Page(
            url=url,
            status=status,
            content_type=content_type,
            body=_read_capped(url, response, limit),
        )


@dataclass(frozen=True)
class _Retry:
    status: int
    retry_after: float | None


def _read_capped(url: str, response: RawResponse, limit: int) -> bytes:
    chunks: list[bytes] = []
    size = 0
    for chunk in response.iter_content(CHUNK_BYTES):
        size += len(chunk)
        if size > limit:
            raise FetchError(url, f"response is larger than the limit of {limit} bytes")
        chunks.append(chunk)
    return b"".join(chunks)


def _retry_after(value: str | None) -> float | None:
    """`Retry-After` in seconds. The HTTP-date form is ignored: backoff applies."""
    if value is None:
        return None
    stripped = value.strip()
    return float(stripped) if stripped.isdigit() else None


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def _check_url(original: str, url: str) -> None:
    """Only HTTPS, and no user name or password in the URL."""
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise FetchError(original, f"only https URLs are fetched, not {url!r}")
    if not parts.hostname:
        raise FetchError(original, f"no host in {url!r}")
    if parts.username is not None or parts.password is not None:
        raise FetchError(original, "credentials in a URL are refused")


def _check_redirect(current: str, target: str) -> None:
    _check_url(current, target)
    if _host(target) != _host(current):
        raise FetchError(current, f"redirect to another host refused: {target}")


# --- the real network -------------------------------------------------------


class _RequestsResponse:
    """Adapts `requests.Response` to `RawResponse`, turning read errors into
    `TransportError` so a connection reset mid-body is retried like one before it."""

    def __init__(self, response: requests.Response) -> None:
        self._response = response

    @property
    def status_code(self) -> int:
        return self._response.status_code

    @property
    def headers(self) -> Mapping[str, str]:
        return self._response.headers

    def iter_content(self, chunk_size: int) -> Iterator[bytes]:
        try:
            yield from self._response.iter_content(chunk_size)
        except requests.RequestException as error:
            raise TransportError(str(error)) from error

    def close(self) -> None:
        self._response.close()


def requests_sender(session: requests.Session | None = None) -> Send:
    """A `Send` over `requests`. Streams the body and never follows redirects."""
    client = session if session is not None else requests.Session()

    def send(
        url: str, params: Mapping[str, str] | None, headers: Mapping[str, str], timeout: float
    ) -> RawResponse:
        try:
            response = client.get(
                url,
                params=params,
                headers=dict(headers),
                timeout=timeout,
                stream=True,
                allow_redirects=False,
            )
        except requests.RequestException as error:
            raise TransportError(str(error)) from error
        return _RequestsResponse(response)

    return send
