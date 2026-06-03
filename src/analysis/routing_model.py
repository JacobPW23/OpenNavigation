from pathlib import Path
from typing import Any

import osmnx as ox
import pandas as pd


WEATHER_PATH = Path("/app/data/processed/weather_observations")
TRAFFIC_PATH = Path("/app/data/processed/traffic_measurements")
SPEED_PATH = Path("/app/data/processed/zdm_speed_by_road.csv")
TRAFFIC_AGG_PATH = Path("/app/data/processed/zdm_traffic_by_road.csv")
TRAFFIC_HOURLY_AGG_PATH = Path("/app/data/processed/zdm_traffic_by_road_hour_direction.csv")

CITY_DELAY_FACTOR = 1.25
INTERSECTION_DELAY_S = 4.0
MAX_TRAFFIC_SPEED_FACTOR = 3.0

OSM_FALLBACK_FACTORS = {
    "motorway": 1.00,
    "trunk": 1.03,
    "primary": 1.05,
    "secondary": 1.10,
    "tertiary": 1.15,
    "unclassified": 1.20,
    "residential": 1.20,
    "living_street": 1.25,
    "service": 1.25,
}


def normalize_text(value: Any) -> str:
    if value is None:
        return ""

    text = str(value).strip().lower()

    replacements = {
        "al. ": "aleje ",
        "al ": "aleje ",
        "aleja ": "aleje ",
        "ul. ": "",
        "ul ": "",
    }

    for old, new in replacements.items():
        text = text.replace(old, new)

    return " ".join(text.split())


def edge_names(name_value: Any) -> list[str]:
    if name_value is None:
        return []

    raw_names = name_value if isinstance(name_value, list) else [name_value]

    names = []
    for raw in raw_names:
        if raw is None:
            continue

        for part in str(raw).split(";"):
            part = part.strip()
            if part:
                names.append(part)

    return names


def edge_highway(highway_value: Any) -> str:
    if highway_value is None:
        return ""
    if isinstance(highway_value, list):
        return ",".join(str(x) for x in highway_value)
    return str(highway_value)


def load_weather_factor() -> float:
    if not WEATHER_PATH.exists():
        print("Brak danych pogodowych. Używam weather_factor = 1.0")
        return 1.0

    df = pd.read_parquet(WEATHER_PATH)

    if df.empty or "weather_factor" not in df.columns:
        print("Dane pogodowe są puste. Używam weather_factor = 1.0")
        return 1.0

    df = df.drop_duplicates(subset=["time"]).sort_values("time")
    factor = float(df["weather_factor"].max())

    print(f"Weather factor użyty w analizie: {factor}")
    return factor


def load_traffic_factors(departure_time: pd.Timestamp | None = None) -> dict[str, float]:
    if departure_time is not None and TRAFFIC_HOURLY_AGG_PATH.exists():
        df = pd.read_csv(TRAFFIC_HOURLY_AGG_PATH)

        required_columns = {"road_key", "measurement_hour"}
        if not df.empty and required_columns.issubset(df.columns):
            hour = int(departure_time.hour)
            traffic_col = "traffic_factor_max"
            if traffic_col not in df.columns:
                traffic_col = "traffic_factor_latest"
            if traffic_col not in df.columns:
                traffic_col = "traffic_factor_mean"

            hourly_df = df[df["measurement_hour"] == hour].copy()
            if not hourly_df.empty and traffic_col in hourly_df.columns:
                hourly_df["road_key_norm"] = hourly_df["road_key"].apply(normalize_text)
                traffic_map = (
                    hourly_df.dropna(subset=["road_key_norm", traffic_col])
                    .groupby("road_key_norm")[traffic_col]
                    .max()
                    .to_dict()
                )

                print(
                    "Wczytane godzinowe współczynniki APR/ZDM z CSV: "
                    f"measurement_hour={hour}"
                )
                print(f"Liczba ulic z godzinowym traffic_factor: {len(traffic_map)}")
                return traffic_map

            print(
                "Brak danych APR dla wybranej godziny. "
                "Wracam do agregacji bez podziału godzinowego."
            )

    if TRAFFIC_AGG_PATH.exists():
        df = pd.read_csv(TRAFFIC_AGG_PATH)

        if not df.empty and "road_key" in df.columns:
            df["road_key_norm"] = df["road_key"].apply(normalize_text)
            traffic_col = "traffic_factor_latest"
            if traffic_col not in df.columns:
                traffic_col = "traffic_factor_max"

            traffic_map = (
                df.dropna(subset=["road_key_norm", traffic_col])
                .set_index("road_key_norm")[traffic_col]
                .to_dict()
            )

            print(f"Liczba ulic z APR/ZDM traffic_factor: {len(traffic_map)}")
            return traffic_map

    if not TRAFFIC_PATH.exists():
        print("Brak danych ruchu APR. Używam traffic_factor = 1.0 dla wszystkich dróg.")
        return {}

    df = pd.read_parquet(TRAFFIC_PATH)

    if df.empty:
        print("Dane ruchu APR są puste. Używam traffic_factor = 1.0 dla wszystkich dróg.")
        return {}

    df = df.drop_duplicates(subset=["station_id", "measurement_time", "road_name", "direction"])
    df["road_key"] = df["road_name"].apply(normalize_text)
    df["measurement_time"] = pd.to_datetime(df["measurement_time"], errors="coerce")

    traffic_map = (
        df.dropna(subset=["road_key", "traffic_factor", "measurement_time"])
        .sort_values("measurement_time")
        .groupby("road_key")
        .tail(1)
        .set_index("road_key")["traffic_factor"]
        .to_dict()
    )

    print(f"Liczba ulic z APR/ZDM traffic_factor: {len(traffic_map)}")
    return traffic_map


