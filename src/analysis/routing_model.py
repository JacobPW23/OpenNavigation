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
INTERSECTION_DELAY_S = 8.0
MAX_TRAFFIC_SPEED_FACTOR = 3.0
WARSAW_CENTER = (52.2297, 21.0122)
WEATHER_NEAREST_MAX_HOURS = 3.0

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


def has_real_departure_date(departure_time: pd.Timestamp | None) -> bool:
    if departure_time is None:
        return False

    timestamp = pd.Timestamp(departure_time)
    return timestamp.date() != pd.Timestamp("2000-01-01").date()


def value_or_none(row: pd.Series, column: str) -> Any:
    if column not in row or pd.isna(row[column]):
        return None

    value = row[column]
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if hasattr(value, "item"):
        value = value.item()
    return value


def weather_context(
    factor: float,
    match_type: str,
    row: pd.Series | None = None,
    departure_time: pd.Timestamp | None = None,
) -> dict[str, Any]:
    context = {
        "available": row is not None,
        "factor": round(float(factor), 3),
        "match_type": match_type,
        "requested_time": (
            pd.Timestamp(departure_time).isoformat()
            if departure_time is not None else None
        ),
        "matched_time": None,
        "temperature_2m": None,
        "precipitation_mm": None,
        "rain_mm": None,
        "snowfall_cm": None,
        "wind_speed_10m_kmh": None,
        "visibility_m": None,
        "weather_code": None,
        "source": None,
    }

    if row is None:
        return context

    context.update({
        "matched_time": value_or_none(row, "time"),
        "temperature_2m": value_or_none(row, "temperature_2m"),
        "precipitation_mm": value_or_none(row, "precipitation_mm"),
        "rain_mm": value_or_none(row, "rain_mm"),
        "snowfall_cm": value_or_none(row, "snowfall_cm"),
        "wind_speed_10m_kmh": value_or_none(row, "wind_speed_10m_kmh"),
        "visibility_m": value_or_none(row, "visibility_m"),
        "weather_code": value_or_none(row, "weather_code"),
        "source": value_or_none(row, "source"),
    })
    return context


def mean_weather_row(df: pd.DataFrame, factor: float) -> pd.Series:
    row_data = {"weather_factor": factor}
    for column in [
        "temperature_2m",
        "precipitation_mm",
        "rain_mm",
        "snowfall_cm",
        "wind_speed_10m_kmh",
        "visibility_m",
    ]:
        if column in df.columns:
            row_data[column] = df[column].mean()
    if "source" in df.columns and not df["source"].dropna().empty:
        row_data["source"] = df["source"].dropna().iloc[-1]
    return pd.Series(row_data)


def load_weather_context(departure_time: pd.Timestamp | None = None) -> dict[str, Any]:
    if not WEATHER_PATH.exists():
        print("Brak danych pogodowych. Używam weather_factor = 1.0")
        return weather_context(1.0, "missing_file", departure_time=departure_time)

    df = pd.read_parquet(WEATHER_PATH)

    if df.empty or "weather_factor" not in df.columns:
        print("Dane pogodowe są puste. Używam weather_factor = 1.0")
        return weather_context(1.0, "empty_or_invalid", departure_time=departure_time)

    df["time"] = pd.to_datetime(df["time"], errors="coerce")
    df = (
        df.dropna(subset=["time", "weather_factor"])
        .drop_duplicates(subset=["time"])
        .sort_values("time")
    )

    if df.empty:
        print("Dane pogodowe nie mają poprawnego czasu. Używam weather_factor = 1.0")
        return weather_context(1.0, "invalid_time", departure_time=departure_time)

    if departure_time is not None:
        timestamp = pd.Timestamp(departure_time)
        if timestamp.tz is not None:
            timestamp = timestamp.tz_convert(None)

        if has_real_departure_date(timestamp):
            df["time_delta_s"] = (df["time"] - timestamp).abs().dt.total_seconds()
            row = df.sort_values("time_delta_s").iloc[0]
            if float(row["time_delta_s"]) <= WEATHER_NEAREST_MAX_HOURS * 3600:
                factor = float(row["weather_factor"])
                print(
                    "Weather factor dobrany po najbliższej godzinie: "
                    f"time={row['time']}, factor={factor}"
                )
                return weather_context(factor, "nearest_time", row, timestamp)

        same_hour = df[df["time"].dt.hour == int(timestamp.hour)]
        if not same_hour.empty:
            factor = float(same_hour["weather_factor"].mean())
            row = mean_weather_row(same_hour, factor)
            print(
                "Weather factor dobrany po godzinie odjazdu: "
                f"hour={int(timestamp.hour)}, factor={round(factor, 3)}"
            )
            return weather_context(factor, "same_hour_average", row, timestamp)

    row = df.sort_values("weather_factor").iloc[-1]
    factor = float(row["weather_factor"])

    print(f"Weather factor użyty w analizie: {factor}")
    return weather_context(factor, "max_available", row, departure_time)


def load_weather_factor(departure_time: pd.Timestamp | None = None) -> float:
    return float(load_weather_context(departure_time)["factor"])


