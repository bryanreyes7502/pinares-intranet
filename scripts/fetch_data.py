#!/usr/bin/env python3
"""
Descarga el RIO de Energía del Coordinador Eléctrico Nacional.

El botón "Descargar" del RIO actual dispara una petición AJAX desde el
navegador. Por eso no dependemos de que Chrome cree físicamente un archivo:
capturamos la respuesta de red de la petición que genera el botón y guardamos
su contenido como CSV.

Salida válida:
    data/data.json

Si la descarga falla, NO se modifica data/data.json.
"""

import base64
import csv
import io
import json
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

RIO_URL = (
    "https://www.coordinador.cl/operacion/documentos/"
    "registro-de-instrucciones-de-operacion-rio-sscc-energia/"
)

OUTPUT_PATH = Path("data/data.json")
DOWNLOAD_DIR = Path("tmp_downloads")
CHILE_TZ = ZoneInfo("America/Santiago")
PAGE_TIMEOUT = 45
NETWORK_TIMEOUT = 90


def create_driver(download_dir: Path) -> webdriver.Chrome:
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

    options.set_capability("goog:loggingPrefs", {"performance": "ALL"})

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

    # Permitir descargas en Chrome headless como respaldo.
    driver.execute_cdp_cmd(
        "Page.setDownloadBehavior",
        {
            "behavior": "allow",
            "downloadPath": str(download_dir.resolve()),
        },
    )

    return driver


def set_input_value(driver, element, value: str) -> None:
    driver.execute_script(
        """
        const element = arguments[0];
        const value = arguments[1];
        const descriptor = Object.getOwnPropertyDescriptor(
            window.HTMLInputElement.prototype, 'value'
        );
        if (descriptor && descriptor.set) {
            descriptor.set.call(element, value);
        } else {
            element.value = value;
        }
        element.dispatchEvent(new Event('input', {bubbles: true}));
        element.dispatchEvent(new Event('change', {bubbles: true}));
        element.dispatchEvent(new Event('blur', {bubbles: true}));
        """,
        element,
        value,
    )


def get_date_inputs(driver):
    inputs = driver.find_elements(By.CSS_SELECTOR, "input[type='date']")
    if len(inputs) >= 2:
        return inputs

    candidates = driver.find_elements(By.CSS_SELECTOR, "input")
    result = []
    for element in candidates:
        attrs = " ".join(
            [
                element.get_attribute("name") or "",
                element.get_attribute("id") or "",
                element.get_attribute("placeholder") or "",
                element.get_attribute("class") or "",
            ]
        ).lower()
        if "fecha" in attrs or "date" in attrs:
            result.append(element)
    return result


def set_energy_dates(driver, fecha: str) -> None:
    wait = WebDriverWait(driver, PAGE_TIMEOUT)
    wait.until(lambda d: len(get_date_inputs(d)) >= 2)

    date_inputs = get_date_inputs(driver)
    if len(date_inputs) < 2:
        raise RuntimeError("No se encontraron los dos campos de fecha de Energía.")

    set_input_value(driver, date_inputs[0], fecha)
    set_input_value(driver, date_inputs[1], fecha)

    # La página actual usa dos select para las horas de Energía.
    selects = driver.find_elements(By.CSS_SELECTOR, "select")
    if len(selects) >= 2:
        driver.execute_script(
            """
            function pick(select, wanted, fallback) {
                const options = Array.from(select.options);
                let idx = options.findIndex(o => {
                    const text = (o.textContent || '').trim();
                    const value = (o.value || '').trim();
                    return wanted.some(x => text.includes(x) || value.includes(x));
                });
                if (idx < 0) idx = fallback;
                if (idx >= 0 && idx < options.length) {
                    select.selectedIndex = idx;
                    select.dispatchEvent(new Event('change', {bubbles:true}));
                }
            }
            pick(arguments[0], ['00:00:00', '00:00'], 0);
            pick(arguments[1], ['23:59:59', '23:59'], arguments[1].options.length - 1);
            """,
            selects[0],
            selects[1],
        )

    time.sleep(1.5)


def find_energy_download_button(driver):
    # El primer enlace/botón visible "Descargar" corresponde a Energía.
    candidates = driver.find_elements(By.XPATH, "//*[self::a or self::button]")
    for element in candidates:
        text = (element.text or "").strip().lower()
        if text == "descargar" and element.is_displayed() and element.is_enabled():
            return element

    # Respaldo para cambios menores del DOM.
    candidates = driver.find_elements(
        By.CSS_SELECTOR,
        "a.download-energia, a[href*='export'], a[class*='download'], button[class*='download']",
    )
    for element in candidates:
        if element.is_displayed() and element.is_enabled():
            return element

    raise RuntimeError("No se encontró el botón 'Descargar' de Energía.")


def drain_performance_logs(driver):
    try:
        return driver.get_log("performance")
    except Exception:
        return []