def load_speed_factors() -> dict[str, float]:
    if not SPEED_PATH.exists():
        print("Brak zagregowanych danych prędkości ZDM. Używam speed_factor = 1.0.")
        return {}

    df = pd.read_csv(SPEED_PATH)

    if df.empty:
        print("Plik prędkości ZDM jest pusty. Używam speed_factor = 1.0.")
        return {}

    speed_col = "avg_speed_kmh_weighted"
    if speed_col not in df.columns:
        speed_col = "avg_speed_kmh_mean"

    speed_map = (
        df.dropna(subset=["road_key", speed_col])
        .set_index("road_key")[speed_col]
        .to_dict()
    )

    print(f"Liczba ulic z prędkością ZDM: {len(speed_map)}")
    return speed_map


def to_float(value: Any, default: float) -> float:
    if value is None:
        return default

    if isinstance(value, list):
        if not value:
            return default
        value = value[0]

    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def match_value_for_edge(edge_data: dict, value_map: dict[str, float]) -> tuple[str | None, float | None]:
    names = edge_names(edge_data.get("name"))

    for name in names:
        edge_key = normalize_text(name)

        if edge_key in value_map:
            return edge_key, float(value_map[edge_key])

        for map_key, value in value_map.items():
            map_key_norm = normalize_text(map_key)

            if map_key_norm and edge_key and (
                map_key_norm in edge_key or edge_key in map_key_norm
            ):
                return map_key, float(value)

    return None, None


def traffic_factor_for_edge(edge_data: dict, traffic_map: dict[str, float]) -> tuple[float, str | None]:
    matched_key, factor = match_value_for_edge(edge_data, traffic_map)

    if factor is None:
        return 1.0, None

    return float(factor), matched_key


def speed_factor_for_edge(edge_data: dict, speed_map: dict[str, float]) -> tuple[float, float | None, str | None]:
    matched_key, measured_speed = match_value_for_edge(edge_data, speed_map)

    if measured_speed is None or measured_speed <= 0:
        return 1.0, None, None

    base_speed = to_float(edge_data.get("speed_kph"), measured_speed)
    factor = max(1.0, base_speed / measured_speed)
    factor = min(factor, 2.5)

    return round(factor, 3), measured_speed, matched_key


def osm_fallback_factor_for_edge(edge_data: dict) -> float:
    highway = edge_highway(edge_data.get("highway"))

    for highway_name, factor in OSM_FALLBACK_FACTORS.items():
        if highway_name in highway:
            return factor

    return 1.20


def combined_traffic_speed_factor(
    traffic_factor: float,
    speed_factor: float,
    has_traffic: bool,
    has_speed: bool,
    osm_fallback_factor: float,
) -> float:
    if has_traffic and has_speed:
        return min(MAX_TRAFFIC_SPEED_FACTOR, traffic_factor * speed_factor)

    if has_traffic:
        return traffic_factor

    if has_speed:
        return speed_factor

    return osm_fallback_factor


def add_costs_to_graph(
    graph,
    weather_factor: float,
    traffic_map: dict[str, float],
    speed_map: dict[str, float],
    city_delay_factor: float = CITY_DELAY_FACTOR,
):
    graph = ox.add_edge_speeds(graph)
    graph = ox.add_edge_travel_times(graph)

    for _, _, _, data in graph.edges(keys=True, data=True):
        base_time = float(data.get("travel_time", 0.0))

        traffic_factor, traffic_road_key = traffic_factor_for_edge(data, traffic_map)
        speed_factor, measured_speed, speed_road_key = speed_factor_for_edge(data, speed_map)
        osm_fallback_factor = osm_fallback_factor_for_edge(data)

        traffic_speed_factor = combined_traffic_speed_factor(
            traffic_factor=traffic_factor,
            speed_factor=speed_factor,
            has_traffic=traffic_road_key is not None,
            has_speed=speed_road_key is not None,
            osm_fallback_factor=osm_fallback_factor,
        )

        if traffic_road_key and speed_road_key:
            cost_source = "zdm_apr_and_speed"
        elif traffic_road_key:
            cost_source = "zdm_apr"
        elif speed_road_key:
            cost_source = "zdm_speed"
        else:
            cost_source = "osm_fallback"

        data["weather_factor"] = weather_factor
        data["traffic_factor"] = traffic_factor
        data["speed_factor"] = speed_factor
        data["traffic_speed_factor"] = traffic_speed_factor
        data["osm_fallback_factor"] = osm_fallback_factor
        data["city_delay_factor"] = city_delay_factor
        data["measured_avg_speed_kmh"] = measured_speed
        data["traffic_road_key"] = traffic_road_key
        data["speed_road_key"] = speed_road_key
        data["cost_source"] = cost_source
        data["cost_time_s"] = (
            base_time
            * weather_factor
            * traffic_speed_factor
            * city_delay_factor
        )

    return graph


