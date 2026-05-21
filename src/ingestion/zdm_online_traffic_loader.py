# src/ingestion/zdm_online_traffic_loader.py

import asyncio
from datetime import datetime
from playwright.async_api import async_playwright, Playwright

# --- Konfiguracja ---
ZDM_URL = "https://zdm.waw.pl/dzialania/badania-i-analizy/analiza-ruchu-na-drogach/analiza-ruchu-na-drogach-online/"
DOWNLOAD_PATH = "data/raw/traffic/zdm_real_traffic.xlsx"

async def run(playwright: Playwright):
    """
    Główna funkcja automatyzująca pobieranie danych ze strony ZDM.
    """
    browser = await playwright.chromium.launch(headless=True) # headless=False do debugowania
    page = await browser.new_page()
    await page.goto(ZDM_URL)

    print("Strona załadowana. Obsługa cookies...")
    try:
        # Kliknięcie przycisku "Odmów" na banerze cookies
        await page.click('text="Odmów"', timeout=5000)
        print("Kliknięto 'Odmów' na banerze cookies.")
    except Exception as e:
        print("Nie znaleziono banera cookies lub wystąpił błąd:", e)

    # Krok 1 - Wybierz rodzaj ruchu
    # Tutaj dodamy kod do kliknięcia odpowiedniego przycisku/opcji
    print("Implementacja Kroku 1: Wybierz rodzaj ruchu")
    await page.click('text="Ruch na jezdniach"')
    print("Kliknięto 'Ruch na jezdniach'.")

    # Krok 2 - Wybierz lokalizacje
    # Tutaj dodamy kod do wyboru lokalizacji z listy lub mapy
    print("Implementacja Kroku 2: Wybierz lokalizacje")
    # 1. Kliknij przycisk "Punkty"
    await page.click('text="Punkty"')
    print("Kliknięto 'Punkty'.")
    
    # 2. Zaznacz wszystkie punkty (checkbox w nagłówku)
    # Używamy selektora opartego o aria-label, aby precyzyjnie zlokalizować checkbox.
    await page.get_by_label("Zaznacz wszystkie wiersze").check()
    print("Zaznaczono wszystkie punkty.")

    # 3. Kliknij przycisk "DALEJ"
    await page.click('button:has-text("DALEJ")')
    print("Kliknięto 'DALEJ'.")

    # Krok 3 - Dostosuj okres i parametry
    print("Implementacja Kroku 3: Dostosuj okres i parametry")

    # 1. Kliknij w pole daty, aby otworzyć kalendarz
    await page.locator('.MuiInputBase-root:has(legend:has-text("Przedział czasowy"))').click()
    print("Otwarto kalendarz.")

    # 2. Kliknij "Zaznacz miesiąc/rok"
    await page.click('button:has-text("Zaznacz miesiąc/rok")')
    print("Kliknięto 'Zaznacz miesiąc/rok'.")

    # 3. Przejdź do bieżącego roku za pomocą strzałek
    current_year = str(datetime.now().year)
    year_button_selector = f'button.buttonPicker:has-text("{current_year}")'
    arrow_button_selector = 'button.buttonPickerArrow:has-text(">")'

    print(f"Szukanie roku {current_year}...")
    while not await page.locator(year_button_selector).is_visible():
        await page.click(arrow_button_selector)
        print("Kliknięto strzałkę w prawo.")
        # Dodajemy małe opóźnienie, aby uniknąć zbyt szybkiego klikania
        await page.wait_for_timeout(100)
    
    print(f"Znaleziono rok {current_year}.")


    # 4. Wybierz bieżący rok
    current_year = str(datetime.now().year)
    # Używamy selektora, który pasuje do dostarczonego HTML: <button class="buttonPicker null">2026</button>
    await page.click(f'button.buttonPicker:has-text("{current_year}")')
    print(f"Wybrano rok {current_year}.")

    print("Oczekiwanie na załadowanie rekordów po wybraniu roku...")
    await page.wait_for_timeout(5000) # Czekaj 5 sekund
    
    # 5. Wybierz typy dni
    print("Wybieranie typów dni...")
    await page.locator('.MuiInputBase-root:has(legend:has-text("Typy dni"))').click()
    
    print("Wybieranie wszystkich typów dni...")
    day_types = await page.get_by_role("option").all()
    for day_type in day_types:
        await day_type.click()
        await page.wait_for_timeout(200) # Małe opóźnienie między kliknięciami

    await page.keyboard.press('Escape') # Zamknij dropdown

    # Wybierz grupowanie lokalizacji
    print("Wybieranie grupowania lokalizacji...")
    await page.locator('.MuiInputBase-root:has(legend:has-text("Grupowanie lokalizacji"))').click()
    await page.get_by_role("option", name="osobno dla każdego kierunku").click()

    # Wybierz sposób prezentacji danych
    print("Wybieranie sposobu prezentacji danych...")
    await page.locator('.MuiInputBase-root:has(legend:has-text("Sposób prezentacji danych"))').click()
    await page.get_by_role("option", name="godzinowo w ciągu doby").click()

    # Generuj raport
    print("Generowanie raportu...")
    await page.get_by_role("button", name="Generuj raport").click()

    # Czekaj na wygenerowanie raportu i przycisk pobierania
    print("Oczekiwanie na wygenerowanie raportu...")
    download_button = page.get_by_role("button", name="Pobierz XLS")
    await download_button.wait_for(timeout=120000) # Czekaj do 2 minut

    # Oczekuj na pobranie pliku i kliknij przycisk
    print("Pobieranie raportu...")
    async with page.expect_download() as download_info:
        await download_button.click()
    
    download = await download_info.value
    
    # Zapisz plik
    await download.save_as(DOWNLOAD_PATH)
    print(f"Raport zapisany w {DOWNLOAD_PATH}")

    print("Zakończono. Zamykanie przeglądarki za 10 sekund.")
    await asyncio.sleep(10)
    await browser.close()

async def main():
    """
    Funkcja uruchomieniowa skryptu.
    """
    async with async_playwright() as playwright:
        await run(playwright)

if __name__ == "__main__":
    asyncio.run(main())
