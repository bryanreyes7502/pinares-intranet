#!/usr/bin/env python3
"""
Descarga el Registro de Instrucciones de Operación del Coordinador Eléctrico
Nacional para el día de hoy (hora de Chile) y lo convierte a JSON para que
el dashboard estático lo pueda leer.

Se ejecuta periódicamente desde un GitHub Action (ver
.github/workflows/update-data.yml).
"""
import csv
import io
import json
import sys
from datetime import datetime
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import requests

BASE_URL = "https://www.coordinador.cl/wp-admin/admin-ajax.php"
TIMEOUT_SECONDS = 30
OUTPUT_PATH = "data/data.json"
CHILE_TZ = ZoneInfo("America/Santiago")


def build_url(fecha: str) -> str:
    params = {
        "action": "export_energia_csv",
        "fecha_inicio": fecha,
        "fecha_termino": fecha,
        "hora_inicio": "00:00:00",
        "hora_termino": "23:59:59",
    }
    return f"{BASE_URL}?{urlencode(params)}"


def fetch_csv(fecha: str) -> str:
    url = build_url(fecha)
    headers = {
        # Un User-Agent identificable es buena práctica al automatizar
        # solicitudes contra un sitio de terceros.
        "User-Agent": "PinaresIntranetBot/1.0 (uso interno; contacto: <completar>)"
    }
    response = requests.get(url, headers=headers, timeout=TIMEOUT_SECONDS)
    response.raise_for_status()
    # El sitio entrega el CSV en latin-1 en varios exportadores de WordPress;
    # si detectas caracteres corruptos en las columnas, ajusta el encoding.
    response.encoding = response.apparent_encoding or "utf-8"
    return response.text


def csv_to_rows(csv_text: str) -> list[dict]:
    # Los exportadores de WordPress suelen usar ';' como separador.
    sample = csv_text[:2048]
    delimiter = ";" if sample.count(";") > sample.count(",") else ","
    reader = csv.DictReader(io.StringIO(csv_text), delimiter=delimiter)
    rows = [row for row in reader]
    return rows


def main() -> int:
    fecha = datetime.now(CHILE_TZ).strftime("%Y-%m-%d")

    try:
        csv_text = fetch_csv(fecha)
        rows = csv_to_rows(csv_text)
        status = "ok"
        error_message = None
    except Exception as exc:  # noqa: BLE001 - queremos capturar cualquier falla y seguir
        rows = []
        status = "error"
        error_message = str(exc)
        print(f"Error al descargar/parsear el CSV: {exc}", file=sys.stderr)

    payload = {
        "fecha_consultada": fecha,
        "actualizado_en": datetime.now(CHILE_TZ).isoformat(),
        "status": status,
        "error": error_message,
        "total_registros": len(rows),
        "registros": rows,
    }

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    # Si falló la descarga, el workflow puede decidir no hacer commit
    # (ver update-data.yml) para no pisar el último dato bueno.
    return 0 if status == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
