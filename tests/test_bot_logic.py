from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from fleet_telegram_bot.bot import FleetTelegramBot, TruckSnapshot
from fleet_telegram_bot.state import StateStore


class FakeSamsara:
    def list_vehicles(self):
        return []

    def vehicle_stats(self, _types):
        return []

    def alert_incidents(self, _ids, _start):
        return []


class FakeTelegram:
    def __init__(self):
        self.messages = []

    def send_message(self, text):
        self.messages.append(text)


def config(tmp_path: Path):
    return {
        "state_db_path": str(tmp_path / "state.sqlite3"),
        "startup_suppresses_arrival_alerts": True,
        "samsara": {
            "stats_types": ["gps"],
            "maintenance_alert_configuration_ids": [],
        },
        "arrival_alerts": {
            "radius_miles": 1.0,
            "confirmation_seconds": 180,
            "cooldown_seconds": 1800,
            "require_speed_below_mph": 10,
            "locations": [
                {
                    "id": "lemont_yard",
                    "name": "Lemont Yard",
                    "latitude": 41.0,
                    "longitude": -88.0,
                }
            ],
        },
        "oil_change_alerts": {
            "enabled": True,
            "cooldown_seconds": 259200,
            "alert_keywords": ["oil change"],
            "oil_life_due_pct": 5,
            "internal_interval_miles": 25000,
            "last_oil_changes_path": str(tmp_path / "missing.json"),
        },
    }


def truck(lat: float, lon: float, trailer: str | None = None, oil_life_pct: float | None = None):
    return TruckSnapshot(
        id="truck-1",
        number="1025",
        trailer_number=trailer,
        latitude=lat,
        longitude=lon,
        speed_mph=0,
        odometer_miles=None,
        engine_hours=None,
        oil_life_pct=oil_life_pct,
        raw_vehicle={},
        raw_stats={},
    )


def make_bot(tmp_path: Path):
    telegram = FakeTelegram()
    bot = FleetTelegramBot(config(tmp_path), StateStore(str(tmp_path / "state.sqlite3")), FakeSamsara(), telegram)
    return bot, telegram


class BotLogicTests(unittest.TestCase):
    def test_startup_inside_location_does_not_send_arrival(self):
        with TemporaryDirectory() as directory:
            bot, telegram = make_bot(Path(directory))
            bot.initialize_arrival_state([truck(41.0, -88.0)], now=1000)
            bot.process_arrivals([truck(41.0, -88.0)], now=1300)
            self.assertEqual(telegram.messages, [])

    def test_arrival_requires_confirmation_and_includes_trailer(self):
        with TemporaryDirectory() as directory:
            bot, telegram = make_bot(Path(directory))
            bot.initialize_arrival_state([truck(42.0, -89.0)], now=1000)
            bot.process_arrivals([truck(41.0, -88.0, trailer="5308")], now=1060)
            bot.process_arrivals([truck(41.0, -88.0, trailer="5308")], now=1239)
            self.assertEqual(telegram.messages, [])
            bot.process_arrivals([truck(41.0, -88.0, trailer="5308")], now=1240)
            self.assertEqual(telegram.messages, ["🚛 Truck 1025 with trailer 5308 arrived at Lemont Yard."])

    def test_arrival_cooldown_is_per_location_truck_pair(self):
        with TemporaryDirectory() as directory:
            bot, telegram = make_bot(Path(directory))
            bot.initialize_arrival_state([truck(42.0, -89.0)], now=1000)
            bot.process_arrivals([truck(41.0, -88.0)], now=1100)
            bot.process_arrivals([truck(41.0, -88.0)], now=1280)
            bot.process_arrivals([truck(42.0, -89.0)], now=1290)
            bot.process_arrivals([truck(41.0, -88.0)], now=1300)
            bot.process_arrivals([truck(41.0, -88.0)], now=1480)
            self.assertEqual(telegram.messages, ["🚛 Truck 1025 arrived at Lemont Yard."])

    def test_oil_change_alert_has_three_day_cooldown(self):
        with TemporaryDirectory() as directory:
            bot, telegram = make_bot(Path(directory))
            bot.process_oil_change_alerts([truck(42.0, -89.0, oil_life_pct=3)], now=1000)
            bot.process_oil_change_alerts([truck(42.0, -89.0, oil_life_pct=3)], now=1000 + 86400)
            self.assertEqual(telegram.messages, ["🛢️ Truck 1025 is due for oil change."])


if __name__ == "__main__":
    unittest.main()
