from __future__ import annotations

"""
Diagnostyka routingu dla jednej pary współrzędnych.

Skrypt liczy te same warianty trasy co route_query.py, ale dodatkowo zapisuje
parametry krawędzi grafu i mapę diagnostyczną z pokolorowanymi kosztami.
"""

import argparse
import json
import math
from pathlib import Path
from typing import Any

import pandas as pd

from routing_model import (
    CITY_DELAY_FACTOR,
    INTERSECTION_DELAY_S,
    edge_highway,
    edge_names,
)
from route_query import (
    DEFAULT_GRAPH_PATH,
    build_weighted_graph,
    calculate_route_variants,
    edge_route_points,
    google_maps_url,
    haversine_m,
    graph_center,
    graph_radius_m,
    parse_coordinate,
    parse_departure_time,
)


DEFAULT_OUTPUT_DIR = Path("/app/data/results/route_diagnostics")

VARIANT_WEIGHTS = {
    "najkrótsza dystansowo": "length",
    "najszybsza bazowo": "travel_time",
    "skorygowana ruchem i pogodą": "cost_time_s",
}

VARIANT_COLORS = {
    "najkrótsza dystansowo": "#2563eb",
    "najszybsza bazowo": "#16a34a",
    "skorygowana ruchem i pogodą": "#dc2626",
}

INTERPOLATION_RADIUS_M = 700
INTERPOLATION_STRENGTH = 0.60
INTERPOLATION_MAX_FACTOR = 2.00


def safe_float(value: Any, default: float | None = None) -> float | None:
    if value is None:
        return default

    try:
        result = float(value)
    except (TypeError, ValueError):
        return default

    if math.isnan(result):
        return default

    return result


def safe_json_value(value: Any) -> Any:
    if value is None:
        return None

    if isinstance(value, float) and math.isnan(value):
        return None

    if isinstance(value, (list, tuple)):
        return [safe_json_value(item) for item in value]

    if isinstance(value, pd.Timestamp):
        return value.isoformat()

    return value


def best_edge_key_data(graph, u: int, v: int, weight: str) -> tuple[Any, dict]:
    edges = graph.get_edge_data(u, v)
    return min(edges.items(), key=lambda item: float(item[1].get(weight, float("inf"))))


def edge_points(graph, u: int, v: int, data: dict) -> list[list[float]]:
    geometry = data.get("geometry")

    if geometry is not None:
        return [[lat, lon] for lon, lat in geometry.coords]

    return [
        [graph.nodes[u]["y"], graph.nodes[u]["x"]],
        [graph.nodes[v]["y"], graph.nodes[v]["x"]],
    ]


def edge_midpoint(graph, u: int, v: int, data: dict) -> tuple[float, float]:
    geometry = data.get("geometry")

    if geometry is not None:
        point = geometry.interpolate(0.5, normalized=True)
        return float(point.y), float(point.x)

    return (
        (float(graph.nodes[u]["y"]) + float(graph.nodes[v]["y"])) / 2,
        (float(graph.nodes[u]["x"]) + float(graph.nodes[v]["x"])) / 2,
    )


def highway_family(highway_value: Any) -> str:
    highway = edge_highway(highway_value)

    for family in (
        "motorway",
        "trunk",
        "primary",
        "secondary",
        "tertiary",
        "residential",
        "unclassified",
        "service",
        "living_street",
    ):
        if family in highway:
            return family

    return "other"


