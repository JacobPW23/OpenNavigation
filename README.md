# OpenNavigation

Projekt z przedmiotu Big Data: **propozycja trasy na podstawie otwartych danych nawigacyjnych dla Warszawy**.

Celem projektu jest przygotowanie potoku przetwarzania danych oraz wykonanie wstępnej analizy tras. Sieć drogowa Warszawy jest reprezentowana jako graf z OpenStreetMap/OSMnx, a koszt przejazdu po krawędziach grafu jest modyfikowany przez dane o natężeniu ruchu, dane prędkości oraz warunki pogodowe.

## Źródła danych

Projekt korzysta z kilku typów danych:

- **OpenStreetMap / OSMnx** — struktura sieci drogowej i bazowe czasy przejazdu,
- **ZDM Warszawa APR** — natężenie ruchu z punktów pomiarowych,
- **ZDM raporty prędkości PDF** — średnie prędkości na wybranych ulicach,
- **Open-Meteo** — dane pogodowe.

Dane ZDM mają charakter punktowy i nie pokrywają całej sieci drogowej. Z tego powodu model działa warstwowo:

1. jeżeli dla krawędzi są dane APR i dane prędkości ZDM, używane są oba źródła,
2. jeżeli są tylko dane APR, używany jest `traffic_factor`,
3. jeżeli są tylko dane prędkości ZDM, używany jest `speed_factor`,
4. jeżeli nie ma danych ZDM, używany jest bazowy czas przejazdu z OSMnx.

Źródło kosztu dla każdej krawędzi jest zapisywane w pliku `route_edges.csv` w kolumnie `cost_source`.

Możliwe wartości:

```text
zdm_apr_and_speed
zdm_apr
zdm_speed
osmnx_base
```

## Technologie

W projekcie wykorzystano:

- Python,
- Docker Compose,
- Apache Kafka,
- Apache Spark Structured Streaming,
- Pandas,
- GeoPandas,
- OSMnx,
- NetworkX,
- PyArrow,
- pypdf.

## Struktura projektu

```text
src/
  ingestion/
    traffic_loader.py
    zdm_traffic_loader.py
    zdm_speed_pdf_parser.py

  processing/
    overpass_transformations.py
    weather_transformations.py
    traffic_transformations.py

  analysis/
    osm_initial_analysis.py
    zdm_speed_aggregation.py
    route_initial_analysis.py

data/
  raw/          # dane surowe, lokalne, niewersjonowane
  processed/    # dane przetworzone
  results/      # wyniki analiz
```

## Uruchomienie środowiska

Zbudowanie obrazu:

```bash
docker compose build
```

Uruchomienie kontenera:

```bash
docker compose up -d
```

Sprawdzenie działania:

```bash
docker compose ps
```

Test importów:

```bash
docker compose exec -T opennavigation python - <<'PY'
import pandas as pd
import geopandas as gpd
import osmnx as ox
import networkx as nx
import pyspark
import confluent_kafka

print("Environment OK")
print("pandas:", pd.__version__)
print("geopandas:", gpd.__version__)
print("osmnx:", ox.__version__)
print("networkx:", nx.__version__)
PY
```

## Dane prędkości ZDM

Surowe raporty PDF z pomiarami prędkości ZDM nie są wersjonowane w repozytorium ze względu na rozmiar. Raporty należy pobrać z mapy interaktywnej ZDM dotyczącej badań prędkości i umieścić lokalnie w katalogu:

```text
data/raw/traffic/speed_reports/
```

Parser PDF:

```bash
docker compose exec opennavigation python src/ingestion/zdm_speed_pdf_parser.py
```

Wynik parsera:

```text
data/raw/traffic/zdm_speed_measurements.csv
```

Agregacja prędkości po ulicach:

```bash
docker compose exec opennavigation python src/analysis/zdm_speed_aggregation.py
```

Wynik agregacji:

```text
data/processed/zdm_speed_by_road.csv
```

Ten plik jest mały i może być commitowany do repozytorium. Dzięki temu inni użytkownicy mogą uruchomić analizę tras bez pobierania wszystkich PDF-ów.

## Dane APR ZDM

Dane APR ZDM należy pobrać ze strony ZDM jako plik XLSX i umieścić lokalnie w katalogu:

```text
data/raw/traffic/
```

Następnie należy uruchomić loader:

```bash
docker compose exec opennavigation python src/ingestion/zdm_traffic_loader.py
```

Loader wysyła dane do topicu Kafka:

```text
warszawa-raw-traffic
```

Transformacja Spark:

```bash
docker compose exec opennavigation spark-submit \
  --packages org.apache.spark:spark-sql-kafka-0-10_2.12:3.5.1 \
  src/processing/traffic_transformations.py
```

Po przetworzeniu pierwszego batcha proces można zatrzymać przez `Ctrl+C`.

Wynik:

```text
data/processed/traffic_measurements/
```

## Dane OSM

Dane OpenStreetMap są przetwarzane do postaci drogowych odcinków Warszawy.

Transformacja Spark:

```bash
docker compose exec opennavigation spark-submit \
  --packages org.apache.spark:spark-sql-kafka-0-10_2.12:3.5.1 \
  src/processing/overpass_transformations.py
```

Wynik:

```text
data/processed/osm_ways/
```

Analiza jakości danych OSM:

```bash
docker compose exec opennavigation python src/analysis/osm_initial_analysis.py
```

