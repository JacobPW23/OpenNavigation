from __future__ import annotations

"""
Lokalny web UI do wybierania punktu startowego i końcowego na mapie.

Serwer używa standardowego http.server, więc nie wymaga Flask/FastAPI. Trasy
liczy tym samym kodem co route_query.py.
"""

import argparse
import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from route_query import (
    CITY_DELAY_FACTOR,
    DEFAULT_GRAPH_PATH,
    INTERSECTION_DELAY_S,
    build_google_comparison,
    build_weighted_graph,
    calculate_route_variants,
    edge_route_points,
    google_maps_url,
    graph_radius_m,
    parse_coordinate,
    parse_departure_time,
    save_results,
    write_route_map,
)


DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8050
DEFAULT_OUTPUT_DIR = Path("/app/data/results/route_web")


def json_response(handler: BaseHTTPRequestHandler, status: HTTPStatus, payload: dict) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def html_response(handler: BaseHTTPRequestHandler, status: HTTPStatus, body: str) -> None:
    body_bytes = body.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(body_bytes)))
    handler.end_headers()
    handler.wfile.write(body_bytes)


def parse_query_coordinate(params: dict[str, list[str]], name: str) -> tuple[float, float]:
    values = params.get(name)
    if not values:
        raise ValueError(f"Brakuje parametru: {name}")
    return parse_coordinate(values[0])


def route_payload(
    graph,
    route_nodes_by_variant: dict[str, list[int]],
    summaries: list[dict],
) -> dict:
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

    return {
        "summaries": summaries,
        "routes": routes,
        "weather": graph.graph.get("weather_context"),
    }


