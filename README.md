# Red.ly Desktop

Aplicación de escritorio (Python + Tkinter) de **Red.ly**: recibe una URL
pública de TikTok, la envía al flujo HTTP de **tikvid.io**, interpreta el HTML
devuelto, muestra las opciones de descarga y descarga el archivo en streaming.
Además acepta **la URL de un perfil**: lista sus vídeos (paginación mediante
**savetikpro.com**) y los descarga en lote, uno a uno, con manifiesto de
reanudación.

Compañera de la aplicación web: <https://red-ly.vercel.app/>

> **No es una API oficial de TikVid ni de SavTikPro.** Los endpoints y el HTML
> no están documentados y pueden cambiar en cualquier momento. El proyecto
> trabaja únicamente con URLs públicas y peticiones HTTP normales, sin
> autenticación, sin cookies personales y sin medidas de evasión.

---

## Características

* **Sidebar de redes sociales**: TikTok, YouTube, Facebook, Instagram, X y
  Threads con iconos propios (`assets/icons/*.png`). Sólo TikTok está
  implementado; al entrar en el resto, la entrada y el botón *Analizar* quedan
  **desactivados** y se explica que están «Próximamente».
* **Tema claro/oscuro** persistente (botón `◐`, guardado en
  `~/.redly/settings.json`). Los iconos se tintan según el tema.
* Análisis de vídeo con **miniatura**, opciones de descarga (MP4 / MP4 HD /
  MP3) y barra de progreso con bytes y porcentaje.
* Perfil (`@usuario`): lista los vídeos, permite **seleccionar con casillas**
  cuáles descargar (por defecto busca 100 vídeos nuevos, ajustable) y los
  descarga en lote dentro de una subcarpeta con el nombre del perfil.
* **Historial** de descargas con reanudación (`downloads/manifest.json`):
  nunca guarda URLs ni tokens, sólo id, estado, archivo y bytes.
* Hilos secundarios: la ventana nunca se bloquea.
* CLI completa en `main.py` (Rich), con modo vídeo, modo perfil y modo lote.

## Requisitos

* Python 3.11+ (probado en Python 3.14)
* Conexión a Internet sólo para el uso real (los tests no la necesitan)

## Instalación

```bash
python -m venv .venv
```

Windows:

```bash
.venv\Scripts\activate
```

Linux / macOS:

```bash
source .venv/bin/activate
```

Instalar dependencias:

```bash
pip install -r requirements.txt
```

`Pillow` es opcional: sin él se omiten la miniatura y los iconos PNG del
sidebar (las redes caen a un respaldo dibujado con Tkinter; el resto funciona
igual).

---

## Uso

### Interfaz gráfica (Tkinter)

```bash
python gui.py
python gui.py --debug
```

Diseño minimalista: sidebar izquierdo con las redes, cabecera con título y
tema, tarjetas con esquinas redondeadas, tipografía del sistema y acento
`#FE2C55`.

```text
┌──┬───────────────────────────────────────────────┐
│🔥│ Red.ly  Descargador de TikTok · Escritorio   │
│  ├───────────────────────────────────────────────┤
│TT│ URL DE TIKTOK                                │
│YT│ [ https://www.tiktok.com/@usuario/video/… ] [Analizar] │
│FB│ Listo · 4 opciones encontradas               │
│IG│ ┌──────┐ Título del vídeo                    │
│X │ │ thumb│ ID 7616480500147965206              │
│TH│ └──────┘                                     │
│  │ OPCIONES DE DESCARGA                         │
│  │ │1│ Download MP4 HD               mp4 · hd  │
│  │ │2│ Download MP4                mp4 · std   │
│  │ │ [Descargar]  [Abrir carpeta]               │
│  │ ████████████████████████  2.8 MB · 100%      │
│◐ⓘ│                                               │
└──┴───────────────────────────────────────────────┘
```

* Enter en el campo de URL también lanza el análisis.
* Miniatura del vídeo con Pillow.
* Si la URL es de un **perfil** (`@usuario` o `/@usuario`), la GUI lista los
  vídeos con casillas de selección y el botón pasa a `Descargar N vídeos`:
  lote secuencial con progreso `Vídeo n/total` y reanudación en la siguiente
  ejecución. Cada perfil se guarda en su propia subcarpeta.
