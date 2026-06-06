from __future__ import annotations

import math
from typing import Any, Iterable, Optional


EARTH_RADIUS_MILES = 3958.7613
METERS_PER_MILE = 1609.344


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * EARTH_RADIUS_MILES * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def nested_get(data: Any, keys: Iterable[str]) -> Any:
    current = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def first_present(data: dict[str, Any], paths: Iterable[Iterable[str]]) -> Any:
    for path in paths:
        value = nested_get(data, path)
        if value not in (None, "", [], {}):
            return value
    return None


def clean_number(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"unknown", "null", "none", "n/a", "na"}:
        return None
    return text


def meters_to_miles(value: float | int | None) -> Optional[float]:
    if value is None:
        return None
    return float(value) / METERS_PER_MILE
