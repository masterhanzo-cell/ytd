#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ytd — Descarga música de YouTube como MP3 (320 kbps), la identifica por huella
acústica (AcoustID + MusicBrainz), corrige título/artista/álbum, incrusta la
carátula y la ordena en tu biblioteca:  ~/Music/YTD/Artista/Álbum/01 - Canción.mp3

Pega un enlace y el programa hace el resto. Soporta playlists (álbumes
completos) y una cola persistente con reintentos.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

# ------------------------------------------------------------------ colores --
DIM, BOLD = "\033[2m", "\033[1m"
CYAN, GREEN, YELLOW, RED, RESET = "\033[36m", "\033[32m", "\033[33m", "\033[31m", "\033[0m"
USE_COLOR = sys.stdout.isatty()


def c(code: str, text: str) -> str:
    return f"{code}{text}{RESET}" if USE_COLOR else text


def info(msg: str) -> None:
    print(c(CYAN, "ℹ ") + msg)


def ok(msg: str) -> None:
    print(c(GREEN, "✔ ") + msg)


def warn(msg: str) -> None:
    print(c(YELLOW, "! ") + msg)


def fail(msg: str) -> None:
    print(c(RED, "✖ ") + msg)
    sys.exit(1)


# ------------------------------------------------------------------- rutas --
def _xdg(kind: str, default: str) -> Path:
    return Path(os.environ.get(kind, Path.home() / default))


CONFIG_DIR = _xdg("XDG_CONFIG_HOME", ".config") / "ytd"
STATE_DIR = _xdg("XDG_STATE_HOME", ".local/state") / "ytd"
CONFIG_FILE = CONFIG_DIR / "config.toml"
QUEUE_FILE = STATE_DIR / "queue.json"
LIBRARY_FILE = STATE_DIR / "library.json"
CACHE_FILE = STATE_DIR / "cache.json"
TMP_DIR = STATE_DIR / "tmp"

DEFAULT_CONFIG = """\
# Configuración de ytd
# ------------------------------------------------------------------
# Clave API de AcoustID (gratis): https://acoustid.org/new-application
# Sin clave la app descarga igual, pero el nombre vendrá del título de
# YouTube en lugar de la huella acústica.
acoustid_api_key = ""

# Carpeta donde se guarda la música
music_dir = "~/Music/YTD"

# Argumentos extra para yt-dlp (opcional), p. ej. cookies de un navegador:
# ytdlp_extra = ["--cookies-from-browser", "firefox"]
ytdlp_extra = []

# Confirmación interactiva cuando la identificación parece dudosa
# (score de huella bajo o título muy divergente del de YouTube).
# Con false se auto-acepta siempre; con true pregunta (o usa ytd dl --yes).
confirm_low_confidence = true
"""


class Config:
    def __init__(self, data: dict):
        self.api_key: str = (data.get("acoustid_api_key") or "").strip()
        self.music_dir: Path = Path(
            (data.get("music_dir") or "~/Music/YTD").strip()
        ).expanduser()
        self.ytdlp_extra: list[str] = list(data.get("ytdlp_extra") or [])
        self.confirm: bool = bool(data.get("confirm_low_confidence", True))


def load_config() -> Config:
    if not CONFIG_FILE.exists():
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(DEFAULT_CONFIG, encoding="utf-8")
        info(f"Configuración creada en {CONFIG_FILE}")
        warn("Añade tu clave de AcoustID ahí para activar la huella acústica")
    with open(CONFIG_FILE, "rb") as fh:
        return Config(tomllib.load(fh))


# ----------------------------------------------------------------- estado --
def _read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        warn(f"No pude leer {path.name}; creo uno nuevo")
        return default


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def load_queue() -> dict:
    return _read_json(QUEUE_FILE, {"items": []})


def save_queue(q: dict) -> None:
    _write_json(QUEUE_FILE, q)


def load_library() -> dict:
    return _read_json(LIBRARY_FILE, {})


# ---------------------------------------------------------------- externos --
def which_or_fail(prog: str, hint: str) -> str:
    p = shutil.which(prog)
    if not p:
        fail(f"Falta '{prog}'. Instálalo con: {hint}")
    return p


def doctor() -> None:
    print("Comprobando dependencias...")
    for prog, pkg in [
        ("yt-dlp", "sudo dnf install yt-dlp"),
        ("ffmpeg", "sudo dnf install ffmpeg"),
        ("fpcalc", "sudo dnf install chromaprint-tools"),
    ]:
        if shutil.which(prog):
            print(f"  {c(GREEN, '[ok]')} {prog}")
        else:
            print(f"  {c(RED, '[!!]')} {prog}  — instala con: {pkg}")
    cfg = load_config()
    if cfg.api_key:
        good = _probe_acoustid(cfg.api_key)
        if good:
            print(f"  {c(GREEN, '[ok]')} AcoustID — la clave funciona ({c(DIM, cfg.api_key[:4] + '...')})")
        else:
            print(f"  {c(RED, '[!!]')} AcoustID — clave inválida o sin conexión")
            print("        Revisa la API key de tu APLICACIÓN en https://acoustid.org")
    else:
        print(f"  {c(YELLOW, '[--]')} Sin clave AcoustID (huella acústica desactivada)")
        print("        Registra una gratis en https://acoustid.org/new-application")
        print(f"        y pégala en {CONFIG_FILE}")
    try:
        import mutagen  # noqa: F401
        print(f"  {c(GREEN, '[ok]')} mutagen (etiquetado MP3)")
    except ImportError:
        print(f"  {c(RED, '[!!]')} mutagen falta — instala con: pip3 install --user mutagen")
    print(f"  Música: {cfg.music_dir}")


class AcoustIdInvalid(Exception):
    pass


