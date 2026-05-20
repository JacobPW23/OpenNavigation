from pathlib import Path
import pandas as pd


INPUT_PATH = Path("/app/data/raw/traffic/zdm_speed_measurements.csv")
OUTPUT_PATH = Path("/app/data/processed/zdm_speed_by_road.csv")


def normalize_road_name(value):
    if pd.isna(value):
        return None

    text = str(value).strip().lower()
    text = text.replace("al. ", "aleje ")
    text = text.replace("ul. ", "")
    text = " ".join(text.split())
    return text


def weighted_mean(group, value_col, weight_col):
    values = group[value_col]
    weights = group[weight_col]

    valid = values.notna() & weights.notna() & (weights > 0)

    if valid.any():
        return round((values[valid] * weights[valid]).sum() / weights[valid].sum(), 2)

    return round(values.mean(), 2)


def main():
    df = pd.read_csv(INPUT_PATH)

    df["road_key"] = df["road_name"].apply(normalize_road_name)
    df["measurement_date"] = pd.to_datetime(df["measurement_date"], errors="coerce")

    grouped_rows = []

    for road_key, group in df.groupby("road_key"):
        if road_key is None:
            continue

        latest = group.sort_values("measurement_date").iloc[-1]

        grouped_rows.append({
            "road_key": road_key,
            "road_name": latest["road_name"],
            "measurements_count": len(group),
            "stations_count": group["station_id"].nunique(),
            "date_min": group["measurement_date"].min().date(),
            "date_max": group["measurement_date"].max().date(),
            "avg_speed_kmh_mean": round(group["avg_speed_kmh"].mean(), 2),
            "avg_speed_kmh_median": round(group["avg_speed_kmh"].median(), 2),
            "avg_speed_kmh_weighted": weighted_mean(group, "avg_speed_kmh", "vehicle_count"),
            "avg_speed_kmh_latest": round(float(latest["avg_speed_kmh"]), 2),
            "vehicle_count_total": int(group["vehicle_count"].fillna(0).sum()),
        })

    result = pd.DataFrame(grouped_rows).sort_values("road_name")

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(OUTPUT_PATH, index=False)

    print(f"Zapisano: {OUTPUT_PATH}")
    print(f"Liczba ulic po agregacji: {len(result)}")
    print()
    print(result.head(30))


if __name__ == "__main__":
    main()