* La barra de historial ofrece **Volver a analizar** y **Borrar historial**.
* El botón `ⓘ` de la sidebar abre *Acerca de* con el enlace a la web:
  <https://red-ly.vercel.app/>
* `python gui.py --debug` activa el modo debug.

### Línea de comandos

```bash
python main.py
```

Se muestra el banner y se pide la URL:

```text
Introduce una URL de TikTok:
> https://www.tiktok.com/@usuario/video/123456789
```

El programa valida la URL, consulta TikVid, muestra título, ID, thumbnail y
las opciones de descarga, y pide qué opción descargar:

```text
Analizando vídeo...
✓ URL válida
✓ TikVid respondió
✓ Información obtenida

Título:
...

ID:
7616480500147965206

Opciones:

[1] Download MP4
[2] Download MP4 HD
[3] Download MP3

Selecciona una opción (1-3):
```

Después resuelve la URL temporal, sigue el redirect y descarga con barra de
progreso:

```text
Resolviendo URL...
✓ URL temporal encontrada
✓ Redirect detectado
✓ CDN encontrado: v16.tokcdn.com

Descargando...
██████████████████████ 100%

✓ Descarga completada

downloads/Redly_7616480500147965206_hd.mp4
```

### Opciones de línea de comandos

```bash
python main.py --url "https://www.tiktok.com/@usuario/video/123" --option 2
python main.py --debug
python main.py --no-download
python main.py --output-dir otra_carpeta
python main.py --profile "@usuario" --max 10
python main.py --batch lista.txt --delay 2 --yes
python main.py --version
```

| Opción | Descripción |
| --- | --- |
| `--url` | URL de TikTok: vídeo (interactivo) o perfil (lote). Si no se indica, se pide por teclado |
| `--profile` | URL de perfil o `@usuario`: lista y descarga en lote |
| `--batch` | Fichero con una URL de TikTok (vídeo o perfil) por línea (`#` comentarios) |
| `--max` | Máximo de vídeos del lote (0 = sin límite) |
| `--delay` | Pausa en segundos entre vídeos (por defecto `1.0`) |
| `--list-only` | Sólo lista los vídeos, no descarga nada |
| `-y` / `--yes` | No pide confirmación antes del lote |
| `--no-resume` | Vuelve a descargar lo ya marcado como completado |
| `--option` | Índice de la opción de descarga (1-based), en modo vídeo |
| `--output-dir` | Carpeta de destino (por defecto `downloads/`) |
| `--no-download` | Resuelve la URL temporal pero no descarga, en modo vídeo |
| `--debug` | Muestra endpoint, statuses, nº de opciones y dominio final |

En `--debug` **nunca** se muestran tokens, cookies ni URLs firmadas completas:
los parámetros sensibles se sustituyen por `<oculto>`.

### Descarga en masa (perfil)

```bash
python main.py --profile "https://www.tiktok.com/@usuario" --max 20
python main.py --profile "@usuario" --list-only
```

```text
Listando perfil... https://www.tiktok.com/@usuario
  · página 1: 11 vídeos
  · página 2: 22 vídeos

Vídeos del perfil (22)

 #  ID                  Título             Vistas
 1  7694475743509646622 Why did Stella...  1.8K
...

Se descargarán 20 vídeo(s) en downloads (pausa 1s entre vídeos).
¿Continuar? [s/N]: s

1/20 Why did Stella do THAT? #pool #fail
      ✓ Redly_7694475743509646622.mp4 (2,953,029 bytes)
...

✓ Lote terminado · 20 descargado(s) · 0 omitido(s) · 0 fallo(s)
```

Cómo funciona:

* **Fuente del listado**: `POST https://savetikpro.com/api/ajaxSearch` con la
  URL del perfil. Devuelve HTML con tarjetas (título, canal, vistas,
  miniatura y un enlace de descarga directo) y un `next_cursor` para pedir la
  página siguiente. Es la misma familia de producto que TikVid, con el flujo
  de perfil ya construido en su interfaz web. Sin API documentada.
* **Orden estrictamente secuencial**: un vídeo a la vez, con pausa
  (`--delay`) entre peticiones. Nunca descarga en paralelo.