def page_html() -> str:
    return """<!doctype html>
<html lang="pl">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>OpenNavigation</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
  <style>
    html,
    body,
    #map {
      height: 100%;
      margin: 0;
    }

    body {
      color: #111827;
      font-family: Arial, sans-serif;
    }

    .panel {
      background: #ffffff;
      border: 1px solid #d1d5db;
      border-radius: 6px;
      box-shadow: 0 8px 24px rgba(0, 0, 0, 0.14);
      left: 16px;
      max-width: 420px;
      padding: 12px;
      position: absolute;
      top: 16px;
      width: calc(100% - 56px);
      z-index: 1000;
    }

    .row {
      display: grid;
      gap: 6px;
      grid-template-columns: 62px 1fr;
      margin-bottom: 8px;
    }

    label {
      color: #374151;
      font-size: 13px;
      line-height: 32px;
    }

    input {
      border: 1px solid #cbd5e1;
      border-radius: 4px;
      box-sizing: border-box;
      font: 14px Arial, sans-serif;
      height: 32px;
      padding: 0 8px;
      width: 100%;
    }

    .actions {
      display: flex;
      gap: 8px;
      margin-top: 10px;
    }

    button {
      background: #111827;
      border: 0;
      border-radius: 4px;
      color: #ffffff;
      cursor: pointer;
      flex: 1;
      font: 14px Arial, sans-serif;
      height: 34px;
      padding: 0 12px;
    }

    button.secondary {
      background: #e5e7eb;
      color: #111827;
    }

    button:disabled {
      cursor: wait;
      opacity: 0.65;
    }

    .status {
      color: #374151;
      font-size: 13px;
      margin-top: 8px;
      min-height: 18px;
    }

    .summary {
      border-top: 1px solid #e5e7eb;
      font-size: 13px;
      margin-top: 10px;
      max-height: 210px;
      overflow: auto;
      padding-top: 8px;
    }

    .summary-row {
      display: grid;
      gap: 6px;
      grid-template-columns: 1fr 70px 70px;
      padding: 4px 0;
    }

    .summary-head {
      color: #6b7280;
      font-size: 12px;
    }

    .legend-line {
      display: inline-block;
      height: 4px;
      margin-right: 6px;
      vertical-align: middle;
      width: 22px;
    }
  </style>
</head>
<body>
  <div id="map"></div>
  <div class="panel">
    <div class="row">
      <label for="origin">Start</label>
      <input id="origin" placeholder="lat,lon">
    </div>
    <div class="row">
      <label for="destination">Cel</label>
      <input id="destination" placeholder="lat,lon">
    </div>
    <div class="row">
      <label for="departure">Odjazd</label>
      <input id="departure" type="datetime-local" step="3600">
    </div>
    <div class="actions">
      <button id="routeButton">Policz</button>
      <button class="secondary" id="clearButton">Wyczyść</button>
    </div>
    <div class="status" id="status"></div>
    <div class="summary" id="summary"></div>
  </div>

  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script>
    const map = L.map("map").setView([52.2297, 21.0122], 12);
    const originInput = document.getElementById("origin");
    const destinationInput = document.getElementById("destination");
    const departureInput = document.getElementById("departure");
    const statusEl = document.getElementById("status");
    const summaryEl = document.getElementById("summary");
    const routeButton = document.getElementById("routeButton");
    const clearButton = document.getElementById("clearButton");

    let originMarker = null;
    let destinationMarker = null;
    let nextPoint = "origin";

    const osmLayer = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: "&copy; OpenStreetMap contributors"
    });
    const blankLayer = L.layerGroup();
    const routeLayer = L.layerGroup().addTo(map);

    osmLayer.addTo(map);
    L.control.layers(
      {
        "Mapa OSM": osmLayer,
        "Bez tła": blankLayer
      },
      {
        "Trasy": routeLayer
      }
    ).addTo(map);

    function formatLatLon(latlng) {
      return `${latlng.lat.toFixed(6)},${latlng.lng.toFixed(6)}`;
    }

    function setMarker(kind, latlng) {
      const label = kind === "origin" ? "Start" : "Cel";
      const marker = L.marker(latlng).addTo(map).bindPopup(label);
      if (kind === "origin") {
        if (originMarker) originMarker.remove();
        originMarker = marker;
        originInput.value = formatLatLon(latlng);
      } else {
        if (destinationMarker) destinationMarker.remove();
        destinationMarker = marker;
        destinationInput.value = formatLatLon(latlng);
      }
    }

    function clearRoutes() {
      routeLayer.clearLayers();
      summaryEl.innerHTML = "";
    }

    function clearAll() {
      clearRoutes();
      if (originMarker) originMarker.remove();
      if (destinationMarker) destinationMarker.remove();
      originMarker = null;
      destinationMarker = null;
      originInput.value = "";
      destinationInput.value = "";
      departureInput.value = "";
      nextPoint = "origin";
      statusEl.textContent = "";
    }

    map.on("click", event => {
      setMarker(nextPoint, event.latlng);
      nextPoint = nextPoint === "origin" ? "destination" : "origin";
    });

    clearButton.addEventListener("click", clearAll);

    function formatValue(value, suffix = "") {
      if (value === null || value === undefined || Number.isNaN(value)) {
        return "brak";
      }
      if (typeof value === "number") {
        return `${Math.round(value * 10) / 10}${suffix}`;
      }
      return `${value}${suffix}`;
    }

    function weatherHtml(weather) {
      if (!weather || !weather.available) {
        return `<div>Brak danych pogodowych, użyto factor: ${weather ? weather.factor : 1}</div>`;
      }

      return `
        <div>Factor: ${weather.factor} (${weather.match_type})</div>
        <div>Czas danych: ${weather.matched_time || "średnia dla godziny"}</div>
        <div>Temperatura: ${formatValue(weather.temperature_2m, " °C")}</div>
        <div>Opad: ${formatValue(weather.precipitation_mm, " mm")}</div>
        <div>Śnieg: ${formatValue(weather.snowfall_cm, " cm")}</div>
        <div>Wiatr: ${formatValue(weather.wind_speed_10m_kmh, " km/h")}</div>
      `;
    }

    function buildRouteParams() {
      const params = new URLSearchParams({
        origin: originInput.value,
        destination: destinationInput.value
      });
      if (departureInput.value) {
        params.set("departure_time", departureInput.value);
      }
      return params;
    }

    routeButton.addEventListener("click", async () => {
      if (!originInput.value || !destinationInput.value) {
        statusEl.textContent = "Wybierz start i cel.";
        return;
      }

      clearRoutes();
      routeButton.disabled = true;
      statusEl.textContent = "Liczenie trasy...";

      try {
        const params = buildRouteParams();
        const response = await fetch(`/route?${params.toString()}`);
        const data = await response.json();

        if (!response.ok) {
          throw new Error(data.error || "Nie udało się policzyć trasy.");
        }

        const bounds = [];
        for (const route of data.routes) {
          const layer = L.polyline(route.points, {
            color: route.color,
            opacity: 0.85,
            weight: 5
          }).bindPopup(route.name);
          routeLayer.addLayer(layer);
          bounds.push(...route.points);
        }

        if (bounds.length > 0) {
          map.fitBounds(bounds, { padding: [30, 30] });
        }

        summaryEl.innerHTML = `
          <div class="summary-row summary-head">
            <span>Wariant</span>
            <span>Dystans</span>
            <span>Czas</span>
          </div>
          ${data.summaries.map(row => `
            <div class="summary-row">
              <span><span class="legend-line" style="background:${data.colors[row.variant]}"></span>${row.variant}</span>
              <span>${row.distance_km} km</span>
              <span>${row.adjusted_time_min} min</span>
            </div>
          `).join("")}
          <div style="border-top:1px solid #e5e7eb; margin-top:8px; padding-top:8px;">
            <div><strong>Godzina APR</strong>: ${data.departure_hour === null ? "brak" : `${data.departure_hour}:00`}</div>
            <div><strong>Typ dnia APR</strong>: ${data.departure_day_type || "brak"}</div>
          </div>
          <div style="border-top:1px solid #e5e7eb; margin-top:8px; padding-top:8px;">
            <div><strong>Pogoda</strong></div>
            ${weatherHtml(data.weather)}
          </div>
          <div style="border-top:1px solid #e5e7eb; margin-top:8px; padding-top:8px;">
            <div><strong>Google Maps</strong></div>
            <div><a href="${data.google.maps_url}" target="_blank" rel="noreferrer">Otwórz porównanie</a></div>
            ${data.google.status === "ok" ? `
              <div>Dystans: ${data.google.distance_km} km</div>
              <div>Czas: ${data.google.duration_min} min</div>
              <div>Różnica czasu OpenNavigation - Google: ${data.google.time_diff_min} min</div>
            ` : `
              <div>${data.google.message || "Automatyczne dane Google niedostępne."}</div>
            `}
          </div>
        `;
        statusEl.textContent = "Gotowe.";
      } catch (error) {
        statusEl.textContent = error.message;
      } finally {
        routeButton.disabled = false;
      }
    });

  </script>
</body>
</html>
"""