def traffic_factor_column(df: pd.DataFrame, prefer_typical: bool) -> str | None:
    if prefer_typical:
        candidates = ["traffic_factor_mean", "traffic_factor_max", "traffic_factor_latest"]
    else:
        candidates = ["traffic_factor_latest", "traffic_factor_mean", "traffic_factor_max"]

    for column in candidates:
        if column in df.columns:
            return column

    return None


def as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "t", "yes", "y"}


def build_traffic_map(df: pd.DataFrame, traffic_col: str) -> dict[str, Any]:
    df = df.dropna(subset=["road_key_norm", traffic_col]).copy()
    if df.empty:
        return {}

    traffic_map = {}
    for road_key, group in df.groupby("road_key_norm"):
        directions = {}
        if "direction_key" in group.columns:
            direction_group = group.dropna(subset=["direction_key"])
            for direction_key, direction_rows in direction_group.groupby("direction_key"):
                directions[direction_key] = float(direction_rows[traffic_col].max())

        traffic_map[road_key] = {
            "factor": float(group[traffic_col].max()),
            "directions": directions,
        }

    return traffic_map


def filter_traffic_by_departure(
    df: pd.DataFrame,
    departure_time: pd.Timestamp,
) -> tuple[pd.DataFrame, str]:
    timestamp = pd.Timestamp(departure_time)
    hour = int(timestamp.hour)
    hourly_df = df[df["measurement_hour"] == hour].copy()
    description = f"measurement_hour={hour}"

    if hourly_df.empty or not has_real_departure_date(timestamp):
        return hourly_df, description

    weekday = int(timestamp.dayofweek)
    is_weekend = weekday >= 5

    if "measurement_weekday" in hourly_df.columns:
        weekday_df = hourly_df[hourly_df["measurement_weekday"] == weekday].copy()
        if not weekday_df.empty:
            return weekday_df, f"{description}, measurement_weekday={weekday}"

    if "is_weekend" in hourly_df.columns:
        weekend_df = hourly_df[hourly_df["is_weekend"].apply(as_bool) == is_weekend].copy()
        if not weekend_df.empty:
            return weekend_df, f"{description}, is_weekend={is_weekend}"

    return hourly_df, description


