# ytd — Descargador de música desde YouTube

CLI para descargar música de YouTube (incluido YouTube Music) en **MP3 320 kbps**,
identificar cada canción por **huella acústica** (AcoustID + MusicBrainz),
corregir su nombre, incrustar carátula y ordenarla en tu biblioteca:

```
~/Música/YTD/
└── Death/
    └── Leprosy/
        ├── 01 - Leprosy.mp3
        ├── 02 - Born Dead.mp3
        ├── 03 - Forgotten Past.mp3
        ...
        └── 05 - Pull the Plug.mp3   ← (vino de YouTube como «04 - Pull the Plug [reissue]»)
```

Bajas el enlace (o la playlist entera) y el sistema hace el resto. Todo lo
descargado queda registrado; los reintentos y las colas son persistentes
entre sesiones, y cada fichero solo se descarga una vez.

---

## 1. Instalación (solo la primera vez)

```bash
# 1) Dependencias del sistema (sudo pedirá tu contraseña)
sudo dnf install yt-dlp chromaprint-tools ffmpeg
# opcionales/recomendados:
sudo dnf install deno          # runtime JS para yt-dlp: silencia warnings y mejora extracción
pip3 install --user mutagen    # etiquetado ID3 de los MP3

# 2) Clave API de AcoustID (gratis, ~5 min)
#    NO uses la «clave personal» de tu cuenta (es para programas como Picard).
#    Registra una APLICACIÓN en https://acoustid.org/new-application y copia
#    la clave DE ESA APLICACIÓN:
nano ~/.config/ytd/config.toml     # → acoustid_api_key = "TU_CLAVE_DE_APLICACIÓN"

# 3) Verificación final
ytd doctor
```

Si todo va bien, `ytd doctor` muestra todos los checks en `[ok]`. Con la clave
mal configurada verás `[!!] AcoustID — clave inválida`; en ese caso re-copia
la clave desde https://acoustid.org (My Applications).

## 2. Uso básico

La via rápida: **pega el enlace** (vídeo o playlist) y el programa encola +
descarga + identifica + ordena todo automáticamente:

```bash
# Una canción
ytd https://youtu.be/AbCdEf12345

# Una playlist/álbum entero (YouTube o YouTube Music)
ytd https://music.youtube.com/playlist?list=PLxcJW-gP3Ijleb9AxFFLTGW0vkli3r0iO

# Busca en YouTube, elige un resultado y el programa se encarga del resto
ytd search "Daft Punk - Discovery"

# Solo encolar sin descargar ahora (para procesos por lotes luego)
ytd add https://youtu.be/xyz | ytd https://youtube.com/playlist?list=...

# Procesar la cola (pendientes que quedaron tras Ctrl+C o de `ytd add`)
ytd dl
```

Durante las descargas verás por cada pista:

```
[3/128] Infernal Death
ℹ Consultando MusicBrainz (2 candidata(s))...
✔ [3/128] → /home/tú/Música/YTD/Death/Scream Bloody Gore/01 - Infernal Death.mp3
```

- Puedes interrumpir con `Ctrl+C` **en cualquier momento**: la cola queda
  guardada al instante y `ytd dl` reanuda sin re-descargar lo ya hecho.
- Los vínculos repetidos no se descargan dos veces (deduplicación por
  id de vídeo, guardada en `~/.local/state/ytd/library.json`).

## 3. Gestión de la cola

```bash
ytd queue         # ver la cola con números (◇ pendiente / ◆ hecha / ✖ fallida)
ytd retry 7       # reintentar el elemento 7
ytd retry failed  # reintentar todas las fallidas
ytd clean         # limpiar de la cola las ya hechas
ytd dl            # procesar la cola pendiente
```

Reset completo (empieza de cero, borra todo el registro):

```bash
rm ~/.local/state/ytd/queue.json ~/.local/state/ytd/cache.json \
   ~/.local/state/ytd/library.json
# y si también quieres olvidar la música: rm -rf ~/Música/YTD
```

## 4. Corregir metadatos (`ytd fix`)

Re-identifica o corrige a mano **sin re-descargar** (recalcula la huella,
consulta de nuevo MusicBrainz y mueve el fichero a su sitio):

```bash
# Detecta y re-identifica pistas que no tengan álbum
ytd fix

# Re-identifica TODA la biblioteca (p. ej. tras mejorar el selector)
ytd fix --todo

# Re-identifica un fichero concreto
ytd fix "~/Música/YTD/Death/Human/14 - Together as One (drum & bass).mp3"

# ...o fija los datos a mano:
ytd fix archivo.mp3 --artista "Death" --album "Spiritual Healing" \
     --pista 5 --anio 1990 --titulo "Spiritual Healing" --cover caratula.jpg
```

Todos los parámetros `--artista/--album/--titulo/--pista/--anio/--cover` son
opcionales: lo que no indiques mantiene el valor existente.

