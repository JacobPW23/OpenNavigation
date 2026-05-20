"""
Loader danych pogodowych dla Warszawy.

Źródło:
  Open-Meteo Forecast API

Wyjście:
  Kafka topic: warszawa-raw-weather

Ten loader pobiera godzinowe dane pogodowe dla Warszawy i zapisuje cały JSON
jako jedną wiadomość w Kafce.
"""

import json
import time
from datetime import datetime, timezone

import requests
from confluent_kafka import Producer


KAFKA_BOOTSTRAP_SERVERS = "localhost:9092"
TOPIC = "warszawa-raw-weather"

# Przyjęte współrzędne dla centrum Warszawy.
WARSAW_LAT = 52.2297
WARSAW_LON = 21.0122

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"


def delivery_report(err, msg):
    if err is not None:
        print(f"Błąd dostarczenia wiadomości: {err}")
    else:
        print(
            f"Message delivered to topic: {msg.topic()} "
            f"[partition: {msg.partition()}, offset: {msg.offset()}]"
        )


def fetch_weather() -> dict:
    params = {
        "latitude": WARSAW_LAT,
        "longitude": WARSAW_LON,
        "hourly": ",".join([
            "temperature_2m",
            "precipitation",
            "rain",
            "snowfall",
            "wind_speed_10m",
            "visibility",
            "weather_code",
        ]),
        "forecast_days": 1,
        "timezone": "Europe/Warsaw",
    }

    response = requests.get(OPEN_METEO_URL, params=params, timeout=30)
    response.raise_for_status()

    data = response.json()

    # Dodajemy metadane techniczne do wiadomości.
    data["_metadata"] = {
        "source": "open-meteo",
        "city": "Warsaw",
        "lat": WARSAW_LAT,
        "lon": WARSAW_LON,
        "ingested_at_utc": datetime.now(timezone.utc).isoformat(),
    }

    return data


def main():
    print("1. Pobieranie danych pogodowych z Open-Meteo...")
    start = time.time()
    data = fetch_weather()
    elapsed = round(time.time() - start, 2)

    hourly = data.get("hourly", {})
    hours = len(hourly.get("time", []))

    print(f"Sukces. Czas pobierania: {elapsed}s")
    print(f"Liczba godzinowych obserwacji/prognoz: {hours}")
    print(f"Zakres czasu: {hourly.get('time', [None])[0]} -> {hourly.get('time', [None])[-1]}")

    producer = Producer({
        "bootstrap.servers": KAFKA_BOOTSTRAP_SERVERS,
        "client.id": "weather-loader",
    })

    payload = json.dumps(data, ensure_ascii=False)

    print(f"2. Wysyłanie danych do Kafki: topic={TOPIC}")
    producer.produce(
        TOPIC,
        key="warsaw-weather",
        value=payload.encode("utf-8"),
        callback=delivery_report,
    )

    producer.flush()
    print("Gotowe. Dane pogodowe znajdują się w Kafce.")


if __name__ == "__main__":
    main()
