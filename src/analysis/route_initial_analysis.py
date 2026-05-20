from pathlib import Path
from typing import Any

import networkx as nx
import osmnx as ox
import pandas as pd


WEATHER_PATH = Path("/app/data/processed/weather_observations")
TRAFFIC_PATH = Path("/app/data/processed/traffic_measurements")
SPEED_PATH = Path("/app/data/processed/zdm_speed_by_road.csv")
OUTPUT_DIR = Path("/app/data/results/route_initial_analysis")


# Punkty testowe: Dworzec Centralny -> Służewiec/Marynarska.
# To dobra trasa testowa, bo przebiega przez obszary z dużym ruchem.
ROUTES = [
    {
        "route_name": "Mokotowska -> Dobra",
        "origin": (52.218662, 21.017080),
        "destination": (52.244974, 21.020670),
    },
]


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

    text = " ".join(text.split())
    return text


def edge_names(name_value: Any) -> list[str]:
    if name_value is None:
        return []

    raw_names = name_value if isinstance(name_value, list) else [name_value]

    names = []
    for raw in raw_names:
        if raw is None:
            continue

        text = str(raw)

        for part in text.split(";"):
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


def load_traffic_factors() -> dict[str, float]:
    if not TRAFFIC_PATH.exists():
        print("Brak danych ruchu. Używam traffic_factor = 1.0 dla wszystkich dróg.")
        return {}

    df = pd.read_parquet(TRAFFIC_PATH)

    if df.empty:
        print("Dane ruchu są puste. Używam traffic_factor = 1.0 dla wszystkich dróg.")
        return {}

    df = df.drop_duplicates(subset=["station_id", "measurement_time", "road_name", "direction"])
    df["road_key"] = df["road_name"].apply(normalize_text)

    traffic_map = (
        df.groupby("road_key")["traffic_factor"]
        .max()
        .to_dict()
    )

    print("Wczytane współczynniki ruchu dla dróg:")
    for road, factor in traffic_map.items():
        print(f"  {road}: {factor}")

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

    print("Wczytane średnie prędkości ZDM dla ulic:")
    for road, speed in list(speed_map.items())[:20]:
        print(f"  {road}: {speed} km/h")

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



def add_costs_to_graph(
    G,
    weather_factor: float,
    traffic_map: dict[str, float],
    speed_map: dict[str, float],
):
    G = ox.add_edge_speeds(G)
    G = ox.add_edge_travel_times(G)

    for _, _, _, data in G.edges(keys=True, data=True):
        base_time = float(data.get("travel_time", 0.0))

        traffic_factor, traffic_road_key = traffic_factor_for_edge(data, traffic_map)
        speed_factor, measured_speed, speed_road_key = speed_factor_for_edge(data, speed_map)

        traffic_speed_factor = max(traffic_factor, speed_factor)

        if traffic_road_key and speed_road_key:
            cost_source = "zdm_apr_and_speed"
        elif traffic_road_key:
            cost_source = "zdm_apr"
        elif speed_road_key:
            cost_source = "zdm_speed"
        else:
            cost_source = "osmnx_base"

        data["weather_factor"] = weather_factor
        data["traffic_factor"] = traffic_factor
        data["speed_factor"] = speed_factor
        data["traffic_speed_factor"] = traffic_speed_factor
        data["measured_avg_speed_kmh"] = measured_speed
        data["traffic_road_key"] = traffic_road_key
        data["speed_road_key"] = speed_road_key
        data["cost_source"] = cost_source
        data["cost_time_s"] = base_time * weather_factor * traffic_speed_factor

    return G


def get_best_edge_data(G, u, v, weight: str) -> dict:
    edges = G.get_edge_data(u, v)
    return min(edges.values(), key=lambda data: float(data.get(weight, float("inf"))))


