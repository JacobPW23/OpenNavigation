import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from confluent_kafka import Producer


KAFKA_BOOTSTRAP_SERVERS = "localhost:9092"
TOPIC = "warszawa-raw-traffic"
INPUT_PATH = Path("/app/data/raw/traffic/zdm_real_traffic.xlsx")


def delivery_report(err, msg):
    if err is not None:
        print(f"Błąd dostarczenia wiadomości: {err}")
    else:
        print(
            f"Message delivered to topic: {msg.topic()} "
            f"[partition: {msg.partition()}, offset: {msg.offset()}]"
        )


def clean(value):
    if pd.isna(value):
        return None
    return str(value).strip()


def parse_date_range(value: str) -> str:
    text = clean(value) or ""
    match = re.search(r"\d{4}-\d{2}-\d{2}", text)
    if not match:
        raise ValueError(f"Nie udało się odczytać daty z: {value}")
    return match.group(0)


def parse_gps(value: str) -> tuple[float | None, float | None]:
    text = clean(value)
    if not text:
        return None, None

    parts = text.split()
    if len(parts) < 2:
        return None, None

    return float(parts[0]), float(parts[1])


def parse_direction(value: str) -> str:
    text = clean(value) or ""
    return text.replace("Kierunek:", "").strip()


def parse_hour(value) -> str:
    if pd.isna(value):
        return ""
    if isinstance(value, float):
        value = int(value)
    return str(value).zfill(2)


def row_to_int(value):
    if pd.isna(value):
        return None
    return int(value)


def parse_sheet(path: Path, sheet_name: str) -> list[dict]:
    df = pd.read_excel(path, sheet_name=sheet_name, header=None)

    station_id = clean(df.iloc[0, 1])
    date_raw = clean(df.iloc[1, 1])
    road_name = clean(df.iloc[2, 1])
    section = clean(df.iloc[3, 1])
    gps_raw = clean(df.iloc[4, 1])

    if not station_id or not date_raw or not road_name:
        print(f"Pomijam arkusz {sheet_name}: brak podstawowych metadanych.")
        return []

    date = parse_date_range(date_raw)
    lat, lon = parse_gps(gps_raw)

    direction_columns = []

    for col_idx in [1, 3]:
        direction_raw = clean(df.iloc[8, col_idx])
        if direction_raw and "Kierunek" in direction_raw:
            direction_columns.append((col_idx, parse_direction(direction_raw)))

    records = []

    for row_idx in range(9, len(df)):
        hour = parse_hour(df.iloc[row_idx, 0])
        if not hour:
            continue

        if not hour.isdigit():
            continue

        measurement_time = f"{date} {hour}:00:00"

        for col_idx, direction in direction_columns:
            vehicle_count = row_to_int(df.iloc[row_idx, col_idx])

            if vehicle_count is None:
                continue

            records.append({
                "source": "zdm_apr_real",
                "station_id": station_id,
                "station_name": f"{road_name} / {section}",
                "road_name": road_name,
                "section": section,
                "direction": direction,
                "lat": lat,
                "lon": lon,
                "measurement_time": measurement_time,
                "period_minutes": 60,
                "vehicle_count": vehicle_count,
                "avg_speed_kmh": None,
                "_metadata": {
                    "ingested_at_utc": datetime.now(timezone.utc).isoformat(),
                    "loader": "zdm_traffic_loader.py",
                    "source_file": str(path),
                    "sheet_name": sheet_name,
                },
            })

    return records


def main():
    if not INPUT_PATH.exists():
        raise FileNotFoundError(f"Nie znaleziono pliku: {INPUT_PATH}")

    producer = Producer({
        "bootstrap.servers": KAFKA_BOOTSTRAP_SERVERS,
        "client.id": "zdm-traffic-loader",
    })

    xls = pd.ExcelFile(INPUT_PATH)

    print(f"1. Czytanie pliku ZDM: {INPUT_PATH}")
    print(f"Liczba arkuszy: {len(xls.sheet_names)}")

    total_sent = 0

    for sheet_name in xls.sheet_names:
        records = parse_sheet(INPUT_PATH, sheet_name)

        print(f"Arkusz {sheet_name}: rekordów {len(records)}")

        for record in records:
            key = f"{record['station_id']}:{record['direction']}:{record['measurement_time']}"

            producer.produce(
                TOPIC,
                key=key.encode("utf-8"),
                value=json.dumps(record, ensure_ascii=False).encode("utf-8"),
                callback=delivery_report,
            )

            total_sent += 1

    producer.flush()

    print(f"Gotowe. Wysłano rekordów ZDM do topicu {TOPIC}: {total_sent}")


if __name__ == "__main__":
    main()
