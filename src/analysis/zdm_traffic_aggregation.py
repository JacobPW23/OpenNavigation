from pathlib import Path
import re

import pandas as pd


INPUT_PATH = Path("/app/data/processed/traffic_measurements")
OUTPUT_PATH = Path("/app/data/processed/zdm_traffic_by_road.csv")
HOURLY_OUTPUT_PATH = Path("/app/data/processed/zdm_traffic_by_road_hour_direction.csv")


def normalize_road_name(value):
    if pd.isna(value):
        return None

    text = str(value).strip().lower()
    text = text.replace("al. ", "aleje ")
    text = text.replace("al ", "aleje ")
    text = text.replace("aleja ", "aleje ")
    text = text.replace("ul. ", "")
    text = re.sub(r"\s+", " ", text)
    return text


def normalize_direction(value):
    if pd.isna(value):
        return None

    text = str(value).strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text or None


def main():
    if not INPUT_PATH.exists():
        raise FileNotFoundError(f"Nie znaleziono danych APR: {INPUT_PATH}")

    df = pd.read_parquet(INPUT_PATH)

    if df.empty:
        raise ValueError("Dane APR są puste.")

    df["road_key"] = df["road_name"].apply(normalize_road_name)
    df["measurement_time"] = pd.to_datetime(df["measurement_time"], errors="coerce")
    df["measurement_hour"] = df["measurement_time"].dt.hour
    df["measurement_weekday"] = df["measurement_time"].dt.dayofweek
    df["is_weekend"] = df["measurement_weekday"] >= 5

    if "direction" not in df.columns:
        df = df.assign(direction=None)
    df["direction_key"] = df["direction"].apply(normalize_direction)

    grouped_rows = []

    for road_key, group in df.dropna(subset=["road_key"]).groupby("road_key"):
        latest = group.sort_values("measurement_time").iloc[-1]
        vc_max = group["vehicle_count"].max()
        vc_mean = group["vehicle_count"].mean()
        if pd.isna(vc_max):
            vc_max = 0
        if pd.isna(vc_mean):
            vc_mean = 0.0

        grouped_rows.append({
            "road_key": road_key,
            "road_name": latest["road_name"],
            "measurements_count": len(group),
            "stations_count": group["station_id"].nunique(),
            "date_min": group["measurement_time"].min(),
            "date_max": group["measurement_time"].max(),
            "traffic_factor_latest": round(float(latest["traffic_factor"]), 3),
            "traffic_factor_max": round(float(group["traffic_factor"].max()), 3),
            "traffic_factor_mean": round(float(group["traffic_factor"].mean()), 3),
            "vehicle_count_max": int(vc_max),
            "vehicle_count_mean": round(float(vc_mean), 2),
            "source_latest": latest.get("source"),
        })

    result = pd.DataFrame(grouped_rows).sort_values("road_name")

    hourly_rows = []

    hourly_group_columns = [
        "road_key",
        "measurement_hour",
        "measurement_weekday",
        "is_weekend",
        "direction_key",
    ]
    hourly_df = df.dropna(subset=["road_key", "measurement_hour"])

    for group_key, group in hourly_df.groupby(hourly_group_columns, dropna=False):
        road_key, measurement_hour, measurement_weekday, is_weekend, direction_key = group_key
        latest = group.sort_values("measurement_time").iloc[-1]
        vc_max = group["vehicle_count"].max()
        vc_mean = group["vehicle_count"].mean()
        if pd.isna(vc_max):
            vc_max = 0
        if pd.isna(vc_mean):
            vc_mean = 0.0

        hourly_rows.append({
            "road_key": road_key,
            "road_name": latest["road_name"],
            "measurement_hour": int(measurement_hour),
            "measurement_weekday": int(measurement_weekday),
            "is_weekend": bool(is_weekend),
            "direction": latest.get("direction"),
            "direction_key": direction_key,
            "measurements_count": len(group),
            "stations_count": group["station_id"].nunique(),
            "date_min": group["measurement_time"].min(),
            "date_max": group["measurement_time"].max(),
            "traffic_factor_latest": round(float(latest["traffic_factor"]), 3),
            "traffic_factor_max": round(float(group["traffic_factor"].max()), 3),
            "traffic_factor_mean": round(float(group["traffic_factor"].mean()), 3),
            "vehicle_count_max": int(vc_max),
            "vehicle_count_mean": round(float(vc_mean), 2),
            "source_latest": latest.get("source"),
        })

    hourly_result = pd.DataFrame(hourly_rows)
    if not hourly_result.empty:
        hourly_result = hourly_result.sort_values(
            ["road_name", "measurement_weekday", "measurement_hour", "direction_key"],
            na_position="last",
        )

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(OUTPUT_PATH, index=False)
    hourly_result.to_csv(HOURLY_OUTPUT_PATH, index=False)

    print(f"Zapisano: {OUTPUT_PATH}")
    print(f"Liczba ulic po agregacji APR: {len(result)}")
    print(f"Zapisano: {HOURLY_OUTPUT_PATH}")
    print(f"Liczba wierszy po agregacji APR po godzinie i kierunku: {len(hourly_result)}")
    print()
    print(result.head(30))


if __name__ == "__main__":
    main()
