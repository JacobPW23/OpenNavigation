import csv
import re
from pathlib import Path

from pypdf import PdfReader


INPUT_DIR = Path("/app/data/raw/traffic/speed_reports")
OUTPUT_PATH = Path("/app/data/raw/traffic/zdm_speed_measurements.csv")

DAY_PATTERN = r"(pon\.|wt\.|śr\.|czw\.|pt\.|sob\.|niedz\.)"
WEEKDAY_TOKENS = ["pon.", "wt.", "śr.", "czw.", "pt.", "sob.", "niedz."]


def extract_text(path: Path) -> str:
    reader = PdfReader(str(path))
    pages = []

    for page in reader.pages:
        text = page.extract_text() or ""
        pages.append(text)

    return "\n".join(pages)


def clean_spaces(value: str | None) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value).replace("\xa0", " ")).strip()


def parse_float_pl(value: str | None) -> float | None:
    if value is None:
        return None

    value = clean_spaces(value).replace(",", ".")

    if value in {"", "-"}:
        return None

    try:
        return float(value)
    except ValueError:
        return None


def parse_int_pl(value: str | None) -> int | None:
    if value is None:
        return None

    value = clean_spaces(value).replace(" ", "")

    if value in {"", "-"}:
        return None

    try:
        return int(value)
    except ValueError:
        return None


def is_weekday_token(token: str) -> bool:
    return token.lower() in WEEKDAY_TOKENS


def is_float_token(token: str) -> bool:
    return bool(re.fullmatch(r"\d+[,.]\d+|-", token))


def normalize_direction(value: str | None) -> str | None:
    text = clean_spaces(value)
    text = text.replace("Kierunek:", "").strip()
    text = text.strip(" -–")

    if not text:
        return None

    lower = text.lower()

    only_weekday_like = lower.replace("-", "").replace("–", "").strip()
    if only_weekday_like in WEEKDAY_TOKENS:
        return None

    if len(lower) <= 10 and any(token in lower for token in WEEKDAY_TOKENS):
        return None

    return text


def parse_station_id(text: str) -> str | None:
    match = re.search(r"Punkt\s+(\d+)", text)
    return match.group(1) if match else None


def parse_street_name(text: str) -> str | None:
    match = re.search(r"Punkt\s+\d+\s*[–-]\s*([^\(\n]+)", text)
    if match:
        return clean_spaces(match.group(1)).replace("ul. ", "")

    match = re.search(r"\d+\s+(ul\.\s+[A-ZŁŚŻŹĆŃÓĘĄa-złśżźćńóęą0-9 .-]+)", text)
    if match:
        return clean_spaces(match.group(1)).replace("ul. ", "")

    return None


def parse_station_name(text: str) -> str | None:
    match = re.search(r"Punkt\s+\d+\s*[–-]\s*[^\(]+\(([^\)]+)\)", text)
    if match:
        return clean_spaces(match.group(1))

    return None


def parse_gps(text: str) -> tuple[float | None, float | None]:
    text_one_line = clean_spaces(text)

    match = re.search(
        r"N\s*([0-9]{2}[,.][0-9]+)\s*E\s*([0-9]{2}[,.][0-9]+)",
        text_one_line,
    )

    if not match:
        return None, None

    lat = parse_float_pl(match.group(1))
    lon = parse_float_pl(match.group(2))

    return lat, lon


def parse_measurement_date(text: str) -> str | None:
    match = re.search(r"(\d{2}\.\d{2}\.\d{4})\s+00:00:00", text)
    if not match:
        return None

    day, month, year = match.group(1).split(".")
    return f"{year}-{month}-{day}"


def extract_section(text: str, start_marker: str, end_marker: str) -> str:
    start = text.find(start_marker)
    if start == -1:
        return ""

    end = text.find(end_marker, start)
    if end == -1:
        return text[start:]

    return text[start:end]


def remove_before_last_header(section: str, header_text: str) -> str:
    section_one_line = clean_spaces(section)
    idx = section_one_line.rfind(header_text)
    if idx == -1:
        return section_one_line
    return section_one_line[idx + len(header_text):].strip()


def cut_after_footer(text: str) -> str:
    footer_markers = [
        "Wykonanie pomiarów",
        "Heller Consult",
        "Przekroczenia dopuszczalnej prędkości",
        "Odstępy niebezpieczne",
        "Udziały przekroczeń",
        "Rozkład prędkości",
    ]

    cut = len(text)
    for marker in footer_markers:
        idx = text.find(marker)
        if idx != -1:
            cut = min(cut, idx)

    return text[:cut].strip()


