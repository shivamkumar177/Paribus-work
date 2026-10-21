"""
Thin HTTP client for the given Hospital Directory API.

Kept deliberately small and transport-injectable so tests can supply a mock
transport instead of hitting the network.
"""

from typing import Any, Optional

import httpx

from app.core.config import settings
from app.core.logging import logger


class HospitalAPIError(RuntimeError):
    """Raised when the Hospital Directory API returns an error."""

    def __init__(self, message: str, status_code: Optional[int] = None, body: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class HospitalClient:
    def __init__(
        self,
        base_url: Optional[str] = None,
        timeout: Optional[float] = None,
        transport: Optional[httpx.BaseTransport] = None,
    ) -> None:
        self.base_url = (base_url or settings.HOSPITAL_API_BASE_URL).rstrip("/")
        self.timeout = timeout or settings.HOSPITAL_API_TIMEOUT
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=self.timeout,
            transport=transport,
            headers={"Accept": "application/json"},
        )

    def close(self) -> None:
        self._client.close()

    # ── Individual hospital ───────────────────────────────────────────────────

    def create_hospital(self, payload: dict) -> dict:
        return self._request("POST", "/hospitals/", json=payload)

    # ── Batch operations ──────────────────────────────────────────────────────

    def get_batch(self, batch_id: str) -> Any:
        return self._request("GET", f"/hospitals/batch/{batch_id}")

    def activate_batch(self, batch_id: str) -> Any:
        return self._request("PATCH", f"/hospitals/batch/{batch_id}/activate")

    # ── Internals ─────────────────────────────────────────────────────────────

    def _request(self, method: str, url: str, **kwargs) -> Any:
        try:
            response = self._client.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            raise HospitalAPIError(f"Network error calling {method} {url}: {exc}") from exc

        if response.status_code >= 400:
            body: Any
            try:
                body = response.json()
            except ValueError:
                body = response.text
            logger.warning(f"[hospital-api] {method} {url} -> {response.status_code}: {body}")
            raise HospitalAPIError(
                f"Hospital API {method} {url} failed ({response.status_code})",
                status_code=response.status_code,
                body=body,
            )

        if not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            return response.text


# Default singleton used by the service layer.
hospital_client = HospitalClient()
