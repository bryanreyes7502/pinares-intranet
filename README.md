# Monitor RIO - Coordinador Eléctrico Nacional

Este paquete reemplaza la descarga directa mediante `requests` del endpoint `admin-ajax.php` por una descarga mediante Chrome/Selenium, reproduciendo la acción del botón "Descargar" de la página oficial del RIO.

## Archivos

- `scripts/fetch_data.py`: abre el RIO, selecciona la fecha actual de Chile, pulsa Descargar y genera `data/data.json`.
- `requirements.txt`: dependencia Selenium.
- `.github/workflows/update-data.yml`: ejecución diaria y actualización del JSON.
- `.gitignore`: evita subir temporales de Selenium.

## Instalación

Reemplazar en el repositorio los archivos correspondientes por los de este paquete y hacer commit/push.

El workflow también se puede ejecutar manualmente desde GitHub Actions mediante `Run workflow`.

## Comportamiento ante errores

Si la descarga falla, el script termina con error y NO reemplaza `data/data.json`. De esta forma queda preservado el último dato válido.

Si Selenium detecta un cambio en la página y falla, el workflow intenta guardar `rio_error.png` como artifact de diagnóstico.