def parse_average_speed_rows(text: str) -> dict[tuple[str | None, str], dict]:
    """
    Parsuje tabelę 'Prędkości średnie'.

    Obsługuje też przypadki, gdzie PDF rozbija jeden rekord na kilka linii:
    Nowy Zjazd
    śr.
    42,36 41,95 ...
    Zajęcza 44,69 44,66 ...
    """
    section = extract_section(text, "Prędkości średnie", "Przekroczenia dopuszczalnej prędkości")
    if not section:
        return {}

    data_text = remove_before_last_header(section, "doba dzień noc doba dzień noc doba dzień noc")
    data_text = cut_after_footer(data_text)
    tokens = data_text.split()

    rows = {}
    i = 0
    last_day = None

    while i < len(tokens):
        direction_tokens = []

        while i < len(tokens) and not is_weekday_token(tokens[i]) and not is_float_token(tokens[i]):
            direction_tokens.append(tokens[i])
            i += 1

        direction = normalize_direction(" ".join(direction_tokens))

        day = None
        if i < len(tokens) and is_weekday_token(tokens[i]):
            day = tokens[i]
            last_day = day
            i += 1
        else:
            day = last_day

        values = []
        while i < len(tokens) and len(values) < 9 and is_float_token(tokens[i]):
            values.append(tokens[i])
            i += 1

        if direction and day and len(values) >= 9:
            rows[(direction, day)] = {
                "direction": direction,
                "day_of_week": day,
                "avg_speed_kmh": parse_float_pl(values[0]),
                "avg_speed_day_kmh": parse_float_pl(values[1]),
                "avg_speed_night_kmh": parse_float_pl(values[2]),
                "avg_speed_light_kmh": parse_float_pl(values[3]),
                "avg_speed_heavy_kmh": parse_float_pl(values[6]),
            }
        else:
            # Zabezpieczenie przed pętlą nieskończoną przy nietypowym fragmencie tekstu.
            i += 1

    return rows


def parse_summary_rows(text: str) -> dict[tuple[str | None, str], dict]:
    """
    Parsuje tabelę 'Zbiorcze zestawienie wyników'.

    Ta tabela bywa gorzej wyciągana z PDF-ów niż 'Prędkości średnie',
    więc traktujemy ją jako źródło pomocnicze.
    """
    section = extract_section(text, "Zbiorcze zestawienie wyników", "Prędkości średnie")
    if not section:
        return {}

    data_text = remove_before_last_header(
        section,
        "Vmax [km/h] V85 [km/h] UVdop [%] Uodst.niebezp. [%] USW [%]"
    )

    # Fallback, jeśli dokładny nagłówek został inaczej wyciągnięty.
    if "Kierunek" in data_text and "USW" in data_text:
        possible_start = data_text.rfind("USW [%]")
        if possible_start != -1:
            data_text = data_text[possible_start + len("USW [%]"):].strip()

    data_text = cut_after_footer(data_text)
    tokens = data_text.split()

    rows = {}
    i = 0
    last_day = None
    last_vdop = None

    while i < len(tokens):
        direction_tokens = []

        while i < len(tokens) and not is_weekday_token(tokens[i]) and not is_float_token(tokens[i]):
            # Liczby całkowite mogą być częścią N lub Vdop, więc zatrzymujemy się,
            # gdy po nazwie zaczynają się dane liczbowe.
            if re.fullmatch(r"\d+", tokens[i]) and direction_tokens:
                break
            direction_tokens.append(tokens[i])
            i += 1

        direction = normalize_direction(" ".join(direction_tokens))

        day = None
        if i < len(tokens) and is_weekday_token(tokens[i]):
            day = tokens[i]
            last_day = day
            i += 1
        else:
            day = last_day

        pre_float = []
        while i < len(tokens) and not is_float_token(tokens[i]):
            pre_float.append(tokens[i])
            i += 1

        vehicle_count = None
        vdop_raw = last_vdop

        if pre_float:
            # Jeżeli jest nawias typu (50), to zakładamy końcówkę jako Vdop.
            paren_idx = None
            for idx, token in enumerate(pre_float):
                if re.fullmatch(r"\(\d+\)", token):
                    paren_idx = idx
                    break

            if paren_idx is not None and paren_idx >= 1:
                n_tokens = pre_float[:paren_idx - 1]
                vdop_tokens = pre_float[paren_idx - 1:paren_idx + 1]
                vehicle_count = parse_int_pl(" ".join(n_tokens))
                vdop_raw = clean_spaces(" ".join(vdop_tokens))
                last_vdop = vdop_raw
            else:
                # W kolejnych wierszach Vdop bywa pominięte, np.:
                # Zajęcza 1 465 44,7 85,7 ...
                vehicle_count = parse_int_pl(" ".join(pre_float))

        values = []
        while i < len(tokens) and len(values) < 6 and is_float_token(tokens[i]):
            values.append(tokens[i])
            i += 1

        if direction and day and len(values) >= 3:
            rows[(direction, day)] = {
                "direction": direction,
                "day_of_week": day,
                "vehicle_count": vehicle_count,
                "vdop_raw": vdop_raw,
                "avg_speed_from_summary_kmh": parse_float_pl(values[0]),
                "vmax_kmh": parse_float_pl(values[1]),
                "v85_kmh": parse_float_pl(values[2]),
            }
        else:
            i += 1

    return rows


