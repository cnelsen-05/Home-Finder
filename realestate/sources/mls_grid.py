from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx


class MLSGridCredentialsMissing(RuntimeError):
    pass


@dataclass
class MLSGridAdapter:
    base_url: str | None = None
    token: str | None = None

    def __post_init__(self) -> None:
        self.base_url = self.base_url or os.getenv("MLS_GRID_BASE_URL")
        self.token = self.token or os.getenv("MLS_GRID_TOKEN")

    def is_configured(self) -> bool:
        return bool(self.base_url and self.token)

    def _require_configured(self) -> None:
        if not self.is_configured():
            raise MLSGridCredentialsMissing(
                "MLS Grid adapter is disabled until MLS_GRID_BASE_URL and MLS_GRID_TOKEN are set "
                "and the user confirms licensing and usage rights."
            )

    def fetch_listing(self, listing_key: str) -> dict[str, Any]:
        self._require_configured()
        url = f"{self.base_url.rstrip('/')}/Property('{listing_key}')"
        headers = {"Authorization": f"Bearer {self.token}"}
        response = httpx.get(url, headers=headers, timeout=20)
        response.raise_for_status()
        return response.json()
