from __future__ import annotations

import json
import time
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class HttpError(RuntimeError):
    pass


class JsonHttpClient:
    def __init__(self, timeout_seconds: int = 30) -> None:
        self.timeout_seconds = timeout_seconds

    def request_json(
        self,
        method: str,
        url: str,
        headers: dict[str, str] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        req = Request(url, data=body, method=method, headers=headers or {})
        if payload is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urlopen(req, timeout=self.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except Exception as exc:  # urllib exceptions vary by failure mode.
            raise HttpError(str(exc)) from exc
        return json.loads(raw) if raw else {}


class SamsaraClient:
    def __init__(self, token: str, base_url: str = "https://api.samsara.com", http: JsonHttpClient | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.http = http or JsonHttpClient()
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }

    def _get_paginated(self, path: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        query = dict(params or {})
        while True:
            url = f"{self.base_url}{path}"
            if query:
                url = f"{url}?{urlencode(query, doseq=True)}"
            response = self.http.request_json("GET", url, self.headers)
            data = response.get("data", [])
            if isinstance(data, list):
                items.extend(data)
            pagination = response.get("pagination") or {}
            if not pagination.get("hasNextPage"):
                return items
            cursor = pagination.get("endCursor")
            if not cursor:
                return items
            query["after"] = cursor

    def list_vehicles(self) -> list[dict[str, Any]]:
        return self._get_paginated("/fleet/vehicles")

    def vehicle_stats(self, stat_types: list[str]) -> list[dict[str, Any]]:
        return self._get_paginated("/fleet/vehicles/stats", {"types": ",".join(stat_types)})

    def alert_incidents(self, configuration_ids: list[str], start_time_rfc3339: str) -> list[dict[str, Any]]:
        if not configuration_ids:
            return []
        return self._get_paginated(
            "/alerts/incidents/stream",
            {
                "configurationIds": ",".join(configuration_ids),
                "startTime": start_time_rfc3339,
            },
        )


class TelegramClient:
    def __init__(self, bot_token: str, chat_id: str, http: JsonHttpClient | None = None) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.http = http or JsonHttpClient()

    def send_message(self, text: str) -> None:
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        self.http.request_json(
            "POST",
            url,
            payload={
                "chat_id": self.chat_id,
                "text": text,
                "disable_web_page_preview": True,
            },
        )
        time.sleep(0.1)
