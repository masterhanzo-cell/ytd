# Progress — ytd (descargador de música desde YouTube)

Registro de desarrollo del CLI `/home/antonio/Nextcloud/YTD/ytd.py`.

---

## 2026-11-04 — Sesión 2: publicación en GitHub + hito «confianza» y «verify»

### Publicado en GitHub
- ✅ Repo público: https://github.com/masterhanzo-cell/ytd (rama `main`,
  gh CLI autenticado en el equipo del usuario).
- ✅ `.gitignore` (excluye estado local, cache, y notas antiguas solo locales).
- ✅ Commits iniciales: código, README revisado y progress.md.

### v1.4 — Puerta de confianza (identificaciones dudosas) [hito 1]
- ✅ `evalua_confianza()`: marca como dudosa cuando el score de AcoustID
  < 0.5 o el título de la huella diverge del de YouTube (Jaccard sobre
  tokens normalizados < 35%).
- ✅ `decide_meta()` con prompt interactivo: `[s]aceptar huella`
  `[y]usar título de YouTube` `[m]editar a mano` (artista/título/álbum/nº
  con valores por defecto) `[n]omitir` (≈ dejan la pista pendiente).
- ✅ Respeta `confirm_low_confidence` del config y flujos no interactivos:
  en `ytd dl --yes` / `ytd fix --yes` / `ytd verify --fix --yes` se auto-acepta.
- ✅ El flujo `fix` mapea `[y]` → «dejar las etiquetas actuales» (sin movimiento).
- ⚠️ Pendiente de ejercicio real con prompts (por ahora se ha usado --yes;
  el camino interactivo está compilado y probado estructuralmente).

### v1.4 — `ytd verify` (auditoría de biblioteca) [hito 2]
- ✅ Sin red: comprueba etiquetas ID3 faltantes (artista/título/álbum),
  carátulas, nombre ⇒ etiquetas (con tolerancia de sufijos «(2)» al detectar
  duplicado vs renombrado), ficheros fuera de su carpeta de álbum (álbumes
  autotitulados incluidos), ficheiros sin registro en `library.json` y entradas
  huérfanas de `library.json`.
- ✅ `--red`: agrupa por artista+álbum, consulta el release en MusicBrainz y
  confronta nº de pista vs título (matches por similitud). Con reintentos
  503/429 generales (`_mb_get_retry`).
- ✅ `--fix`: dos fases —
  1. deduplicación: borra los duplicados **idénticos** (comparación MD5) y
     reasigna las rutas de `library.json` a la copia canónica;
  2. loop `fix_auto_one` (huella + MusicBrainz + retag + move) sobre el resto,
     incluyendo las incidencias encontradas por `--red`.
- ✅ Cambio en el flujo fix que da un resultado visible importantes:
  `fix_auto_one` usa la miniatura de YouTube como respaldo de carátula
  buscando la URL desde `library.json`/`queue.json` (antes el fix dejaba sin
  carátula los match sin cover art en CAA).

### Auditoría real resultante (muestra del resultado en mi biblioteca — 394 ficheros)
- ✅ Casos «(2)» de Dream Theater resultaron ser **tomás distintas de live**
  (fingerprints diferentes): verify los dej función «para revisión» por diseño
  (correcto: MD5 divergía; solo eran duplicados a niveles «byte»).
- ✅ Las carátulas faltantes (Symbolic, bonus de SBG…) quedaron pendientes y
  se resolvieron con que ai hija. Tras el `--fix`: 31 → 11 incidencias.
- ℹ️ Restante (decisiones del usuario): 3 pistas DT sin match acústico,
  «posible duplicado» de tomas live distintas (sale bien conservar), y el caso
  Clearlight/Spiritual Healing (dos listings de YouTube cuyos AUDIO real no
  corresponde al título — el fingerprint responde honesto a lo que suena).