def load_traffic_factors(departure_time: pd.Timestamp | None = None) -> dict[str, Any]:
    if departure_time is not None and TRAFFIC_HOURLY_AGG_PATH.exists():
        df = pd.read_csv(TRAFFIC_HOURLY_AGG_PATH)

        required_columns = {"road_key", "measurement_hour"}
        if not df.empty and required_columns.issubset(df.columns):
            traffic_col = traffic_factor_column(df, prefer_typical=True)
            hourly_df, filter_description = filter_traffic_by_departure(df, departure_time)
            if not hourly_df.empty and traffic_col in hourly_df.columns:
                hourly_df["road_key_norm"] = hourly_df["road_key"].apply(normalize_text)
                if "direction_key" not in hourly_df.columns and "direction" in hourly_df.columns:
                    hourly_df["direction_key"] = hourly_df["direction"].apply(normalize_text)
                traffic_map = build_traffic_map(hourly_df, traffic_col)

                print(
                    "Wczytane godzinowe współczynniki APR/ZDM z CSV: "
                    f"{filter_description}, column={traffic_col}"
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
            traffic_col = traffic_factor_column(df, prefer_typical=False)
            traffic_map = build_traffic_map(df, traffic_col) if traffic_col else {}

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


def match_entry_for_edge(edge_data: dict, value_map: dict[str, Any]) -> tuple[str | None, Any | None]:
    names = edge_names(edge_data.get("name"))

    for name in names:
        edge_key = normalize_text(name)

        if edge_key in value_map:
            return edge_key, value_map[edge_key]

        for map_key, value in value_map.items():
            map_key_norm = normalize_text(map_key)

            if map_key_norm and edge_key and (
                map_key_norm in edge_key or edge_key in map_key_norm
            ):
                return map_key, value

    return None, None


def match_value_for_edge(edge_data: dict, value_map: dict[str, float]) -> tuple[str | None, float | None]:
    matched_key, value = match_entry_for_edge(edge_data, value_map)

    if value is None:
        return None, None

    return matched_key, float(value)


def distance_to_warsaw_center(node_data: dict) -> float | None:
    lat = node_data.get("y")
    lon = node_data.get("x")

    if lat is None or lon is None:
        return None

    return (float(lat) - WARSAW_CENTER[0]) ** 2 + (float(lon) - WARSAW_CENTER[1]) ** 2


def traffic_direction_for_edge(graph, u, v, directions: dict[str, float]) -> str | None:
    if not directions:
        return None

    direction_keys = list(directions)
    has_center = any("centrum" in key for key in direction_keys)
    has_outbound = any(
        "granica" in key or "zewn" in key or "poza" in key
        for key in direction_keys
    )

    if not has_center or not has_outbound:
        return None

    start_distance = distance_to_warsaw_center(graph.nodes[u])
    end_distance = distance_to_warsaw_center(graph.nodes[v])
    if start_distance is None or end_distance is None:
        return None

    preferred = "centrum" if end_distance < start_distance else "granica"
    for direction_key in direction_keys:
        if preferred in direction_key:
            return direction_key

    return None


def traffic_factor_for_edge(
    edge_data: dict,
    traffic_map: dict[str, Any],
    graph=None,
    u=None,
    v=None,
) -> tuple[float, str | None, str | None]:
    matched_key, entry = match_entry_for_edge(edge_data, traffic_map)

    if entry is None:
        return 1.0, None, None

    if isinstance(entry, dict):
        directions = entry.get("directions") or {}
        direction_key = None
        if graph is not None and u is not None and v is not None:
            direction_key = traffic_direction_for_edge(graph, u, v, directions)

        if direction_key and direction_key in directions:
            return float(directions[direction_key]), matched_key, direction_key

        return float(entry.get("factor", 1.0)), matched_key, None

    return float(entry), matched_key, None


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


def node_intersection_delay_s(graph, node, base_delay_s: float) -> float:
    if base_delay_s <= 0:
        return 0.0

    node_data = graph.nodes[node]
    node_highway = str(node_data.get("highway", ""))
    if "traffic_signals" in node_highway:
        return min(base_delay_s * 2.5, 30.0)

    street_count = to_float(node_data.get("street_count"), 0.0)
    if street_count >= 4:
        return base_delay_s
    if street_count >= 3:
        return base_delay_s * 0.75

    return min(base_delay_s * 0.15, 1.5)


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
    traffic_map: dict[str, Any],
    speed_map: dict[str, float],
    city_delay_factor: float = CITY_DELAY_FACTOR,
    intersection_delay_s: float = INTERSECTION_DELAY_S,
):
    graph = ox.add_edge_speeds(graph)
    graph = ox.add_edge_travel_times(graph)

    for u, v, _, data in graph.edges(keys=True, data=True):
        base_time = float(data.get("travel_time", 0.0))

        traffic_factor, traffic_road_key, traffic_direction_key = traffic_factor_for_edge(
            data,
            traffic_map,
            graph=graph,
            u=u,
            v=v,
        )
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

        edge_cost_time_s = (
            base_time
            * weather_factor
            * traffic_speed_factor
            * city_delay_factor
        )
        node_delay_s = node_intersection_delay_s(graph, v, intersection_delay_s)

        data["weather_factor"] = weather_factor
        data["traffic_factor"] = traffic_factor
        data["speed_factor"] = speed_factor
        data["traffic_speed_factor"] = traffic_speed_factor
        data["osm_fallback_factor"] = osm_fallback_factor
        data["city_delay_factor"] = city_delay_factor
        data["measured_avg_speed_kmh"] = measured_speed
        data["traffic_road_key"] = traffic_road_key
        data["traffic_direction_key"] = traffic_direction_key
        data["speed_road_key"] = speed_road_key
        data["cost_source"] = cost_source
        data["edge_cost_time_s"] = edge_cost_time_s
        data["intersection_delay_s"] = node_delay_s
        data["cost_time_s"] = edge_cost_time_s + node_delay_s

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
    total_edge_cost_time_s = 0.0
    total_intersection_delay_s = 0.0
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
        edge_cost_time_s = float(data.get("edge_cost_time_s", data.get("cost_time_s", base_time_s)))
        edge_intersection_delay_s = float(data.get("intersection_delay_s", 0.0))
        cost_time_s = float(data.get("cost_time_s", edge_cost_time_s + edge_intersection_delay_s))
        traffic_factor = float(data.get("traffic_factor", 1.0))
        speed_factor = float(data.get("speed_factor", 1.0))
        traffic_speed_factor = float(data.get("traffic_speed_factor", 1.0))
        cost_source = data.get("cost_source", "osm_fallback")

        total_length_m += length_m
        total_base_time_s += base_time_s
        total_edge_cost_time_s += edge_cost_time_s
        total_intersection_delay_s += edge_intersection_delay_s
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
            "edge_cost_time_s": round(edge_cost_time_s, 2),
            "intersection_delay_s": round(edge_intersection_delay_s, 2),
            "cost_time_s": round(cost_time_s, 2),
            "traffic_road_key": data.get("traffic_road_key"),
            "traffic_direction_key": data.get("traffic_direction_key"),
            "speed_road_key": data.get("speed_road_key"),
            "cost_source": cost_source,
        })

    if total_intersection_delay_s > 0:
        intersection_count = sum(
            1 for row in edge_rows
            if float(row.get("intersection_delay_s", 0.0)) > 0
        )
        intersection_delay_total_s = total_intersection_delay_s
        adjusted_time_s = total_cost_time_s
    else:
        intersection_count = max(0, len(route_nodes) - 2)
        intersection_delay_total_s = intersection_count * intersection_delay_s
        total_edge_cost_time_s = total_cost_time_s
        adjusted_time_s = total_cost_time_s + intersection_delay_total_s

    summary = {
        "route_name": route_name,
        "variant": variant,
        "weight": weight,
        "distance_km": round(total_length_m / 1000, 2),
        "base_time_min": round(total_base_time_s / 60, 2),
        "edge_adjusted_time_min": round(total_edge_cost_time_s / 60, 2),
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