* **Reanudación**: `downloads/manifest.json` guarda, por ID de vídeo, el
  estado (`ok`/`error`), el nombre de archivo y los bytes — nunca URLs ni
  tokens, que caducan en ~1 hora. Si vuelves a ejecutar el mismo comando, los
  ya descargados se omiten y los fallidos se reintentan con tokens frescos.
* **Ficheros de lote**: cada línea puede ser un perfil (se expande) o un
  vídeo suelto (se analiza y se elige el mejor MP4 automáticamente).
* **Subcarpetas**: en los perfiles cada vídeo se guarda en
  `downloads/<perfil>/` (saneado, máx. 64 caracteres).
* **Límites**: en la GUI, «vídeos nuevos a buscar» por defecto 100
  (1–1000); en CLI el límite lo marca `--max` (0 = todos, con
  `BATCH_MAX_PAGES` como red de seguridad).

En el modo vídeo suelto, la búsqueda intenta primero SavTikPro y, si no
devuelve opciones, cae automáticamente a TikVid (`search_any`).

---

## Arquitectura

```text
Redly-Desktop/
│
├── app/
│   ├── __init__.py
│   ├── client.py        # HTTP: POST /api/ajaxSearch + reintentos + URL helpers
│   ├── parser.py        # HTML -> TikTokResult (selectores con fallback)
│   ├── models.py        # Modelos Pydantic (TikTokResult, ProfileVideo, ...)
│   ├── savetik.py       # Fuente SavTikPro: perfiles paginados + 1 vídeo
│   ├── batch.py         # Lote secuencial + manifest.json de reanudación
│   ├── downloader.py    # Resolución de redirects + descarga streaming
│   ├── platforms.py     # Registro de redes (slug, icono, acento, hosts, ready)
│   ├── gui.py           # Interfaz Tkinter (sidebar, temas, tarjetas, lote)
│   ├── exceptions.py    # Jerarquía TikVidError
│   └── config.py        # Endpoints, headers, timeouts, selectores, .env
│
├── assets/
│   └── icons/           # PNG del sidebar (redes, marca 🔥, info, modos)
│
├── tests/
│   ├── test_parser.py
│   ├── test_client.py       # con mocking, sin red
│   ├── test_downloader.py   # con mocks, sin red
│   ├── test_savetik.py      # paginación, tarjetas, fallback (mocks)
│   ├── test_batch.py        # lote, manifiesto, reanudación (mocks)
│   ├── test_gui.py          # Tkinter sin red (se omite sin display)
│   └── fixtures/
│       ├── ajax_search_response.html   # HTML anonimizado
│       ├── savetik_profile_page.html   # tarjetas de perfil anonimizadas
│       └── savetik_detail.html         # detalle de vídeo anonimizado
│
├── main.py              # CLI (Rich) con modo vídeo y modo lote
├── gui.py               # Lanzador de la interfaz gráfica
├── downloads/           # Salida (gitignored): vídeos + manifest.json
├── requirements.txt
├── .env.example
├── .gitignore
└── README.md
```

Responsabilidades separadas: `client.py` no parsea HTML, `parser.py` no hace
peticiones HTTP, `savetik.py` sólo lista y resuelve, `batch.py` sólo orquesta
el orden y el manifiesto, `downloader.py` no conoce el flujo de búsqueda,
`platforms.py` sólo describe las redes y `gui.py` sólo orquesta la interfaz
(reutiliza la fuente, el lote y `DownloadManager`).

---

## Flujo HTTP

1. Validación de la URL (debe ser `tiktok.com` o un subdominio).
2. Petición:

```http
POST https://tikvid.io/api/ajaxSearch
Content-Type: application/x-www-form-urlencoded; charset=UTF-8
X-Requested-With: XMLHttpRequest
Accept: application/json, text/javascript, */*; q=0.01
```

3. Payload:

```text
q=<URL_TIKTOK>
lang=en
```

4. Respuesta:

```json
{
    "status": "ok",
    "data": "<HTML>"
}
```

5. El HTML de `data` se pasa a `TikVidParser`.

Los headers del navegador están centralizados en `app/config.py`
(`HEADERS`). Sin ellos, Cloudflare responde `403` con una página de
"Just a moment...". No se copian cookies ni tokens del navegador.

### Reintentos y timeouts