## 5. Verificar la biblioteca (`ytd verify`)

Auditoría de tu biblioteca **sin usar la red** (rápido) o contra MusicBrainz:

```bash
ytd verify                    # estructural: etiquetas faltantes, carátulas,
                              # nombres ≠ etiquetas, duplicados, rutas obsoletas
ytd verify --red              # además: nº de pista/título contra MusicBrainz
ytd verify --fix              # corrige automáticamente lo que puede:
                              #   · borra duplicados idénticos (MD5)
                              #   · re-identifica los ficheros con incidencias
                              #   · limpia entradas obsoletas de library.json
ytd verify --fix --red        # auditoría completa + corrección
```

Los casos que `verify` **no** corrige solo (para que decidas tú):

- «posible duplicado» con audio **distinto**: son dos grabaciones diferentes
  (p. ej. dos tomas live de la misma canción) que acabaron con el mismo
  nombre — decide cuál conservar;
- «sin registro en library.json»: el fichero no guarda el id del vídeo de
  origen (p. ej. descargas manuales);
- falsos positivos de AcoustID mirando el AUDIO real.

Multas de confianza: cuando la huella da un **score bajo** o un título muy
distinto al de YouTube, el programa te pregunta:

```
! Identificación dudosa: título divergente de YouTube (20% de similitud)    huella:  «Spiritual Healing» — Clearlight [Spiritual Healing]
    youtube: «Death - Spiritual Healing»
  ¿Qué hago? [s]aceptar huella  [y]usar título de YouTube  [m]editar a mano  [n]omitir esta pista:
```

- Al añadir la cola con `ytd dl --yes` o `ytd fix --yes`, se acepta sin
  preguntar (útil en descargas nocturnas grandes).
- Configurable con `confirm_low_confidence = true/false` en el config.

## 6. Otros comandos

```bash
ytd config        # muestra los valores actuales y la ruta del config
ytd stats         # salud de la biblioteca de un vistazo (rápido, sin red)
ytd doctor        # comprueba dependencias (yt-dlp, ffmpeg, fpcalc, mutagen), API key
ytd help
```

## 7. Configuración (`~/.config/ytd/config.toml`)

| Clave              | Descripción                                              |
|--------------------|----------------------------------------------------------|
| `acoustid_api_key` | Clave **de aplicación** de AcoustID (no la personal)     |
| `music_dir`        | Carpeta destino (por defecto `~/Música/YTD`)             |
| `ytdlp_extra`      | Argumentos extra para yt-dlp, p. ej. cookies de navegador |
| `confirm_low_confidence` | Pregunta cuando la identificación parece dudosa    |

Ejemplo con cookies (útil para vídeos con restricción de edad o 18+):

```toml
ytdlp_extra = ["--cookies-from-browser", "firefox"]
```

## 8. Cómo funciona la identificación

1. **Descarga**: yt-dlp extrae el audio y ffmpeg lo convierte a MP3 CBR
   320 kbps en una carpeta temporal.
2. **Huella**: `fpcalc` (Chromaprint) calcula la huella del audio resultante
   (es independiente del bitrate/nombre: identifica por «ADN» acústica).
3. **AcoustID**: la huella se consulta en AcoustID → devuelve una lista de
   grabaciones MusicBrainz candidatas con scores.
4. **Fallback por título**: si AcoustID no encuentra nada, se busca el
   título de YouTube (limpio de «(Live)», «(2022 Remaster)»…) en la base
   de MusicBrainz. Solo se acepta si similitud de título ≥ 50 % y el
   artista cuadra; si el título es ambiguo (varios artistas lo reclaman
   y no conocemos el artista), se pide decisión humana (`ytd edit`) en
   lugar de arriesgar un fichero mal clasificado.
5. **MusicBrainz**: se pregunta por álbum, año, nº de pista. El selector
   aplica reglas de calidad: prefiere la grabación de estudio (no «live»/
   demo), la posición de pista coherente con el tamaño del disco y la
   edición original sobre reediciones/remasterizaciones.
6. **Carátula**: se baja de Cover Art Archive; si no hay, se recurre a la
   miniatura del vídeo original.
7. **Re-etiquetado**: mutagen escribe ID3 (título, artista, álbum, año,
   nº pista, carátula) — el resto del audio se deja intacto.
8. **Biblioteca**: el fichero se mueve a `Artista/Álbum/NN - Título.mp3`
   o `Artista/Título.mp3` si no se identificó el álbum.

Falsos positivos: AcoustID es muy fiable, pero una playlist puede contener
un vídeo cuyo AUDIO no corresponde al título indicado por el uploader. En
ese caso el sistema etiqueta lo que el AUDIO realmente es. Solución:
descarga el tema correcto de otro vídeo (`ytd search "Artista - Tema"`) y
elimina el fichero equivocado.

## 8b. Editor visual de metadatos (`ytd edit`)

