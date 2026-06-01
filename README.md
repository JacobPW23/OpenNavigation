# OpenNavigation

Projekt z przedmiotu Big Data: **propozycja trasy na podstawie otwartych danych nawigacyjnych dla Warszawy**.

Celem projektu jest przygotowanie potoku przetwarzania danych oraz wykonanie wstępnej analizy tras. Sieć drogowa Warszawy jest reprezentowana jako graf z OpenStreetMap/OSMnx, a koszt przejazdu jest korygowany na podstawie danych o natężeniu ruchu, prędkości oraz pogodzie.

## Wykorzystywane dane

Projekt korzysta z kilku źródeł danych:

- **OpenStreetMap / OSMnx** — graf drogowy Warszawy i bazowe czasy przejazdu,
- **ZDM Warszawa APR** — natężenie ruchu drogowego,
- **TomTom Traffic Flow API** — bieżące prędkości i stan ruchu dla wybranych punktów w Warszawie,
- **ZDM raporty prędkości PDF** — średnie prędkości na wybranych ulicach,
- **Open-Meteo** — dane pogodowe.

Dane ZDM są punktowe i nie pokrywają całej sieci drogowej. Dlatego model działa warstwowo:

1. jeśli dostępne są dane APR i dane prędkości ZDM — używane są oba źródła,
2. jeśli dostępne jest tylko APR — używany jest `traffic_factor`,
3. jeśli dostępne są tylko dane prędkości — używany jest `speed_factor`,
4. jeśli brak danych ZDM — używany jest bazowy czas przejazdu z OSMnx.

Źródło kosztu dla każdej krawędzi jest zapisywane w kolumnie `cost_source`.

Możliwe wartości:

```text
zdm_apr_and_speed
zdm_apr
zdm_speed
osmnx_base
```





## Technologie

Projekt wykorzystuje:

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
    zdm_traffic_loader.py
    zdm_speed_pdf_parser.py

  processing/
    overpass_transformations.py
    weather_transformations.py
    traffic_transformations.py

  analysis/
    osm_initial_analysis.py
    zdm_speed_aggregation.py
    zdm_traffic_aggregation.py
    route_initial_analysis.py

data/
  raw/          # dane surowe, niewersjonowane
  processed/    # małe dane zagregowane i dane przetworzone
  results/      # wyniki analiz, niewersjonowane
```

## Dane dostępne po sklonowaniu repozytorium

Repozytorium zawiera małe zagregowane pliki danych potrzebne do uruchomienia analizy tras:

```text
data/processed/zdm_speed_by_road.csv
data/processed/zdm_traffic_by_road.csv
```

Dzięki temu po sklonowaniu repozytorium nie trzeba pobierać dużych plików PDF ani XLSX, aby uruchomić podstawową analizę tras.

Surowe dane nie są commitowane:

```text
data/raw/
data/results/
PDF
XLSX
Parquet
checkpointy Spark
```

## Szybkie uruchomienie po sklonowaniu repozytorium

```bash
git clone <URL_REPO>
cd OpenNavigation
```

Zbuduj obraz:

```bash
docker compose build
```

Uruchom kontener:

```bash
docker compose up -d
```

Sprawdź status:

```bash
docker compose ps
```

Uruchom analizę tras:

```bash
docker compose exec opennavigation python src/analysis/route_initial_analysis.py
```

Wyniki pojawią się w katalogu:

```text
data/results/route_initial_analysis/
```

Najważniejsze pliki wynikowe:

```text
routes_comparison.csv
route_edges.csv
summary.md
```

Podgląd wyników:

```bash
cat data/results/route_initial_analysis/routes_comparison.csv
```

## Sprawdzenie działania modelu warstwowego

Po uruchomieniu analizy można sprawdzić, z jakich źródeł danych korzystały krawędzie trasy:

```bash
python3 - <<'PY'
import pandas as pd

df = pd.read_csv("data/results/route_initial_analysis/route_edges.csv")

