"""
Loader danych ruchu drogowego.

Wejście:
  /app/data/raw/traffic/traffic_measurements.csv

Wyjście:
  Kafka topic: warszawa-raw-traffic

Plik CSV reprezentuje tabelaryczne dane pomiarowe zgodne ze strukturą danych,
które mogą pochodzić z ZDM lub GDDKiA.
"""

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

from confluent_kafka import Producer


KAFKA_BOOTSTRAP_SERVERS = "localhost:9092"
TOPIC = "warszawa-raw-traffic"
INPUT_PATH = Path("/app/data/raw/traffic/traffic_measurements.csv")


REQUIRED_COLUMNS = {
    "source",
    "station_id",
    "station_name",
    "road_name",
    "direction",
    "lat",
    "lon",
    "measurement_time",
    "period_minutes",
    "vehicle_count",
    "avg_speed_kmh",
}


def delivery_report(err, msg):
    if err is not None:
        print(f"Błąd dostarczenia wiadomości: {err}")
    else:
        print(
            f"Message delivered to topic: {msg.topic()} "
            f"[partition: {msg.partition()}, offset: {msg.offset()}]"
        )


def normalize_row(row: dict) -> dict:
    return {
        "source": row["source"],
        "station_id": row["station_id"],
        "station_name": row["station_name"],
        "road_name": row["road_name"],
        "direction": row["direction"],
        "lat": float(row["lat"]),
        "lon": float(row["lon"]),
        "measurement_time": row["measurement_time"],
        "period_minutes": int(row["period_minutes"]),
        "vehicle_count": int(row["vehicle_count"]),
        "avg_speed_kmh": float(row["avg_speed_kmh"]) if row["avg_speed_kmh"] else None,
        "_metadata": {
            "ingested_at_utc": datetime.now(timezone.utc).isoformat(),
            "loader": "traffic_loader.py",
        },
    }


def main():
    if not INPUT_PATH.exists():
        raise FileNotFoundError(f"Nie znaleziono pliku: {INPUT_PATH}")

    producer = Producer({
        "bootstrap.servers": KAFKA_BOOTSTRAP_SERVERS,
        "client.id": "traffic-loader",
    })

    print(f"1. Czytanie pliku CSV: {INPUT_PATH}")

    sent = 0

    with INPUT_PATH.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)

        missing = REQUIRED_COLUMNS - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"Brakuje kolumn w CSV: {sorted(missing)}")

        for row in reader:
            payload = normalize_row(row)
            key = f"{payload['station_id']}:{payload['measurement_time']}"

            producer.produce(
                TOPIC,
                key=key.encode("utf-8"),
                value=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                callback=delivery_report,
            )

            sent += 1

    producer.flush()

    print(f"Gotowe. Wysłano rekordów do topicu {TOPIC}: {sent}")


if __name__ == "__main__":
    main()
