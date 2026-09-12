#!/usr/bin/env python3
"""
Descarga el Registro de Instrucciones de Operación (RIO) - Energía
usando Chrome/Selenium, evitando la llamada HTTP directa a admin-ajax.php
que puede devolver HTTP 403 desde GitHub Actions.

Salida:
    data/data.json

La fecha se calcula en hora de Chile (America/Santiago).
"""

import csv
import io
import json
import os
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

RIO_URL = (
    "https://www.coordinador.cl/operacion/documentos/"
    "registro-de-instrucciones-de-operacion-rio-sscc-energia/"
)

OUTPUT_PATH = Path("data/data.json")
DOWNLOAD_DIR = Path("tmp_downloads")
CHILE_TZ = ZoneInfo("America/Santiago")
DOWNLOAD_TIMEOUT = 120
PAGE_TIMEOUT = 45


def create_driver(download_dir: Path) -> webdriver.Chrome:
    """Crea Chrome headless configurado para descargas automáticas."""
    download_dir.mkdir(parents=True, exist_ok=True)

    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument(
        "--user-agent=Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/153.0.0.0 Safari/537.36"
    )

    options.add_experimental_option(
        "prefs",
        {
            "download.default_directory": str(download_dir.resolve()),
            "download.prompt_for_download": False,
            "download.directory_upgrade": True,
            "safebrowsing.enabled": True,
        },
    )

    driver = webdriver.Chrome(options=options)

    # Permite descargas en headless Chrome.
    driver.execute_cdp_cmd(
        "Page.setDownloadBehavior",
        {
            "behavior": "allow",
            "downloadPath": str(download_dir.resolve()),
        },
    )

    return driver


def set_input_value(driver, element, value: str) -> None:
    """Establece un valor de input y dispara eventos para el JavaScript del sitio."""
    driver.execute_script(
        """
        const element = arguments[0];
        const value = arguments[1];
        const descriptor = Object.getOwnPropertyDescriptor(
            window.HTMLInputElement.prototype,
            'value'
        );
        if (descriptor && descriptor.set) {
            descriptor.set.call(element, value);
        } else {
            element.value = value;
        }
        element.dispatchEvent(new Event('input', { bubbles: true }));
        element.dispatchEvent(new Event('change', { bubbles: true }));
        element.dispatchEvent(new Event('blur', { bubbles: true }));
        """,
        element,
        value,
    )


def get_date_inputs(driver):
    """Obtiene los inputs de fecha de la página en orden de aparición."""
    inputs = driver.find_elements(By.CSS_SELECTOR, "input[type='date']")
    if len(inputs) >= 2:
        return inputs

    # Respaldo: algunos cambios del sitio pueden eliminar type=date.
    inputs = driver.find_elements(By.CSS_SELECTOR, "input")
    date_like = []
    for element in inputs:
        attrs = " ".join(
            [
                element.get_attribute("name") or "",
                element.get_attribute("id") or "",
                element.get_attribute("placeholder") or "",
                element.get_attribute("class") or "",
            ]
        ).lower()
        if "fecha" in attrs or "date" in attrs:
            date_like.append(element)
    return date_like


def set_energy_dates(driver, fecha: str) -> None:
    """Configura fecha inicio y fecha término de Energía."""
    wait = WebDriverWait(driver, PAGE_TIMEOUT)
    wait.until(lambda d: len(get_date_inputs(d)) >= 2)

    date_inputs = get_date_inputs(driver)
    if len(date_inputs) < 2:
        raise RuntimeError("No se encontraron los dos campos de fecha de Energía.")

    set_input_value(driver, date_inputs[0], fecha)
    set_input_value(driver, date_inputs[1], fecha)

    # Hora 00:00:00 a 23:59:59. Seleccionamos por texto cuando existen selects.
    selects = driver.find_elements(By.CSS_SELECTOR, "select")
    if len(selects) >= 2:
        driver.execute_script(
            """
            function selectFirstMatching(select, patterns, fallbackIndex) {
                const options = Array.from(select.options);
                let index = -1;
                for (const pattern of patterns) {
                    index = options.findIndex(o =>
                        (o.text || '').trim().includes(pattern) ||
                        (o.value || '').trim().includes(pattern)
                    );
                    if (index >= 0) break;
                }
                if (index < 0 && options.length > fallbackIndex) index = fallbackIndex;
                if (index >= 0) {
                    select.selectedIndex = index;
                    select.dispatchEvent(new Event('change', { bubbles: true }));
                }
            }
            selectFirstMatching(arguments[0], ['00:00:00', '00:00'], 0);
            selectFirstMatching(arguments[1], ['23:59:59', '23:59'], arguments[1].options.length - 1);
            """,
            selects[0],
            selects[1],
        )

    time.sleep(2)


