# Intranet Pinares — Registro de Instrucciones de Operación (CEN)

Este proyecto contiene todo lo necesario para publicar un panel que se actualiza
solo cada 5 minutos con los datos del Coordinador Eléctrico Nacional.

## Cómo funciona (arquitectura)

```
GitHub Actions (cron cada 5 min)
   └─ scripts/fetch_data.py descarga el CSV del Coordinador
      └─ lo transforma en data/data.json
         └─ lo commitea al repo
            └─ GitHub Pages sirve index.html + data/data.json
               └─ Cloudflare (DNS + Access) da el dominio pinares.cl y el login
```

Puntos importantes que debes saber antes de empezar:

- **GitHub Pages no puede pedir usuario/contraseña por sí solo.** Es hosting
  estático: cualquier "login" hecho solo con HTML/JavaScript se puede saltar
  mirando el código fuente, porque no hay servidor que valide nada. Por eso
  el login real se hace con **Cloudflare Access** (gratis hasta 50 usuarios),
  que se pone delante de tu dominio y exige iniciar sesión (con
  usuario/contraseña, Google, Microsoft, o un código por correo) antes de
  dejar pasar a nadie al sitio. El código igual queda 100% alojado en GitHub.
- **El endpoint de coordinador.cl no es una API oficial**, es el exportador
  interno de su WordPress. Puede cambiar de formato o bloquear solicitudes
  automatizadas sin aviso. Revisa los términos de uso del sitio y considera
  bajar la frecuencia si notas bloqueos.
- **"Cada 5 minutos" es el mínimo técnico de GitHub Actions, no una garantía.**
  GitHub puede demorar la ejecución varios minutos en repos con poca
  actividad. Es lo más cercano a tiempo real que se puede lograr gratis con
  este enfoque.
- El repositorio debe ser **público** para que GitHub Pages y las 288
  ejecuciones diarias de Actions sean gratis. Como el dato en sí es público
  (información del Coordinador), esto no debería ser un problema — el acceso
  al sitio web sigue quedando protegido por Cloudflare Access.

## Paso 1 — Crear el repositorio en GitHub

1. Entra a github.com, crea una cuenta si no tienes.
2. Crea un repositorio nuevo, público, llamado por ejemplo `pinares-intranet`.
3. Sube todos los archivos de esta carpeta (`index.html`, `data/`, `scripts/`,
   `.github/`, este `README.md`) al repositorio. Puedes hacerlo arrastrando
   los archivos en la interfaz web de GitHub ("Add file → Upload files") si
   no usas git desde la terminal.

## Paso 2 — Activar GitHub Pages

1. En el repositorio, ve a **Settings → Pages**.
2. En "Build and deployment", elige **Deploy from a branch**.
3. Selecciona la rama `main` y la carpeta `/ (root)`.
4. Guarda. En un par de minutos tu sitio quedará disponible en
   `https://<tu-usuario>.github.io/pinares-intranet/`.

## Paso 3 — Activar la actualización automática

1. Ve a la pestaña **Actions** del repositorio. Deberías ver el workflow
   "Actualizar Registro CEN".
2. Ejecútalo una vez manualmente (botón "Run workflow") para comprobar que
   funciona y que `data/data.json` se actualiza con datos reales.
3. Desde ahí en adelante se ejecutará solo cada 5 minutos.

Si la ejecución falla, revisa el log: lo más probable es que el formato del
CSV o la URL hayan cambiado, y haya que ajustar `scripts/fetch_data.py`.

## Paso 4 — Conectar el dominio www.pinares.cl

1. En **Settings → Pages → Custom domain**, escribe `www.pinares.cl` y
   guarda. Esto crea un archivo `CNAME` en tu repo automáticamente.
2. En el proveedor donde administras el dominio `pinares.cl`, crea un
   registro **CNAME** para `www` que apunte a `<tu-usuario>.github.io`.

Hasta aquí ya tendrías el sitio funcionando en tu dominio, pero **todavía sin
login**. Para el login real, sigue el paso 5.

## Paso 5 — Agregar el login con Cloudflare Access (gratis)

1. Crea una cuenta gratis en cloudflare.com y agrega el dominio `pinares.cl`
   (Cloudflare te pedirá cambiar los servidores DNS (nameservers) del
   dominio a los de Cloudflare — esto se hace en el mismo lugar donde
   compraste el dominio).
2. Dentro de Cloudflare, recrea el registro CNAME de `www` apuntando a
   `<tu-usuario>.github.io`, con el ícono de nube **activado (naranja)** —
   eso es lo que permite que Cloudflare intercepte el tráfico.
3. Ve a **Zero Trust → Access → Applications → Add an application → Self-hosted**.
4. Configura el dominio protegido como `www.pinares.cl`.
5. En "Policies", crea una política con los correos de las personas de tu
   empresa que deben tener acceso (o un dominio de correo completo, ej.
   `@pinares.cl`, si todos deben entrar).
6. Guarda. Desde ahora, al entrar a `www.pinares.cl` primero se pedirá
   iniciar sesión (código por correo o el método que elijas) antes de
   mostrar el panel.

## Archivos incluidos

| Archivo | Qué hace |
|---|---|
| `index.html` | El panel visual. Lee `data/data.json` y se refresca solo cada 5 min. |
| `scripts/fetch_data.py` | Descarga el CSV del día desde coordinador.cl y lo convierte a JSON. |
| `.github/workflows/update-data.yml` | El "reloj" que ejecuta el script cada 5 minutos y sube el resultado. |
| `data/data.json` | El dato más reciente. Se sobrescribe automáticamente. |

## Personalización pendiente

- No conozco la estructura exacta de columnas del CSV del Coordinador —
  `fetch_data.py` las detecta automáticamente y `index.html` las muestra
  todas tal cual vienen. Si quieres que se resalten columnas específicas
  (ej. hora, tipo de instrucción, instalación afectada), dime los nombres
  de esas columnas una vez que veas el primer `data.json` real y ajusto el
  diseño.
