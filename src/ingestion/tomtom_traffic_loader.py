"""
Loader danych real-time traffic z TomTom Traffic Flow API.

Wejście:
  TomTom Traffic Flow API

Wyjście:
  Kafka topic: warszawa-raw-traffic

Loader cyklicznie pobiera stan ruchu dla wybranych punktów w Warszawie i
normalizuje go do tego samego schematu, którego używa obecny pipeline ruchu.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
import sys
from typing import Any

import requests
from confluent_kafka import Producer
import pandas as pd
from pathlib import Path


KAFKA_BOOTSTRAP_SERVERS = "localhost:9092"
TOPIC = "warszawa-raw-traffic"
TOMTOM_API_KEY = os.getenv("TOMTOM_API_KEY", "").strip()
TOMTOM_FLOW_URL = os.getenv(
    "TOMTOM_FLOW_URL",
    "https://api.tomtom.com/traffic/services/4/flowSegmentData/absolute/10/json",
)
TOMTOM_POLL_INTERVAL_SECONDS = int(os.getenv("TOMTOM_POLL_INTERVAL_SECONDS", "300"))
TOMTOM_RUN_ONCE = os.getenv("TOMTOM_RUN_ONCE", "0").strip().lower() in {"1", "true", "yes"}



TOMTOM_MAPPING_PATH = Path(os.getenv("TOMTOM_MAPPING_PATH", "/app/data/processed/tomtom_edge_mapping.parquet"))
TOMTOM_MAX_CELLS_PER_POLL = int(os.getenv("TOMTOM_MAX_CELLS_PER_POLL", "50"))


def delivery_report(err, msg):
    if err is not None:
        print(f"Błąd dostarczenia wiadomości: {err}")
    else:
        print(
            f"Message delivered to topic: {msg.topic()} "
            f"[partition: {msg.partition()}, offset: {msg.offset()}]"
        )


def _extract_speed(payload: dict[str, Any]) -> float | None:
    for key in ("currentSpeed", "averageSpeed", "freeFlowSpeed"):
        value = payload.get(key)
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _extract_confidence(payload: dict[str, Any]) -> float | None:
    value = payload.get("confidence")
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def fetch_flow_segment(lat: float, lon: float) -> dict[str, Any]:
    response = requests.get(
        TOMTOM_FLOW_URL,
        params={
            "key": TOMTOM_API_KEY,
            "point": f"{lat},{lon}",
            "unit": "KMPH",
            "style": "relative",
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json().get("flowSegmentData", {})


def normalize_segment(segment_cfg: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    ingested_at = datetime.now(timezone.utc).isoformat()
    current_speed = _extract_speed(payload)

    return {
        "source": "tomtom_real_time",
        "station_id": segment_cfg["station_id"],
        "station_name": segment_cfg["station_name"],
        "road_name": segment_cfg["road_name"],
        "direction": segment_cfg["direction"],
        "lat": segment_cfg["lat"],
        "lon": segment_cfg["lon"],
        "geohash": segment_cfg.get("geohash"),
        "measurement_time": ingested_at,
        "period_minutes": max(1, TOMTOM_POLL_INTERVAL_SECONDS // 60),
        "vehicle_count": None,
        "avg_speed_kmh": current_speed,
        "_metadata": {
            "ingested_at_utc": ingested_at,
            "loader": "tomtom_traffic_loader.py",
        },
    }


def produce_snapshot(producer: Producer) -> int:
    if not TOMTOM_API_KEY:
        raise RuntimeError("Brak TOMTOM_API_KEY w środowisku.")

    sent = 0
    # Require a precomputed mapping (edge->geohash); do not fallback to static points
    if not TOMTOM_MAPPING_PATH.exists():
        raise RuntimeError(
            f"Mapping required but not found at {TOMTOM_MAPPING_PATH}. "
            "Run the mapping generator before starting the loader."
        )

    try:
        mapping = pd.read_parquet(TOMTOM_MAPPING_PATH)

        if "geohash" in mapping.columns:
                grouped = (
                    mapping.dropna(subset=["geohash"]) 
                    .groupby("geohash")
                    .agg({"mid_lat": "median", "mid_lon": "median", "priority_score": "sum"})
                    .reset_index()
                )

                # sort by priority descending and limit how many cells to poll per snapshot
                grouped = grouped.sort_values("priority_score", ascending=False)
                cells = grouped.head(TOMTOM_MAX_CELLS_PER_POLL).to_dict(orient="records")

                for cell in cells:
                    lat = float(cell["mid_lat"])
                    lon = float(cell["mid_lon"])
                    geohash = cell["geohash"]

                    try:
                        payload = fetch_flow_segment(lat, lon)
                        segment_cfg = {
                            "station_id": f"tomtom_cell_{geohash}",
                            "road_name": f"cell_{geohash}",
                            "station_name": f"cell_{geohash}",
                            "direction": None,
                            "lat": lat,
                            "lon": lon,
                            "geohash": geohash,
                        }

                        normalized = normalize_segment(segment_cfg, payload)
                        normalized["_metadata"]["tomtom_confidence"] = _extract_confidence(payload)
                        normalized["_metadata"]["tomtom_free_flow_speed_kmh"] = payload.get("freeFlowSpeed")
                        normalized["_metadata"]["tomtom_current_travel_time_s"] = payload.get("currentTravelTime")
                        normalized["_metadata"]["tomtom_free_flow_travel_time_s"] = payload.get("freeFlowTravelTime")

                        key = f"{normalized['station_id']}:{normalized['measurement_time']}"

                        producer.produce(
                            TOPIC,
                            key=key.encode("utf-8"),
                            value=json.dumps(normalized, ensure_ascii=False).encode("utf-8"),
                            callback=delivery_report,
                        )

                        sent += 1
                        print(
                            f"TomTom cell {geohash}: speed={normalized['avg_speed_kmh']} km/h "
                            f"| confidence={normalized['_metadata'].get('tomtom_confidence')}"
                        )
                    except Exception as exc:
                        print(f"Błąd dla cell {geohash}: {exc}")
        else:
            raise RuntimeError(
                f"Mapping at {TOMTOM_MAPPING_PATH} does not contain 'geohash' column."
            )
    except Exception as exc:
        raise RuntimeError(f"Error loading required TomTom mapping: {exc}")

    producer.flush()
    return sent


def main() -> None:
    producer = Producer(
        {
            "bootstrap.servers": KAFKA_BOOTSTRAP_SERVERS,
            "client.id": "tomtom-traffic-loader",
        }
    )

    print(f"TomTom loader started. Topic: {TOPIC}")
    print(f"Polling interval: {TOMTOM_POLL_INTERVAL_SECONDS}s")
    if not TOMTOM_MAPPING_PATH.exists():
        print(
            f"ERROR: required mapping not found at {TOMTOM_MAPPING_PATH}.\n"
            "Generate mapping with: python src/processing/tomtom_edge_mapping.py"
        )
        sys.exit(1)
    print(f"Mode: mapped cells from {TOMTOM_MAPPING_PATH}")

    while True:
        sent = produce_snapshot(producer)
        print(f"Wysłano rekordów TomTom do topicu {TOPIC}: {sent}")

        if TOMTOM_RUN_ONCE:
            break

        time.sleep(TOMTOM_POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()