### Ideas para siguientes versiones
- [ ] Confirmación interactiva cuando el score de AcoustID sea bajo ✓ (hecho v1.4)
- [ ] `ytd repair-only-tags` (retag sin re-consulta API).
- [ ] Modo `--dry-run`.
- [ ] Fallback a servidores Invidious/Piped si YouTube bloquea.
- [ ] Respaldos del título de YouTube → búsqueda MusicBrainz cuando AcoustID no
       vuelve con match (los «Artista desconocido»).
- [ ] `ytd dedupe` interactiva por huella (candidato: los «(2)» DT).

---

## 2026-11-03 — Sesión 1: versión base + puesta a punto identificación

### Construcción inicial (v1.0 → v1.1)
- ✅ CLI completa contra `yt-dlp` + `ffmpeg`: MP3 **CBR 320 kbps**.
- ✅ **Cola persistente** en `~/.local/state/ytd/queue.json`; enlaces de video
  o de playlist; soporte YouTube Music; `Ctrl+C` seguro (el progreso se guarda).
- ✅ Subcomandos: `add`, `dl`, `queue`, `retry <n>` / `retry failed`,
  `search "texto"` (top-5 con elección interactiva), `clean`, `config`,
  `doctor`, `help`.
- ✅ Deduplicación por id de vídeo (`library.json`); si la biblioteca ya
  tiene un id y el fichero existe, se salta la descarga.
- ✅ Etiquetado ID3 completo con `mutagen` (título, artista, álbum, año,
  nº pista, carátula APIC), v2.3 para máxima compatibilidad.
- ✅ Carátulas: Cover Art Archive → fallback miniatura de YouTube.
- ✅ Config en `~/.config/ytd/config.toml` (creada automáticamente), con
  `acoustid_api_key`, `music_dir`, `ytdlp_extra`.
- ✅ `doctor`: comprueba yt-dlp/ffmpeg/fpcalc/mutagen/clave API + música dir.

### Puesta a punto del sistema de identificación
- ✅ *(bug)* `yt-dlp: no such option: --discard-warnings` → quitado el flag.
- ✅ *(bug)* AcoustID por GET → **POST** (las huellas son largas; la API
  oficial lo recomienda). Petición validada sin errores.
