from __future__ import annotations

"""
Buduje bazowy graf drogowy OSMnx i zapisuje go do GraphML.

Ten skrypt uruchamia się raz przed trasowaniem, żeby route_query.py i
route_web.py mogły później wczytywać gotowy graf z pliku.
"""

import argparse
from pathlib import Path

import osmnx as ox

from route_query import DEFAULT_GRAPH_PATH, parse_coordinate


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Zbuduj lokalny graf drogowy OSMnx.")
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_GRAPH_PATH,
        help=f"Plik wynikowy GraphML. Domyślnie: {DEFAULT_GRAPH_PATH}",
    )
    parser.add_argument(
        "--place",
        default="Warsaw, Poland",
        help='Obszar OSMnx dla graph_from_place, np. "Warsaw, Poland".',
    )
    parser.add_argument(
        "--center",
        type=parse_coordinate,
        default=None,
        help='Opcjonalny środek jako "lat,lon". Jeśli podany, używa graph_from_point.',
    )
    parser.add_argument(
        "--dist-m",
        type=int,
        default=12_000,
        help="Promień w metrach dla --center.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()

    ox.settings.use_cache = True
    ox.settings.log_console = False

    if args.center:
        print(f"Pobieranie grafu OSMnx z punktu: center={args.center}, dist={args.dist_m} m")
        graph = ox.graph_from_point(
            args.center,
            dist=args.dist_m,
            network_type="drive",
            simplify=True,
        )
    else:
        print(f"Pobieranie grafu OSMnx dla obszaru: {args.place}")
        graph = ox.graph_from_place(
            args.place,
            network_type="drive",
            simplify=True,
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    ox.save_graphml(graph, args.output)

    print(f"Zapisano graf: {args.output}")
    print(f"Węzły: {len(graph.nodes)}, krawędzie: {len(graph.edges)}")


if __name__ == "__main__":
    main()