def summarize_route(G, route_nodes: list[int], route_name: str, variant: str, weight: str) -> tuple[dict, list[dict]]:
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
        data = get_best_edge_data(G, u, v, weight)

        length_m = float(data.get("length", 0.0))
        base_time_s = float(data.get("travel_time", 0.0))
        cost_time_s = float(data.get("cost_time_s", base_time_s))
        traffic_factor = float(data.get("traffic_factor", 1.0))

        total_length_m += length_m
        total_base_time_s += base_time_s
        total_cost_time_s += cost_time_s
        max_traffic_factor = max(max_traffic_factor, traffic_factor)

        speed_factor = float(data.get("speed_factor", 1.0))
        traffic_speed_factor = float(data.get("traffic_speed_factor", 1.0))
        cost_source = data.get("cost_source", "osmnx_base")

        max_speed_factor = max(max_speed_factor, speed_factor)
        max_traffic_speed_factor = max(max_traffic_speed_factor, traffic_speed_factor)

        if speed_factor > 1.0:
            speed_impacted_edges += 1

        if data.get("traffic_road_key"):
            zdm_apr_edges += 1

        if data.get("speed_road_key"):
            zdm_speed_edges += 1

        if cost_source == "osmnx_base":
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
            "speed_factor": float(data.get("speed_factor", 1.0)),
            "traffic_speed_factor": float(data.get("traffic_speed_factor", 1.0)),
            "measured_avg_speed_kmh": data.get("measured_avg_speed_kmh"),
            "weather_factor": float(data.get("weather_factor", 1.0)),
            "cost_time_s": round(cost_time_s, 2),
            "speed_factor": speed_factor,
            "traffic_speed_factor": traffic_speed_factor,
            "traffic_road_key": data.get("traffic_road_key"),
            "speed_road_key": data.get("speed_road_key"),
            "cost_source": cost_source,
        })

    summary = {
        "route_name": route_name,
        "variant": variant,
        "weight": weight,
        "distance_km": round(total_length_m / 1000, 2),
        "base_time_min": round(total_base_time_s / 60, 2),
        "adjusted_time_min": round(total_cost_time_s / 60, 2),
        "max_traffic_factor": max_traffic_factor,
        "traffic_impacted_edges": impacted_edges,
        "edge_count": len(edge_rows),
        "max_speed_factor": max_speed_factor,
        "max_traffic_speed_factor": max_traffic_speed_factor,
        "speed_impacted_edges": speed_impacted_edges,
        "zdm_apr_edges": zdm_apr_edges,
        "zdm_speed_edges": zdm_speed_edges,
        "osmnx_only_edges": osmnx_only_edges,
    }

    return summary, edge_rows


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    weather_factor = load_weather_factor()
    traffic_map = load_traffic_factors()
    speed_map = load_speed_factors()

    print("\nPobieranie grafu drogowego OSMnx dla centralnej Warszawy...")
    print("Pierwsze uruchomienie może potrwać kilka minut.")

    ox.settings.use_cache = True
    ox.settings.log_console = False

    # Obszar wokół centrum Warszawy, wystarczający dla pierwszej trasy testowej.
    center_point = (52.21, 21.00)
    G = ox.graph_from_point(
        center_point,
        dist=9000,
        network_type="drive",
        simplify=True,
    )

    print(f"Graf pobrany. Liczba węzłów: {len(G.nodes)}, liczba krawędzi: {len(G.edges)}")

    G = add_costs_to_graph(G, weather_factor, traffic_map, speed_map)

    route_summaries = []
    route_edges = []

    variants = [
        ("najkrótsza dystansowo", "length"),
        ("najszybsza bazowo", "travel_time"),
        ("skorygowana ruchem i pogodą", "cost_time_s"),
    ]

    for route_def in ROUTES:
        route_name = route_def["route_name"]
        origin_lat, origin_lon = route_def["origin"]
        dest_lat, dest_lon = route_def["destination"]

        origin_node = ox.distance.nearest_nodes(G, origin_lon, origin_lat)
        dest_node = ox.distance.nearest_nodes(G, dest_lon, dest_lat)

        print(f"\nAnaliza trasy: {route_name}")
        print(f"Origin node: {origin_node}")
        print(f"Destination node: {dest_node}")

        for variant_name, weight in variants:
            route_nodes = nx.shortest_path(G, origin_node, dest_node, weight=weight)

            summary, edges = summarize_route(
                G=G,
                route_nodes=route_nodes,
                route_name=route_name,
                variant=variant_name,
                weight=weight,
            )

            route_summaries.append(summary)
            route_edges.extend(edges)

            print(
                f"{variant_name}: "
                f"{summary['distance_km']} km, "
                f"base={summary['base_time_min']} min, "
                f"adjusted={summary['adjusted_time_min']} min"
            )

    summaries_df = pd.DataFrame(route_summaries)
    edges_df = pd.DataFrame(route_edges)

    summaries_path = OUTPUT_DIR / "routes_comparison.csv"
    edges_path = OUTPUT_DIR / "route_edges.csv"
    summary_md_path = OUTPUT_DIR / "summary.md"

    summaries_df.to_csv(summaries_path, index=False)
    edges_df.to_csv(edges_path, index=False)

    with summary_md_path.open("w", encoding="utf-8") as f:
        f.write("# Wstępna analiza tras\n\n")
        f.write("Analiza porównuje trzy warianty trasy: najkrótszą dystansowo, najszybszą według bazowego czasu przejazdu oraz trasę skorygowaną o dane ruchu i pogodę.\n\n")
        f.write("## Wykorzystane współczynniki\n\n")
        f.write(f"- Weather factor: {weather_factor}\n")
        f.write(f"- Liczba dróg z przypisanym traffic_factor: {len(traffic_map)}\n\n")
        f.write("## Wyniki\n\n")
        f.write(summaries_df.to_markdown(index=False))
        f.write("\n\n")
        f.write("## Wniosek\n\n")
        f.write(
            "Wariant skorygowany ruchem i pogodą wykorzystuje ten sam graf drogowy, "
            "ale modyfikuje koszt przejazdu wybranych krawędzi. Dzięki temu potok danych "
            "zaczyna odpowiadać na właściwy problem projektowy: nie tylko opisuje sieć drogową, "
            "ale pozwala porównywać trasy według różnych kryteriów kosztu.\n"
        )

    print(f"\nZapisano: {summaries_path}")
    print(f"Zapisano: {edges_path}")
    print(f"Zapisano: {summary_md_path}")
    print("Analiza tras zakończona poprawnie.")


if __name__ == "__main__":
    main()
