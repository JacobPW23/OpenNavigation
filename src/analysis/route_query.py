from __future__ import annotations

"""
Interfejs CLI do liczenia trasy dla współrzędnych latitude,longitude.

Skrypt używa wspólnego modelu kosztów z routing_model.py i przyjmuje start
oraz cel z argumentów polecenia.
"""

import argparse
import html
import json
import math
import os
import re
from pathlib import Path
from urllib.parse import urlencode

import networkx as nx
import osmnx as ox
import pandas as pd
import requests

from routing_model import (
    CITY_DELAY_FACTOR,
    INTERSECTION_DELAY_S,
    add_costs_to_graph,
    get_best_edge_data,
    load_speed_factors,
    load_traffic_factors,
    load_weather_context,
    summarize_route,
)


DEFAULT_OUTPUT_DIR = Path("/app/data/results/route_query")
DEFAULT_GRAPH_PATH = Path("/app/data/processed/routing_graph.graphml")
GOOGLE_ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"


def parse_coordinate(value: str) -> tuple[float, float]:
    parts = [part.strip() for part in value.split(",")]

    if len(parts) != 2:
        raise argparse.ArgumentTypeError("Podaj współrzędne jako: latitude,longitude")

    try:
        lat = float(parts[0])
        lon = float(parts[1])
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Współrzędne muszą być liczbami.") from exc

    if not -90 <= lat <= 90:
        raise argparse.ArgumentTypeError("Latitude musi być w zakresie -90..90.")

    if not -180 <= lon <= 180:
        raise argparse.ArgumentTypeError("Longitude musi być w zakresie -180..180.")

    return lat, lon


def parse_departure_time(value: str | None) -> pd.Timestamp | None:
    if not value:
        return None

    value = value.strip()

    try:
        if re.fullmatch(r"\d{1,2}", value):
            hour = int(value)
            if not 0 <= hour <= 23:
                raise ValueError
            return pd.Timestamp(f"2000-01-01 {hour:02d}:00:00")

        if re.fullmatch(r"\d{1,2}:\d{2}(:\d{2})?", value):
            return pd.Timestamp(f"2000-01-01 {value}")

        return pd.Timestamp(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            'Czas odjazdu podaj jako "8", "08:00", "2026-01-01 08:00:00" '
            'albo "2026-01-01T08:00".'
        ) from exc


def haversine_m(point_a: tuple[float, float], point_b: tuple[float, float]) -> float:
    lat1, lon1 = point_a
    lat2, lon2 = point_b

    radius_m = 6_371_000
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    value = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )

    return 2 * radius_m * math.atan2(math.sqrt(value), math.sqrt(1 - value))


def graph_center(origin: tuple[float, float], destination: tuple[float, float]) -> tuple[float, float]:
    return (
        (origin[0] + destination[0]) / 2,
        (origin[1] + destination[1]) / 2,
    )


def graph_radius_m(
    origin: tuple[float, float],
    destination: tuple[float, float],
    buffer_m: int,
    override_m: int | None,
) -> int:
    if override_m is not None:
        return override_m

    direct_distance_m = haversine_m(origin, destination)
    return max(3_000, int(direct_distance_m / 2 + buffer_m))


def google_maps_url(origin: tuple[float, float], destination: tuple[float, float]) -> str:
    params = urlencode({
        "api": "1",
        "origin": f"{origin[0]},{origin[1]}",
        "destination": f"{destination[0]},{destination[1]}",
        "travelmode": "driving",
    })
    return f"https://www.google.com/maps/dir/?{params}"


def duration_to_seconds(value: str | None) -> float | None:
    if not value or not value.endswith("s"):
        return None
    try:
        return float(value[:-1])
    except ValueError:
        return None