Para revisar y corregir un fichero con comodidad (muy útil para los casos que
quedaron «para revisión» en `verify`):

```bash
ytd edit archivo.mp3        # panel con estado actual + sugerencias
ytd edit archivo.mp3 --yes  # aplica directamente la mejor sugerencia
```

```
──────────────────────────────────────────────────────────────────────
  Editor de metadatos — Dream Theater/…/03 - Panic Attack (2).mp3
──────────────────────────────────────────────────────────────────────
  Fichero: Dream Theater/Chaos in Motion 2007–2008/03 - Panic Attack (2).mp3
    Artista: Dream Theater
    Título:  Panic Attack
    Álbum:   Chaos in Motion 2007–2008    Pista: 3   Año: 2008   Carátula: sí
──────────────────────────────────────────────────────────────────────
  Sugerencias (por prioridad):
    [1] Panic Attack — Dream Theater  huella
    [2] Panic Attack — Dream Theater  búsqueda
    ...
  ¿Siguiente paso? [1-N] usar sugerencia · [e]ditar campos · [g]uardar como está · [q]salir:
```

- **Sugerencia 1** = lo que AcoustID reconoce por huella; **2..N** = búsquedas
  en MusicBrainz por título/artista actuales.
- Al elegir una sugerencia se muestran los datos completos (álbum, pista, año,
  carátula) y se pide confirmación antes de mover/retiquetar.
- Si la sugerencia no aporta carátula, se conserva la existente.
- `[e]` permite editar campos a mano (artista, título, álbum, año, pista).
- `[g]` normaliza el fichero (moverlo a su ruta canónica) con las etiquetas
  actuales.

## 9. Solución de problemas

- **«yt-dlp: error: no such option»**: probablemente tu yt-dlp es una
  versión vieja / cambiaron flags. Actualiza: `sudo dnf upgrade yt-dlp`.
- **WARNING de «JavaScript runtime»**: no es fatal; instálale `deno`
  (`sudo dnf install deno`) y desaparece.
- **«MusicBrainz saturado; reintento en Xs»**: normal; el sistema reintentará
  automáticamente (hasta 4 intentos) antes de rendirse.
- **«AcoustID no respondió 400»**:
  - clave inválida/errónea → revisala con `ytd doctor`; usa la clave de
    la APLICACIÓN, no tu clave personal.
  - «Sin coincidencias acústicas»: este upload no tiene match en la base;
    entonces se busca el título de YouTube en MusicBrainz (fallback por
    título, v1.7). Si aún así no se puede (título ambiguo o sin match),
    usa `ytd edit archivo.mp3` y elige la sugerencia correcta del panel.
- **«yt-dlp terminó con código X»**: suelen ser bloqueos de YouTube.
  Usa cookies en el config (`ytdlp_extra = ["--cookies-from-browser", "firefox"]`)
  o actualiza yt-dlp.
- **Nombre mal calculado** (p. ej. «(drum & bass)» en el título):
  ejecuta `ytd fix archivo.mp3` — el selector elige la edición studio.

## 10. Ficheros de estado (dónde vive la memoria)

```
~/.config/ytd/config.toml        # configuración
~/.local/state/ytd/
├── queue.json                   # cola persistente de descargas
├── library.json                 # id de vídeo → fichero MP3 (deduplicación)
├── cache.json                   # caché huella→metadatos (acelera re-fix)
└── tmp/                         # temporal de cada descarga
```

---

## Registro de versión rápida

- **v1.7** — Fallback inteligente: cuando AcoustID no da match, se busca el
  título de YouTube en MusicBrainz (con limpieza de «(Live)»/«Remaster» y
  validación de título+artista); ambigüedades se dejan para decisión humana.
  Bug corregido: `cleanup_title` mataba «Artista - Título».
- **v1.6** — `ytd stats`: salud de la biblioteca de un vistazo — ficheros,
  tamaño, duración, catálogo, carátulas/álbum/artistas pendientes, duplicados
  en un mismo álbum, álbumes incompletos, estado de cola y registro.
- **v1.5** — `ytd edit`: editor visual interactivo de metadatos con
  sugerencias (huella AcoustID + búsqueda MusicBrainz), preview antes de
  aplicar, conservación de carátula y campo año en la edición manual.
- **v1.4** — `ytd verify` (auditoría sin red y `--red` contra MusicBrainz,
  `--fix` con deduplicación por MD5), puerta de confianza interactiva para
  identificaciones dudosas (`confirm_low_confidence`, `--yes`).
- **v1.3** — Selector multi-candidato studio/original, `ytd fix` (auto, `--todo`,
  manual), reintentos MusicBrainz, cache por huella, `~/Música/YTD` XDG.
- **v1.2** — Fix AcoustID POST, scorers MusicBrainz (media/tracks), README.
- **v1.1** — Cola persistente, dedupe, subcomandos, `doctor`.
- **v1.0** — Descarga + MP3 320k + tagging básico.