class RouteHandler(BaseHTTPRequestHandler):
    server_version = "OpenNavigationRouteWeb/1.0"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)

        if parsed.path == "/":
            html_response(self, HTTPStatus.OK, page_html())
            return

        if parsed.path == "/route":
            self.handle_route(parsed.query)
            return

        if parsed.path == "/health":
            json_response(self, HTTPStatus.OK, {"status": "ok"})
            return

        json_response(self, HTTPStatus.NOT_FOUND, {"error": "Nie znaleziono endpointu."})

    def log_message(self, format: str, *args) -> None:
        print(f"{self.address_string()} - {format % args}")

    def handle_route(self, query: str) -> None:
        params = parse_qs(query)

        try:
            origin = parse_query_coordinate(params, "origin")
            destination = parse_query_coordinate(params, "destination")
            route_name = params.get("name", ["route_web"])[0]
            departure_values = params.get("departure_time", [None])
            departure_time = parse_departure_time(departure_values[0])
            departure_hour = int(departure_time.hour) if departure_time is not None else None
            departure_day_type = None
            if departure_time is not None and str(departure_time.date()) != "2000-01-01":
                departure_day_type = "weekend" if departure_time.dayofweek >= 5 else "dzień roboczy"
            graph_dist_m = graph_radius_m(
                origin=origin,
                destination=destination,
                buffer_m=self.server.buffer_m,
                override_m=self.server.graph_dist_m,
            )
            graph = build_weighted_graph(
                origin=origin,
                destination=destination,
                graph_dist_m=graph_dist_m,
                graph_path=self.server.graph_path,
                refresh_graph=self.server.refresh_graph,
                departure_time=departure_time,
                city_delay_factor=self.server.city_delay_factor,
                intersection_delay_s=self.server.intersection_delay_s,
            )
            self.server.refresh_graph = False

            summaries, edge_rows, route_nodes_by_variant = calculate_route_variants(
                graph=graph,
                origin=origin,
                destination=destination,
                route_name=route_name,
                intersection_delay_s=self.server.intersection_delay_s,
            )
            google_comparison = None
            if not self.server.skip_google:
                google_comparison = build_google_comparison(
                    summaries=summaries,
                    origin=origin,
                    destination=destination,
                    api_key=self.server.google_api_key,
                )
            else:
                google_comparison = {
                    "status": "skipped",
                    "maps_url": google_maps_url(origin, destination),
                    "message": "Porównanie z Google Maps zostało pominięte.",
                }

            self.server.output_dir.mkdir(parents=True, exist_ok=True)
            map_path = self.server.output_dir / "route_map.html"
            write_route_map(
                graph=graph,
                route_nodes_by_variant=route_nodes_by_variant,
                origin=origin,
                destination=destination,
                output_path=map_path,
            )
            save_results(
                summaries,
                edge_rows,
                self.server.output_dir,
                map_path,
                google_comparison,
            )

            payload = route_payload(graph, route_nodes_by_variant, summaries)
            payload["google"] = google_comparison
            payload["departure_hour"] = departure_hour
            payload["departure_day_type"] = departure_day_type
            payload["colors"] = {
                "najkrótsza dystansowo": "#2563eb",
                "najszybsza bazowo": "#16a34a",
                "skorygowana ruchem i pogodą": "#dc2626",
            }
            payload["outputs"] = {
                "routes_comparison": str(self.server.output_dir / "routes_comparison.csv"),
                "route_edges": str(self.server.output_dir / "route_edges.csv"),
                "summary": str(self.server.output_dir / "summary.md"),
                "route_map": str(map_path),
            }
            json_response(self, HTTPStatus.OK, payload)
        except Exception as exc:
            json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(exc)})

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Uruchom lokalny web UI trasowania.")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--graph-path", type=Path, default=DEFAULT_GRAPH_PATH)
    parser.add_argument("--refresh-graph", action="store_true")
    parser.add_argument("--graph-dist-m", type=int, default=None)
    parser.add_argument("--buffer-m", type=int, default=3_000)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--city-delay-factor", type=float, default=CITY_DELAY_FACTOR)
    parser.add_argument("--intersection-delay-s", type=float, default=INTERSECTION_DELAY_S)
    parser.add_argument("--google-api-key", default=os.getenv("GOOGLE_MAPS_API_KEY"))
    parser.add_argument("--skip-google", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    server = ThreadingHTTPServer((args.host, args.port), RouteHandler)
    server.graph_path = args.graph_path
    server.refresh_graph = args.refresh_graph
    server.graph_dist_m = args.graph_dist_m
    server.buffer_m = args.buffer_m
    server.output_dir = args.output_dir
    server.city_delay_factor = args.city_delay_factor
    server.intersection_delay_s = args.intersection_delay_s
    server.google_api_key = args.google_api_key
    server.skip_google = args.skip_google
    print(f"OpenNavigation web UI: http://localhost:{args.port}")
    print(f"GraphML: {args.graph_path}")
    server.serve_forever()


if __name__ == "__main__":
    main()