print("Źródła kosztu:")
print(df["cost_source"].value_counts())
PY
```

Jeśli pojawiają się wartości takie jak:

```text
zdm_apr
zdm_speed
zdm_apr_and_speed
osmnx_base
```

to znaczy, że model korzysta z danych ZDM oraz fallbacku OSMnx.

## Analiza tras

Trasy testowe są zdefiniowane w pliku:

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

Wynik `routes_comparison.csv` zawiera między innymi:

```text
distance_km
base_time_min
adjusted_time_min
traffic_impacted_edges
speed_impacted_edges
zdm_apr_edges
zdm_speed_edges
osmnx_only_edges
```

Plik `route_edges.csv` zawiera szczegóły każdej krawędzi trasy, między innymi:

```text
traffic_factor
speed_factor
traffic_speed_factor
measured_avg_speed_kmh
cost_source
```

## Interpretacja wyników

Jeżeli wariant skorygowany ma większy dystans, ale niższy `adjusted_time_min`, oznacza to, że dodatkowe dane ZDM wpłynęły na wybór korzystniejszej trasy.

Przykład interpretacji:

```text
Trasa najkrótsza może być krótsza dystansowo, ale prowadzić przez odcinki o większym natężeniu ruchu lub niższej średniej prędkości. Trasa skorygowana może być dłuższa, ale mieć niższy koszt po uwzględnieniu danych ZDM.
```

Czasy przejazdu należy traktować jako **koszt porównawczy tras**, a nie dokładną predykcję czasu znaną z systemów komercyjnych. Model nie uwzględnia wszystkich czynników, takich jak sygnalizacja świetlna, kolejki na skrzyżowaniach, manewry skrętu czy aktualny ruch live.

## Odtworzenie danych prędkości ZDM od zera

Surowe raporty PDF z pomiarami prędkości ZDM nie są wersjonowane ze względu na rozmiar.

Aby odtworzyć dane prędkości od zera, należy uruchomić skrypt do pobierania raportów PDF z mapy ZDM dotyczącej badań prędkości:

```bash
docker compose exec opennavigation python src/ingestion/zdm_speed_raport_downloader.py
```
Pobrane raporty PDF będą znajdować się w katalogu:

```text
data/raw/traffic/speed_reports/
```

Następnie uruchomić parser:

```bash
docker compose exec opennavigation python src/ingestion/zdm_speed_pdf_parser.py
```

Wynik parsera:

```text
data/raw/traffic/zdm_speed_measurements.csv
```

Następnie wykonać agregację po ulicach:

```bash
docker compose exec opennavigation python src/analysis/zdm_speed_aggregation.py
```

Wynik:

```text
data/processed/zdm_speed_by_road.csv
```

## Odtworzenie danych APR ZDM od zera

Aby pobrać dane APR ZDM jako raport XLSX ze strony ZDM należy wykonać skrypt:

```bash
docker compose exec opennavigation python src/ingestion/zdm_online_traffic_loader.py
```
Skrypt pobierze raport do katalogu:

```text
data/raw/traffic/
```

Następnie uruchomić loader:

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

Po przetworzeniu pierwszego batcha można zatrzymać proces przez `Ctrl+C`.

Wynik:

```text
data/processed/traffic_measurements/
```

Następnie można utworzyć mały plik agregacji APR:

```bash
docker compose exec opennavigation python src/analysis/zdm_traffic_aggregation.py
```

Wynik:

```text
data/processed/zdm_traffic_by_road.csv
```

## Odtworzenie danych TomTom od zera

Surowe dane TomTom nie są wersjonowane w repozytorium (dostarczane są przez zewnętrzne API).

Należy skopiować zmienne środowiskowe do `.env`

```bash
cp .env.example .env
```

`.env.example`:
```bash
TOMTOM_API_KEY=your_real_key_here
TOMTOM_MAPPING_PATH=/app/data/processed/tomtom_edge_mapping.parquet
TOMTOM_POLL_INTERVAL_SECONDS=300
TOMTOM_RUN_ONCE=0
TOMTOM_MAX_CELLS_PER_POLL=50
```

Ustawić należy włazny klucz API ze strony `my.tomtom.com`.


Wygenerować mapping krawędzi -> komórki (wymagane):

```bash
docker compose exec opennavigation python src/processing/tomtom_edge_mapping.py
```

Plik wynikowy:

```text
data/processed/tomtom_edge_mapping.parquet
```

Uruchomić loader TomTom (jednorazowo, test):

```bash
docker compose exec opennavigation sh -c 'export TOMTOM_RUN_ONCE=1; python src/ingestion/tomtom_traffic_loader.py'
```

Loader publikuje znormalizowane rekordy do topicu:

```text
warszawa-raw-traffic
```

Uruchomić transformację Spark, aby przetworzyć wiadomości i zapisać Parquet:

```bash
docker compose exec opennavigation spark-submit \
  --packages org.apache.spark:spark-sql-kafka-0-10_2.12:3.5.1 \
  src/processing/traffic_transformations.py
```

Wynik transformacji:

```text
data/processed/traffic_measurements/
```

Wykonać agregację (używa tej samej procedury, co agregacja ZDM):

```bash
docker compose exec opennavigation python src/analysis/zdm_traffic_aggregation.py
```

Wynik:

```text
data/processed/zdm_traffic_by_road.csv
```


## Analiza OSM

Wstępna analiza danych OSM:

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

## Test środowiska

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

## Ograniczenia

- Dane ZDM są punktowe i nie pokrywają całej sieci drogowej.
- Część krawędzi korzysta tylko z danych OSMnx.
- Dopasowanie danych ZDM do grafu odbywa się głównie po nazwie ulicy.
- Parser PDF obsługuje standardowy układ raportów ZDM, ale nie każdy PDF musi zostać sparsowany idealnie.
- W dalszych etapach można poprawić dopasowanie przez przypisanie punktów GPS do najbliższych krawędzi grafu.

## Git i dane

Commitować należy:

```text
kod źródłowy
Dockerfile
docker-compose.yml
requirements.txt
README.md
data/processed/zdm_speed_by_road.csv
data/processed/zdm_traffic_by_road.csv
```

Nie commitować:

```text
data/raw/
data/results/
PDF
XLSX
Parquet
checkpointów Spark
dużych wyników uruchomień
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
!data/processed/zdm_traffic_by_road.csv

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
