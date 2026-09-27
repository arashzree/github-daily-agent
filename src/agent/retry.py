"""HTTP retry helper shared by the API clients."""

from collections.abc import Callable
from typing import Any

import requests

TIMEOUT_SECONDS = 20
MAX_TRIES = 3


def send(
    session: requests.Session,
    method: str,
    url: str,
    *,
    service: str,
    error: type[Exception],
    sleep: Callable[[float], None],
    **kwargs: Any,
) -> requests.Response:
    """Send a request, retrying timeouts, connection errors and 5xx (3 tries, 1s/2s backoff).

    Returns the first non-5xx response; the caller maps 4xx to its own errors.
    Raises `error` once retries are exhausted.
    """
    for attempt in range(1, MAX_TRIES + 1):
        try:
            resp = session.request(method, url, timeout=TIMEOUT_SECONDS, **kwargs)
        except (requests.Timeout, requests.ConnectionError) as exc:
            if attempt == MAX_TRIES:
                raise error(f"{service} unreachable after {MAX_TRIES} tries: {exc}") from exc
        else:
            if resp.status_code < 500:
                return resp
            if attempt == MAX_TRIES:
                raise error(f"{service} returned {resp.status_code} after {MAX_TRIES} tries for {url}")
        sleep(2 ** (attempt - 1))
    raise AssertionError("unreachable")
