# Progress — ytd (descargador de música desde YouTube)

Registro de desarrollo del CLI `/home/antonio/Nextcloud/YTD/ytd.py`.

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