* `REQUEST_TIMEOUT = 30` y `DOWNLOAD_TIMEOUT = 120` (configurables por `.env`).
* Reintentos limitados (`RETRY_ATTEMPTS = 3`) con backoff exponencial breve,
  sólo para errores transitorios: timeouts, errores de conexión y
  `429/500/502/503/504`.

---

## Parsing

El parser no depende de una estructura exacta. Para cada dato se prueba una
lista de selectores priorizados y, si ninguno funciona, se devuelve `None`
(sin romper la ejecución):

* **Título**: `div.thumbnail h3` → `div.content h3` → `h3` → `h1` → `[class*=title]`
* **Thumbnail**: `div.thumbnail img` → `div.image-tik img` → `video[poster]` → primer `img`
* **Video ID**: `input#TikTokId` → enlaces `tiktok.com/@user/video/<id>` → atributos `data-*`
* **Opciones**: `a.tik-button-dl` → `.dl-action a[href]` → enlaces con hosts de CDN →
  cualquier enlace cuyo texto parezca un botón de descarga

Se excluyen enlaces de navegación (`more-video`, popup) y se deduplican las
URLs repetidas.

### Formato y calidad

A partir del texto del botón:

| Botón | `format` | `quality` |
| --- | --- | --- |
| `Download MP4` | `mp4` | `standard` |
| `Download MP4 HD` | `mp4` | `hd` |
| `Download MP3` | `mp3` | `audio` |

Si el texto no aporta información, se infiere de la URL
(`mime_type=video_mp4`, `.mp3`, etc.).

---

## SnapCDN y URLs temporales

Algunas opciones apuntan a:

```text
https://dl.snapcdn.app/get?token=...
```

y otras son URLs directas del CDN (`v16m.tiktokcdn-us.com`,
`v16.tokcdn.com`, ...). `DownloadManager.resolve_url()` sigue las
redirecciones manualmente (hasta `MAX_REDIRECTS`) y devuelve:

```python
RedirectInfo(
    original_url="...",
    final_url="...",
    redirected=True,
    status_code=200,
    hops=1,
)
```

Si no hay redirect: `redirected=False`.

Estas URLs están **firmadas y son temporales**. El proyecto:

* no las guarda en base de datos,
* no las escribe en archivos permanentes,
* no las registra completas en logs (se enmascaran con `config.mask_url`),
* no las versiona en Git.

Sólo se usan durante la ejecución.

---

## Descarga

La descarga usa `stream=True` y escribe por chunks de 1 MiB, con barra de
progreso de Rich en la CLI y con el callback `on_progress(bytes, total)` en
la GUI. Si la respuesta falla o queda un archivo parcial, éste se elimina.