def parse_pdf(path: Path) -> list[dict]:
    text = extract_text(path)

    station_id = parse_station_id(text)
    road_name = parse_street_name(text)
    station_name = parse_station_name(text)
    lat, lon = parse_gps(text)
    measurement_date = parse_measurement_date(text)

    summary_rows = parse_summary_rows(text)
    speed_rows = parse_average_speed_rows(text)

    keys = set(summary_rows.keys()) | set(speed_rows.keys())

    records = []

    for key in sorted(keys, key=lambda x: (str(x[0]), str(x[1]))):
        summary = summary_rows.get(key, {})
        speed = speed_rows.get(key, {})

        direction = speed.get("direction") or summary.get("direction")
        day_of_week = speed.get("day_of_week") or summary.get("day_of_week")

        avg_speed_kmh = speed.get("avg_speed_kmh")
        if avg_speed_kmh is None:
            avg_speed_kmh = summary.get("avg_speed_from_summary_kmh")

        records.append({
            "source": "zdm_speed_pdf",
            "source_file": path.name,
            "station_id": station_id,
            "station_name": station_name,
            "road_name": road_name,
            "direction": direction,
            "lat": lat,
            "lon": lon,
            "measurement_date": measurement_date,
            "measurement_time": f"{measurement_date} 00:00:00" if measurement_date else None,
            "period_minutes": 1440,
            "day_of_week": day_of_week,
            "vehicle_count": summary.get("vehicle_count"),
            "avg_speed_kmh": avg_speed_kmh,
            "avg_speed_day_kmh": speed.get("avg_speed_day_kmh"),
            "avg_speed_night_kmh": speed.get("avg_speed_night_kmh"),
            "avg_speed_light_kmh": speed.get("avg_speed_light_kmh"),
            "avg_speed_heavy_kmh": speed.get("avg_speed_heavy_kmh"),
            "vmax_kmh": summary.get("vmax_kmh"),
            "v85_kmh": summary.get("v85_kmh"),
            "vdop_raw": summary.get("vdop_raw"),
        })

    return records


def deduplicate_records(records: list[dict]) -> list[dict]:
    unique = {}

    for record in records:
        key = (
            record.get("station_id"),
            record.get("measurement_date"),
            record.get("road_name"),
            record.get("direction"),
            record.get("avg_speed_kmh"),
        )
        unique[key] = record

    return list(unique.values())


def main():
    if not INPUT_DIR.exists():
        raise FileNotFoundError(f"Nie znaleziono katalogu: {INPUT_DIR}")

    pdf_files = sorted(INPUT_DIR.glob("*.pdf"))

    if not pdf_files:
        raise FileNotFoundError(f"Brak plików PDF w katalogu: {INPUT_DIR}")

    all_records = []
    files_without_records = []
    files_without_avg_speed = []

    for path in pdf_files:
        print(f"Parsowanie: {path.name}")
        records = parse_pdf(path)
        print(f"  rekordów: {len(records)}")

        if not records:
            print("  UWAGA: nie znaleziono rekordów prędkości w tym pliku.")
            files_without_records.append(path.name)

        if records and not any(r.get("avg_speed_kmh") is not None for r in records):
            print("  UWAGA: znaleziono rekordy, ale bez avg_speed_kmh.")
            files_without_avg_speed.append(path.name)

        all_records.extend(records)

    all_records = deduplicate_records(all_records)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "source",
        "source_file",
        "station_id",
        "station_name",
        "road_name",
        "direction",
        "lat",
        "lon",
        "measurement_date",
        "measurement_time",
        "period_minutes",
        "day_of_week",
        "vehicle_count",
        "avg_speed_kmh",
        "avg_speed_day_kmh",
        "avg_speed_night_kmh",
        "avg_speed_light_kmh",
        "avg_speed_heavy_kmh",
        "vmax_kmh",
        "v85_kmh",
        "vdop_raw",
    ]

    with OUTPUT_PATH.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_records)

    parsed_with_speed = sum(1 for r in all_records if r.get("avg_speed_kmh") is not None)

    print(f"\nZapisano: {OUTPUT_PATH}")
    print(f"Liczba PDF-ów: {len(pdf_files)}")
    print(f"Liczba rekordów po deduplikacji: {len(all_records)}")
    print(f"Liczba rekordów z avg_speed_kmh: {parsed_with_speed}")

    if files_without_records:
        print("\nPDF-y bez żadnych rekordów:")
        for filename in files_without_records:
            print(f"  - {filename}")

    if files_without_avg_speed:
        print("\nPDF-y z rekordami, ale bez avg_speed_kmh:")
        for filename in files_without_avg_speed:
            print(f"  - {filename}")


if __name__ == "__main__":
    main()