- ✅ *(aclaración)* La API key de **aplicación** (https://acoustid.org/new-application)
  es distinta de la clave **personal** de la cuenta de AcoustID; el doctor
  se reescribió para validarla con un mini-lookup real (`invalid API key`
  vs `invalid fingerprint` como discriminante).
- ✅ *(bug)* Respuestas de AcoustID: el campo es **`recordings`** (plural);
  se leía `recording` → ahora la identificación devuelve matches correctos.
- ✅ *(bug)* MusicBrainz con `fmt=json`: el release trae `media[].tracks[]`
  (no `medium-list`/`track-list`); `select_release` reescrito acorde.
- ✅ *(bug)* `library.json` nunca se escribía al terminar cada track; ahora se
  persiste a cada éxito y se re-encolan automáticamente las pistas marcadas
  `done` cuyo fichero ya no existe (biblioteca borrada a mano).
- ✅ `music_dir` ajustado al XDG real (`~/Música/YTD`, no `~/Music/YTD`).
- ✅ Reintentos automáticos ante MusicBrainz **503/429/5xx**
  (3 reintentos con backoff: 3s/6s/9s) — verificado en playlists grandes.

### Selector de release (núcleo de la detección de álbum)
- ✅ Múltiples grabaciones candidatas por huella de AcoustID (antes solo
  la primera, a veces versiones «live»/demo emparejadas por error).
- ✅ Preferencia: **estudio > live** (filtra títulos con «(live…», «rehearsal»,
  «(demo…», «MTV», «session»), posición de pista **coherente con el
  tamaño del disco** (`pos ≤ track-count`), y **edición original > reedición**
  (desempate por fecha más antigua).
- ✅ *Verificación empírica*: tracklists de MusicBrainz comparados a mano
  (Leprosy 1988 8 pistas, SBG 1987 10+bonus, Spiritual Healing 8, Human 8,
  Symbolic 9, TSOP 9+bonus): la biblioteca resultante casa al 100% con el
  orden oficial; se corrigieron los residuos («22 - Pull the Plug» → 05,
  «01 - Open Casket» → 06, «14/15 - Together as One/Secret Face» → 03/04
  y el resto «(drum & bass)» desapareció).

### Nuevo comando `ytd fix` (reparación sin re-descargar)
- ✅ `ytd fix` → re-identifica las pistas **sin álbum** (huella nueva +
  MusicBrainz + retag + move).
- ✅ `ytd fix --todo` → re-identifica TODA la biblioteca (limpia también el
  cache de huellas para recalcular con el selector actual).
- ✅ `ytd fix <file.mp3>` → fichero concreto, siempre re-identifica.
- ✅ Corrección manual: `--artista --album --titulo --pista --anio --cover`
  (lo omitido mantiene el valor previo; `--cover` acepta cualquier JPG/PNG).
- ✅ Mueve el fichero a su destino final y actualiza `library.json`.
- ✅ *(bug fix)* suffix «(2)» cuando el fichero fuente ya ocupaba el nombre
  destino (`final_path(..., src=...)`).

### Hallazgo interesante
- ℹ️ La playlist «Death - Discography» tenía **un vídeo mal etiquetado por
  el uploader** («Spiritual Healing» con el audio de «Without Words» de
  Clearlight). AcoustID score 0.967 → el audio real se identificó con éxito;
  el sistema creó la carpeta Clearlight correspondiente. Para resolver del
  lado del usuario: pillar el audio correcto de otro vídeo
  (`ytd search "Death - Spiritual Healing"`) y borrar el fichero del falso positivo.

### Resultado final de la prueba real
```
~/Música/YTD/Death/...          63 pistas (discografía completa, numeradas y con carátulas)
  Scream Bloody Gore/…          12 (10 de LP + 2 bonus)
  Leprosy/…                     8
  Spiritual Healing/…           7 de 8 (falta la homónima: falso positivo en la playlist)
  Human/…                       8
  Individual Thought Patterns/… 10
  Symbolic/…                    9
  The Sound of Perseverance/…   9
Clearlight/…                    1 (audio subido mal por el autor de la playlist)
```

### Ideas para siguientes versiones
- [ ] Confirmación interactiva cuando el score de AcoustID sea bajo
      (< 0.5) o cuando el título de la huella diverge mucho del de YouTube.
- [ ] `ytd repair-only-tags` (retag sin re-consulta API).
- [ ] Modo `--dry-run`.
- [ ] Fallback a servidores Invidious/Piped si YouTube bloquea.

## v1.5 — Editor visual de metadatos (`ytd edit`)

### Qué se añadió
- Subcomando `ytd edit <archivo.mp3>` (+ flag `--yes`): panel interactivo
  con el estado actual del fichero (artista, título, álbum, pista, año,
  carátula) y sugerencias ordenadas por prioridad:
  1. huella acústica (AcoustID, recalculada al vuelo con caché);
  2. búsqueda de grabaciones en MusicBrainz (WS2 search `recording`)
     por título+artista actuales; fallback a solo título.
- Las sugerencias se muestran legibles («Panic Attack — Dream Theater»),
  no como rids crudos.
- Al elegir una sugerencia se recuperan álbum/pista/año/carátula desde
  MusicBrainz (con `select_release` + Cover Art Archive), se muestra una
  **vista previa** completa y se pide confirmación antes de mover/retag.
- Si la sugerencia no trae carátula, **se conserva la existente** (evita
  perder arte al reaplicar).
- `[e]` → edición manual campo a campo (ahora incluye **año**);
  `[g]` → guardar tal cual (normaliza ruta canónica); `[q]` → salir.
- Helpers nuevos: `_truncar`, `_mb_search_recordings`, `_meta_desde_recording`,
  `_apply_meta`, `_estado` (panel).

### Pruebas hechas
- `printf 'q\n' | ytd edit <real>`: panel + sugerencias OK, sin mutación.
- Ciclo completo en sandbox (`/tmp/opencode/testlib`, config local con
  `music_dir` falso): sugerencia huella aplicada → fichero movido a
  `Death/Symbolic/03 - Empty Words.mp3`, ID3 correctos y **APIC sí**
  (carátula conservada). Sandbox borrado.

### Notas
- `ytd edit` funciona sobre cualquier ruta (no solo la biblioteca); el
  destino final siempre se calcula dentro de `music_dir`.
- La sección nueva del README es la 8b (entre «Cómo funciona» y
  «Solución de problemas»).

## v1.6 — `ytd stats` (salud de la biblioteca de un vistazo)

### Qué se añadió
- Subcomando `ytd stats`: escaneo local rápido (sin red) de `music_dir` y
  muestra: nº de MP3, tamaño total, duración total (mutagen MP3), nº de
  artistas/álbumes y per-artistas top.
- Salud de etiquetas: carátulas faltantes, ficheros sin álbum, «Artista
  desconocido», duplicados y numeración con huecos.
- Duplicados en DOS niveles para evitar ruido:
  * **en un mismo álbum** (artista+título repetidos en la misma carpeta) →
    alerta real (casi siempre sufijos «(2)»);
  * «mismo tema en varios discos» → línea informativa (live/compilaciones,
    es normal).
- Álbumes incompletos: nº máximo de pista > nº de ficheros con esa pista
  → lista los números presentes (p. ej. Death/Spiritual Healing 1,2,3,4,6,7,8).
- Cola y registro: pendientes/hechas/fallidas de la cola y entradas
  obsoletas de library.json, con los comandos de corrección sugeridos.
- Helpers: `_bytes_h`, `_duracion_h`, `_duracion_mp3`.

### Prueba real (biblioteca del usuario)
- 538 MP3 · 7.9 GiB · 58 h 18 min · 5 artistas · 60 álbumes.
- Detecta: 2 «Artista desconocido», 43 ficheros duplicados en 21 grupos,
  4 entradas obsoletas, 2 fallidas en cola — coincide con lo conocido.


## v1.7 — Fallback inteligente (título de YouTube → MusicBrainz)

### Qué se añadió
- `_mb_fallback_meta(yt_title)`: cuando AcoustID no da match, busca el tema
  en MusicBrainz (WS2 search `recording`) usando el título de YouTube
  limpio («Sepultura - Territory (Live) (2022 Remaster)» → art «Sepultura»,
  tema «Territory»). Añadido helper `_para_busqueda()`: quita paréntesis
  clasificadores ((Live), (2022 Remaster), [HQ]…) y palabras live/remaster.
- Validación estricta del candidato: título con similitud ≥ 50 % Y artista
  coherente (si había artista en el título). Si no hay artista adivinado y
  varios artistas reclaman el mismo título → ambigüedad → None (seguro).
- Enchufado en DOS flujos:
  * `process_one` (descargas nuevas): antes de caer a «título de YouTube»,
    prueba el fallback; el resultado pasa por `decide_meta` (con --yes /
    config se auto-acepta tras las advertencias).
  * `fix_auto_one` (reparación sin re-descargar): si no hay match de
    huella, recupera el título original de YouTube desde library.json
    (`_yn_titulo_origen`) porque suele incluir el artista, y luego fallback.
- `cmd_edit`: cuando no hay ninguna sugerencia por etiquetas, también
  consulta con el título original de YouTube («búsqueda YT».
- **Bug corregido**: `cleanup_title` borraba «Artista - Título» entero
  (patrón `[-|]` interpretaba el separador como ruido de canal) → la
  similitud del control de confianza quedaba viciada y el fallback no
  conseguía artista/título. Ahora solo limpia «| Canal» y similares.

### Pruebas (caso real: «Artista desconocido/Territory (Live)»)
- Con artista en el título → Sepultura/Territory + álbum + pista + carátula ✔
- Sin artista en el título y 4-6 artistas con «Territory» → None seguro ✔
  (correcto: es un tema repetido en Chaos A.D./Under a Pale Grey Sky/Metal
  Veins; es decisión humana con `ytd edit`)
- Sandbox de fix_auto_one descarta el movimiento si el fallback no cuadra ✔

### Notas
- El prompt de confianza ahora dice «huella:» o «sugerencia:» según origen.