def fetch_google_route(
    origin: tuple[float, float],
    destination: tuple[float, float],
    api_key: str | None,
) -> dict:
    maps_url = google_maps_url(origin, destination)

    if not api_key:
        return {
            "status": "missing_api_key",
            "maps_url": maps_url,
            "message": "Otwórz link, aby porównać trasę w Google Maps.",
        }

    request_body = {
        "origin": {
            "location": {
                "latLng": {
                    "latitude": origin[0],
                    "longitude": origin[1],
                }
            }
        },
        "destination": {
            "location": {
                "latLng": {
                    "latitude": destination[0],
                    "longitude": destination[1],
                }
            }
        },
        "travelMode": "DRIVE",
        "routingPreference": "TRAFFIC_AWARE",
        "computeAlternativeRoutes": False,
        "languageCode": "pl-PL",
        "units": "METRIC",
    }
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": "routes.duration,routes.distanceMeters",
    }

    try:
        response = requests.post(
            GOOGLE_ROUTES_URL,
            headers=headers,
            json=request_body,
            timeout=30,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        return {
            "status": "error",
            "maps_url": maps_url,
            "message": str(exc),
        }

    data = response.json()
    routes = data.get("routes", [])
    if not routes:
        return {
            "status": "no_route",
            "maps_url": maps_url,
            "message": "Google Routes API nie zwróciło trasy.",
        }

    route = routes[0]
    distance_m = route.get("distanceMeters")
    duration_s = duration_to_seconds(route.get("duration"))

    return {
        "status": "ok",
        "maps_url": maps_url,
        "distance_km": round(distance_m / 1000, 2) if distance_m is not None else None,
        "duration_min": round(duration_s / 60, 2) if duration_s is not None else None,
    }


def build_google_comparison(
    summaries: list[dict],
    origin: tuple[float, float],
    destination: tuple[float, float],
    api_key: str | None,
) -> dict:
    google = fetch_google_route(origin, destination, api_key)
    adjusted = next(
        (row for row in summaries if row["variant"] == "skorygowana ruchem i pogodą"),
        None,
    )

    if google.get("status") == "ok" and adjusted:
        google["opennav_distance_km"] = adjusted["distance_km"]
        google["opennav_adjusted_time_min"] = adjusted["adjusted_time_min"]
        google["distance_diff_km"] = round(adjusted["distance_km"] - google["distance_km"], 2)
        google["time_diff_min"] = round(
            adjusted["adjusted_time_min"] - google["duration_min"],
            2,
        )

    return google


def load_or_download_base_graph(
    origin: tuple[float, float],
    destination: tuple[float, float],
    graph_dist_m: int,
    graph_path: Path,
    refresh_graph: bool,
):
    ox.settings.use_cache = True
    ox.settings.log_console = False

    if graph_path.exists() and not refresh_graph:
        print(f"Wczytywanie grafu OSMnx z pliku: {graph_path}")
        graph = ox.load_graphml(graph_path)
        print(f"Graf wczytany. Węzły: {len(graph.nodes)}, krawędzie: {len(graph.edges)}")
        return graph

    center = graph_center(origin, destination)
    print(f"Pobieranie grafu OSMnx: center={center}, dist={graph_dist_m} m")

    graph = ox.graph_from_point(
        center,
        dist=graph_dist_m,
        network_type="drive",
        simplify=True,
    )

    print(f"Graf pobrany. Węzły: {len(graph.nodes)}, krawędzie: {len(graph.edges)}")

    graph_path.parent.mkdir(parents=True, exist_ok=True)
    ox.save_graphml(graph, graph_path)
    print(f"Zapisano graf bazowy: {graph_path}")

    return graph


def build_weighted_graph(
    origin: tuple[float, float],
    destination: tuple[float, float],
    graph_dist_m: int,
    graph_path: Path = DEFAULT_GRAPH_PATH,
    refresh_graph: bool = False,
    departure_time: pd.Timestamp | None = None,
    city_delay_factor: float = CITY_DELAY_FACTOR,
    intersection_delay_s: float = INTERSECTION_DELAY_S,
):
    weather = load_weather_context(departure_time)
    weather_factor = float(weather["factor"])
    traffic_map = load_traffic_factors(departure_time)
    speed_map = load_speed_factors()

    graph = load_or_download_base_graph(
        origin=origin,
        destination=destination,
        graph_dist_m=graph_dist_m,
        graph_path=graph_path,
        refresh_graph=refresh_graph,
    )
    graph = add_costs_to_graph(
        graph,
        weather_factor,
        traffic_map,
        speed_map,
        city_delay_factor=city_delay_factor,
        intersection_delay_s=intersection_delay_s,
    )
    graph.graph["weather_context"] = weather
    return graph


def calculate_route_variants(
    graph,
    origin: tuple[float, float],
    destination: tuple[float, float],
    route_name: str,
    intersection_delay_s: float = INTERSECTION_DELAY_S,
) -> tuple[list[dict], list[dict], dict[str, list[int]]]:
    origin_node = ox.distance.nearest_nodes(graph, origin[1], origin[0])
    destination_node = ox.distance.nearest_nodes(graph, destination[1], destination[0])

    variants = [
        ("najkrótsza dystansowo", "length"),
        ("najszybsza bazowo", "travel_time"),
        ("skorygowana ruchem i pogodą", "cost_time_s"),
    ]

    summaries = []
    edge_rows = []
    route_nodes_by_variant = {}

    for variant_name, weight in variants:
        route_nodes = nx.shortest_path(graph, origin_node, destination_node, weight=weight)
        summary, edges = summarize_route(
            graph=graph,
            route_nodes=route_nodes,
            route_name=route_name,
            variant=variant_name,
            weight=weight,
            intersection_delay_s=intersection_delay_s,
        )

        summaries.append(summary)
        edge_rows.extend(edges)
        route_nodes_by_variant[variant_name] = route_nodes

        print(
            f"{variant_name}: "
            f"{summary['distance_km']} km, "
            f"base={summary['base_time_min']} min, "
            f"adjusted={summary['adjusted_time_min']} min"
        )

    return summaries, edge_rows, route_nodes_by_variant


def edge_route_points(graph, route_nodes: list[int], weight: str) -> list[list[float]]:
    points = []

    for u, v in zip(route_nodes[:-1], route_nodes[1:]):
        edge_data = get_best_edge_data(graph, u, v, weight)
        geometry = edge_data.get("geometry")

        if geometry is not None:
            edge_points = [[lat, lon] for lon, lat in geometry.coords]
        else:
            edge_points = [
                [graph.nodes[u]["y"], graph.nodes[u]["x"]],
                [graph.nodes[v]["y"], graph.nodes[v]["x"]],
            ]

        if points and edge_points and points[-1] == edge_points[0]:
            points.extend(edge_points[1:])
        else:
            points.extend(edge_points)

    return points


def write_route_map(
    graph,
    route_nodes_by_variant: dict[str, list[int]],
    origin: tuple[float, float],
    destination: tuple[float, float],
    output_path: Path,
) -> None:
    variant_weights = {
        "najkrótsza dystansowo": "length",
        "najszybsza bazowo": "travel_time",
        "skorygowana ruchem i pogodą": "cost_time_s",
    }
    variant_colors = {
        "najkrótsza dystansowo": "#2563eb",
        "najszybsza bazowo": "#16a34a",
        "skorygowana ruchem i pogodą": "#dc2626",
    }

    routes = []
    for variant_name, nodes in route_nodes_by_variant.items():
        routes.append({
            "name": variant_name,
            "color": variant_colors[variant_name],
            "points": edge_route_points(graph, nodes, variant_weights[variant_name]),
        })

    center = graph_center(origin, destination)
    payload = {
        "center": [center[0], center[1]],
        "origin": [origin[0], origin[1]],
        "destination": [destination[0], destination[1]],
        "routes": routes,
    }

    html_content = f"""<!doctype html>
<html lang="pl">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>OpenNavigation route query</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
  <style>
    html, body, #map {{
      height: 100%;
      margin: 0;
    }}

    .legend {{
      background: white;
      border: 1px solid #d1d5db;
      border-radius: 6px;
      box-shadow: 0 2px 10px rgba(0, 0, 0, 0.12);
      color: #111827;
      font: 14px/1.4 Arial, sans-serif;
      padding: 10px 12px;
    }}

    .legend-item {{
      align-items: center;
      display: flex;
      gap: 8px;
      margin: 4px 0;
      white-space: nowrap;
    }}

    .legend-line {{
      display: inline-block;
      height: 4px;
      width: 26px;
    }}
  </style>
</head>
<body>
  <div id="map"></div>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script>
    const data = {json.dumps(payload, ensure_ascii=False)};
    const map = L.map("map").setView(data.center, 13);

    L.tileLayer("https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png", {{
      maxZoom: 19,
      attribution: "&copy; OpenStreetMap contributors"
    }}).addTo(map);

    const bounds = [];

    L.marker(data.origin).addTo(map).bindPopup("Start");
    L.marker(data.destination).addTo(map).bindPopup("Cel");
    bounds.push(data.origin, data.destination);

    for (const route of data.routes) {{
      const line = L.polyline(route.points, {{
        color: route.color,
        opacity: 0.85,
        weight: 5
      }}).addTo(map);
      line.bindPopup(route.name);
      bounds.push(...route.points);
    }}

    if (bounds.length > 0) {{
      map.fitBounds(bounds, {{ padding: [30, 30] }});
    }}

    const legend = L.control({{ position: "topright" }});
    legend.onAdd = function () {{
      const div = L.DomUtil.create("div", "legend");
      div.innerHTML = data.routes.map(route => `
        <div class="legend-item">
          <span class="legend-line" style="background:${{route.color}}"></span>
          <span>${{route.name}}</span>
        </div>
      `).join("");
      return div;
    }};
    legend.addTo(map);
  </script>
</body>
</html>
"""

    output_path.write_text(html_content, encoding="utf-8")


def save_results(
    summaries: list[dict],
    edge_rows: list[dict],
    output_dir: Path,
    map_path: Path,
    google_comparison: dict | None = None,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    summaries_df = pd.DataFrame(summaries)
    edges_df = pd.DataFrame(edge_rows)

    summaries_path = output_dir / "routes_comparison.csv"
    edges_path = output_dir / "route_edges.csv"
    summary_path = output_dir / "summary.md"
    google_path = output_dir / "google_comparison.csv"

    summaries_df.to_csv(summaries_path, index=False)
    edges_df.to_csv(edges_path, index=False)
    if google_comparison:
        pd.DataFrame([google_comparison]).to_csv(google_path, index=False)

    with summary_path.open("w", encoding="utf-8") as f:
        f.write("# Route query\n\n")
        f.write("Wynik zapytania trasowego dla współrzędnych podanych z CLI.\n\n")
        f.write("## Wyniki\n\n")
        f.write(summaries_df.to_markdown(index=False))
        f.write("\n\n")
        f.write("## Mapa\n\n")
        f.write(f"- HTML: {html.escape(str(map_path))}\n")
        if google_comparison:
            f.write("\n## Porównanie z Google Maps\n\n")
            f.write(f"- Status: {google_comparison.get('status')}\n")
            f.write(f"- Link: {google_comparison.get('maps_url')}\n")
            if google_comparison.get("status") == "ok":
                f.write(f"- Google distance_km: {google_comparison.get('distance_km')}\n")
                f.write(f"- Google duration_min: {google_comparison.get('duration_min')}\n")
                f.write(f"- OpenNavigation distance_km: {google_comparison.get('opennav_distance_km')}\n")
                f.write(
                    "- OpenNavigation adjusted_time_min: "
                    f"{google_comparison.get('opennav_adjusted_time_min')}\n"
                )
                f.write(f"- Distance diff km: {google_comparison.get('distance_diff_km')}\n")
                f.write(f"- Time diff min: {google_comparison.get('time_diff_min')}\n")
            elif google_comparison.get("message"):
                f.write(f"- Informacja: {google_comparison.get('message')}\n")

    print(f"Zapisano: {summaries_path}")
    print(f"Zapisano: {edges_path}")
    print(f"Zapisano: {summary_path}")
    print(f"Zapisano: {map_path}")
    if google_comparison:
        print(f"Zapisano: {google_path}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Policz warianty trasy dla współrzędnych latitude,longitude."
    )
    parser.add_argument(
        "--origin",
        required=True,
        type=parse_coordinate,
        help='Punkt startowy jako "lat,lon", np. "52.218662,21.017080".',
    )
    parser.add_argument(
        "--destination",
        required=True,
        type=parse_coordinate,
        help='Punkt docelowy jako "lat,lon", np. "52.244974,21.020670".',
    )
    parser.add_argument(
        "--name",
        default="route_query",
        help="Nazwa trasy zapisywana w wynikach.",
    )
    parser.add_argument(
        "--departure-time",
        type=parse_departure_time,
        default=None,
        help='Czas odjazdu do wyboru godzinowych danych APR, np. "2026-01-01 08:00:00".',
    )
    parser.add_argument(
        "--city-delay-factor",
        type=float,
        default=CITY_DELAY_FACTOR,
        help=f"Miejski mnożnik opóźnienia krawędzi. Domyślnie: {CITY_DELAY_FACTOR}",
    )
    parser.add_argument(
        "--intersection-delay-s",
        type=float,
        default=INTERSECTION_DELAY_S,
        help=f"Kara za węzeł/sygnalizację w sekundach. Domyślnie: {INTERSECTION_DELAY_S}",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Katalog wyników. Domyślnie: {DEFAULT_OUTPUT_DIR}",
    )
    parser.add_argument(
        "--buffer-m",
        type=int,
        default=3_000,
        help="Bufor dodany do promienia grafu, gdy --graph-dist-m nie jest podany.",
    )
    parser.add_argument(
        "--graph-dist-m",
        type=int,
        default=None,
        help="Ręczny promień grafu OSMnx w metrach.",
    )
    parser.add_argument(
        "--graph-path",
        type=Path,
        default=DEFAULT_GRAPH_PATH,
        help=f"Plik GraphML z grafem drogowym. Domyślnie: {DEFAULT_GRAPH_PATH}",
    )
    parser.add_argument(
        "--refresh-graph",
        action="store_true",
        help="Pobierz graf z OSMnx ponownie i nadpisz --graph-path.",
    )
    parser.add_argument(
        "--google-api-key",
        default=os.getenv("GOOGLE_MAPS_API_KEY"),
        help="Klucz Google Routes API. Domyślnie z GOOGLE_MAPS_API_KEY.",
    )   
    parser.add_argument(
        "--skip-google",
        action="store_true",
        help="Nie twórz porównania z Google Maps.",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    graph_dist_m = graph_radius_m(
        origin=args.origin,
        destination=args.destination,
        buffer_m=args.buffer_m,
        override_m=args.graph_dist_m,
    )

    graph = build_weighted_graph(
        origin=args.origin,
        destination=args.destination,
        graph_dist_m=graph_dist_m,
        graph_path=args.graph_path,
        refresh_graph=args.refresh_graph,
        departure_time=args.departure_time,
        city_delay_factor=args.city_delay_factor,
        intersection_delay_s=args.intersection_delay_s,
    )
    summaries, edge_rows, route_nodes_by_variant = calculate_route_variants(
        graph=graph,
        origin=args.origin,
        destination=args.destination,
        route_name=args.name,
        intersection_delay_s=args.intersection_delay_s,
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    map_path = args.output_dir / "route_map.html"
    write_route_map(
        graph=graph,
        route_nodes_by_variant=route_nodes_by_variant,
        origin=args.origin,
        destination=args.destination,
        output_path=map_path,
    )
    google_comparison = None
    if not args.skip_google:
        google_comparison = build_google_comparison(
            summaries=summaries,
            origin=args.origin,
            destination=args.destination,
            api_key=args.google_api_key,
        )
        print(f"Google Maps: {google_comparison.get('maps_url')}")

    save_results(summaries, edge_rows, args.output_dir, map_path, google_comparison)


if __name__ == "__main__":
    main()
