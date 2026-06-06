# FLEET Department Telegram Bot

Bot Python pentru alerte în grupul Telegram al departamentului FLEET:

- sosire camion la Lemont Yard, Romeoville Shop sau Mokena Shop;
- oil change / scheduled maintenance due.

Botul folosește Samsara REST API și salvează starea într-o bază persistentă, ca restarturile să nu producă alerte false. Local folosește SQLite; pe Railway folosește automat Postgres când există `DATABASE_URL`.

## Setup

1. Copiază configurația:

   ```bash
   cp config.example.json config.json
   cp .env.example .env
   ```

2. Editează `config.json`:

   - setează coordonatele reale pentru `Lemont Yard`, `Romeoville Shop`, `Mokena Shop`;
   - lasă raza la `1.0` mile sau ajustează per locație cu `radius_miles`;
   - adaugă ID-urile alertelor Samsara în `maintenance_alert_configuration_ids`, dacă există alerte Scheduled Maintenance configurate în Samsara;
   - ajustează `last_oil_changes_path` dacă vrei calcul intern după ultimul odometer de oil change.

3. Editează `.env`:

   ```bash
   SAMSARA_API_TOKEN=...
   TELEGRAM_BOT_TOKEN=...
   TELEGRAM_CHAT_ID=...
   FLEET_LOCATION_LEMONT_YARD_LATITUDE=...
   FLEET_LOCATION_LEMONT_YARD_LONGITUDE=...
   FLEET_LOCATION_ROMEOVILLE_SHOP_LATITUDE=...
   FLEET_LOCATION_ROMEOVILLE_SHOP_LONGITUDE=...
   FLEET_LOCATION_MOKENA_SHOP_LATITUDE=...
   FLEET_LOCATION_MOKENA_SHOP_LONGITUDE=...
   ```

4. Încarcă variabilele și pornește botul:

   ```bash
   set -a
   source .env
   set +a
   python3 -m fleet_telegram_bot --config config.json
   ```

Pentru un test rapid cu un singur ciclu:

```bash
python3 -m fleet_telegram_bot --config config.json --once
```

## Railway

Repository-ul include `railway.json` și `Procfile`, deci Railway poate porni workerul cu:

```bash
python -m fleet_telegram_bot --config config.example.json
```

În Railway trebuie adăugat un serviciu Postgres în același proiect. Railway va injecta `DATABASE_URL`, iar botul va crea tabelele automat la prima pornire.

Variabile necesare în Railway:

```text
SAMSARA_API_TOKEN
TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID
FLEET_LOCATION_LEMONT_YARD_LATITUDE
FLEET_LOCATION_LEMONT_YARD_LONGITUDE
FLEET_LOCATION_ROMEOVILLE_SHOP_LATITUDE
FLEET_LOCATION_ROMEOVILLE_SHOP_LONGITUDE
FLEET_LOCATION_MOKENA_SHOP_LATITUDE
FLEET_LOCATION_MOKENA_SHOP_LONGITUDE
```

Variabile opționale:

```text
FLEET_MAINTENANCE_ALERT_CONFIGURATION_IDS
FLEET_POLL_INTERVAL_SECONDS
FLEET_LAST_OIL_CHANGES_JSON
```

## Reguli Implementate

Arrival alerts:

- la startup, camioanele deja aflate în geofence sunt marcate ca `INSIDE_LOCATION` fără mesaj;
- mesajul se trimite doar la tranziția `OUTSIDE_LOCATION` -> `INSIDE_LOCATION`;
- intrarea trebuie confirmată timp de `confirmation_seconds`, implicit 180 secunde;
- opțional verifică viteza sub `require_speed_below_mph`, implicit 10 mph;
- cooldown-ul este separat per camion și per locație, implicit 30 minute;
- trailerul este inclus doar când există și este valid.

Oil change alerts:

- trimite maximum o alertă la 3 zile per truck pentru același ciclu;
- poate folosi incidente Samsara Scheduled Maintenance, dacă sunt configurate ID-urile alertelor;
- poate calcula intern după `oilLifeRemainingMilliPercent` sau după odometer și ultimul oil change salvat local.

## Mesaje

Cu trailer:

```text
🚛 Truck 1025 with trailer 5308 arrived at Lemont Yard.
```

Fără trailer:

```text
🚛 Truck 1025 arrived at Romeoville Shop.
```

Oil change:

```text
🛢️ Truck 1025 is due for oil change.
```

## Teste

```bash
python3 -m unittest discover -s tests -v
```

## Note Samsara

Botul folosește endpointurile oficiale Samsara pentru vehicles, telematics stats și, opțional, alert incidents:

- Vehicles / quickstart: https://developers.samsara.com/docs/getting-started
- Vehicle stats snapshot: https://developers.samsara.com/docs/vehicle-stats-snapshot
- Planned & preventative maintenance: https://developers.samsara.com/docs/planned-preventative-maintenance
- Alert incidents stream: https://developers.samsara.com/reference/getincidents

Tokenul Samsara trebuie să aibă permisiuni pentru Vehicles / Vehicle Statistics și Alerts dacă folosești partea de Scheduled Maintenance incidents.
