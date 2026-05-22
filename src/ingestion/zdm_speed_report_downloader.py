import asyncio
import os
import re
from pathlib import Path
from urllib.parse import urljoin
from playwright.async_api import async_playwright, Playwright
import httpx

# --- Konfiguracja ---
DASHBOARD_URL = "https://zdm-warszawa.maps.arcgis.com/apps/dashboards/20bfffc9bae544b5a1cdfaad7be38c40"
DOWNLOAD_DIR = Path("data/raw/traffic/speed_reports")
SCROLL_CONTAINER_SELECTOR = ".tabulator-tableholder" # Selektor kontenera do przewijania

async def download_file(url: str, destination_folder: Path):
    async with httpx.AsyncClient() as client:
        try:
            # Najpierw wyślij zapytanie HEAD, aby uzyskać nagłówki bez pobierania treści
            head_response = await client.head(url, follow_redirects=True, timeout=30.0)
            head_response.raise_for_status()

            file_name = "unknown_report.pdf" # Domyślna nazwa
            if 'content-disposition' in head_response.headers:
                # Wyciągnij nazwę pliku z nagłówka, np. "attachment; filename="nazwa_pliku.pdf""
                d = head_response.headers['content-disposition']
                match = re.search(r'filename="([^"]+)"', d)
                if match:
                    file_name = match.group(1)

            destination_path = destination_folder / file_name

            if destination_path.exists():
                print(f"Pominięto (plik już istnieje): {destination_path}")
                return

            # Teraz pobierz właściwy plik
            print(f"Pobieranie: {file_name} z {url}")
            response = await client.get(url, follow_redirects=True, timeout=120.0)
            response.raise_for_status()
            
            destination_path.write_bytes(response.content)
            print(f"Zapisano: {destination_path}")

        except httpx.RequestError as e:
            print(f"Błąd sieciowy podczas pobierania {url}: {e}")
        except Exception as e:
            print(f"Nieoczekiwany błąd podczas przetwarzania {url}: {e}")

async def run(playwright: Playwright):
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Folder docelowy: {DOWNLOAD_DIR.resolve()}")

    browser = await playwright.chromium.launch(headless=True)
    page = await browser.new_page()
    
    print(f"Nawigacja do: {DASHBOARD_URL}")
    try:
        await page.goto(DASHBOARD_URL, wait_until="networkidle", timeout=90000)
    except Exception as e:
        print(f"Nie udało się załadować strony w wyznaczonym czasie: {e}")
        await browser.close()
        return

    print("Strona załadowana. Wyszukiwanie linków do pobrania...")

    # --- Logika przewijania wirtualnej tabeli ---
    scroll_container = page.locator(SCROLL_CONTAINER_SELECTOR)
    try:
        await scroll_container.wait_for(state="visible", timeout=30000)
    except Exception:
        print(f"Nie znaleziono kontenera do przewijania w wyznaczonym czasie: {SCROLL_CONTAINER_SELECTOR}")
        await browser.close()
        return

    print("Znaleziono kontener tabeli. Rozpoczynanie precyzyjnego przewijania...")
    
    unique_links = set()
    
    # Pętla przewijająca tabelę krok po kroku
    while True:
        # Zbierz linki widoczne w aktualnym "oknie" tabeli
        links_in_view = await page.get_by_role("link", name="Wyświetl").all()
        for link in links_in_view:
            href = await link.get_attribute("href")
            if href:
                unique_links.add(href)
        
        print(f"Zebrano dotychczas {len(unique_links)} unikalnych linków.")

        # Przewiń kontener o jego własną wysokość, aby załadować kolejną partię danych
        last_scroll_top = await scroll_container.evaluate("el => el.scrollTop")
        await scroll_container.evaluate("el => { el.scrollTop += el.clientHeight; }")
        
        # Poczekaj na załadowanie się nowych elementów
        await page.wait_for_timeout(1500)
        
        # Sprawdź, czy przewinięcie faktycznie nastąpiło
        new_scroll_top = await scroll_container.evaluate("el => el.scrollTop")
        
        if new_scroll_top == last_scroll_top:
            # Jeśli pozycja przewinięcia się nie zmieniła, to znaczy, że jesteśmy na końcu.
            # Zbierz ostatnie linki na wszelki wypadek.
            links_in_view = await page.get_by_role("link", name="Wyświetl").all()
            for link in links_in_view:
                href = await link.get_attribute("href")
                if href:
                    unique_links.add(href)
            print("Osiągnięto koniec tabeli. Zakończono przewijanie.")
            break

    if not unique_links:
        print("Nie znaleziono żadnych linków 'Wyświetl' na stronie po przewinięciu.")
        await browser.close()
        return

    print(f"Znaleziono łącznie {len(unique_links)} unikalnych linków. Rozpoczynanie pobierania...")

    tasks = []
    for href in unique_links:
        # Upewnij się, że URL jest absolutny
        absolute_url = urljoin(page.url, href)
        tasks.append(download_file(absolute_url, DOWNLOAD_DIR))

    if tasks:
        await asyncio.gather(*tasks)
    
    print("Wszystkie zadania pobierania zakończone.")
    await browser.close()

async def main():
    async with async_playwright() as playwright:
        await run(playwright)

if __name__ == "__main__":
    asyncio.run(main())
