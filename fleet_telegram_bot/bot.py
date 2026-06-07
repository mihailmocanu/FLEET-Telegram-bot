from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from .clients import SamsaraClient, TelegramClient
from .state import StateStore
from .utils import clean_number, first_present, haversine_miles, meters_to_miles


LOGGER = logging.getLogger("fleet_telegram_bot")


@dataclass(frozen=True)
class Location:
    id: str
    name: str
    latitude: float
    longitude: float
    radius_miles: float


@dataclass
class TruckSnapshot:
    id: str
    number: str
    trailer_number: Optional[str]
    latitude: Optional[float]
    longitude: Optional[float]
    speed_mph: Optional[float]
    odometer_miles: Optional[float]
    engine_hours: Optional[float]
    oil_life_pct: Optional[float]
    raw_vehicle: dict[str, Any]
    raw_stats: dict[str, Any]


def load_config(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as file:
        config = json.load(file)
    apply_env_overrides(config)
    return config


def apply_env_overrides(config: dict[str, Any]) -> None:
    for location in config["arrival_alerts"]["locations"]:
        prefix = f"FLEET_LOCATION_{location['id'].upper()}"
        lat = os.environ.get(f"{prefix}_LATITUDE")
        lon = os.environ.get(f"{prefix}_LONGITUDE")
        radius = os.environ.get(f"{prefix}_RADIUS_MILES")
        if lat:
            location["latitude"] = float(lat)
        if lon:
            location["longitude"] = float(lon)
        if radius:
            location["radius_miles"] = float(radius)

    maintenance_ids = os.environ.get("FLEET_MAINTENANCE_ALERT_CONFIGURATION_IDS")
    if maintenance_ids:
        config["samsara"]["maintenance_alert_configuration_ids"] = [
            item.strip() for item in maintenance_ids.split(",") if item.strip()
        ]

    poll_interval = os.environ.get("FLEET_POLL_INTERVAL_SECONDS")
    if poll_interval:
        config["poll_interval_seconds"] = int(poll_interval)


def env_value(config: dict[str, Any], section: str, key: str) -> str:
    env_name = config[section][key]
    value = os.environ.get(env_name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {env_name}")
    return value


def _stat_value(stats: dict[str, Any], name: str) -> Any:
    value = stats.get(name)
    if isinstance(value, dict):
        return value.get("value")
    return value


def _gps_from_stats(stats: dict[str, Any]) -> tuple[Optional[float], Optional[float], Optional[float]]:
    gps = stats.get("gps")
    if isinstance(gps, dict):
        lat = first_present(gps, [["latitude"], ["lat"]])
        lon = first_present(gps, [["longitude"], ["lon"], ["lng"]])
        speed = first_present(gps, [["speedMilesPerHour"], ["speedMph"], ["speed"]])
        return _float_or_none(lat), _float_or_none(lon), _float_or_none(speed)
    return None, None, None


def _float_or_none(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _truck_number(vehicle: dict[str, Any], stats: dict[str, Any]) -> str:
    value = first_present(
        vehicle,
        [
            ["name"],
            ["externalIds", "samsara.serial"],
            ["externalIds", "samsara.vin"],
            ["vin"],
            ["id"],
        ],
    )
    return clean_number(value) or clean_number(stats.get("name")) or str(vehicle.get("id") or stats.get("id"))


def _trailer_number(vehicle: dict[str, Any], stats: dict[str, Any]) -> Optional[str]:
    candidates = [
        first_present(vehicle, [["trailer", "name"], ["trailer", "id"]]),
        first_present(vehicle, [["currentTrailer", "name"], ["currentTrailer", "id"]]),
        first_present(vehicle, [["trailerAssignment", "trailer", "name"], ["trailerAssignment", "trailer", "id"]]),
        first_present(stats, [["trailer", "name"], ["trailer", "id"]]),
    ]
    for candidate in candidates:
        number = clean_number(candidate)
        if number:
            return number
    return None


def build_snapshots(vehicles: list[dict[str, Any]], stats_rows: list[dict[str, Any]]) -> list[TruckSnapshot]:
    vehicles_by_id = {str(v.get("id")): v for v in vehicles if v.get("id") is not None}
    stats_by_id = {str(s.get("id") or s.get("vehicleId")): s for s in stats_rows if s.get("id") or s.get("vehicleId")}
    all_ids = sorted(set(vehicles_by_id) | set(stats_by_id))
    snapshots: list[TruckSnapshot] = []
    for truck_id in all_ids:
        vehicle = vehicles_by_id.get(truck_id, {})
        stats = stats_by_id.get(truck_id, {})
        lat, lon, speed = _gps_from_stats(stats)
        odometer = meters_to_miles(_float_or_none(_stat_value(stats, "obdOdometerMeters")))
        engine_hours = _float_or_none(_stat_value(stats, "engineHours"))
        oil_life_raw = _float_or_none(_stat_value(stats, "oilLifeRemainingMilliPercent"))
        oil_life_pct = None if oil_life_raw is None else oil_life_raw / 1000
        snapshots.append(
            TruckSnapshot(
                id=truck_id,
                number=_truck_number(vehicle, stats),
                trailer_number=_trailer_number(vehicle, stats),
                latitude=lat,
                longitude=lon,
                speed_mph=speed,
                odometer_miles=odometer,
                engine_hours=engine_hours,
                oil_life_pct=oil_life_pct,
                raw_vehicle=vehicle,
                raw_stats=stats,
            )
        )
    return snapshots


class FleetTelegramBot:
    def __init__(
        self,
        config: dict[str, Any],
        store: StateStore,
        samsara: SamsaraClient,
        telegram: TelegramClient,
    ) -> None:
        self.config = config
        self.store = store
        self.samsara = samsara
        self.telegram = telegram
        arrival = config["arrival_alerts"]
        self.locations = [
            Location(
                id=item["id"],
                name=item["name"],
                latitude=float(item["latitude"]),
                longitude=float(item["longitude"]),
                radius_miles=float(item.get("radius_miles", arrival["radius_miles"])),
            )
            for item in arrival["locations"]
        ]

    def run_forever(self) -> None:
        stop = False

        def _stop(_signum: int, _frame: object) -> None:
            nonlocal stop
            stop = True

        signal.signal(signal.SIGINT, _stop)
        signal.signal(signal.SIGTERM, _stop)

        while not stop:
            started = time.time()
            try:
                self.run_once(int(started))
            except Exception:
                LOGGER.exception("Polling cycle failed")
            elapsed = time.time() - started
            time.sleep(max(1, int(self.config.get("poll_interval_seconds", 60) - elapsed)))

    def run_once(self, now: int | None = None) -> None:
        now = now or int(time.time())
        snapshots = self.fetch_snapshots()
        startup_done = self.store.get_setting("startup_initialized") == "true"
        if not startup_done and self.config.get("startup_suppresses_arrival_alerts", True):
            self.initialize_arrival_state(snapshots, now)
            self.store.set_setting("startup_initialized", "true")
            LOGGER.info("Startup state initialized for %s trucks", len(snapshots))
            return
        self.process_arrivals(snapshots, now)
        self.process_oil_change_alerts(snapshots, now)

    def fetch_snapshots(self) -> list[TruckSnapshot]:
        vehicles = self.samsara.list_vehicles()
        stats = self.samsara.vehicle_stats(self.config["samsara"]["stats_types"])
        return build_snapshots(vehicles, stats)

    def initialize_arrival_state(self, snapshots: list[TruckSnapshot], now: int) -> None:
        for truck in snapshots:
            for location in self.locations:
                state = "INSIDE_LOCATION" if self._inside_location(truck, location) else "OUTSIDE_LOCATION"
                self.store.set_geofence_state(truck.id, location.id, state, now)
                self.store.clear_pending(truck.id, location.id)

    def process_arrivals(self, snapshots: list[TruckSnapshot], now: int) -> None:
        arrival_config = self.config["arrival_alerts"]
        confirmation_seconds = int(arrival_config["confirmation_seconds"])
        speed_limit = arrival_config.get("require_speed_below_mph")
        cooldown_seconds = int(arrival_config["cooldown_seconds"])
        for truck in snapshots:
            for location in self.locations:
                inside = self._inside_location(truck, location)
                previous = self.store.get_geofence_state(truck.id, location.id)
                if not inside:
                    self.store.set_geofence_state(truck.id, location.id, "OUTSIDE_LOCATION", now)
                    self.store.clear_pending(truck.id, location.id)
                    continue
                if previous == "INSIDE_LOCATION":
                    continue
                pending_since = self.store.get_pending_since(truck.id, location.id)
                if pending_since is None:
                    self.store.set_pending_since(truck.id, location.id, now)
                    continue
                if now - pending_since < confirmation_seconds:
                    continue
                if speed_limit is not None and truck.speed_mph is not None and truck.speed_mph > float(speed_limit):
                    continue
                if self._in_cooldown("arrival", truck.id, location.id, now, cooldown_seconds):
                    self.store.set_geofence_state(truck.id, location.id, "INSIDE_LOCATION", now)
                    self.store.clear_pending(truck.id, location.id)
                    continue
                self.telegram.send_message(self._arrival_message(truck, location))
                self.store.set_cooldown("arrival", truck.id, location.id, now)
                self.store.set_geofence_state(truck.id, location.id, "INSIDE_LOCATION", now)
                self.store.clear_pending(truck.id, location.id)

    def process_oil_change_alerts(self, snapshots: list[TruckSnapshot], now: int) -> None:
        oil_config = self.config.get("oil_change_alerts", {})
        if not oil_config.get("enabled", True):
            return
        due_by_truck = self._oil_due_from_internal_rules(snapshots)
        due_by_truck.update(self._oil_due_from_samsara_incidents(now))
        cooldown_seconds = int(oil_config["cooldown_seconds"])
        for truck in snapshots:
            cycle_key = due_by_truck.get(truck.id)
            if not cycle_key:
                continue
            existing = self.store.get_cooldown("oil_change", truck.id)
            if existing and existing["cycle_key"] == cycle_key and now - int(existing["last_sent_at"]) < cooldown_seconds:
                continue
            self.telegram.send_message(f"🛢️ Truck {truck.number} is due for oil change.")
            self.store.set_cooldown("oil_change", truck.id, "", now, cycle_key)

    def _inside_location(self, truck: TruckSnapshot, location: Location) -> bool:
        if truck.latitude is None or truck.longitude is None:
            return False
        distance = haversine_miles(truck.latitude, truck.longitude, location.latitude, location.longitude)
        return distance <= location.radius_miles

    def _in_cooldown(self, alert_type: str, truck_id: str, location_id: str, now: int, seconds: int) -> bool:
        row = self.store.get_cooldown(alert_type, truck_id, location_id)
        return bool(row and now - int(row["last_sent_at"]) < seconds)

    def _arrival_message(self, truck: TruckSnapshot, location: Location) -> str:
        if truck.trailer_number:
            return f"🚛 Truck {truck.number} with trailer {truck.trailer_number} arrived at {location.name}."
        return f"🚛 Truck {truck.number} arrived at {location.name}."

    def _oil_due_from_samsara_incidents(self, now: int) -> dict[str, str]:
        config = self.config["samsara"]
        ids = config.get("maintenance_alert_configuration_ids", [])
        if not ids:
            return {}
        since = datetime.fromtimestamp(now, UTC) - timedelta(days=7)
        incidents = self.samsara.alert_incidents(ids, since.isoformat().replace("+00:00", "Z"))
        keywords = [k.lower() for k in self.config["oil_change_alerts"]["alert_keywords"]]
        due: dict[str, str] = {}
        for incident in incidents:
            text = json.dumps(incident, sort_keys=True).lower()
            if not any(keyword in text for keyword in keywords):
                continue
            if "resolved" in text and "active" not in text:
                continue
            truck_id = clean_number(
                first_present(
                    incident,
                    [
                        ["source", "id"],
                        ["vehicle", "id"],
                        ["details", "vehicle", "id"],
                        ["asset", "id"],
                    ],
                )
            )
            if truck_id:
                due[truck_id] = clean_number(incident.get("id")) or "samsara-maintenance-alert"
        return due

    def _oil_due_from_internal_rules(self, snapshots: list[TruckSnapshot]) -> dict[str, str]:
        oil_config = self.config["oil_change_alerts"]
        due: dict[str, str] = {}
        oil_life_due_pct = oil_config.get("oil_life_due_pct")
        last_oil_changes = self._load_last_oil_changes(oil_config.get("last_oil_changes_path"))
        interval_miles = oil_config.get("internal_interval_miles")
        for truck in snapshots:
            if oil_life_due_pct is not None and truck.oil_life_pct is not None and truck.oil_life_pct <= float(oil_life_due_pct):
                due[truck.id] = f"oil-life-{int(truck.oil_life_pct)}"
                continue
            record = last_oil_changes.get(truck.number) or last_oil_changes.get(truck.id)
            if interval_miles and record and truck.odometer_miles is not None:
                last_miles = _float_or_none(record.get("odometer_miles"))
                if last_miles is not None and truck.odometer_miles - last_miles >= float(interval_miles):
                    due[truck.id] = f"odometer-{int(last_miles)}-{int(float(interval_miles))}"
        return due

    def _load_last_oil_changes(self, path: str | None) -> dict[str, Any]:
        raw = os.environ.get("FLEET_LAST_OIL_CHANGES_JSON")
        if raw:
            return json.loads(raw)
        if not path:
            return {}
        file_path = Path(path)
        if not file_path.exists():
            return {}
        with open(file_path, "r", encoding="utf-8") as file:
            return json.load(file)


def main() -> None:
    parser = argparse.ArgumentParser(description="FLEET Department Telegram bot")
    parser.add_argument("--config", default="config.json", help="Path to bot config JSON")
    parser.add_argument("--once", action="store_true", help="Run one polling cycle and exit")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(level=getattr(logging, args.log_level.upper()), format="%(asctime)s %(levelname)s %(message)s")
    config = load_config(args.config)
    store = StateStore(config["state_db_path"])
    samsara = SamsaraClient(
        env_value(config, "samsara", "api_token_env"),
        config["samsara"].get("base_url", "https://api.samsara.com"),
    )
    telegram = TelegramClient(
        env_value(config, "telegram", "bot_token_env"),
        env_value(config, "telegram", "chat_id_env"),
    )
    bot = FleetTelegramBot(config, store, samsara, telegram)
    if args.once:
        bot.run_once()
    else:
        bot.run_forever()


if __name__ == "__main__":
    main()