def _probe_acoustid(key: str) -> bool:
    """Valida la clave con un mini-lookup real (huella dummy: la clave es lo
    que se comprueba, no la huella)."""
    try:
        params = urllib.parse.urlencode(
            {"client": key, "duration": 10, "fingerprint": "AQAA0Em", "format": "json"}
        )
        req = urllib.request.Request(
            "https://api.acoustid.org/v2/lookup",
            data=params.encode(),
            headers={
                "User-Agent": "ytd/1.0",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        try:
            body = urllib.request.urlopen(req, timeout=15).read()
        except urllib.error.HTTPError as e:
            body = e.read()
        msg = (json.loads(body).get("error") or {}).get("message", "")
        return "api key" not in msg.lower()
    except Exception:
        return False


def _http_get(url: str, ua: str = "ytd/1.0", timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": ua})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _http_post(url: str, params: dict, ua: str = "ytd/1.0", timeout: int = 30) -> bytes:
    data = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"User-Agent": ua, "Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


_last_mb_call = 0.0


def _mb_get(url: str):
    """GET a MusicBrainz con nivel de peticiones (1/s) respetado."""
    global _last_mb_call
    wait = 1.1 - (time.monotonic() - _last_mb_call)
    if wait > 0:
        time.sleep(wait)
    _last_mb_call = time.monotonic()
    return json.loads(_http_get(url, ua="ytd/1.0 (biblioteca personal)"))


def _mb_get_retry(url: str, tries: int = 4):
    """Igual que _mb_get pero con reintentos ante 503/429/5xx."""
    for intento in range(tries):
        try:
            return _mb_get(url)
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and intento < tries - 1:
                pausa = 3 * (intento + 1)
                warn(f"MusicBrainz saturado ({e.code}); reintento en {pausa}s...")
                time.sleep(pausa)
                continue
            raise
    return None


# --------------------------------------------------------------- YouTube ---
def ytdlp_json(url: str, cfg: Config, flat: bool = False) -> dict:
    cmd = ["yt-dlp", "--no-warnings"]
    if flat:
        cmd.append("--flat-playlist")
    cmd += ["-J", url] + cfg.ytdlp_extra
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True, check=True, timeout=240
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("yt-dlp tardó demasiado en responder")
    except subprocess.CalledProcessError as e:
        lines = (e.stderr or "").strip().splitlines()
        raise RuntimeError(lines[-1] if lines else "error desconocido de yt-dlp")
    return json.loads(out.stdout)


def expand_url(url: str, cfg: Config) -> tuple[str | None, list[dict]]:
    """Resolve la URL. Devuelve (título_playlist | None, [ {vid, title, url} ])."""
    info("Analizando el enlace...")
    data = ytdlp_json(url, cfg, flat=True)
    entries_raw = data.get("entries")
    if entries_raw is None:  # vídeo suelto
        return None, [
            {
                "vid": data.get("id"),
                "title": data.get("title") or data.get("id"),
                "url": data.get("webpage_url") or url,
            }
        ]
    entries = []
    for e in entries_raw:
        if not e:
            continue
        vid = e.get("id")
        u = e.get("webpage_url") or e.get("url")
        if not u and vid:
            u = f"https://youtu.be/{vid}"
        if not u:
            continue
        entries.append({"vid": vid, "title": e.get("title") or vid, "url": u})
    plname = data.get("title")
    return plname, entries


def download_to_mp3(url: str, dst_dir: Path, cfg: Config) -> Path:
    dst_dir.mkdir(parents=True, exist_ok=True)
    pattern = str(dst_dir / "%(title)s [%(id)s].%(ext)s")
    cmd = [
        "yt-dlp", "-x",
        "--audio-format", "mp3",
        "--audio-quality", "320K",   # CBR 320 kbps
        "--newline", "--quiet",
        "--no-playlist",
        "-o", pattern,
        url,
    ] + cfg.ytdlp_extra
    proc = subprocess.run(cmd)
    if proc.returncode != 0:
        raise RuntimeError(f"yt-dlp terminó con código {proc.returncode}")
    files = sorted(dst_dir.glob("*.mp3"))
    if not files:
        raise RuntimeError("no se generó ningún MP3")
    return files[0]


def extract_thumbnail(url: str, cfg: Config) -> str | None:
    try:
        return ytdlp_json(url, cfg).get("thumbnail")
    except Exception:
        return None


# ------------------------------------------------------- identificación ---
def cleanup_title(t: str) -> str:
    """Quita ruido típico de títulos de YouTube: (Official Video), | canal, etc."""
    patrones = [
        r"\s*[(\[]\s*(?:official\s+)?(?:music\s+)?(?:video|audio|lyric[s]?"
        r"|lyrical|visualizer|mv|hd|hq|4k)[^)\]]*[)\]]\s*",
        r"\s*【[^】]*】\s*",
        r"\s+[-|]\s+.*$",
    ]
    for pat in patrones:
        t = re.sub(pat, " ", t, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", t).strip() or t


def split_artist_title(t: str) -> tuple[str | None, str]:
    m = re.match(r"^(.{1,60}?)\s+[-–—]\s+(.+)$", t)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return None, t


def fpcalc(path: Path) -> tuple[str, int]:
    out = subprocess.run(
        ["fpcalc", "-json", "-length", "120", str(path)],
        capture_output=True, text=True, check=True, timeout=120,
    )
    data = json.loads(out.stdout)
    return data["fingerprint"], int(data["duration"])


def acoustid_lookup(path: Path, cfg: Config, cache: dict) -> dict | None:
    """Huella acústica → AcoustID. Devuelve None si no hay match o falla."""
    if not shutil.which("fpcalc"):
        warn("fpcalc no está instalado (sudo dnf install chromaprint-tools)")
        return None
    if not cfg.api_key:
        warn("Sin clave de AcoustID (acoustid_api_key en el config)")
        return None
    try:
        fp, duration = fpcalc(path)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        warn(f"fpcalc falló: {e}")
        return None
    key = hashlib.sha1(fp.encode()).hexdigest()[:16]
    if (cache.get(key) or {}).get("candidates"):
        return cache[key]
    try:
        # POST: las huellas son largas y la API oficial recomienda no usar GET
        resp = json.loads(
            _http_post(
                "https://api.acoustid.org/v2/lookup",
                {
                    "client": cfg.api_key,
                    "duration": duration,
                    "fingerprint": fp,
                    "format": "json",
                    "meta": "recordings releases media",
                },
            )
        )
    except Exception as e:
        warn(f"AcoustID no respondió: {e}")
        return None
    if resp.get("status") != "ok":
        msg = str(resp.get("error", "error"))
        warn(f"AcoustID: {msg}")
        if "key" in msg.lower():
            raise AcoustIdInvalid()
        return None
    results = resp.get("results") or []
    if not results:
        info("Sin coincidencias acústicas")
        return None
    best = max(results, key=lambda r: r.get("score", 0))
    recs_raw = [r for r in (best.get("recordings") or []) if r and r.get("id")]
    if not recs_raw:
        return None
    rec = recs_raw[0]
    artists = [a.get("name", "") for a in rec.get("artists") or []]
    candidatos = []
    for r in recs_raw[:5]:
        a = [x.get("name", "") for x in r.get("artists") or []]
        candidatos.append(
            {"rid": r.get("id"), "title": r.get("title") or "", "artist": a[0] if a else ""}
        )
    meta = {
        "_fp_key": key,
        "score": best.get("score"),
        "title": rec.get("title") or "",
        "artist": artists[0] if artists else "",
        "rid": rec.get("id"),
        "candidates": candidatos,
    }
    if not meta["title"] and not meta["artist"]:
        return None
    cache[key] = meta
    return meta


def mb_recording_info(rid: str) -> dict | None:
    url = (
        f"https://musicbrainz.org/ws/2/recording/{rid}"
        f"?fmt=json&inc=artists+releases+media+release-groups"
    )
    for intento in range(4):
        try:
            return _mb_get(url)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code in (429, 500, 502, 503, 504) and intento < 3:
                pausa = 3 * (intento + 1)
                warn(f"MusicBrainz saturado ({e.code}); reintento en {pausa}s...")
                time.sleep(pausa)
                continue
            warn(f"MusicBrainz respondió {e.code}")
            return None
        except Exception as e:
            warn(f"MusicBrainz no respondió: {e}")
            return None
    return None


def select_release(rec: dict) -> tuple[str | None, int | None, bool]:
    """Elige el mejor álbum (release) del recording: (release_id, nº pista, es_live).

    Con fmt=json, cada release trae 'media' y cada medium 'tracks' con la
    entrada de la pista de esta grabación (posición y número en el disco).
    Prefiere matches de estudio sobre versiones live/demo (2ª pasada)."""
    releases = rec.get("releases") or []
    if not releases:
        return None, None, False
    marcadores = ("live", "rehearsal", "(demo", "mtv", "session ")

    def es_live(titulo: str) -> bool:
        t = (titulo or "").lower()
        return any(m in t for m in marcadores)

    def score(rel: dict) -> float:
        s = 0.0
        if rel.get("date"):
            s += 2
        rg = rel.get("release-group") or {}
        pt = (rg.get("primary-type") or "").lower()
        sec = {t.lower() for t in (rg.get("secondary-types") or [])}
        if pt == "album":
            s += 1
        if "compilation" in sec:  # evita recopilatorios (números de pista engañosos)
            s -= 5
        if len(rel.get("media") or []) == 1:
            s += 1
        return s

    releases.sort(key=score, reverse=True)
    respuesta = None
    for sin_live in (True, False):          # primero intento studio; luego, como respaldo, con live
        for rel in releases:
            for med in rel.get("media") or []:
                for tr in med.get("tracks") or []:
                    try:
                        pos = int(tr.get("position") or tr.get("number") or 0)
                    except (TypeError, ValueError):
                        pos = 0
                    if not pos:
                        continue
                    living = es_live(tr.get("title"))
                    if sin_live and living:
                        continue
                    # coherencia: el nº de pista debe caber en ese disco
                    track_count = med.get("track-count") or 0
                    if track_count and pos > track_count:
                        break  # esta edición es sospechosa; probamos con la siguiente
                    r = (rel["id"], pos, living)
                    if sin_live:
                        return r
                    respuesta = respuesta or r
    return respuesta or (None, None, False)


def cover_art(release_id: str) -> bytes | None:
    url = f"https://coverartarchive.org/release/{release_id}/front-500"
    try:
        data = _http_get(url, ua="ytd/1.0", timeout=25)
    except Exception:
        return None
    return data if len(data) > 500 else None


def enrich(fpmeta: dict, thumb_url: str | None) -> dict:
    """Combina AcoustID + MusicBrainz (+ carátula) en metadatos finales.

    AcoustID puede devolver varias grabaciones candidatas (versiones studio,
    live, demo con la misma huella): se prueba cada una en MusicBrainz y se
    queda con la que aporte un álbum de estudio coherente."""
    artist = fpmeta.get("artist") or ""
    title = fpmeta.get("title") or ""
    album, year, track, cover = "", "", None, None

    rids = []
    if fpmeta.get("rid"):
        rids.append(fpmeta["rid"])
    for cand in (fpmeta.get("candidates") or []):
        if cand.get("rid") and cand["rid"] not in rids:
            rids.append(cand["rid"])

    elegido = None  # dict con la mejor propuesta
    info(f"Consultando MusicBrainz ({min(len(rids), 4)} candidata(s))...")
    for rid in rids[:4]:
        rec = mb_recording_info(rid)
        if not rec:
            continue
        rel_id, pos, living = select_release(rec)
        if not rel_id:
            continue
        credits = rec.get("artist-credit") or []
        cred_name = (credits[0].get("artist") or {}).get("name") if credits else ""
        rel = next((r for r in rec["releases"] if r.get("id") == rel_id), None)
        fecha = (rel or {}).get("date") or "9999"
        buena = not living
        mejorando = (
            elegido is None
            or (buena and not elegido["buena"])          # studio antes que live
            or (buena == elegido["buena"] and fecha < elegido["fecha"])  # edición original antes que reedición
        )
        if mejorando:
            elegido = {"buena": buena, "fecha": fecha, "rec": rec, "rel": rel,
                       "rel_id": rel_id, "pos": pos, "cred": cred_name}
    if elegido:
        rec = elegido["rec"]
        artist = fpmeta.get("artist") or artist or elegido["cred"] or ""
        title = rec.get("title") or title
        elegido_rel = elegido["rel"]
        if elegido_rel:
            album = elegido_rel.get("title") or ""
            year = (elegido_rel.get("date") or "")[:4]
        if elegido["pos"]:
            track = elegido["pos"]
        cover = cover_art(elegido["rel_id"]) if elegido["rel_id"] else None
    if not cover and thumb_url:
        info("Sin carátula en MusicBrainz; uso la miniatura de YouTube")
        try:
            cover = _http_get(thumb_url, ua="ytd/1.0", timeout=20)
        except Exception:
            cover = None
    return {
        "artist": artist,
        "title": title,
        "album": album,
        "year": year,
        "track": track,
        "cover": cover,
    }


# ------------------------------------------------------- confianza manual --
def _tokens(s: str) -> set:
    s = re.sub(r"[^\w\s]", " ", (s or "").lower())
    return {w for w in s.split() if len(w) > 1}


def _similarity(a: str, b: str) -> float:
    """Jaccard simple sobre tokens normalizados (0..1)."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def evalua_confianza(yt_title: str, meta: dict, fpmeta: dict | None):
    """Decisiones de si la identificación es dudosa: (dudosa, motivos)."""
    motivos = []
    score = (fpmeta or {}).get("score", 1.0)
    if fpmeta and 0 < score < 0.5:
        motivos.append(f"score de huella bajo ({score:.2f})")
    sim = _similarity(cleanup_title(yt_title or ""), meta.get("title") or "")
    if sim < 0.35:
        motivos.append(f"título divergente de YouTube ({sim:.0%} de similitud)")
    return bool(motivos), motivos


def yt_meta_fallback(yt_title: str) -> dict:
    """Metadatos desde el título de YouTube como último recurso."""
    title = cleanup_title(yt_title or "")
    guessed, name = split_artist_title(title)
    return {
        "artist": guessed or "Artista desconocido",
        "title": name or "Sin título",
        "album": "", "year": "", "track": None, "cover": None,
    }


def _pedir_edicion_manual(meta: dict) -> dict:
    print(f"  Edita los campos (Enter = mantener el valor actual):")
    meta = dict(meta)
    nuevo = input(f"  artista  [{meta['artist']}]: ").strip()
    if nuevo:
        meta["artist"] = nuevo
    nuevo = input(f"  título   [{meta['title']}]: ").strip()
    if nuevo:
        meta["title"] = nuevo
    nuevo = input(f"  álbum    [{meta.get('album') or '-'}]: ").strip()
    if nuevo:
        meta["album"] = nuevo
    nuevo = input(f"  año      [{meta.get('year') or '-'}]: ").strip()
    if str(nuevo).isdigit():
        meta["year"] = nuevo
    nuevo = input(f"  nº pista [{meta.get('track') or '-'}]: ").strip()
    if str(nuevo).isdigit():
        meta["track"] = int(nuevo)
    return meta


def decide_meta(cfg: Config, yt_title: str, meta: dict, fpmeta: dict | None,
                yes: bool) -> dict | None:
    """Cuando la huella parece dudosa pregunta al usuario (o auto-acepta).

    Devuelve los metadatos definitivos, o None si el usuario elige omitir."""
    dudosa, motivos = evalua_confianza(yt_title, meta, fpmeta)
    if not dudosa:
        return meta
    for m in motivos:
        warn(f"Identificación dudosa: {m}")
    print(f"    huella:  «{meta.get('title')}» — {meta.get('artist')}"
          f"{(' [' + meta['album'] + ']') if meta.get('album') else ''}")
    print(f"    youtube: «{cleanup_title(yt_title or '')}»")
    if yes or not cfg.confirm or not sys.stdin.isatty():
        warn("Aceptando la huella automáticamente (--yes / config)")
        return meta
    while True:
        try:
            eleccion = input(
                "  ¿Qué hago? [s]aceptar huella  [y]usar título de YouTube  "
                "[m]editar a mano  [n]omitir esta pista: "
            ).strip().lower()
        except (EOFError, KeyboardInterrupt):
            return None
        if eleccion in ("s", ""):
            return meta
        if eleccion == "y":
            info("Usando los metadatos de YouTube")
            return yt_meta_fallback(yt_title)
        if eleccion == "m":
            return _pedir_edicion_manual(meta)
        if eleccion in ("n", "x", "q"):
            return None
        warn("Opción no válida (s/y/m/n)")


# --------------------------------------------------------------- etiquetas --
def tag_mp3(path: Path, meta: dict) -> None:
    try:
        from mutagen.id3 import (  # type: ignore
            APIC, ID3, TALB, TDRC, TIT2, TPE1, TPE2, TRCK,
        )
    except ImportError:
        fail("Falta 'mutagen': pip3 install --user mutagen")
    try:
        tags = ID3(str(path))
    except Exception:
        tags = ID3()
    tags.delall("APIC")
    tags["TIT2"] = TIT2(encoding=3, text=meta["title"])
    tags["TPE1"] = TPE1(encoding=3, text=meta["artist"])
    if meta.get("album"):
        tags["TALB"] = TALB(encoding=3, text=meta["album"])
        tags["TPE2"] = TPE2(encoding=3, text=meta["artist"])
    if meta.get("track"):
        tags["TRCK"] = TRCK(encoding=3, text=str(meta["track"]))
    if meta.get("year"):
        tags["TDRC"] = TDRC(encoding=3, text=meta["year"])
    if meta.get("cover"):
        mime = "image/jpeg"
        if meta["cover"][:8] == b"\x89PNG\r\n\x1a\n":
            mime = "image/png"
        tags["APIC"] = APIC(
            encoding=3, mime=mime, type=3, desc="Front cover", data=meta["cover"]
        )
    tags.save(str(path), v2_version=3)


def sanitize(part: str) -> str:
    part = re.sub(r'[\\/:*?"<>|]', "-", part)
    part = re.sub(r"[\x00-\x1f]", "", part)
    part = re.sub(r"\s+", " ", part).strip(" .")
    return part[:120] or "Desconocido"


def final_path(cfg: Config, meta: dict, src: Path | None = None) -> Path:
    artist = sanitize(meta.get("artist") or "Artista desconocido")
    dest = (
        cfg.music_dir / artist / sanitize(meta["album"])
        if meta.get("album") else cfg.music_dir / artist
    )
    prefix = f"{meta['track']:02d} - " if meta.get("track") else ""
    name = sanitize(meta.get("title") or "Sin título")
    p = dest / f"{prefix}{name}.mp3"
    i = 2
    while p.exists() and (src is None or p != src):
        p = dest / f"{prefix}{name} ({i}).mp3"
        i += 1
    return p


# --------------------------------------------------------------- procesado --
def process_one(cfg: Config, cache: dict, item: dict,
                yes: bool = False) -> tuple[Path | None, dict | None]:
    """Descarga un vídeo → MP3 320k → identifica → etiqueta → mueve a la
    biblioteca. Devuelve (None, None) si el usuario omite la pista."""
    tmpdir = TMP_DIR / uuid.uuid4().hex[:8]
    try:
        mp3 = download_to_mp3(item["url"], tmpdir, cfg)
        thumb = extract_thumbnail(item["url"], cfg)
        yt_title = item.get("title") or mp3.stem
        try:
            fpmeta = acoustid_lookup(mp3, cfg, cache)
        except AcoustIdInvalid:
            raise
        except Exception as e:
            warn(f"Huella acústica no disponible: {e}")
            fpmeta = None
        if fpmeta:
            meta = enrich(fpmeta, thumb)
            if not meta["title"]:
                meta["title"] = cleanup_title(yt_title)
                if not meta["artist"]:
                    guessed, _ = split_artist_title(yt_title)
                    meta["artist"] = guessed or "Artista desconocido"
            meta2 = decide_meta(cfg, yt_title, meta, fpmeta, yes)
            if meta2 is None:
                info("Pista omitida; queda pendiente para revisarla a mano")
                return None, None
            meta = meta2
        else:
            info("Uso el título de YouTube (sin huella acústica)")
            meta = yt_meta_fallback(yt_title)
            if thumb:
                try:
                    meta["cover"] = _http_get(thumb, ua="ytd/1.0", timeout=20)
                except Exception:
                    pass
        dest = final_path(cfg, meta)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(mp3), str(dest))
        tag_mp3(dest, meta)
        return dest, meta
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def process_queue(cfg: Config, yes: bool = False) -> None:
    which_or_fail("yt-dlp", "sudo dnf install yt-dlp")
    q = load_queue()
    lib = load_library()
    cache = _read_json(CACHE_FILE, {})
    # Sanity: pistas marcadas 'done' cuyo fichero ya no existe (biblioteca
    # borrada a mano) se re-encolan para volver a descargarlas.
    reencoladas = 0
    for it in q["items"]:
        vid = it.get("vid")
        ruta = lib.get(vid, {}).get("path") if vid else None
        if it["status"] == "done" and not (ruta and Path(ruta).exists()):
            it["status"], it["error"] = "pending", None
            reencoladas += 1
    if reencoladas:
        save_queue(q)
        info(f"{reencoladas} pistas «done» sin fichero en biblioteca: re-encoladas")
    pending = [it for it in q["items"] if it["status"] == "pending"]
    if not pending:
        ok("La cola está vacía. Añade enlaces con: ytd <URL>")
        return
    total = len(pending)
    print(f"\n{c(BOLD, str(total))} canciones por descargar\n")
    hechos = 0
    for idx, item in enumerate(pending, 1):
        vid = item.get("vid")
        if vid and vid in lib and Path(lib[vid]["path"]).exists():
            item["status"] = "done"
            save_queue(q)
            ok(f"[{idx}/{total}] «{lib[vid]['title']}» ya está en tu biblioteca")
            continue
        nombre = item.get("title") or item["url"]
        print(f"\n{c(BOLD, f'[{idx}/{total}]')} {c(CYAN, nombre)}")
        try:
            dest, meta2 = process_one(cfg, cache, item, yes=yes)
            if dest is None:      # el usuario eligió omitir
                continue
        except KeyboardInterrupt:
            warn("Interrumpido. La cola queda a salvo; continúa con 'ytd dl'")
            _write_json(CACHE_FILE, cache)
            save_queue(q)
            sys.exit(130)
        except Exception as e:
            item["status"] = "failed"
            item["error"] = str(e)
            save_queue(q)
            print(c(RED, f"  ✖ Falló: {e}  (puedes reintentar con 'ytd retry {q['items'].index(item) + 1}')"))
            continue
        if vid:
            lib[vid] = {"path": str(dest), "title": meta2["title"]}
            _write_json(LIBRARY_FILE, lib)
        item["status"], item["error"] = "done", None
        _write_json(CACHE_FILE, cache)
        save_queue(q)  # guardo tras cada éxito: Ctrl+C no pierde progreso
        ok(f"[{idx}/{total}] → {c(GREEN, dest)}")
        hechos += 1
    _write_json(CACHE_FILE, cache)
    print(f"\n{c(GREEN, BOLD)}✔ {hechos} nuevas canciones en {cfg.music_dir}{c(RESET, '')}")


# ----------------------------------------------------------------- fix ------
def _load_tags(path: Path):
    try:
        from mutagen.id3 import ID3
        return ID3(str(path))
    except Exception:
        return None


def _textframe(tags, name: str) -> str:
    if tags is None:
        return ""
    vals = tags.getall(name)
    if not vals:
        return ""
    try:
        return str(vals[0].text[0]) if vals[0].text else ""
    except Exception:
        return ""


def _update_library_path(lib: dict, old: Path, new: Path) -> None:
    for vid, entrada in lib.items():
        if isinstance(entrada, dict) and entrada.get("path") == str(old):
            entrada["path"] = str(new)
            _write_json(LIBRARY_FILE, lib)
            return


def thumb_for_file(path: Path, cfg: Config) -> str | None:
    """Busca la URL del vídeo en library.json/queue para obtener su miniatura."""
    lib = load_library()
    vid = next((vid for vid, v in lib.items()
                if isinstance(v, dict) and v.get("path") == str(path)), None)
    if not vid:
        return None
    url = next((it.get("url") for it in load_queue()["items"]
                if it.get("vid") == vid), None)
    return extract_thumbnail(url, cfg) if url else None


def fix_auto_one(cfg: Config, cache: dict, path: Path, yes: bool = False) -> tuple[bool, str]:
    """Re-identifica un fichero ya descargado (huella → álbum) y lo reubica."""
    tags = _load_tags(path)
    titulo = _textframe(tags, "TIT2") or path.stem
    artista_old = _textframe(tags, "TPE1") or _textframe(tags, "TPE2")
    try:
        fpmeta = acoustid_lookup(path, cfg, cache)
    except AcoustIdInvalid:
        return False, "clave de AcoustID inválida"
    except Exception as e:
        fpmeta = None
        warn(f"Huella no disponible: {e}")
    if not (fpmeta and fpmeta.get("rid")):
        return False, "sin coincidencias acústicas"
    thumb = thumb_for_file(path, cfg)
    meta = enrich(fpmeta, thumb)
    if not meta["title"]:
        meta["title"] = titulo
    if not meta["artist"]:
        meta["artist"] = artista_old
    if not (meta["artist"] and meta["album"]):
        return False, "MusicBrainz no aportó álbum"
    decision = decide_meta(cfg, titulo, meta, fpmeta, yes)
    if decision is None:
        return False, "omitida"
    if decision is not meta and not decision.get("album"):
        info("  mantener etiquetas actuales (título de YouTube elegido)")
        return False, "sin cambios (se mantienen las etiquetas actuales)"
    meta = decision
    dest = final_path(cfg, meta, src=path)
    if dest != path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(dest))
        info(f"  movida → {dest}")
    tag_mp3(dest, meta)
    _update_library_path(load_library(), path, dest)
    return True, str(dest)


def fix_manual(cfg: Config, path: Path, fa) -> tuple[bool, str]:
    """Fija los metadatos a mano y reubica la pista (sin identificación)."""
    tags = _load_tags(path)
    meta = {
        "artist": fa.artista or _textframe(tags, "TPE1") or "Artista desconocido",
        "title": fa.titulo or _textframe(tags, "TIT2") or path.stem,
        "album": fa.album or _textframe(tags, "TALB"),
        "year": fa.anio or _textframe(tags, "TDRC").split("-")[0],
        "track": fa.pista,
        "cover": None,
    }
    if fa.cover:
        img = Path(fa.cover).expanduser()
        if not img.exists():
            return False, f"no encuentro la carátula {img}"
        meta["cover"] = img.read_bytes()
    elif tags:
        apics = tags.getall("APIC")
        if apics:
            meta["cover"] = apics[0].data  # conserva la carátula existente
    if not (meta["album"] or meta["artist"]):
        return False, "indica al menos --artista o --album"
    dest = final_path(cfg, meta, src=path)
    if dest != path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(dest))
        info(f"  movida → {dest}")
    tag_mp3(dest, meta)
    _update_library_path(load_library(), path, dest)
    return True, str(dest)


def cmd_fix(cfg: Config, fargs) -> None:
    cache = _read_json(CACHE_FILE, {})
    if fargs.archivo:
        p = Path(fargs.archivo).expanduser().resolve()
        if not p.exists():
            fail(f"No existe {p}")
        manual = bool(
            fargs.artista or fargs.album or fargs.titulo
            or fargs.pista is not None or fargs.anio or fargs.cover
        )
        resultado = fix_manual(cfg, p, fargs) if manual else fix_auto_one(cfg, cache, p, yes=bool(fargs.yes))
        _write_json(CACHE_FILE, cache)
        if resultado[0]:
            ok(f"Corregida: {resultado[1]}")
        else:
            warn(f"No se pudo: {resultado[1]}")
        return
    # sin fichero: re-identificar pistas de la biblioteca
    cfg.music_dir.mkdir(parents=True, exist_ok=True)
    if fargs.todo:
        # fuerza recálculo de huellas/candidatos con el selector actual
        CACHE_FILE.unlink(missing_ok=True)
        cache = {}
        candidatos = sorted(cfg.music_dir.rglob("*.mp3"))
        info(f"Re-identificando TODA la biblioteca ({len(candidatos)} pistas)...")
    else:
        candidatos = []
        for f in sorted(cfg.music_dir.rglob("*.mp3")):
            tags = _load_tags(f)
            if not tags or not tags.getall("TALB"):
                candidatos.append(f)
        if not candidatos:
            ok("No hay pistas sin álbum en tu biblioteca")
            return
        info(f"{len(candidatos)} pistas sin álbum; re-identificando (sin re-descargar)...")
    _write_json(CACHE_FILE, cache)
    hechas = 0
    for i, f in enumerate(candidatos, 1):
        print(f"\n{c(BOLD, f'[{i}/{len(candidatos)}]')} {f.relative_to(cfg.music_dir)}")
        try:
            bien, msg = fix_auto_one(cfg, cache, f, yes=bool(fargs.yes))
            _write_json(CACHE_FILE, cache)
        except Exception as e:
            bien, msg = False, str(e)
        if bien:
            hechas += 1
            ok(f"  {msg}")
        else:
            print(c(RED, "  ✖ ") + msg)
    print(f"\n{c(GREEN, f'✔ {hechas}/{len(candidatos)} pistas corregidas')}")


# ------------------------------------------------------ edición interactiva --
def _truncar(s: str, n: int = 60) -> str:
    s = str(s or "")
    return s if len(s) <= n else s[: n - 1] + "…"


BAR = "─" * 78


def _mb_search_recordings(artista: str, titulo: str, limit: int = 5) -> list[dict]:
    """Búsqueda de grabaciones en MusicBrainz (Web Search) por artista/título."""
    terminos = [f'recording:"{titulo}"']
    if artista:
        terminos.append(f'artist:"{artista}"')
    q = urllib.parse.quote(" AND ".join(terminos))
    url = (f"https://musicbrainz.org/ws/2/recording/?query={q}"
           f"&fmt=json&limit={limit}")
    try:
        data = _mb_get_retry(url) or {}
    except Exception as e:
        warn(f"Búsqueda MusicBrainz no disponible: {e}")
        return []
    candidatos = []
    for r in data.get("recordings") or []:
        cred = r.get("artist-credit") or []
        artista_found = (cred[0].get("artist") or {}).get("name") \
            if cred and isinstance(cred[0], dict) else ""
        candidatos.append({
            "rid": r.get("id"),
            "title": r.get("title") or "",
            "artist": artista_found,
        })
    return candidatos


def _meta_desde_recording(rid: str) -> dict | None:
    """Construye metadatos completos (álbum/pista/año/carátula) de una grabación MB."""
    rec = mb_recording_info(rid)
    if not rec:
        return None
    rel_id, pos, living = select_release(rec)
    credits = rec.get("artist-credit") or []
    artist = (credits[0].get("artist") or {}).get("name") if credits else ""
    album, year, cover = "", "", None
    if rel_id:
        cover = cover_art(rel_id)
        rel = next((r for r in rec["releases"] if r.get("id") == rel_id), None)
        if rel:
            album = rel.get("title") or ""
            year = (rel.get("date") or "")[:4]
    return {
        "artist": artist,
        "title": rec.get("title") or "",
        "album": album,
        "year": year,
        "track": pos,
        "cover": cover,
    }


def _apply_meta(cfg: Config, path: Path, meta: dict) -> Path:
    """Reubica + reetiqueta un fichero con los metadatos dados."""
    dest = final_path(cfg, meta, src=path)
    if dest != path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(dest))
    tag_mp3(dest, meta)
    _update_library_path(load_library(), path, dest)
    return dest


def _estado(meta: dict, fichero: str) -> None:
    print(f"  {c(DIM, BAR)}")
    tiene_cover = bool(meta.get("cover"))
    print(f"  {c(BOLD, 'Fichero')}: {c(DIM, fichero)}")
    print(f"    {c(BOLD, 'Artista')}: {_truncar(meta.get('artist'))}")
    print(f"    {c(BOLD, 'Título')}:  {_truncar(meta.get('title'))}")
    print(f"    {c(BOLD, 'Álbum')}:   {_truncar(meta.get('album'))}    "
          f"{c(BOLD, 'Pista')}: {meta.get('track') or '-'}    "
          f"{c(BOLD, 'Año')}: {meta.get('year') or '-'}    "
          f"{c(BOLD, 'Carátula')}: {'sí' if tiene_cover else 'no'}")
    print(f"  {c(DIM, BAR)}")


def cmd_edit(cfg: Config, pathstr: str, yes: bool = False) -> None:
    """Editor interactivo de metadatos con sugerencias MusicBrainz/huella."""
    if not pathstr:
        fail("Uso: ytd edit <archivo.mp3>")
    path = Path(pathstr).expanduser().resolve()
    if not path.exists():
        fail(f"No existe {path}")
    cache = _read_json(CACHE_FILE, {})
    tags = _load_tags(path)

    meta_actual = {
        "artist": _textframe(tags, "TPE1"),
        "title": _textframe(tags, "TIT2") or path.stem,
        "album": _textframe(tags, "TALB"),
        "year": _textframe(tags, "TDRC").split("-")[0],
        "track": int(_textframe(tags, "TRCK").split("/")[0] or 0) or None,
        "cover": (tags.getall("APIC")[0].data if tags.getall("APIC") else None),
    }
    fichero_rel = str(path.relative_to(cfg.music_dir)) \
        if path.is_relative_to(cfg.music_dir) else str(path)
    estampas: list[tuple[str, str, str]] = []  # (origen, rid, etiqueta legible)

    fpmeta = None
    try:
        fpmeta = acoustid_lookup(path, cfg, cache)
        _write_json(CACHE_FILE, cache)
    except Exception as e:
        warn(f"Huella acústica no disponible: {e}")
    if fpmeta and fpmeta.get("rid"):
        etiqueta = f"{_truncar(fpmeta.get('title'), 28)} — {_truncar(fpmeta.get('artist'), 25)}"
        estampas.append(("huella", fpmeta["rid"], etiqueta))
    for cand in _mb_search_recordings(meta_actual["artist"], meta_actual["title"]):
        if cand.get("rid") and not any(rid == cand["rid"] for _, rid, _ in estampas):
            etiqueta = f"{_truncar(cand.get('title'), 28)} — {_truncar(cand.get('artist'), 25)}"
            estampas.append(("búsqueda", cand["rid"], etiqueta))
    if not estampas and meta_actual["title"]:
        for cand in _mb_search_recordings("", meta_actual["title"]):
            if cand.get("rid"):
                estampas.append(("búsqueda", cand["rid"],
                                 f"{_truncar(cand.get('title'), 28)} — {_truncar(cand.get('artist'), 25)}"))
        if not estampas:
            print("  (sin sugerencias para estos datos)")

    # con --yes, aplica directamente la mejor sugerencia disponible
    if yes and estampas:
        meta_sug = _meta_desde_recording(estampas[0][1])
        if meta_sug and meta_sug["album"]:
            if not meta_sug.get("cover") and meta_actual.get("cover"):
                meta_sug["cover"] = meta_actual["cover"]
            dest = _apply_meta(cfg, path, meta_sug)
            ok(f"Sugerencia aplicada: {dest}")
            return

    while True:
        print()
        print(c(BOLD, BAR))
        print(c(BOLD, f"  Editor de metadatos — {fichero_rel}"))
        print(c(BOLD, BAR))
        _estado(meta_actual, fichero_rel)
        if estampas:
            print(f"  {c(BOLD, 'Sugerencias')} (por prioridad):")
            for i, (origen, rid, etiqueta) in enumerate(estampas, 1):
                print(f"    [{i}] {c(CYAN, etiqueta)}  {c(DIM, origen)}")
        try:
            eleccion = input(
                "  ¿Siguiente paso? [1-N] usar sugerencia · [e]ditar campos · "
                "[g]uardar como está · [q]salir: "
            ).strip().lower()
        except (EOFError, KeyboardInterrupt):
            return
        if eleccion in ("q", ""):
            return
        if eleccion == "e":
            meta_actual = _pedir_edicion_manual(meta_actual)
            continue
        if eleccion == "g":
            dest = _apply_meta(cfg, path, meta_actual)
            ok(f"Guardado: {dest}")
            return
        if eleccion.isdigit() and 1 <= int(eleccion) <= len(estampas):
            origen, rid, _ = estampas[int(eleccion) - 1]
            info(f"Recuperando «{origen}» ({rid}) desde MusicBrainz...")
            meta_sug = _meta_desde_recording(rid)
            if not meta_sug:
                warn("Sin datos en MusicBrainz para esa sugerencia")
                continue
            if not meta_sug.get("cover") and meta_actual.get("cover"):
                meta_sug["cover"] = meta_actual["cover"]  # conserva la actual
            _estado(meta_sug, fichero_rel)
            try:
                aplicar = input("  ¿Aplicar estos metadatos? [s]/n: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                return
            if aplicar in ("", "s", "y"):
                dest = _apply_meta(cfg, path, meta_sug)
                ok(f"Aplicado: {dest}")
                return
            continue
        warn("Opción no válida")


# ------------------------------------------------------------ verificación --
def _md5(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
def cmd_verify(cfg: Config, fargs) -> None:
    """Audita la biblioteca MP3: etiquetas, carátulas, nombres, librería y
    (con --red) coherencia de nº de pista/título contra MusicBrainz."""
    archivos = sorted(cfg.music_dir.rglob("*.mp3"))
    if not archivos:
        ok(f"No hay ningún MP3 en {cfg.music_dir}")
        return
    lib = load_library()
    rutas_lib = {v.get("path") for v in lib.values() if isinstance(v, dict)}
    problemas = []   # (fichero, [mensajes])
    limpios = 0
    print(f"Revisando {len(archivos)} ficheros en {cfg.music_dir}\n")
    for f in archivos:
        msgs = []
        tags = _load_tags(f)
        artista = _textframe(tags, "TPE1")
        titulo = _textframe(tags, "TIT2")
        album = _textframe(tags, "TALB")
        pista_txt = _textframe(tags, "TRCK").split("/")[0]
        if not tags:
            msgs.append("sin etiquetas ID3")
        else:
            if not artista:
                msgs.append("sin artista")
            if not titulo:
                msgs.append("sin título")
            if not album:
                msgs.append("sin álbum")
            elif f.parent.parent == cfg.music_dir:
                msgs.append("fuera de su carpeta de álbum")
            if tags and not tags.getall("APIC"):
                msgs.append("sin carátula")
        # nombre del fichero ≠ etiquetas (tolerando sufijos «(2)» de repetidos)
        if titulo:
            pref = f"{int(pista_txt):02d} - " if pista_txt.isdigit() else ""
            esperado = f"{pref}{sanitize(titulo)}.mp3"
            if f.name != esperado:
                hermano = f.parent / esperado
                if hermano.exists():
                    msgs.append("posible duplicado (ya existe el fichero canónico)")
                else:
                    msgs.append("nombre no corresponde con etiquetas")
        if str(f) not in rutas_lib:
            msgs.append("sin registro en library.json")
        if msgs:
            problemas.append((f, msgs))
            print(c(YELLOW, "  ✖ ") + str(f.relative_to(cfg.music_dir))
                  + c(DIM, f"  ({'; '.join(msgs)})"))
        else:
            limpios += 1
    # entradas de la librería que apuntan a ficheros inexistentes
    obsoletas = [
        (vid, v["path"]) for vid, v in lib.items()
        if isinstance(v, dict) and v.get("path") and not Path(v["path"]).exists()
    ]
    for vid, p in obsoletas:
        print(c(YELLOW, "  ✖ ") + f"library.json apunta a {c(DIM, p)} (no existe)")

    # --- con --red: contra MusicBrainz ---
    if fargs.red and problemas:
        grupos: dict[tuple, list] = {}
        for f, msgs in problemas:
            tags = _load_tags(f)
            clave = (_textframe(tags, "TPE1"), _textframe(tags, "TALB"))
            if clave[0] and clave[1]:
                grupos.setdefault(clave, []).append(f)
        info(f"Comprobando {len(grupos)} álbumes contra MusicBrainz (--red)...")
        for (artista, album), fichs in grupos.items():
            q = urllib.parse.quote(f'release:"{album}" AND artist:"{artista}"')
            try:
                hits = (_mb_get_retry(
                    f"https://musicbrainz.org/ws/2/release/?query={q}&fmt=json&limit=5"
                ) or {}).get("releases") or []
            except Exception as e:
                warn(f"No pude consultar MB para «{album}»: {e}")
                continue
            if not hits:
                warn(f"«{album}» de {artista} no está en MusicBrainz")
                continue
            try:
                rel = _mb_get_retry(
                    f"https://musicbrainz.org/ws/2/release/{hits[0]['id']}"
                    f"?fmt=json&inc=media+recordings"
                ) or {}
            except Exception as e:
                warn(f"No pude consultar el release de «{album}»: {e}")
                continue
            tr_list = [(tr.get("position"), tr.get("title"))
                       for med in rel.get("media", [])
                       for tr in med.get("tracks", [])]
            for f in fichs:
                tags = _load_tags(f)
                n = _textframe(tags, "TRCK").split("/")[0]
                titulo = _textframe(tags, "TIT2")
                nuevo = next((t for pos, t in tr_list if str(pos) == n), None)
                if nuevo is None:
                    problema_red = f"pista {n} no existe en «{album}» (MB)"
                elif _similarity(titulo, nuevo) < 0.5:
                    problema_red = f"pista {n}: etiqueta «{titulo}» vs MB «{nuevo}»"
                else:
                    continue
                rode = None
                for idx, (fl, ms) in enumerate(problemas):
                    if fl == f:
                        rode = idx
                        break
                if rode is None:
                    problemas.append((f, [problema_red]))
                    print(c(YELLOW, "  ✖ ") + str(f.relative_to(cfg.music_dir))
                          + c(DIM, f"  ({problema_red})"))
                else:
                    problemas[rode][1].append(problema_red)
                    print(c(YELLOW, "  ✖ ") + str(f.relative_to(cfg.music_dir))
                          + c(DIM, f"  ({problema_red})"))

    # --- resumen ---
    if not problemas:
        ok(f"Verificación: {limpios}/{len(archivos)} ficheros impecables")
        if obsoletas:
            subt = f"\n{c(YELLOW, str(len(obsoletas)))} entradas de library.json sin fichero"
            print(subt)
            if fargs.fix:
                for vid, p in obsoletas:
                    lib.pop(vid, None)
                _write_json(LIBRARY_FILE, lib)
                ok("Entradas obsoletas de library.json eliminadas")
            else:
                info("Corrección rápida:  ytd verify --fix")
        return
    print(f"\n{c(YELLOW, str(len(problemas)))} ficheros con incidencias, "
          f"{c(GREEN, str(limpios))} limpios")
    if fargs.fix:
        # 1) duplicados: borrar los idénticos (MD5), actualizar library.json
        quedan = []
        eliminados = 0
        for f, msgs in problemas:
            sindup = [m for m in msgs if not m.startswith("posible duplicado")]
            if len(sindup) != len(msgs):  # había un mensaje de duplicado
                tags = _load_tags(f)
                pista_txt = _textframe(tags, "TRCK").split("/")[0]
                pref = f"{int(pista_txt):02d} - " if pista_txt.isdigit() else ""
                canon = f.parent / f"{pref}{sanitize(_textframe(tags, 'TIT2'))}.mp3"
                try:
                    iguales = canon.exists() and _md5(f) == _md5(canon)
                except Exception:
                    iguales = False
                if iguales:
                    f.unlink()
                    for vid, v in lib.items():
                        if isinstance(v, dict) and v.get("path") == str(f):
                            lib[vid] = {"path": str(canon),
                                        "title": v.get("title", canon.stem)}
                    _write_json(LIBRARY_FILE, lib)
                    eliminados += 1
                    print(c(GREEN, f"  ✔ borrado duplicado idéntico: {f.name}"))
                else:
                    sindup.append("duplicado con audio distinto (revisa a mano)")
            if sindup:
                quedan.append((f, sindup))
        problemas = quedan
        if eliminados:
            print(f"{c(GREEN, f'  {eliminados} duplicados idénticos eliminados')}\n")
        # 2) re-identificación del resto
        if problemas:
            info("Aplicando corrección automática sobre los ficheros con incidencias...")
        cache = _read_json(CACHE_FILE, {})
        corregidos = 0
        for i, (f, _) in enumerate(problemas, 1):
            print(f"\n{c(BOLD, f'[{i}/{len(problemas)}]')} {f.relative_to(cfg.music_dir)}")
            try:
                bien, msg = fix_auto_one(cfg, cache, f, yes=bool(fargs.yes))
                _write_json(CACHE_FILE, cache)
            except Exception as e:
                bien, msg = False, str(e)
            if bien:
                corregidos += 1
                ok(f"  {msg}")
            else:
                print(c(RED, "  ✖ ") + msg)
        print(f"\n{c(GREEN, f'✔ {corregidos + eliminados}/{corregidos + eliminados + len(problemas)} incidencias resueltas')}")
    else:
        info("Corrección rápida sugerida:  ytd verify --fix  "
             "(o ytd fix archivo.mp3 para corregir a mano)")
def cmd_urls(cfg: Config, urls: list[str]) -> None:
    which_or_fail("yt-dlp", "sudo dnf install yt-dlp")
    q = load_queue()
    lib = load_library()
    nuevas = 0
    for url in urls:
        try:
            plname, entries = expand_url(url, cfg)
        except RuntimeError as e:
            fail(str(e))
        if plname:
            ok(f"Playlist «{plname}»: {len(entries)} pistas")
        for e in entries:
            if e.get("vid") and e["vid"] in lib and Path(lib[e["vid"]]["path"]).exists():
                ok(f"  ya en biblioteca: {e['title']}")
                continue
            if any(
                i.get("vid") == e.get("vid") and i["status"] == "pending"
                for i in q["items"]
            ):
                warn(f"  ya en cola: {e['title']}")
                continue
            q["items"].append({
                "uuid": uuid.uuid4().hex[:8],
                "vid": e.get("vid"),
                "url": e["url"],
                "title": e["title"],
                "status": "pending",
                "error": None,
                "playlist": plname,
            })
            nuevas += 1
    save_queue(q)
    if nuevas == 0:
        ok("Nada nuevo por descargar")
        return
    info(f"{nuevas} nuevas en la cola. Descargando...")
    process_queue(cfg)


def cmd_search(cfg: Config, query: str) -> None:
    if not query:
        fail("Uso: ytd search \"artista - álbum\"")
    info(f"Buscando en YouTube: {query}")
    try:
        data = ytdlp_json(f"ytsearch5:{query}", cfg, flat=True)
    except RuntimeError as e:
        fail(str(e))
    entries = [e for e in (data.get("entries") or []) if e]
    if not entries:
        warn("Sin resultados")
        return
    for i, e in enumerate(entries, 1):
        dur = e.get("duration") or 0
        mm, ss = divmod(int(dur), 60)
        print(f"  [{i}] {e.get('title') or e.get('id')}")
        canal = e.get("channel") or e.get("uploader") or ""
        print(f"      {c(DIM, f'{mm}:{ss:02d}   {canal}'.strip())}")
    try:
        eleccion = input("¿Cuál descargo? (1-5, vacío cancela): ").strip()
    except (EOFError, KeyboardInterrupt):
        eleccion = ""
    if not (eleccion.isdigit() and 1 <= int(eleccion) <= len(entries)):
        warn("Cancelado")
        return
    e = entries[int(eleccion) - 1]
    url = e.get("url") or (f"https://youtu.be/{e['id']}" if e.get("id") else e.get("webpage_url"))
    cmd_urls(cfg, [url])


def cmd_queue() -> None:
    items = load_queue()["items"]
    if not items:
        ok("Cola vacía. Pega enlaces con: ytd <URL>")
        return
    icons = {"pending": "◇", "done": "◆", "failed": "✖"}
    colors = {"pending": YELLOW, "done": GREEN, "failed": RED}
    label = {"pending": "pendiente", "done": "hecha", "failed": "fallida"}
    cuentas = {"pending": 0, "done": 0, "failed": 0}
    for i, it in enumerate(items, 1):
        s = it["status"]
        cuentas[s] = cuentas.get(s, 0) + 1
        info_extra = ""
        if s == "failed" and it.get("error"):
            info_extra = c(DIM, f"  ({it['error']})")
        if it.get("playlist"):
            info_extra += c(DIM, f"  «{it['playlist']}»")
        print(
            f"  {i:3d} {c(colors[s], icons[s])} "
            f"{c(DIM, label[s]):17} {it.get('title') or it['url']}{info_extra}"
        )
    print(
        f"\n  {c(YELLOW, str(cuentas['pending']))} pendientes · "
        f"{c(GREEN, str(cuentas['done']))} hechas · "
        f"{c(RED, str(cuentas['failed']))} fallidas"
    )


def cmd_retry(arg: str) -> None:
    items = load_queue()["items"]
    if arg == "failed":
        n = 0
        for it in items:
            if it["status"] == "failed":
                it["status"], it["error"] = "pending", None
                n += 1
        save_queue({"items": items})
        if n:
            ok(f"{n} pistas re-agendadas. Ejecuta 'ytd dl' para procesarlas")
        else:
            warn("No hay pistas fallidas")
        return
    if not arg.isdigit() or not (1 <= int(arg) <= len(items)):
        fail(f"Uso: ytd retry <número de la cola> | ytd retry failed")
    idx = int(arg) - 1
    items[idx]["status"], items[idx]["error"] = "pending", None
    q = {"items": items}
    save_queue(q)
    ok(f"#{arg} re-agendada: {items[idx].get('title') or items[idx]['url']}")
    info("Ejecuta 'ytd dl' para procesar la cola")


def cmd_clean() -> None:
    q = load_queue()
    antes = len(q["items"])
    q["items"] = [it for it in q["items"] if it["status"] != "done"]
    save_queue(q)
    ok(f"Eliminadas {antes - len(q['items'])} entradas hechas de la cola")


def cmd_config() -> None:
    cfg = load_config()
    print(f"Archivo de configuración: {c(BOLD, str(CONFIG_FILE))}")
    print(f"  music_dir = {cfg.music_dir}")
    print(f"  acoustid_api_key = {'•••' + cfg.api_key[-4:] if cfg.api_key else c(RED, '(vacía)')}")
    if not cfg.api_key:
        print("\n  Registra tu clave gratis en: https://acoustid.org/new-application")


# -------------------------------------------------------------------- CLI --
_SUBS = {"add", "dl", "queue", "retry", "search", "fix", "edit", "verify", "clean", "config", "doctor", "help"}


def main() -> None:
    ap = argparse.ArgumentParser(
        prog="ytd",
        description="Descarga música de YouTube en MP3 con identificación acústica",
        epilog=(
            "Ejemplos:\n"
            "  ytd https://youtu.be/AbCdEf12345\n"
            "  ytd https://www.youtube.com/playlist?list=PLxyz...\n"
            "  ytd search 'Daft Punk - Discovery'\n"
            "  ytd add <URL> | ytd dl | ytd queue | ytd retry failed | ytd doctor\n"
            "  ytd fix                       # re-identifica pistas sin álbum (sin re-descargar)\n"
            "  ytd fix file.mp3 --album 'Álbum' --pista 3 --artista 'Grupo'  # a mano\n"
            "  ytd verify --red              # audita la biblioteca contra MusicBrainz\n"
            "  ytd edit archivo.mp3          # editor interactivo con sugerencias\n"
            "Cualquier URL se encola y se descarga automáticamente.\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("args", nargs="*",
                    help="enlaces de YouTube o un subcomando (add/dl/queue/retry/search/fix/verify/clean/config/doctor)")
    ap.add_argument("--dl", action="store_true", help="solo procesar la cola pendiente")
    ap.add_argument("--yes", "-y", action="store_true",
                    help="aceptar sin preguntas (dl / fix / verify --fix)")
    ns, extra = ap.parse_known_args()
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    cfg = load_config()
    words = list(ns.args) + list(extra)
    if words and words[0] in _SUBS:
        sub = words[0]
        rest = words[1:]
        if sub == "doctor":
            doctor()
        elif sub == "queue":
            cmd_queue()
        elif sub == "clean":
            cmd_clean()
        elif sub == "config":
            cmd_config()
        elif sub == "dl":
            process_queue(cfg, yes=bool(ns.yes))
        elif sub == "add":
            if not rest:
                fail("Uso: ytd add <URL> [URL...]")
            cmd_urls(cfg, rest)
        elif sub == "edit":
            eap = argparse.ArgumentParser(prog="ytd edit")
            eap.add_argument("archivo", help="MP3 a editar")
            eap.add_argument("--yes", "-y", action="store_true",
                             help="aplicar directamente la mejor sugerencia")
            cmd_edit(cfg, eap.parse_args(rest).archivo,
                     yes=bool(eap.parse_args(rest).yes))
        elif sub == "verify":
            vap = argparse.ArgumentParser(prog="ytd verify")
            vap.add_argument("--red", action="store_true",
                             help="compara nº de pista/título contra MusicBrainz")
            vap.add_argument("--fix", action="store_true",
                             help="re-identifica los ficheros con incidencias")
            vap.add_argument("--yes", "-y", action="store_true",
                             help="aceptar la huella sin pedir confirmación")
            cmd_verify(cfg, vap.parse_args(rest))
        elif sub == "retry":
            if not rest:
                fail('Uso: ytd retry <n> | ytd retry failed')
            cmd_retry(rest[0])
        elif sub == "search":
            cmd_search(cfg, " ".join(rest))
        elif sub == "fix":
            fap = argparse.ArgumentParser(prog="ytd fix")
            fap.add_argument("archivo", nargs="?", default=None,
                             help="MP3 concreto (vacío: re-identifica las pistas sin álbum)")
            fap.add_argument("--todo", action="store_true",
                             help="re-identificar toda la biblioteca (ignora etiquetas actuales)")
            fap.add_argument("--artista", "--artist", dest="artista")
            fap.add_argument("--album", "--álbum", dest="album")
            fap.add_argument("--titulo", "--título", "--title", dest="titulo")
            fap.add_argument("--pista", "--track", dest="pista", type=int)
            fap.add_argument("--anio", "--año", "--year", dest="anio")
            fap.add_argument("--cover", help="imagen de la carátula (jpg/png)")
            fap.add_argument("--yes", "-y", action="store_true",
                             help="aceptar la huella sin pedir confirmación")
            cmd_fix(cfg, fap.parse_args(rest))
        elif sub == "help":
            ap.print_help()
        return
    if ns.dl:
        process_queue(cfg)
        return
    if words:
        cmd_urls(cfg, list(words))
        return
    if any(it["status"] == "pending" for it in load_queue()["items"]):
        warn("Tienes la cola pendiente, procesándola...")
        process_queue(cfg)
    else:
        ap.print_help()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()
        sys.exit(130)
    except BrokenPipeError:
        pass