def spatial_bucket(lat: float, lon: float, cell_deg: float) -> tuple[int, int]:
    return int(lat // cell_deg), int(lon // cell_deg)


def apply_interpolated_traffic(
    graph,
    radius_m: int = INTERPOLATION_RADIUS_M,
    strength: float = INTERPOLATION_STRENGTH,
    max_factor: float = INTERPOLATION_MAX_FACTOR,
) -> dict:
    """Propagate measured traffic factors to nearby uncovered edges.

    This is deliberately diagnostic/experimental. It mutates the provided graph
    so routes can be recalculated and compared with the normal model.
    """

    cell_deg = max(0.001, radius_m / 111_000)
    sources = []

    for u, v, key, data in graph.edges(keys=True, data=True):
        cost_source = str(data.get("cost_source", ""))
        factor = safe_float(data.get("traffic_speed_factor"), 1.0)

        if not cost_source.startswith("zdm") or factor is None or factor <= 1.0:
            continue

        lat, lon = edge_midpoint(graph, u, v, data)
        source = {
            "edge_id": edge_id(u, v, key),
            "lat": lat,
            "lon": lon,
            "factor": factor,
            "road_key": data.get("traffic_road_key") or data.get("speed_road_key"),
            "highway_family": highway_family(data.get("highway")),
        }
        source["bucket"] = spatial_bucket(lat, lon, cell_deg)
        sources.append(source)

    index: dict[tuple[int, int], list[dict]] = {}
    for source in sources:
        index.setdefault(source["bucket"], []).append(source)

    updated_edges = 0
    total_candidate_edges = 0
    max_interpolated_factor = 1.0
    bucket_delta = 2

    for u, v, key, data in graph.edges(keys=True, data=True):
        original_source = str(data.get("cost_source", ""))
        if original_source.startswith("zdm"):
            data["interpolated_traffic_factor"] = 1.0
            data["interpolation_source_count"] = 0
            data["interpolation_distance_m"] = None
            data["cost_source_original"] = original_source
            continue

        lat, lon = edge_midpoint(graph, u, v, data)
        bucket = spatial_bucket(lat, lon, cell_deg)
        candidates = []

        for bucket_lat in range(bucket[0] - bucket_delta, bucket[0] + bucket_delta + 1):
            for bucket_lon in range(bucket[1] - bucket_delta, bucket[1] + bucket_delta + 1):
                candidates.extend(index.get((bucket_lat, bucket_lon), []))

        if not candidates:
            data["interpolated_traffic_factor"] = 1.0
            data["interpolation_source_count"] = 0
            data["interpolation_distance_m"] = None
            data["cost_source_original"] = original_source
            continue

        target_family = highway_family(data.get("highway"))
        weighted_excess = 0.0
        weight_total = 0.0
        used_distances = []

        for candidate in candidates:
            distance_m = haversine_m((lat, lon), (candidate["lat"], candidate["lon"]))
            if distance_m > radius_m:
                continue

            family_weight = 1.35 if candidate["highway_family"] == target_family else 0.75
            distance_weight = 1 / max(80.0, distance_m)
            weight = distance_weight * family_weight

            weighted_excess += (candidate["factor"] - 1.0) * weight
            weight_total += weight
            used_distances.append(distance_m)

        if weight_total <= 0:
            data["interpolated_traffic_factor"] = 1.0
            data["interpolation_source_count"] = 0
            data["interpolation_distance_m"] = None
            data["cost_source_original"] = original_source
            continue

        current_factor = safe_float(data.get("traffic_speed_factor"), 1.0) or 1.0
        interpolated_factor = 1.0 + strength * (weighted_excess / weight_total)
        interpolated_factor = min(max_factor, max(1.0, interpolated_factor))
        new_factor = max(current_factor, interpolated_factor)

        data["cost_source_original"] = original_source
        data["pre_interpolation_traffic_speed_factor"] = current_factor
        data["interpolated_traffic_factor"] = round(interpolated_factor, 3)
        data["interpolation_source_count"] = len(used_distances)
        data["interpolation_distance_m"] = round(sum(used_distances) / len(used_distances), 1)

        if new_factor > current_factor + 0.001:
            base_time = safe_float(data.get("travel_time"), 0.0) or 0.0
            weather_factor = safe_float(data.get("weather_factor"), 1.0) or 1.0
            city_delay_factor = safe_float(data.get("city_delay_factor"), 1.0) or 1.0

            data["traffic_speed_factor"] = round(new_factor, 3)
            data["cost_source"] = "interpolated_traffic"
            data["cost_time_s"] = base_time * weather_factor * new_factor * city_delay_factor
            updated_edges += 1
            max_interpolated_factor = max(max_interpolated_factor, new_factor)

        total_candidate_edges += 1

    return {
        "enabled": True,
        "radius_m": radius_m,
        "strength": strength,
        "max_factor": max_factor,
        "source_edges": len(sources),
        "candidate_edges": total_candidate_edges,
        "updated_edges": updated_edges,
        "max_interpolated_factor": round(max_interpolated_factor, 3),
    }


def edge_id(u: int, v: int, key: Any) -> str:
    return f"{u}:{v}:{key}"


def route_edge_ids(graph, route_nodes: list[int], weight: str) -> set[str]:
    ids = set()

    for u, v in zip(route_nodes[:-1], route_nodes[1:]):
        key, _ = best_edge_key_data(graph, u, v, weight)
        ids.add(edge_id(u, v, key))

    return ids


def graph_edge_records(graph, route_edge_id_set: set[str]) -> list[dict]:
    records = []

    for u, v, key, data in graph.edges(keys=True, data=True):
        record_id = edge_id(u, v, key)
        name = "; ".join(edge_names(data.get("name")))
        cost_source = str(data.get("cost_source", "unknown"))
        traffic_speed_factor = safe_float(data.get("traffic_speed_factor"), 1.0)
        is_interpolated = cost_source == "interpolated_traffic"

        records.append({
            "edge_id": record_id,
            "u": u,
            "v": v,
            "key": key,
            "name": name,
            "highway": edge_highway(data.get("highway")),
            "length_m": safe_float(data.get("length"), 0.0),
            "speed_kph": safe_float(data.get("speed_kph")),
            "base_time_s": safe_float(data.get("travel_time"), 0.0),
            "cost_time_s": safe_float(data.get("cost_time_s"), 0.0),
            "traffic_factor": safe_float(data.get("traffic_factor"), 1.0),
            "speed_factor": safe_float(data.get("speed_factor"), 1.0),
            "traffic_speed_factor": traffic_speed_factor,
            "osm_fallback_factor": safe_float(data.get("osm_fallback_factor"), 1.0),
            "weather_factor": safe_float(data.get("weather_factor"), 1.0),
            "city_delay_factor": safe_float(data.get("city_delay_factor"), 1.0),
            "measured_avg_speed_kmh": safe_float(data.get("measured_avg_speed_kmh")),
            "traffic_road_key": data.get("traffic_road_key"),
            "speed_road_key": data.get("speed_road_key"),
            "cost_source": cost_source,
            "cost_source_original": data.get("cost_source_original"),
            "pre_interpolation_traffic_speed_factor": safe_float(
                data.get("pre_interpolation_traffic_speed_factor")
            ),
            "interpolated_traffic_factor": safe_float(
                data.get("interpolated_traffic_factor"), 1.0
            ),
            "interpolation_source_count": safe_float(
                data.get("interpolation_source_count"), 0.0
            ),
            "interpolation_distance_m": safe_float(data.get("interpolation_distance_m")),
            "is_route_edge": record_id in route_edge_id_set,
            "has_external_data": cost_source.startswith("zdm"),
            "is_interpolated": is_interpolated,
            "from_lat": graph.nodes[u]["y"],
            "from_lon": graph.nodes[u]["x"],
            "to_lat": graph.nodes[v]["y"],
            "to_lon": graph.nodes[v]["x"],
            "points": edge_points(graph, u, v, data),
        })

    return records


def choose_map_records(records: list[dict], max_map_edges: int) -> list[dict]:
    if max_map_edges <= 0 or len(records) <= max_map_edges:
        return records

    route_records = [record for record in records if record["is_route_edge"]]
    data_records = [
        record
        for record in records
        if not record["is_route_edge"] and record["has_external_data"]
    ]
    interpolated_records = [
        record
        for record in records
        if not record["is_route_edge"]
        and not record["has_external_data"]
        and record["is_interpolated"]
    ]
    other_records = [
        record
        for record in records
        if not record["is_route_edge"]
        and not record["has_external_data"]
        and not record["is_interpolated"]
    ]

    selected = route_records + data_records + interpolated_records
    remaining = max(0, max_map_edges - len(selected))

    if remaining > 0 and other_records:
        step = max(1, len(other_records) // remaining)
        selected.extend(other_records[::step][:remaining])

    return selected[:max_map_edges]


def map_record(record: dict) -> dict:
    popup_fields = [
        "name",
        "highway",
        "length_m",
        "speed_kph",
        "base_time_s",
        "cost_time_s",
        "traffic_factor",
        "speed_factor",
        "traffic_speed_factor",
        "osm_fallback_factor",
        "weather_factor",
        "city_delay_factor",
        "cost_source_original",
        "pre_interpolation_traffic_speed_factor",
        "interpolated_traffic_factor",
        "interpolation_source_count",
        "interpolation_distance_m",
        "measured_avg_speed_kmh",
        "traffic_road_key",
        "speed_road_key",
        "cost_source",
    ]

    return {
        "edge_id": record["edge_id"],
        "points": record["points"],
        "is_route_edge": record["is_route_edge"],
        "has_external_data": record["has_external_data"],
        "is_interpolated": record["is_interpolated"],
        "traffic_speed_factor": record["traffic_speed_factor"],
        "cost_source": record["cost_source"],
        "popup": {field: safe_json_value(record.get(field)) for field in popup_fields},
    }


def route_overlap_rows(route_nodes_by_variant: dict[str, list[int]], graph) -> list[dict]:
    route_edges_by_variant = {}

    for variant, nodes in route_nodes_by_variant.items():
        weight = VARIANT_WEIGHTS[variant]
        route_edges_by_variant[variant] = route_edge_ids(graph, nodes, weight)

    rows = []
    variants = list(route_edges_by_variant)

    for idx, left in enumerate(variants):
        for right in variants[idx + 1:]:
            left_edges = route_edges_by_variant[left]
            right_edges = route_edges_by_variant[right]
            union_count = len(left_edges | right_edges)
            common_count = len(left_edges & right_edges)
            overlap_ratio = common_count / union_count if union_count else 1.0

            rows.append({
                "left_variant": left,
                "right_variant": right,
                "left_edges": len(left_edges),
                "right_edges": len(right_edges),
                "common_edges": common_count,
                "overlap_ratio": round(overlap_ratio, 4),
                "same_node_sequence": route_nodes_by_variant[left] == route_nodes_by_variant[right],
            })

    return rows


def diagnostic_payload(
    origin: tuple[float, float],
    destination: tuple[float, float],
    graph,
    route_nodes_by_variant: dict[str, list[int]],
    summaries: list[dict],
    map_records: list[dict],
    parameters: dict,
) -> dict:
    routes = []
    for variant, nodes in route_nodes_by_variant.items():
        routes.append({
            "name": variant,
            "color": VARIANT_COLORS[variant],
            "points": edge_route_points(graph, nodes, VARIANT_WEIGHTS[variant]),
        })

    center = graph_center(origin, destination)
    return {
        "center": [center[0], center[1]],
        "origin": [origin[0], origin[1]],
        "destination": [destination[0], destination[1]],
        "routes": routes,
        "edges": [map_record(record) for record in map_records],
        "summaries": summaries,
        "parameters": parameters,
    }


def build_diagnostic_result(
    origin: tuple[float, float],
    destination: tuple[float, float],
    route_name: str,
    departure_time: pd.Timestamp | None,
    graph_path: Path,
    refresh_graph: bool,
    graph_dist_m: int | None,
    buffer_m: int,
    city_delay_factor: float,
    intersection_delay_s: float,
    max_map_edges: int,
    use_interpolation: bool = False,
    interpolation_radius_m: int = INTERPOLATION_RADIUS_M,
    interpolation_strength: float = INTERPOLATION_STRENGTH,
) -> dict:
    resolved_graph_dist_m = graph_radius_m(
        origin=origin,
        destination=destination,
        buffer_m=buffer_m,
        override_m=graph_dist_m,
    )

    graph = build_weighted_graph(
        origin=origin,
        destination=destination,
        graph_dist_m=resolved_graph_dist_m,
        graph_path=graph_path,
        refresh_graph=refresh_graph,
        departure_time=departure_time,
        city_delay_factor=city_delay_factor,
    )

    if use_interpolation:
        interpolation = apply_interpolated_traffic(
            graph,
            radius_m=interpolation_radius_m,
            strength=interpolation_strength,
        )
    else:
        interpolation = {
            "enabled": False,
            "radius_m": interpolation_radius_m,
            "strength": interpolation_strength,
            "max_factor": INTERPOLATION_MAX_FACTOR,
            "source_edges": 0,
            "candidate_edges": 0,
            "updated_edges": 0,
            "max_interpolated_factor": 1.0,
        }

    summaries, route_edges, route_nodes_by_variant = calculate_route_variants(
        graph=graph,
        origin=origin,
        destination=destination,
        route_name=route_name,
        intersection_delay_s=intersection_delay_s,
    )

    all_route_edge_ids = set()
    for variant, nodes in route_nodes_by_variant.items():
        all_route_edge_ids.update(route_edge_ids(graph, nodes, VARIANT_WEIGHTS[variant]))

    records = graph_edge_records(graph, all_route_edge_ids)
    map_records = choose_map_records(records, max_map_edges)
    overlap_rows = route_overlap_rows(route_nodes_by_variant, graph)

    parameters = {
        "origin": origin,
        "destination": destination,
        "route_name": route_name,
        "departure_time": departure_time.isoformat() if departure_time is not None else None,
        "departure_hour": int(departure_time.hour) if departure_time is not None else None,
        "graph_dist_m": resolved_graph_dist_m,
        "graph_path": str(graph_path),
        "refresh_graph": refresh_graph,
        "buffer_m": buffer_m,
        "city_delay_factor": city_delay_factor,
        "intersection_delay_s": intersection_delay_s,
        "graph_nodes": len(graph.nodes),
        "graph_edges": len(graph.edges),
        "map_edges": len(map_records),
        "google_maps_url": google_maps_url(origin, destination),
        "interpolation": interpolation,
    }

    payload = diagnostic_payload(
        origin=origin,
        destination=destination,
        graph=graph,
        route_nodes_by_variant=route_nodes_by_variant,
        summaries=summaries,
        map_records=map_records,
        parameters=parameters,
    )

    return {
        "graph": graph,
        "summaries": summaries,
        "route_edges": route_edges,
        "route_nodes_by_variant": route_nodes_by_variant,
        "records": records,
        "map_records": map_records,
        "overlap_rows": overlap_rows,
        "parameters": parameters,
        "payload": payload,
    }


def write_diagnostic_map(
    output_path: Path,
    origin: tuple[float, float],
    destination: tuple[float, float],
    graph,
    route_nodes_by_variant: dict[str, list[int]],
    summaries: list[dict],
    map_records: list[dict],
    parameters: dict,
) -> None:
    payload = diagnostic_payload(
        origin=origin,
        destination=destination,
        graph=graph,
        route_nodes_by_variant=route_nodes_by_variant,
        summaries=summaries,
        map_records=map_records,
        parameters=parameters,
    )

    html_content = f"""<!doctype html>
<html lang="pl">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>OpenNavigation diagnostics</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
  <style>
    html, body, #map {{
      height: 100%;
      margin: 0;
    }}

    body {{
      color: #111827;
      font-family: Arial, sans-serif;
    }}

    .panel {{
      background: #ffffff;
      border: 1px solid #d1d5db;
      border-radius: 6px;
      box-shadow: 0 8px 24px rgba(0, 0, 0, 0.14);
      left: 16px;
      max-height: calc(100vh - 56px);
      max-width: 440px;
      overflow: auto;
      padding: 12px;
      position: absolute;
      top: 16px;
      width: calc(100% - 56px);
      z-index: 1000;
    }}

    .title {{
      font-size: 15px;
      font-weight: 700;
      margin-bottom: 8px;
    }}

    .summary-row {{
      display: grid;
      gap: 6px;
      grid-template-columns: 1fr 68px 68px;
      font-size: 13px;
      padding: 4px 0;
    }}

    .muted {{
      color: #6b7280;
      font-size: 12px;
    }}

    .legend-line {{
      display: inline-block;
      height: 4px;
      margin-right: 6px;
      vertical-align: middle;
      width: 22px;
    }}

    .popup-table {{
      border-collapse: collapse;
      font-size: 12px;
    }}

    .popup-table td {{
      border-bottom: 1px solid #e5e7eb;
      padding: 3px 6px;
      vertical-align: top;
    }}

    .popup-table td:first-child {{
      color: #6b7280;
      white-space: nowrap;
    }}
  </style>
</head>
<body>
  <div id="map"></div>
  <div class="panel">
    <div class="title">Diagnostyka OpenNavigation</div>
    <div class="muted">
      Cienkie linie pokazują surowy graf, czyli możliwe krawędzie tras.
      Grube linie pokazują pokrycie danymi ZDM albo eksperymentalną interpolację.
    </div>
    <div style="border-top:1px solid #e5e7eb; margin-top:8px; padding-top:8px;">
      <div class="summary-row muted">
        <span>Wariant</span>
        <span>Dystans</span>
        <span>Czas</span>
      </div>
      <div id="summary"></div>
    </div>
    <div style="border-top:1px solid #e5e7eb; margin-top:8px; padding-top:8px;">
      <div><span class="legend-line" style="background:#475569"></span>surowy graf</div>
      <div><span class="legend-line" style="background:#111827"></span>pokrycie ZDM/speed</div>
      <div><span class="legend-line" style="background:#6b7280"></span>interpolacja</div>
    </div>
    <div class="muted" style="border-top:1px solid #e5e7eb; margin-top:8px; padding-top:8px;">
      Godzina APR: <span id="departureHour"></span><br>
      Krawędzie na mapie: <span id="edgeCount"></span><br>
      Pełne dane są w plikach CSV/JSON obok tej mapy.
    </div>
  </div>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script>
    const data = {json.dumps(payload, ensure_ascii=False)};
    const map = L.map("map").setView(data.center, 13);

    const osmLayer = L.tileLayer("https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png", {{
      maxZoom: 19,
      attribution: "&copy; OpenStreetMap contributors"
    }});
    const blankLayer = L.layerGroup();
    const baseMaps = {{
      "Mapa OSM": osmLayer,
      "Bez tła": blankLayer
    }};
    osmLayer.addTo(map);

    function escapeHtml(value) {{
      if (value === null || value === undefined) return "";
      return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
    }}

    function popupHtml(edge) {{
      const rows = Object.entries(edge.popup).map(([key, value]) => `
        <tr>
          <td>${{escapeHtml(key)}}</td>
          <td>${{escapeHtml(value)}}</td>
        </tr>
      `).join("");
      return `<table class="popup-table">${{rows}}</table>`;
    }}

    const bounds = [];
    const rawGraphLayer = L.layerGroup().addTo(map);
    const coverageLayer = L.layerGroup().addTo(map);
    const routeLayer = L.layerGroup().addTo(map);

    for (const edge of data.edges) {{
      const rawLine = L.polyline(edge.points, {{
        color: "#475569",
        opacity: 0.78,
        weight: 2.5
      }}).bindPopup(popupHtml(edge));
      rawGraphLayer.addLayer(rawLine);

      if (edge.has_external_data || edge.is_interpolated) {{
        const coveredLine = L.polyline(edge.points, {{
          color: edge.has_external_data ? "#111827" : "#6b7280",
          dashArray: edge.is_interpolated ? "8 6" : null,
          opacity: edge.has_external_data ? 0.88 : 0.70,
          weight: edge.has_external_data ? 7 : 5
        }}).bindPopup(popupHtml(edge));
        coverageLayer.addLayer(coveredLine);
      }}
    }}

    L.marker(data.origin).addTo(map).bindPopup("Start");
    L.marker(data.destination).addTo(map).bindPopup("Cel");
    bounds.push(data.origin, data.destination);

    for (const route of data.routes) {{
      const line = L.polyline(route.points, {{
        color: route.color,
        opacity: 0.95,
        weight: 6
      }}).bindPopup(route.name);
      routeLayer.addLayer(line);
      bounds.push(...route.points);
    }}

    if (bounds.length > 0) {{
      map.fitBounds(bounds, {{ padding: [30, 30] }});
    }}

    L.control.layers(baseMaps, {{
      "surowy graf": rawGraphLayer,
      "pokrycie danych": coverageLayer,
      "warianty tras": routeLayer
    }}).addTo(map);

    document.getElementById("summary").innerHTML = data.summaries.map(row => `
      <div class="summary-row">
        <span><span class="legend-line" style="background:${{data.routes.find(route => route.name === row.variant).color}}"></span>${{escapeHtml(row.variant)}}</span>
        <span>${{escapeHtml(row.distance_km)}} km</span>
        <span>${{escapeHtml(row.adjusted_time_min)}} min</span>
      </div>
    `).join("");
    document.getElementById("departureHour").textContent =
      data.parameters.departure_hour === null ? "brak" : `${{data.parameters.departure_hour}}:00`;
    document.getElementById("edgeCount").textContent = data.edges.length;
  </script>
</body>
</html>
"""

    output_path.write_text(html_content, encoding="utf-8")


def write_summary(
    output_path: Path,
    summaries: list[dict],
    overlap_rows: list[dict],
    parameters: dict,
) -> None:
    summaries_df = pd.DataFrame(summaries)
    overlap_df = pd.DataFrame(overlap_rows)

    with output_path.open("w", encoding="utf-8") as f:
        f.write("# Diagnostyka routingu\n\n")
        f.write("## Parametry\n\n")
        f.write(f"- Origin: {parameters['origin']}\n")
        f.write(f"- Destination: {parameters['destination']}\n")
        f.write(f"- Departure time: {parameters['departure_time']}\n")
        f.write(f"- Departure hour: {parameters['departure_hour']}\n")
        f.write(f"- Graph path: {parameters['graph_path']}\n")
        f.write(f"- Graph nodes: {parameters['graph_nodes']}\n")
        f.write(f"- Graph edges: {parameters['graph_edges']}\n")
        f.write(f"- City delay factor: {parameters['city_delay_factor']}\n")
        f.write(f"- Intersection delay s: {parameters['intersection_delay_s']}\n\n")
        f.write("## Interpolacja\n\n")
        f.write(
            json.dumps(parameters.get("interpolation", {}), ensure_ascii=False, indent=2)
        )
        f.write("\n\n")

        f.write("## Warianty tras\n\n")
        f.write(summaries_df.to_markdown(index=False))
        f.write("\n\n")

        f.write("## Podobieństwo tras\n\n")
        f.write(overlap_df.to_markdown(index=False))
        f.write("\n\n")

        same_routes = (
            not overlap_df.empty
            and bool(overlap_df["same_node_sequence"].all())
        )

        if same_routes:
            f.write(
                "Wszystkie warianty mają tę samą sekwencję węzłów. "
                "To oznacza, że obecne współczynniki zmieniają koszt tej trasy, "
                "ale nie są wystarczająco silne albo wystarczająco różnicujące, "
                "żeby wskazać inną ścieżkę w grafie.\n"
            )
        else:
            f.write(
                "Co najmniej jeden wariant różni się sekwencją węzłów. "
                "Warto porównać krawędzie w `route_edges.csv` oraz mapę HTML.\n"
            )


def write_diagnostic_result(result: dict, output_dir: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)

    csv_records = [
        {key: value for key, value in record.items() if key != "points"}
        for record in result["records"]
    ]

    routes_path = output_dir / "routes_comparison.csv"
    route_edges_path = output_dir / "route_edges.csv"
    graph_edges_path = output_dir / "graph_edges.csv"
    overlap_path = output_dir / "route_overlap.csv"
    parameters_path = output_dir / "parameters.json"
    map_path = output_dir / "diagnostic_map.html"
    summary_path = output_dir / "diagnostic_summary.md"

    pd.DataFrame(result["summaries"]).to_csv(routes_path, index=False)
    pd.DataFrame(result["route_edges"]).to_csv(route_edges_path, index=False)
    pd.DataFrame(csv_records).to_csv(graph_edges_path, index=False)
    pd.DataFrame(result["overlap_rows"]).to_csv(overlap_path, index=False)
    parameters_path.write_text(
        json.dumps(result["parameters"], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    write_diagnostic_map(
        output_path=map_path,
        origin=result["parameters"]["origin"],
        destination=result["parameters"]["destination"],
        graph=result["graph"],
        route_nodes_by_variant=result["route_nodes_by_variant"],
        summaries=result["summaries"],
        map_records=result["map_records"],
        parameters=result["parameters"],
    )
    write_summary(
        summary_path,
        result["summaries"],
        result["overlap_rows"],
        result["parameters"],
    )

    return {
        "routes_comparison": str(routes_path),
        "route_edges": str(route_edges_path),
        "graph_edges": str(graph_edges_path),
        "route_overlap": str(overlap_path),
        "parameters": str(parameters_path),
        "diagnostic_map": str(map_path),
        "diagnostic_summary": str(summary_path),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Zapisz diagnostykę grafu i kosztów dla jednej trasy."
    )
    parser.add_argument("--origin", required=True, type=parse_coordinate)
    parser.add_argument("--destination", required=True, type=parse_coordinate)
    parser.add_argument("--name", default="route_diagnostics")
    parser.add_argument("--departure-time", type=parse_departure_time, default=None)
    parser.add_argument("--city-delay-factor", type=float, default=CITY_DELAY_FACTOR)
    parser.add_argument("--intersection-delay-s", type=float, default=INTERSECTION_DELAY_S)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--buffer-m", type=int, default=3_000)
    parser.add_argument("--graph-dist-m", type=int, default=None)
    parser.add_argument("--graph-path", type=Path, default=DEFAULT_GRAPH_PATH)
    parser.add_argument("--refresh-graph", action="store_true")
    parser.add_argument(
        "--max-map-edges",
        type=int,
        default=12_000,
        help="Limit krawędzi rysowanych w HTML. 0 oznacza brak limitu.",
    )
    parser.add_argument("--use-interpolation", action="store_true")
    parser.add_argument("--interpolation-radius-m", type=int, default=INTERPOLATION_RADIUS_M)
    parser.add_argument("--interpolation-strength", type=float, default=INTERPOLATION_STRENGTH)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    result = build_diagnostic_result(
        origin=args.origin,
        destination=args.destination,
        route_name=args.name,
        departure_time=args.departure_time,
        graph_path=args.graph_path,
        refresh_graph=args.refresh_graph,
        graph_dist_m=args.graph_dist_m,
        buffer_m=args.buffer_m,
        city_delay_factor=args.city_delay_factor,
        intersection_delay_s=args.intersection_delay_s,
        max_map_edges=args.max_map_edges,
        use_interpolation=args.use_interpolation,
        interpolation_radius_m=args.interpolation_radius_m,
        interpolation_strength=args.interpolation_strength,
    )

    outputs = write_diagnostic_result(result, args.output_dir)

    for output_path in outputs.values():
        print(f"Zapisano: {output_path}")


if __name__ == "__main__":
    main()
