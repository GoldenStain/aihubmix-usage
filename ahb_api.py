"""Query AIHubMix billing, model, and account endpoints over HTTP."""

from __future__ import annotations

import http.client
import json
import time
import urllib.error
import urllib.parse
import urllib.request


DEFAULT_BASE = "https://aihubmix.com"
USER_AGENT = "aihubmix-usage/1.0"


class ApiError(Exception):
    """Raised when an AIHubMix endpoint is unreachable or returns an error."""


def normalize_base(url: str) -> str:
    """Strip a trailing slash and an optional OpenAI-style /v1 suffix from a base URL."""
    text = (url or "").strip().rstrip("/")
    if text.endswith("/v1"):
        text = text[:-3].rstrip("/")
    return text or DEFAULT_BASE


def auth_headers(token: str) -> dict[str, str]:
    """Build the Authorization header, matching the scheme each key format expects."""
    if not token:
        return {}
    scheme = "Bearer" if token.startswith("sk-") else ""
    return {"Authorization": f"{scheme} {token}".strip()}


class Client:
    """A minimal AIHubMix HTTP client that retries through transient connection resets."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE,
        key: str = "",
        timeout: float = 20.0,
        retries: int = 5,
        backoff: float = 0.4,
    ):
        self.base = normalize_base(base_url)
        self.key = key
        self.timeout = timeout
        self.retries = max(1, int(retries))
        self.backoff = backoff

    def _get(self, path: str, params: dict | None = None, key: str | None = None) -> dict:
        url = f"{self.base}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
        headers.update(auth_headers(self.key if key is None else key))
        last = "unknown error"
        for attempt in range(self.retries):
            request = urllib.request.Request(url, headers=headers, method="GET")
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    body = response.read().decode("utf-8", "replace")
                return json.loads(body) if body.strip() else {}
            except urllib.error.HTTPError as error:
                detail = error.read().decode("utf-8", "replace").strip()
                if error.code >= 500 and attempt < self.retries - 1:
                    last = f"HTTP {error.code}"
                    time.sleep(self.backoff * (attempt + 1))
                    continue
                raise ApiError(_describe_http(error.code, path, detail)) from None
            except (
                urllib.error.URLError,
                http.client.HTTPException,
                ConnectionError,
                TimeoutError,
                json.JSONDecodeError,
            ) as error:
                last = f"{type(error).__name__}: {error}"
                if attempt < self.retries - 1:
                    time.sleep(self.backoff * (attempt + 1))
                    continue
        raise ApiError(f"cannot reach {self.base}{path}: {last}")

    def remain(self) -> float:
        """Remaining balance in US dollars for the current key."""
        return float(self._get("/dashboard/billing/remain").get("total_usage", 0.0) or 0.0)

    def subscription(self) -> dict:
        """Billing subscription object with the quota limits in US dollars."""
        return self._get("/dashboard/billing/subscription")

    def usage(self, start: str | None = None, end: str | None = None) -> float:
        """Lifetime used amount in cents; the date range is accepted but not filtered."""
        params = {}
        if start:
            params["start_date"] = start
        if end:
            params["end_date"] = end
        return float(self._get("/dashboard/billing/usage", params or None).get("total_usage", 0.0) or 0.0)

    def system_status(self) -> dict:
        """Public system status, including the quota-per-dollar unit and system name."""
        return self._get("/api/status").get("data", {}) or {}

    def models(self) -> list[dict]:
        """Models the current key may call."""
        data = self._get("/v1/models").get("data", [])
        return data if isinstance(data, list) else []

    def user_self(self, manage_key: str) -> dict:
        """Account profile and balance; requires a Manage Key (access token)."""
        return self._get("/api/user/self", key=manage_key).get("data", {}) or {}

    def tokens(self, manage_key: str) -> list[dict]:
        """Per-key quota objects; requires a Manage Key (access token)."""
        data = self._get("/api/token/", key=manage_key).get("data", [])
        return data if isinstance(data, list) else []


def _describe_http(code: int, path: str, detail: str) -> str:
    hint = {
        401: "the key is missing, invalid, or is not a Manage Key",
        403: "the key is not allowed to read this endpoint",
        404: "endpoint not found on this host",
    }.get(code, "")
    message = f"HTTP {code} for {path}"
    if hint:
        message += f" ({hint})"
    if detail:
        message += f": {detail[:200]}"
    return message