def capture_ajax_csv(driver, started_at: float, download_dir: Path) -> Path | None:
    """
    Busca en el tráfico de Chrome la respuesta generada por el botón.
    Devuelve el CSV aunque la respuesta venga como Blob/fetch y Chrome no
    genere un archivo físico.
    """
    requests = {}
    responses = {}
    completed = set()
    seen_urls = []
    deadline = time.time() + NETWORK_TIMEOUT

    while time.time() < deadline:
        for entry in drain_performance_logs(driver):
            try:
                message = json.loads(entry["message"])["message"]
            except Exception:
                continue

            method = message.get("method")
            params = message.get("params", {})

            if method == "Network.requestWillBeSent":
                request = params.get("request", {})
                request_id = params.get("requestId")
                url = request.get("url", "")
                if request_id:
                    requests[request_id] = {
                        "url": url,
                        "method": request.get("method", ""),
                        "postData": request.get("postData"),
                        "time": time.time(),
                    }
                    low = url.lower()
                    if "admin-ajax.php" in low or "export_energia_csv" in low:
                        if url not in seen_urls:
                            seen_urls.append(url)
                            print(f"Petición AJAX detectada: {url}")

            elif method == "Network.responseReceived":
                request_id = params.get("requestId")
                response = params.get("response", {})
                if request_id:
                    responses[request_id] = response
                    url = response.get("url", "").lower()
                    if "admin-ajax.php" in url or "export_energia_csv" in url:
                        status = response.get("status")
                        content_type = response.get("mimeType", "")
                        print(
                            f"Respuesta AJAX: HTTP {status} | "
                            f"Content-Type: {content_type} | URL {response.get('url', '')}"
                        )

            elif method == "Network.loadingFinished":
                request_id = params.get("requestId")
                if request_id:
                    completed.add(request_id)

        # Procesar respuestas candidatas completadas.
        for request_id, response in list(responses.items()):
            req = requests.get(request_id, {})
            url = (response.get("url") or req.get("url") or "").lower()
            status = response.get("status")
            mime = (response.get("mimeType") or "").lower()

            candidate = (
                "admin-ajax.php" in url
                or "export_energia_csv" in url
                or "text/csv" in mime
                or "application/csv" in mime
                or "csv" in mime
            )

            if not candidate or request_id not in completed:
                continue

            try:
                body = driver.execute_cdp_cmd(
                    "Network.getResponseBody",
                    {"requestId": request_id},
                )
            except Exception:
                continue

            body_text = body.get("body", "")
            if body.get("base64Encoded"):
                try:
                    content = base64.b64decode(body_text)
                except Exception:
                    continue
            else:
                content = body_text.encode("utf-8", errors="replace")

            # Si es HTTP 403/4xx, guardamos una copia para diagnóstico y no
            # la confundimos con un CSV.
            if status is not None and int(status) >= 400:
                debug = Path("rio_ajax_error.txt")
                debug.write_text(
                    "STATUS: " + str(status) + "\n"
                    + "URL: " + (response.get("url") or req.get("url") or "") + "\n"
                    + "METHOD: " + str(req.get("method", "")) + "\n"
                    + "POST DATA: " + str(req.get("postData")) + "\n\n"
                    + content.decode("utf-8", errors="replace")[:20000],
                    encoding="utf-8",
                )
                raise RuntimeError(
                    f"El botón fue ejecutado, pero el endpoint respondió HTTP {status}. "
                    "Se generó rio_ajax_error.txt para diagnóstico."
                )

            # Validar mínimamente que parece CSV y no HTML.
            sample = content[:1000].lstrip().lower()
            if b"<html" in sample or b"<!doctype" in sample:
                continue

            target = download_dir / f"rio_energia_{started_at:.0f}.csv"
            target.write_bytes(content)
            if target.stat().st_size > 0:
                return target

        time.sleep(0.5)

    return None


def wait_physical_csv(download_dir: Path, before: set[Path], timeout: int = 15) -> Path | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        current = set(download_dir.iterdir())
        new_files = current - before
        candidates = [
            p for p in new_files
            if p.is_file() and p.suffix.lower() == ".csv"
        ]
        if candidates:
            candidate = max(candidates, key=lambda p: p.stat().st_mtime)
            size1 = candidate.stat().st_size
            time.sleep(1)
            if candidate.exists() and candidate.stat().st_size == size1 and size1 > 0:
                return candidate
        time.sleep(0.5)
    return None


def download_rio(fecha: str) -> Path:
    if DOWNLOAD_DIR.exists():
        shutil.rmtree(DOWNLOAD_DIR)
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

    driver = create_driver(DOWNLOAD_DIR)
    started_at = time.time()

    try:
        print(f"Abriendo: {RIO_URL}")
        driver.get(RIO_URL)

        WebDriverWait(driver, PAGE_TIMEOUT).until(
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

        # Primero intentamos capturar la respuesta AJAX/Fetch.
        csv_path = capture_ajax_csv(driver, started_at, DOWNLOAD_DIR)
        if csv_path:
            print(f"CSV obtenido desde la respuesta de red: {csv_path}")
            return csv_path

        # Respaldo: algunas versiones sí crean un archivo físico.
        csv_path = wait_physical_csv(DOWNLOAD_DIR, before)
        if csv_path:
            print(f"CSV descargado físicamente: {csv_path}")
            return csv_path

        raise TimeoutError(
            f"No se obtuvo CSV después de {NETWORK_TIMEOUT} segundos."
        )

    except Exception:
        try:
            driver.save_screenshot("rio_error.png")
            print("Se generó rio_error.png para diagnóstico.", file=sys.stderr)
        except Exception:
            pass
        raise
    finally:
        driver.quit()


def csv_to_rows(csv_text: str) -> list[dict]:
    csv_text = csv_text.lstrip("\ufeff")
    sample = csv_text[:4096]
    delimiter = ";" if sample.count(";") > sample.count(",") else ","
    reader = csv.DictReader(io.StringIO(csv_text), delimiter=delimiter)
    return list(reader)


def read_csv_file(csv_path: Path) -> list[dict]:
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


def write_json(fecha: str, rows: list[dict]) -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "fecha_consultada": fecha,
        "actualizado_en": datetime.now(CHILE_TZ).isoformat(),
        "status": "ok",
        "error": None,
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
            raise RuntimeError("La respuesta CSV no contiene registros.")
        write_json(fecha, rows)
        print(f"JSON generado correctamente. Registros: {len(rows)}")
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        # No modificar data/data.json si la descarga falla.
        return 1


if __name__ == "__main__":
    sys.exit(main())
