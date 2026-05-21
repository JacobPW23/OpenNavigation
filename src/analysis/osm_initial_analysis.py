from pathlib import Path

import pandas as pd


INPUT_PATH = Path("/app/data/processed/osm_ways")
OUTPUT_DIR = Path("/app/data/results/osm_initial_analysis")


def pct(value: float) -> float:
    return round(value * 100, 2)


def save_csv(df: pd.DataFrame, filename: str) -> None:
    path = OUTPUT_DIR / filename
    df.to_csv(path, index=False)
    print(f"Zapisano: {path}")


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if not INPUT_PATH.exists():
        raise FileNotFoundError(
            f"Nie znaleziono danych wejściowych: {INPUT_PATH}. "
            "Najpierw uruchom transformację Overpass -> Parquet."
        )

    df_raw = pd.read_parquet(INPUT_PATH)

    raw_records = len(df_raw)
    duplicated_ids = int(df_raw["id"].duplicated().sum())

    df = df_raw.drop_duplicates(subset=["id"]).copy()
    records = len(df)

    # Uporządkowanie typów
    df["oneway"] = df["oneway"].fillna(False).astype(bool)
    df["has_name"] = df["name"].notna() & (df["name"].astype(str).str.strip() != "")
    df["has_maxspeed"] = df["maxspeed_raw"].notna() & (df["maxspeed_raw"].astype(str).str.strip() != "")
    df["has_lanes"] = df["lanes_raw"].notna() & (df["lanes_raw"].astype(str).str.strip() != "")

    # 1. Ogólne podsumowanie zbioru
    overview = pd.DataFrame([
        {"metric": "raw_records_before_deduplication", "value": raw_records},
        {"metric": "records_after_deduplication", "value": records},
        {"metric": "duplicated_ids_removed", "value": duplicated_ids},
        {"metric": "unique_highway_types", "value": df["highway"].nunique()},
        {"metric": "named_roads_percent", "value": pct(df["has_name"].mean())},
        {"metric": "maxspeed_available_percent", "value": pct(df["has_maxspeed"].mean())},
        {"metric": "lanes_available_percent", "value": pct(df["has_lanes"].mean())},
        {"metric": "oneway_roads_percent", "value": pct(df["oneway"].mean())},
        {"metric": "avg_node_count_per_way", "value": round(df["node_count"].mean(), 2)},
        {"metric": "avg_estimated_speed_kmh", "value": round(df["estimated_speed_kmh"].mean(), 2)},
        {"metric": "avg_lanes", "value": round(df["lanes"].mean(), 2)},
    ])
    save_csv(overview, "01_dataset_overview.csv")

    # 2. Rozkład typów dróg
    highway_distribution = (
        df.groupby("highway")
        .size()
        .reset_index(name="count")
        .sort_values("count", ascending=False)
    )
    highway_distribution["percent"] = highway_distribution["count"].apply(lambda x: pct(x / records))
    save_csv(highway_distribution, "02_highway_distribution.csv")

    # 3. Kompletność atrybutów
    completeness = pd.DataFrame([
        {
            "attribute": "name",
            "available_count": int(df["has_name"].sum()),
            "missing_count": int((~df["has_name"]).sum()),
            "available_percent": pct(df["has_name"].mean()),
        },
        {
            "attribute": "maxspeed",
            "available_count": int(df["has_maxspeed"].sum()),
            "missing_count": int((~df["has_maxspeed"]).sum()),
            "available_percent": pct(df["has_maxspeed"].mean()),
        },
        {
            "attribute": "lanes",
            "available_count": int(df["has_lanes"].sum()),
            "missing_count": int((~df["has_lanes"]).sum()),
            "available_percent": pct(df["has_lanes"].mean()),
        },
        {
            "attribute": "oneway",
            "available_count": records,
            "missing_count": 0,
            "available_percent": 100.0,
        },
    ])
    save_csv(completeness, "03_attribute_completeness.csv")

    # 4. Prędkości według typu drogi
    speed_by_highway = (
        df.groupby("highway")["estimated_speed_kmh"]
        .agg(["count", "mean", "min", "max"])
        .reset_index()
        .sort_values("count", ascending=False)
    )
    speed_by_highway["mean"] = speed_by_highway["mean"].round(2)
    save_csv(speed_by_highway, "04_speed_by_highway.csv")

    # 5. Liczba pasów według typu drogi
    lanes_by_highway = (
        df.groupby("highway")["lanes"]
        .agg(["count", "mean", "min", "max"])
        .reset_index()
        .sort_values("count", ascending=False)
    )
    lanes_by_highway["mean"] = lanes_by_highway["mean"].round(2)
    save_csv(lanes_by_highway, "05_lanes_by_highway.csv")

    # 6. Drogi jednokierunkowe według typu drogi
    oneway_by_highway = (
        df.groupby("highway")["oneway"]
        .agg(total="count", oneway_count="sum")
        .reset_index()
        .sort_values("total", ascending=False)
    )
    oneway_by_highway["oneway_percent"] = oneway_by_highway.apply(
        lambda row: pct(row["oneway_count"] / row["total"]),
        axis=1,
    )
    save_csv(oneway_by_highway, "06_oneway_by_highway.csv")

    # 7. Przykłady rekordów z brakami danych
    missing_examples = df[
        (~df["has_name"]) | (~df["has_maxspeed"]) | (~df["has_lanes"])
    ][
        [
            "id",
            "highway",
            "name",
            "maxspeed_raw",
            "lanes_raw",
            "estimated_speed_kmh",
            "lanes",
            "oneway",
            "node_count",
        ]
    ].head(30)
    save_csv(missing_examples, "07_missing_attribute_examples.csv")

    summary_path = OUTPUT_DIR / "summary.md"
    with summary_path.open("w", encoding="utf-8") as f:
        f.write("# Wstępna analiza danych OpenStreetMap dla Warszawy\n\n")
        f.write("Analiza została wykonana na podstawie znormalizowanych danych OSM zapisanych w formacie Parquet.\n\n")
        f.write("## Podsumowanie zbioru\n\n")
        f.write(f"- Liczba rekordów przed deduplikacją: {raw_records}\n")
        f.write(f"- Liczba rekordów po deduplikacji: {records}\n")
        f.write(f"- Liczba usuniętych duplikatów identyfikatora odcinka: {duplicated_ids}\n")
        f.write(f"- Liczba typów dróg `highway`: {df['highway'].nunique()}\n")
        f.write(f"- Udział odcinków z nazwą ulicy: {pct(df['has_name'].mean())}%\n")
        f.write(f"- Udział odcinków z tagiem `maxspeed`: {pct(df['has_maxspeed'].mean())}%\n")
        f.write(f"- Udział odcinków z tagiem `lanes`: {pct(df['has_lanes'].mean())}%\n")
        f.write(f"- Udział odcinków jednokierunkowych: {pct(df['oneway'].mean())}%\n")
        f.write(f"- Średnia oszacowana prędkość: {round(df['estimated_speed_kmh'].mean(), 2)} km/h\n")
        f.write(f"- Średnia liczba pasów: {round(df['lanes'].mean(), 2)}\n\n")

        f.write("## Wstępne wnioski\n\n")
        f.write(
            "Dane OSM zawierają wystarczającą strukturę sieci drogowej do budowy grafu tras. "
            "Najważniejszym ograniczeniem jest niepełna dostępność wybranych atrybutów, "
            "zwłaszcza ograniczeń prędkości i liczby pasów. Z tego powodu w dalszym potoku "
            "konieczne jest stosowanie wartości domyślnych zależnych od typu drogi `highway`.\n\n"
        )
        f.write(
            "Uzyskany zbiór może zostać wykorzystany jako baza do wyliczania kosztu przejazdu "
            "dla krawędzi grafu. W kolejnych etapach zostanie wzbogacony o dane o natężeniu ruchu "
            "oraz dodatkowe źródła danych ograniczające homogeniczność źródeł.\n"
        )

    print(f"Zapisano: {summary_path}")
    print("\nAnaliza zakończona poprawnie.")


if __name__ == "__main__":
    main()
