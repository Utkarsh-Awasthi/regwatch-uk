"""A polite HTTP client: identifies itself, rate-limits, retries transient errors."""
from __future__ import annotations

import time

import httpx

from ..config import settings


class Fetcher:
    def __init__(self, delay_s: float | None = None, client: httpx.Client | None = None):
        self.delay_s = settings.request_delay_s if delay_s is None else delay_s
        self.client = client or httpx.Client(
            headers={"User-Agent": settings.user_agent},
            timeout=httpx.Timeout(30.0, connect=10.0),
            follow_redirects=True,
        )
        self._last = 0.0

    def _wait(self):
        gap = time.monotonic() - self._last
        if gap < self.delay_s:
            time.sleep(self.delay_s - gap)
        self._last = time.monotonic()

    def get(self, url: str, params: dict | None = None, retries: int = 3) -> httpx.Response | None:
        """GET with backoff. Returns None for 404/410 (the page is gone)."""
        for attempt in range(retries + 1):
            self._wait()
            try:
                r = self.client.get(url, params=params)
            except httpx.TransportError:
                if attempt == retries:
                    raise
                time.sleep(2 ** attempt)
                continue
            if r.status_code in (404, 410):
                return None
            if r.status_code == 429 or r.status_code >= 500:
                if attempt == retries:
                    r.raise_for_status()
                time.sleep(float(r.headers.get("retry-after", 2 ** attempt)))
                continue
            r.raise_for_status()
            return r
        return None

    def json(self, url: str, params: dict | None = None):
        r = self.get(url, params)
        return r.json() if r is not None else None

    def text(self, url: str, params: dict | None = None) -> str | None:
        r = self.get(url, params)
        return r.text if r is not None else None