def find_energy_download_button(driver):
    """Encuentra el primer enlace/botón Descargar, que corresponde a Energía."""
    candidates = driver.find_elements(By.XPATH, "//*[self::a or self::button]")

    for element in candidates:
        text = (element.text or "").strip().lower()
        if text == "descargar" or "descargar" in text:
            if element.is_displayed() and element.is_enabled():
                return element

    # Respaldo: buscar por clase/atributos conocidos o por href.
    candidates = driver.find_elements(
        By.CSS_SELECTOR,
        "a[href*='export'], a.download-energia, a[class*='download'], button[class*='download']",
    )
    for element in candidates:
        if element.is_displayed() and element.is_enabled():
            return element

    raise RuntimeError("No se encontró el botón 'Descargar' de Energía.")


def wait_for_csv(download_dir: Path, before: set[Path]) -> Path:
    """Espera hasta que Chrome termine de descargar un CSV."""
    deadline = time.time() + DOWNLOAD_TIMEOUT

    while time.time() < deadline:
        current = set(download_dir.iterdir())
        new_files = current - before

        # Chrome usa .crdownload mientras la descarga está incompleta.
        csv_files = [
            p for p in new_files
            if p.is_file() and p.suffix.lower() == ".csv"
        ]

        if csv_files:
            candidate = max(csv_files, key=lambda p: p.stat().st_mtime)

            # Confirmamos que el archivo no sigue creciendo.
            size1 = candidate.stat().st_size
            time.sleep(1)
            size2 = candidate.stat().st_size
            if size1 == size2 and size2 > 0:
                return candidate

        time.sleep(1)

    raise TimeoutError(
        f"No apareció el CSV descargado después de {DOWNLOAD_TIMEOUT} segundos."
    )


def download_rio(fecha: str) -> Path:
    """Abre el RIO, configura hoy y pulsa Descargar."""
    if DOWNLOAD_DIR.exists():
        shutil.rmtree(DOWNLOAD_DIR)
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

    driver = create_driver(DOWNLOAD_DIR)

    try:
        print(f"Abriendo: {RIO_URL}")
        driver.get(RIO_URL)

        wait = WebDriverWait(driver, PAGE_TIMEOUT)
        wait.until(
            EC.presence_of_element_located(
                (By.XPATH, "//*[contains(normalize-space(), 'Descargar Datos Energía')]")
            )
        )

        print(f"Configurando fecha de Energía: {fecha}")
        set_energy_dates(driver, fecha)

        before = set(DOWNLOAD_DIR.iterdir())
        button = find_energy_download_button(driver)

        print("Presionando 'Descargar'...")
        driver.execute_script("arguments[0].click();", button)

        csv_path = wait_for_csv(DOWNLOAD_DIR, before)
        print(f"CSV descargado: {csv_path}")
        return csv_path

    except Exception:
        # Capturamos screenshot para diagnóstico si algo cambia en la página.
        try:
            driver.save_screenshot("rio_error.png")
            print("Se generó rio_error.png para diagnóstico.", file=sys.stderr)
        except Exception:
            pass
        raise
    finally:
        driver.quit()


def csv_to_rows(csv_text: str) -> list[dict]:
    """Convierte el CSV a una lista de diccionarios."""
    csv_text = csv_text.lstrip("\ufeff")
    sample = csv_text[:4096]
    delimiter = ";" if sample.count(";") > sample.count(",") else ","

    reader = csv.DictReader(io.StringIO(csv_text), delimiter=delimiter)
    return list(reader)


def read_csv_file(csv_path: Path) -> list[dict]:
    """Lee el CSV con las codificaciones más habituales."""
    last_error = None

    for encoding in ("utf-8-sig", "utf-8", "latin-1", "cp1252"):
        try:
            text = csv_path.read_text(encoding=encoding)
            rows = csv_to_rows(text)
            print(f"CSV leído correctamente con encoding: {encoding}")
            return rows
        except Exception as exc:
            last_error = exc

    raise RuntimeError(f"No fue posible leer el CSV: {last_error}")


def write_json(fecha: str, rows: list[dict], status: str, error_message: str | None) -> None:
    """Escribe data/data.json."""
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "fecha_consultada": fecha,
        "actualizado_en": datetime.now(CHILE_TZ).isoformat(),
        "status": status,
        "error": error_message,
        "total_registros": len(rows),
        "registros": rows,
    }

    with OUTPUT_PATH.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def main() -> int:
    now_chile = datetime.now(CHILE_TZ)
    fecha = now_chile.strftime("%Y-%m-%d")

    print("=" * 70)
    print("RIO - COORDINADOR ELÉCTRICO NACIONAL")
    print(f"Hora Chile: {now_chile.isoformat()}")
    print(f"Fecha consultada: {fecha}")
    print("=" * 70)

    try:
        csv_path = download_rio(fecha)
        rows = read_csv_file(csv_path)

        if not rows:
            raise RuntimeError("La descarga terminó pero el CSV no contiene registros.")

        write_json(fecha, rows, "ok", None)
        print(f"JSON generado correctamente. Registros: {len(rows)}")
        return 0

    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        # Importante: no tocamos data/data.json cuando la descarga falla.
        # Así se conserva el último dato bueno.
        return 1


if __name__ == "__main__":
    sys.exit(main())
