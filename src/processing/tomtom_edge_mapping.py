from pathlib import Path
from typing import Dict, Any

import osmnx as ox
import networkx as nx
import pandas as pd
from shapely.geometry import Point

try:
    import pygeohash as pgh
except Exception:
    pgh = None


OUTPUT_DIR = Path("/app/data/processed")


def edge_midpoint_latlon(u: int, v: int, G: nx.Graph) -> tuple[float, float]:
    data = G.get_edge_data(u, v)
    # pick first edge data
    first = next(iter(data.values()))

    geom = first.get("geometry")
    if geom is not None:
        pt = geom.interpolate(0.5, normalized=True)
        return float(pt.y), float(pt.x)

    # fallback to node coordinates
    u_data = G.nodes[u]
    v_data = G.nodes[v]
    lat = (u_data.get("y", 0.0) + v_data.get("y", 0.0)) / 2.0
    lon = (u_data.get("x", 0.0) + v_data.get("x", 0.0)) / 2.0
    return float(lat), float(lon)


def compute_priority(highway: str | list | None, G: nx.Graph, u: int, v: int) -> float:
    # simple heuristic: highway importance + node degree
    hw = "" if highway is None else (highway[0] if isinstance(highway, list) else str(highway))
    hw = str(hw)
    weight = 1.0
    if "motorway" in hw:
        weight = 5.0
    elif "trunk" in hw:
        weight = 4.0
    elif "primary" in hw:
        weight = 3.0
    elif "secondary" in hw:
        weight = 2.0
    elif "tertiary" in hw:
        weight = 1.5

    deg = (G.degree(u) + G.degree(v)) / 2.0
    return float(weight * (1 + deg / 4.0))


def build_cell_id(lat: float, lon: float, precision: int) -> str:
    if pgh is not None:
        return pgh.encode(lat, lon, precision=precision)

    # Fallback: stable grid cell string when pygeohash is unavailable.
    # The divisor gets smaller for larger precision values, which produces
    # finer cells without requiring an extra dependency.
    cell_size = max(0.001, 0.1 / float(precision))
    lat_bucket = round(lat / cell_size)
    lon_bucket = round(lon / cell_size)
    return f"grid_{precision}_{lat_bucket}_{lon_bucket}"


def main(geohash_precision: int = 6, center_point: tuple = (52.21, 21.0), dist: int = 9000):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print("Pobieranie grafu OSMnx...")
    ox.settings.use_cache = True
    ox.settings.log_console = False

    G = ox.graph_from_point(center_point, dist=dist, network_type="drive", simplify=True)

    print(f"Graf pobrany. Węzłów: {len(G.nodes)}, krawędzi: {len(G.edges)}")

    rows: list[Dict[str, Any]] = []

    for u, v, key, data in G.edges(keys=True, data=True):
        lat, lon = edge_midpoint_latlon(u, v, G)

        geohash = build_cell_id(lat, lon, geohash_precision)

        name = data.get("name")
        highway = data.get("highway")

        # Normalize possible list values to strings for Parquet/CSV
        if isinstance(name, list):
            name_str = "; ".join(str(x) for x in name if x is not None)
        else:
            name_str = None if name is None else str(name)

        if isinstance(highway, list):
            highway_str = ",".join(str(x) for x in highway if x is not None)
        else:
            highway_str = None if highway is None else str(highway)
        length = float(data.get("length", 0.0))

        priority = compute_priority(highway, G, u, v)

        rows.append({
            "u": int(u),
            "v": int(v),
            "key": int(key) if isinstance(key, int) or (isinstance(key, str) and key.isdigit()) else str(key),
            "name": name_str,
            "highway": highway_str,
            "length_m": round(length, 2),
            "mid_lat": lat,
            "mid_lon": lon,
            "geohash": geohash,
            "priority_score": priority,
        })

    df = pd.DataFrame(rows)

    csv_path = OUTPUT_DIR / "tomtom_edge_mapping.csv"
    parquet_path = OUTPUT_DIR / "tomtom_edge_mapping.parquet"

    df.to_csv(csv_path, index=False)
    df.to_parquet(parquet_path, index=False)

    print(f"Zapisano mapping: {csv_path} i {parquet_path}")


if __name__ == "__main__":
    main()