Wyniki:

```text
data/results/osm_initial_analysis/
```

Najważniejsze pliki:

```text
01_dataset_overview.csv
02_highway_distribution.csv
03_attribute_completeness.csv
summary.md
```

## Dane pogodowe

Dane pogodowe są traktowane jako dodatkowa warstwa wpływająca na koszt przejazdu.

Wynik przetwarzania pogody:

```text
data/processed/weather_observations/
```

W analizie tras wykorzystywana jest wartość:

```text
weather_factor
```

Jeżeli dane pogodowe nie są dostępne, model używa wartości domyślnej:

```text
weather_factor = 1.0
```

## Analiza tras

Główny skrypt analizy tras:

```bash
docker compose exec opennavigation python src/analysis/route_initial_analysis.py
```

Trasy testowe są definiowane w pliku:

```text
src/analysis/route_initial_analysis.py
```

w zmiennej:

```python
ROUTES = [
    {
        "route_name": "Mokotowska -> Dobra",
        "origin": (52.218662, 21.017080),
        "destination": (52.244974, 21.020670),
    },
]
```

Współrzędne są podawane jako:

```text
(latitude, longitude)
```

Analiza porównuje trzy warianty:

1. trasę najkrótszą dystansowo,
2. trasę najszybszą według bazowego czasu OSMnx,
3. trasę skorygowaną o dane ruchu, prędkości i pogodę.

Wyniki są zapisywane do:

```text
data/results/route_initial_analysis/
```

Najważniejsze pliki:

```text
routes_comparison.csv
route_edges.csv
summary.md
```

`routes_comparison.csv` zawiera porównanie wariantów tras.

`route_edges.csv` zawiera szczegóły dla każdej krawędzi, między innymi:

```text
traffic_factor
speed_factor
traffic_speed_factor
measured_avg_speed_kmh
cost_source
```

`summary.md` zawiera podsumowanie wyników w formacie Markdown.

## Sprawdzenie wpływu danych ZDM

Po uruchomieniu analizy tras można sprawdzić, które źródła danych zostały użyte na krawędziach trasy:

```bash
python3 - <<'PY'
import pandas as pd

df = pd.read_csv("data/results/route_initial_analysis/route_edges.csv")

print("Źródła kosztu:")
print(df["cost_source"].value_counts())
print()

print("Krawędzie z danymi ZDM:")
print(df[df["cost_source"] != "osmnx_base"][[
    "variant",
    "edge_order",
    "name",
    "traffic_factor",
    "speed_factor",
    "measured_avg_speed_kmh",
    "cost_source"
]].to_string(index=False))
PY
```

## Interpretacja wyników

Przykładowa sytuacja:

```text
wariant najkrótszy:
distance = 4.73 km
base_time = 5.99 min
adjusted_time = 7.12 min

wariant skorygowany:
distance = 4.80 km
base_time = 5.82 min
adjusted_time = 6.68 min
```

Oznacza to, że wariant skorygowany może być nieco dłuższy dystansowo, ale korzystniejszy po uwzględnieniu danych ZDM. Pokazuje to, że dodatkowe warstwy danych rzeczywiście wpływają na ocenę trasy.

## Ograniczenia

- Dane ZDM są punktowe i nie pokrywają całej sieci drogowej.
- Część ulic ma tylko dane APR, część tylko dane prędkości, część oba źródła, a część tylko bazowe dane OSMnx.
- Dopasowanie danych ZDM do krawędzi grafu odbywa się po nazwie ulicy.
- Parser PDF obsługuje standardowy układ raportów ZDM, ale część plików PDF może wymagać dalszej obsługi.
- W dalszych etapach można poprawić dopasowanie danych przez przypisanie punktów GPS do najbliższych krawędzi grafu.

## Git i dane

Nie należy commitować:

```text
data/raw/
data/results/
PDF
XLSX
Parquet
checkpointów Spark
dużych wyników uruchomień
```

Do repozytorium można commitować:

```text
kod źródłowy
Dockerfile / docker-compose.yml
requirements.txt
README.md
data/processed/zdm_speed_by_road.csv
```

Przykładowe reguły `.gitignore`:

```gitignore
# Raw input data
data/raw/*
!data/raw/.gitkeep
!data/raw/traffic/
!data/raw/traffic/.gitkeep

# Heavy ZDM source files
data/raw/traffic/speed_reports/
data/raw/traffic/*.xlsx
data/raw/traffic/*.xls
data/raw/traffic/*.pdf

# Pipeline outputs
data/processed/*
!data/processed/.gitkeep
!data/processed/zdm_speed_by_road.csv

data/results/*
!data/results/.gitkeep

# Runtime / cache
spark-warehouse/
checkpoints/
*.log
*.pid

# Python
__pycache__/
*.pyc
.venv/
venv/
.env

# OS / IDE
.DS_Store
.idea/
.vscode/
```

## Odtworzenie najważniejszej analizy

Minimalna ścieżka uruchomienia analizy tras po sklonowaniu repozytorium:

```bash
docker compose build
docker compose up -d
docker compose exec opennavigation python src/analysis/route_initial_analysis.py
```

Warunkiem jest obecność pliku:

```text
data/processed/zdm_speed_by_road.csv
```

oraz wcześniej przygotowanych danych APR/pogody, jeżeli analiza ma korzystać z pełnego modelu warstwowego.