def get_best_edge_data(graph, u, v, weight: str) -> dict:
    edges = graph.get_edge_data(u, v)
    return min(edges.values(), key=lambda data: float(data.get(weight, float("inf"))))


def summarize_route(
    graph,
    route_nodes: list[int],
    route_name: str,
    variant: str,
    weight: str,
    intersection_delay_s: float = INTERSECTION_DELAY_S,
) -> tuple[dict, list[dict]]:
    edge_rows = []

    total_length_m = 0.0
    total_base_time_s = 0.0
    total_cost_time_s = 0.0
    max_traffic_factor = 1.0
    impacted_edges = 0
    max_speed_factor = 1.0
    max_traffic_speed_factor = 1.0
    speed_impacted_edges = 0
    zdm_apr_edges = 0
    zdm_speed_edges = 0
    osmnx_only_edges = 0

    for idx, (u, v) in enumerate(zip(route_nodes[:-1], route_nodes[1:])):
        data = get_best_edge_data(graph, u, v, weight)

        length_m = float(data.get("length", 0.0))
        base_time_s = float(data.get("travel_time", 0.0))
        cost_time_s = float(data.get("cost_time_s", base_time_s))
        traffic_factor = float(data.get("traffic_factor", 1.0))
        speed_factor = float(data.get("speed_factor", 1.0))
        traffic_speed_factor = float(data.get("traffic_speed_factor", 1.0))
        cost_source = data.get("cost_source", "osm_fallback")

        total_length_m += length_m
        total_base_time_s += base_time_s
        total_cost_time_s += cost_time_s
        max_traffic_factor = max(max_traffic_factor, traffic_factor)
        max_speed_factor = max(max_speed_factor, speed_factor)
        max_traffic_speed_factor = max(max_traffic_speed_factor, traffic_speed_factor)

        if speed_factor > 1.0:
            speed_impacted_edges += 1
        if data.get("traffic_road_key"):
            zdm_apr_edges += 1
        if data.get("speed_road_key"):
            zdm_speed_edges += 1
        if cost_source in {"osmnx_base", "osm_fallback"}:
            osmnx_only_edges += 1
        if traffic_factor > 1.0:
            impacted_edges += 1

        edge_rows.append({
            "route_name": route_name,
            "variant": variant,
            "edge_order": idx,
            "u": u,
            "v": v,
            "name": "; ".join(edge_names(data.get("name"))),
            "highway": edge_highway(data.get("highway")),
            "length_m": round(length_m, 2),
            "base_time_s": round(base_time_s, 2),
            "traffic_factor": traffic_factor,
            "speed_factor": speed_factor,
            "traffic_speed_factor": traffic_speed_factor,
            "osm_fallback_factor": float(data.get("osm_fallback_factor", 1.0)),
            "measured_avg_speed_kmh": data.get("measured_avg_speed_kmh"),
            "weather_factor": float(data.get("weather_factor", 1.0)),
            "city_delay_factor": float(data.get("city_delay_factor", 1.0)),
            "cost_time_s": round(cost_time_s, 2),
            "traffic_road_key": data.get("traffic_road_key"),
            "speed_road_key": data.get("speed_road_key"),
            "cost_source": cost_source,
        })

    intersection_count = max(0, len(route_nodes) - 2)
    intersection_delay_total_s = intersection_count * intersection_delay_s
    adjusted_time_s = total_cost_time_s + intersection_delay_total_s

    summary = {
        "route_name": route_name,
        "variant": variant,
        "weight": weight,
        "distance_km": round(total_length_m / 1000, 2),
        "base_time_min": round(total_base_time_s / 60, 2),
        "edge_adjusted_time_min": round(total_cost_time_s / 60, 2),
        "intersection_delay_min": round(intersection_delay_total_s / 60, 2),
        "adjusted_time_min": round(adjusted_time_s / 60, 2),
        "max_traffic_factor": max_traffic_factor,
        "traffic_impacted_edges": impacted_edges,
        "edge_count": len(edge_rows),
        "intersection_count": intersection_count,
        "intersection_delay_s": intersection_delay_s,
        "max_speed_factor": max_speed_factor,
        "max_traffic_speed_factor": max_traffic_speed_factor,
        "speed_impacted_edges": speed_impacted_edges,
        "zdm_apr_edges": zdm_apr_edges,
        "zdm_speed_edges": zdm_speed_edges,
        "osmnx_only_edges": osmnx_only_edges,
    }

    return summary, edge_rows