Los nombres de archivo se generan y sanean localmente
(`Redly_<id>_<calidad>.<ext>`, prefijo configurable con `FILENAME_PREFIX`);
nunca se usa el nombre que envía el servidor sin sanitizar: se eliminan
caracteres inválidos, `..`, `/`, `\` y se respetan los nombres reservados de
Windows.

---

## Tests

```bash
python -m pytest tests -q
```

**78 tests**, todos sin Internet:

* `test_parser.py`: título, thumbnail, video ID, MP4/MP4 HD/MP3, elementos
  ausentes, HTML incompleto, enlaces de navegación excluidos, deduplicación.
* `test_client.py`: mocking completo de la sesión HTTP; validación de URL,
  respuestas no JSON, `status != ok`, HTML sin opciones y reintentos.
* `test_downloader.py`: saneado de nombres (`Redly_`), resolución de
  redirects, descarga por chunks, URL expirada y limpieza de archivos
  parciales.
* `test_savetik.py`: clasificación de URLs, tarjetas de perfil, paginación
  por `cursor`/`page`, deduplicación, límite `max_videos`, reintentos y
  fallback a TikVid.
* `test_batch.py`: descarga secuencial, manifiesto (sin URLs/tokens),
  reanudación, reintentos de fallos, límite, pausa, callbacks y `summarize`.
* `test_gui.py`: crea la ventana (oculta), placeholder, validación, carga de
  resultados, selección de tarjetas, cambio de tema, título *Red.ly* y
  contratos de la interfaz; se omite si no hay display gráfico.

Los fixtures `tests/fixtures/*.html` son copias anonimizadas del HTML real
(tokens sustituidos por valores ficticios).

---

## Configuración

Todo lo configurable vive en `app/config.py`:

```text
TIKVID_BASE_URL
TIKVID_AJAX_SEARCH_URL
SAVETIK_BASE_URL
SAVETIK_AJAX_SEARCH_URL
REQUEST_TIMEOUT
DOWNLOAD_TIMEOUT
DOWNLOAD_DIRECTORY
MANIFEST_PATH
SETTINGS_PATH           # tema elegido (~/.redly/settings.json)
FILENAME_PREFIX         # "Redly" -> Redly_<id>.mp4
MAX_FILENAME_LENGTH
MAX_FOLDER_LENGTH
ALLOWED_DOWNLOAD_HOSTS  # allowlist de descarga
MAX_REDIRECTS
RETRY_ATTEMPTS
BATCH_DELAY_SECONDS
BATCH_PAGE_DELAY_SECONDS
BATCH_MAX_PAGES
BATCH_DEFAULT_MAX_VIDEOS
HEADERS
SELECTORS
```

Copia `.env.example` a `.env` para sobreescribir valores (se lee sin
dependencias adicionales y sin sobreescribir variables ya definidas).

---

## Descubrimientos documentados (no implementados)

El HTML analizado contiene un bloque JavaScript con estas variables:

```javascript
k_exp = "...";
k_token = "...";
k_url_convert = "https://s2.tik-cdn.com/api/json/convert";
```

**No se usa ese endpoint.** Antes de convertirlo en un módulo hay que
verificar en DevTools si realmente se llama, con qué método, payload y
respuesta.

---

## Limitaciones

* TikVid y SavTikPro no ofrecen APIs públicas documentadas: los endpoints, el
  HTML, las clases CSS y SnapCDN pueden cambiar sin aviso.
* El acceso puede estar protegido por Cloudflare; una respuesta HTML en
  lugar de JSON se reporta como error claro.
* Las URLs temporales expiran (error `403/404/410` → *"La URL temporal ha
  expirado."*). En el lote, un token caducado marca el vídeo como fallo y la
  siguiente ejecución lo reintenta con un token fresco.
* El listado de perfil depende de savetikpro: si el servicio cambia el
  `next_cursor` o las clases de las tarjetas, habrá que ajustar
  `app/savetik.py`.
* El scraping directo de `tiktok.com/@usuario` **no está implementado**: la
  web devuelve una pasarela WAF a las peticiones de `requests`, y evadirla
  queda fuera del alcance de este proyecto.
* Sólo TikTok está implementado. Las demás redes aparecen en la sidebar pero
  están deshabilitadas («Próximamente»).
* No hay base de datos, usuarios, API REST ni Docker.
* La GUI (Tkinter) no arrastra y suelta archivos ni integra bandeja del
  sistema: es una capa de presentación sobre el mismo flujo HTTP.

---

## Roadmap

```text
1. HTTP + parser + descarga (CLI)     ✓
2. Interfaz gráfica (Tkinter)         ✓
3. Lote por perfil + reanudación      ✓
4. Multi-plataforma (sidebar + UI)    ✓ (sólo TikTok operativo)
5. YouTube / Instagram / Facebook…    ··
6. Proveedor reutilizable en Laravel  ·· ·
```

La interfaz se hizo con **Tkinter** (incluido con Python) en lugar de Flet
para no añadir dependencias ni peso; `main.py` sigue siendo la CLI completa.
El registro de redes vive en `app/platforms.py`, de modo que habilitar una
plataforma nueva sólo requiere añadir su entrada y su implementación.

---

## Créditos

El cliente utiliza código y dependencias derivados de proyectos de terceros:

* **TikVid** — <https://tikvid.io>: cliente HTTP del flujo de descarga,
  parser de metadatos y jerarquía de excepciones.
* **SavTikPro** — <https://savetikpro.com>: endpoint de búsqueda y listado de
  perfiles (lote).

Ambos pertenecen a terceros; Red.ly no está afiliado a ellos ni los opera.
Más detalles en la web: <https://red-ly.vercel.app/>

---

## Licencia / uso responsable

Proyecto de estudio técnico. Trabaja sólo con URLs públicas, respeta los
términos de las plataformas involucradas y no intenta evadir CAPTCHAs ni
controles de acceso.
