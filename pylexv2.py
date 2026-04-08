#!/usr/bin/env python3
"""
PyLex - A Plex Media Server clone written in Python
====================================================
v1.2 — Todas las funciones solicitadas
Run: python pylex.py
Then open: http://localhost:7777
"""

import os
import sys
import json
import html as html_mod
import mimetypes
import hashlib
import hmac
import secrets
import threading
import time
import re
import sqlite3
import socket
import ipaddress
import logging
from pathlib import Path
from datetime import datetime, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, unquote, quote

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger('pylex')

# ── Configuration ─────────────────────────────────────────────────────────────

PORT             = 7777
DB_PATH          = "pylex.db"
THUMB_DIR        = "thumbs"
CHUNK_SIZE       = 1024 * 1024
SESSION_DAYS     = 30
COOKIE_NAME      = "pylex_session"
AUTO_SCAN_HOURS  = 4

VIDEO_EXTENSIONS = {'.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.webm',
                    '.m4v', '.mpeg', '.mpg', '.ts', '.3gp', '.ogv'}
AUDIO_EXTENSIONS = {'.mp3', '.flac', '.ogg', '.wav', '.aac', '.m4a',
                    '.wma', '.opus', '.aiff', '.ape', '.wv'}
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp',
                    '.tiff', '.svg'}
MEDIA_EXTENSIONS = VIDEO_EXTENSIONS | AUDIO_EXTENSIONS | IMAGE_EXTENSIONS

# ── Database ───────────────────────────────────────────────────────────────────

def _migrate_db(conn):
    migrations = [
        "ALTER TABLE libraries ADD COLUMN created_by INTEGER",
        "ALTER TABLE libraries ADD COLUMN created_at TEXT DEFAULT CURRENT_TIMESTAMP",
        "ALTER TABLE media ADD COLUMN year INTEGER",
        "ALTER TABLE media ADD COLUMN last_played TEXT",
        "ALTER TABLE media ADD COLUMN duration REAL DEFAULT 0",
        "ALTER TABLE media ADD COLUMN genre TEXT",
        "ALTER TABLE media ADD COLUMN artist TEXT",
        "ALTER TABLE media ADD COLUMN album TEXT",
        "ALTER TABLE media ADD COLUMN track INTEGER",
        "ALTER TABLE media ADD COLUMN width INTEGER",
        "ALTER TABLE media ADD COLUMN height INTEGER",
        "ALTER TABLE media ADD COLUMN progress REAL DEFAULT 0",
        "ALTER TABLE media ADD COLUMN position REAL DEFAULT 0",
    ]
    added = []
    for sql in migrations:
        try:
            conn.execute(sql)
            col = sql.split('ADD COLUMN')[1].strip().split()[0]
            added.append(col)
        except sqlite3.OperationalError:
            pass
    if added:
        conn.commit()
        log.info("Migración BD: columnas añadidas → %s", ', '.join(added))
    return added

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    c = conn.cursor()
    c.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT    NOT NULL UNIQUE,
            display  TEXT    NOT NULL,
            pw_hash  TEXT    NOT NULL,
            pw_salt  TEXT    NOT NULL,
            role     TEXT    NOT NULL DEFAULT 'viewer',
            avatar   TEXT    DEFAULT '🎬',
            created_at TEXT  DEFAULT CURRENT_TIMESTAMP,
            last_login TEXT
        );
        CREATE TABLE IF NOT EXISTS sessions (
            token      TEXT PRIMARY KEY,
            user_id    INTEGER NOT NULL,
            created_at TEXT    DEFAULT CURRENT_TIMESTAMP,
            expires_at TEXT    NOT NULL,
            ip         TEXT,
            ua         TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS libraries (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT NOT NULL,
            path       TEXT NOT NULL,
            type       TEXT NOT NULL,
            created_by INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            last_scan  TEXT
        );
        CREATE TABLE IF NOT EXISTS media (
            id          TEXT PRIMARY KEY,
            library_id  INTEGER,
            title       TEXT NOT NULL,
            path        TEXT NOT NULL,
            type        TEXT NOT NULL,
            size        INTEGER DEFAULT 0,
            duration    REAL    DEFAULT 0,
            year        INTEGER,
            genre       TEXT,
            artist      TEXT,
            album       TEXT,
            track       INTEGER,
            width       INTEGER,
            height      INTEGER,
            added_at    TEXT DEFAULT CURRENT_TIMESTAMP,
            last_played TEXT,
            play_count  INTEGER DEFAULT 0,
            progress    REAL    DEFAULT 0,
            FOREIGN KEY (library_id) REFERENCES libraries(id)
        );
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
        CREATE TABLE IF NOT EXISTS activity_log (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            media_id   TEXT NOT NULL,
            action     TEXT NOT NULL DEFAULT 'play',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (media_id) REFERENCES media(id)
        );
        INSERT OR IGNORE INTO settings VALUES ('server_name', 'PyLex Media Server');
        INSERT OR IGNORE INTO settings VALUES ('auto_scan_hours', '4');
    """)
    conn.commit()
    _migrate_db(conn)
    conn.close()

def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def needs_setup() -> bool:
    db = get_db()
    row = db.execute("SELECT COUNT(*) as n FROM users WHERE role='admin'").fetchone()
    db.close()
    return row['n'] == 0

# ── Auth helpers ───────────────────────────────────────────────────────────────

def hash_password(password: str, salt: str = None) -> tuple:
    if salt is None:
        salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 200_000)
    return h.hex(), salt

def verify_password(password: str, pw_hash: str, pw_salt: str) -> bool:
    h, _ = hash_password(password, pw_salt)
    return hmac.compare_digest(h, pw_hash)

def create_session(user_id: int, ip: str, ua: str) -> str:
    token = secrets.token_hex(32)
    expires = (datetime.now() + timedelta(days=SESSION_DAYS)).isoformat()
    db = get_db()
    db.execute("INSERT INTO sessions(token,user_id,expires_at,ip,ua) VALUES(?,?,?,?,?)",
               (token, user_id, expires, ip, ua))
    db.execute("UPDATE users SET last_login=? WHERE id=?",
               (datetime.now().isoformat(), user_id))
    db.commit()
    db.close()
    return token

def get_session_user(token: str):
    if not token:
        return None
    db = get_db()
    row = db.execute("""
        SELECT u.id, u.username, u.display, u.role, u.avatar, u.last_login,
               s.expires_at
        FROM sessions s JOIN users u ON s.user_id = u.id
        WHERE s.token = ?
    """, (token,)).fetchone()
    db.close()
    if not row:
        return None
    if datetime.fromisoformat(row['expires_at']) < datetime.now():
        revoke_session(token)
        return None
    return dict(row)

def revoke_session(token: str):
    db = get_db()
    db.execute("DELETE FROM sessions WHERE token=?", (token,))
    db.commit()
    db.close()

def parse_cookie(header: str) -> dict:
    cookies = {}
    for part in (header or '').split(';'):
        if '=' in part:
            k, v = part.strip().split('=', 1)
            cookies[k.strip()] = v.strip()
    return cookies

def make_session_cookie(token: str, max_age: int = SESSION_DAYS * 86400) -> str:
    return f"{COOKIE_NAME}={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={max_age}"

def clear_session_cookie() -> str:
    return f"{COOKIE_NAME}=; Path=/; HttpOnly; Max-Age=0"

# ── Media helpers ──────────────────────────────────────────────────────────────

def make_id(path: str) -> str:
    return hashlib.md5(path.encode()).hexdigest()

def human_size(n: int) -> str:
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"

def get_mime(path: str) -> str:
    ext = Path(path).suffix.lower()
    mime_map = {
        '.mp4': 'video/mp4',  '.mkv': 'video/x-matroska',
        '.avi': 'video/x-msvideo', '.mov': 'video/quicktime',
        '.webm': 'video/webm', '.ogv': 'video/ogg',
        '.mp3': 'audio/mpeg', '.flac': 'audio/flac',
        '.ogg': 'audio/ogg',  '.wav': 'audio/wav',
        '.aac': 'audio/aac',  '.m4a': 'audio/mp4',
        '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
        '.png': 'image/png',  '.gif': 'image/gif',
        '.webp': 'image/webp', '.svg': 'image/svg+xml',
    }
    return mime_map.get(ext, mimetypes.guess_type(path)[0] or 'application/octet-stream')

def guess_media_type(path: str) -> str:
    ext = Path(path).suffix.lower()
    if ext in VIDEO_EXTENSIONS: return 'video'
    if ext in AUDIO_EXTENSIONS: return 'audio'
    if ext in IMAGE_EXTENSIONS: return 'image'
    return 'unknown'

# ── Thumbnails ─────────────────────────────────────────────────────────────────

def _thumb_path(media_id: str) -> str:
    os.makedirs(THUMB_DIR, exist_ok=True)
    return os.path.join(THUMB_DIR, f"{media_id}.jpg")

def _extract_audio_art(fpath: str):
    try:
        import mutagen
        ext = Path(fpath).suffix.lower()
        if ext == '.mp3':
            from mutagen.id3 import ID3, APIC
            tags = ID3(fpath)
            for tag in tags.values():
                if isinstance(tag, APIC):
                    return tag.data
        elif ext in ('.m4a', '.aac', '.mp4'):
            from mutagen.mp4 import MP4
            tags = MP4(fpath)
            if 'covr' in tags and tags['covr']:
                return bytes(tags['covr'][0])
        elif ext == '.flac':
            from mutagen.flac import FLAC
            tags = FLAC(fpath)
            if tags.pictures:
                return tags.pictures[0].data
        elif ext == '.ogg':
            import base64
            import struct
            from mutagen.oggvorbis import OggVorbis
            tags = OggVorbis(fpath)
            if 'metadata_block_picture' in tags:
                raw = base64.b64decode(tags['metadata_block_picture'][0])
                pic_type = struct.unpack('>I', raw[:4])[0]
                mime_len = struct.unpack('>I', raw[4:8])[0]
                offset = 8 + mime_len
                desc_len = struct.unpack('>I', raw[offset:offset+4])[0]
                offset += 4 + desc_len + 16
                data_len = struct.unpack('>I', raw[offset:offset+4])[0]
                return raw[offset+4:offset+4+data_len]
    except Exception:
        pass
    return None

def _extract_video_frame(fpath: str, out: str) -> bool:
    import subprocess
    for cmd in (['ffmpeg'], ['ffmpeg.exe']):
        try:
            r = subprocess.run(
                cmd + ['-y', '-ss', '00:00:05', '-i', fpath,
                       '-vframes', '1', '-vf', 'scale=320:-2',
                       '-q:v', '5', out],
                capture_output=True, timeout=20
            )
            if os.path.isfile(out) and os.path.getsize(out) > 100:
                return True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
    return False

def get_or_make_thumb(media_id: str, fpath: str, mtype: str):
    if mtype == 'image':
        return fpath
    out = _thumb_path(media_id)
    if os.path.isfile(out) and os.path.getsize(out) > 100:
        return out
    if mtype == 'audio':
        art = _extract_audio_art(fpath)
        if art:
            with open(out, 'wb') as f:
                f.write(art)
            return out
    elif mtype == 'video':
        if _extract_video_frame(fpath, out):
            return out
    return None

def clean_title(name: str) -> str:
    name = re.sub(r'\.(mp4|mkv|avi|mov|wmv|flv|webm|mp3|flac|ogg|wav)$', '', name, flags=re.I)
    name = re.sub(r'[\._]', ' ', name)
    name = re.sub(r'\b(19[0-9]{2}|20[0-3][0-9])\b.*', '', name)
    name = re.sub(r'\b(1080p|720p|480p|4K|HDR|BluRay|WEB|HEVC|x264|x265|AAC|DTS)\b.*', '',
                  name, flags=re.I)
    return name.strip().title() or name

def extract_year(name: str):
    m = re.search(r'\b(19[0-9]{2}|20[0-3][0-9])\b', name)
    return int(m.group(1)) if m else None

def read_audio_tags(fpath: str) -> dict:
    """Lee metadatos de audio con mutagen. Devuelve dict con artist/album/track/year/genre."""
    result = {}
    try:
        import mutagen
        from mutagen import File as MFile
        f = MFile(fpath, easy=True)
        if f is None:
            return result
        def _first(key):
            v = f.get(key)
            return v[0] if v else None
        result['artist'] = _first('artist') or _first('albumartist')
        result['album']  = _first('album')
        result['genre']  = _first('genre')
        t = _first('tracknumber')
        if t:
            try:
                result['track'] = int(str(t).split('/')[0])
            except (ValueError, TypeError):
                pass
        d = _first('date') or _first('year')
        if d:
            try:
                result['year'] = int(str(d)[:4])
            except (ValueError, TypeError):
                pass
        if hasattr(f, 'info') and hasattr(f.info, 'length'):
            result['duration'] = f.info.length
    except Exception:
        pass
    return result

def scan_library(library_id: int, path: str, lib_type: str) -> int:
    count = 0
    try:
        conn = sqlite3.connect(DB_PATH, timeout=15)
        conn.execute("PRAGMA journal_mode=WAL")
        c = conn.cursor()
        for root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            for fname in files:
                if Path(fname).suffix.lower() not in MEDIA_EXTENSIONS:
                    continue
                fpath = os.path.join(root, fname)
                fid   = make_id(fpath)
                try:
                    stat = os.stat(fpath)
                except OSError:
                    continue

                # Read audio tags if audio file
                tags = {}
                if lib_type == 'music' or guess_media_type(fpath) == 'audio':
                    tags = read_audio_tags(fpath)

                c.execute("""
                    INSERT OR IGNORE INTO media
                    (id, library_id, title, path, type, size, year, artist, album,
                     track, genre, duration, added_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (fid, library_id,
                      clean_title(Path(fname).stem), fpath,
                      guess_media_type(fpath), stat.st_size,
                      tags.get('year') or extract_year(fname),
                      tags.get('artist'), tags.get('album'),
                      tags.get('track'), tags.get('genre'),
                      tags.get('duration', 0),
                      datetime.now().isoformat()))
                c.execute("UPDATE media SET size=? WHERE id=? AND size!=?",
                          (stat.st_size, fid, stat.st_size))
                count += 1
        c.execute("UPDATE libraries SET last_scan=? WHERE id=?",
                  (datetime.now().isoformat(), library_id))
        conn.commit()
        conn.close()
        log.info("Biblioteca %d escaneada: %d archivos encontrados", library_id, count)
    except Exception as e:
        log.error("Error escaneando biblioteca %d: %s", library_id, e)
    return count

# ══════════════════════════════════════════════════════════════════════════════
# HTML / CSS base
# ══════════════════════════════════════════════════════════════════════════════

BASE_CSS = """
*{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#1a1a1f;--bg2:#222228;--bg3:#2a2a32;
  --card:#252530;--card-h:#2e2e3c;
  --border:#333344;
  --accent:#e5a00d;--accent2:#cc8800;
  --text:#e8e8f0;--text2:#9898b0;--text3:#5a5a72;
  --green:#2ecc71;--red:#e74c3c;--blue:#3498db;--orange:#e67e22;
  --r:8px;--rl:14px;
  --shadow:0 4px 24px rgba(0,0,0,.5);
  --t:.2s ease;
}
html,body{height:100%;background:var(--bg);color:var(--text);
  font-family:'Barlow',sans-serif;font-size:15px;line-height:1.5}
a{color:inherit;text-decoration:none}
button{cursor:pointer;border:none;font-family:'Barlow',sans-serif}

.app{display:flex;min-height:100vh}
.sidebar{width:220px;min-height:100vh;background:var(--bg2);
  border-right:1px solid var(--border);display:flex;flex-direction:column;
  flex-shrink:0;position:sticky;top:0;height:100vh;overflow-y:auto}
.main{flex:1;display:flex;flex-direction:column;min-height:100vh;overflow:hidden}

.logo{padding:22px 20px 18px;border-bottom:1px solid var(--border);
  display:flex;align-items:center;gap:10px}
.logo-icon{width:36px;height:36px;background:var(--accent);border-radius:8px;
  display:flex;align-items:center;justify-content:center;font-size:18px;
  font-family:'Barlow Condensed',sans-serif;font-weight:700;color:#1a1a1f}
.logo-text{font-family:'Barlow Condensed',sans-serif;font-size:22px;font-weight:700;
  letter-spacing:.5px}
.logo-sub{font-size:10px;color:var(--text3)}
.nav-section{padding:12px 0 4px}
.nav-label{padding:0 16px 6px;font-size:10px;font-weight:600;
  letter-spacing:1.5px;text-transform:uppercase;color:var(--text3)}
.nav-item{display:flex;align-items:center;gap:10px;padding:9px 16px;
  transition:var(--t);font-size:14px;font-weight:500;color:var(--text2);position:relative}
.nav-item:hover{background:var(--bg3);color:var(--text)}
.nav-item.active{background:rgba(229,160,13,.12);color:var(--accent)}
.nav-item.active::before{content:'';position:absolute;left:0;top:0;bottom:0;
  width:3px;background:var(--accent);border-radius:0 2px 2px 0}
.nav-icon{width:18px;text-align:center;font-size:16px}
.sidebar-footer{margin-top:auto;padding:16px;border-top:1px solid var(--border)}
.server-status{display:flex;align-items:center;gap:8px;font-size:12px;color:var(--text2)}
.status-dot{width:8px;height:8px;background:var(--green);border-radius:50%;
  box-shadow:0 0 0 2px rgba(46,204,113,.2);animation:pulse 2s infinite}
@keyframes pulse{0%,100%{box-shadow:0 0 0 2px rgba(46,204,113,.2)}
  50%{box-shadow:0 0 0 5px rgba(46,204,113,0)}}

.user-chip{display:flex;align-items:center;gap:8px;padding:10px 12px;
  background:var(--bg3);border-radius:var(--r);margin-bottom:10px;cursor:pointer;
  border:1px solid var(--border);transition:var(--t)}
.user-chip:hover{border-color:var(--accent)}
.user-avatar{width:30px;height:30px;background:var(--bg2);border-radius:50%;
  display:flex;align-items:center;justify-content:center;font-size:16px;flex-shrink:0}
.user-name{font-size:13px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.user-role{font-size:10px;color:var(--text3);font-weight:500;text-transform:uppercase;letter-spacing:.5px}
.role-badge-admin{color:var(--accent)}
.role-badge-viewer{color:var(--blue)}

.topbar{background:var(--bg2);border-bottom:1px solid var(--border);
  padding:12px 24px;display:flex;align-items:center;gap:16px;
  position:sticky;top:0;z-index:100}
.page-title{font-family:'Barlow Condensed',sans-serif;font-size:22px;font-weight:700;letter-spacing:.3px}
.topbar-actions{margin-left:auto;display:flex;gap:10px;align-items:center}

.btn{padding:8px 16px;border-radius:var(--r);font-size:13px;font-weight:600;
  transition:var(--t);display:inline-flex;align-items:center;gap:6px}
.btn-primary{background:var(--accent);color:#1a1a1f}
.btn-primary:hover{background:var(--accent2)}
.btn-ghost{background:var(--bg3);color:var(--text2)}
.btn-ghost:hover{background:var(--border);color:var(--text)}
.btn-danger{background:var(--red);color:#fff}
.btn-danger:hover{opacity:.85}
.btn-sm{padding:5px 12px;font-size:12px}
.btn-icon{padding:7px;border-radius:var(--r);background:var(--bg3);color:var(--text2);
  display:inline-flex;align-items:center;justify-content:center;font-size:15px;transition:var(--t)}
.btn-icon:hover{background:var(--border);color:var(--text)}

.search-wrap{position:relative;flex:1;max-width:400px}
.search-input{width:100%;background:var(--bg3);border:1px solid var(--border);
  border-radius:var(--r);padding:8px 12px 8px 36px;color:var(--text);
  font-size:14px;font-family:'Barlow',sans-serif;transition:var(--t)}
.search-input:focus{outline:none;border-color:var(--accent);background:var(--card)}
.search-icon{position:absolute;left:10px;top:50%;transform:translateY(-50%);
  color:var(--text3);font-size:15px;pointer-events:none}

.content{flex:1;padding:24px}

.stats-bar{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
  gap:16px;margin-bottom:28px}
.stat-card{background:var(--card);border:1px solid var(--border);
  border-radius:var(--rl);padding:18px 20px}
.stat-value{font-family:'Barlow Condensed',sans-serif;font-size:32px;font-weight:700;
  color:var(--accent)}
.stat-label{font-size:12px;color:var(--text2);margin-top:2px;font-weight:500}

.section-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:16px}
.section-title{font-family:'Barlow Condensed',sans-serif;font-size:18px;font-weight:700;letter-spacing:.3px}
.media-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));
  gap:16px;margin-bottom:32px}
.media-card{background:var(--card);border-radius:var(--rl);overflow:hidden;
  border:1px solid var(--border);transition:var(--t);cursor:pointer;position:relative}
.media-card:hover{transform:translateY(-4px);box-shadow:var(--shadow);
  border-color:var(--accent);background:var(--card-h)}
.media-thumb{width:100%;aspect-ratio:2/3;background:var(--bg3);
  display:flex;align-items:center;justify-content:center;position:relative;overflow:hidden}
.media-thumb.land{aspect-ratio:16/9}
.media-thumb-icon{font-size:40px;opacity:.25;z-index:0}
.thumb-img{position:absolute;inset:0;width:100%;height:100%;
  object-fit:cover;object-position:center;display:block;z-index:1}
.media-type-badge{position:absolute;top:8px;right:8px;background:rgba(0,0,0,.7);
  border-radius:4px;padding:2px 6px;font-size:10px;font-weight:600;
  letter-spacing:.5px;text-transform:uppercase;z-index:2}
.badge-video{color:#3498db}.badge-audio{color:#2ecc71}.badge-image{color:#e67e22}
.progress-bar{position:absolute;bottom:0;left:0;right:0;height:3px;background:rgba(255,255,255,.1)}
.progress-fill{height:100%;background:var(--accent)}
.media-info{padding:10px 12px}
.media-title{font-size:13px;font-weight:600;white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis;margin-bottom:3px}
.media-meta{font-size:11px;color:var(--text2)}

.lib-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:16px}
.lib-card{background:var(--card);border:1px solid var(--border);border-radius:var(--rl);padding:20px}
.lib-icon{font-size:28px;margin-bottom:12px}
.lib-name{font-size:16px;font-weight:700;margin-bottom:4px}
.lib-path{font-size:11px;color:var(--text3);word-break:break-all;margin-bottom:12px}
.lib-stats{display:flex;gap:16px;margin-bottom:16px}
.lib-stat{font-size:12px;color:var(--text2)}
.lib-stat strong{color:var(--text);font-weight:600}
.lib-footer{display:flex;gap:8px;border-top:1px solid var(--border);padding-top:14px}

.lib-home-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:16px;margin-bottom:8px}
.lib-home-card{background:var(--card);border:1px solid var(--border);border-radius:var(--rl);
  padding:24px 20px;transition:var(--t);display:block}
.lib-home-card:hover{transform:translateY(-3px);box-shadow:var(--shadow);
  border-color:var(--accent);background:var(--card-h)}
.lib-home-icon{font-size:36px;margin-bottom:12px}
.lib-home-name{font-size:16px;font-weight:700;margin-bottom:6px;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.lib-home-meta{font-size:12px;color:var(--accent);font-weight:600;margin-bottom:3px}
.lib-home-scan{font-size:11px;color:var(--text3)}

.table{width:100%;border-collapse:collapse}
.table th,.table td{padding:11px 14px;text-align:left;border-bottom:1px solid var(--border)}
.table th{font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.8px;color:var(--text3);background:var(--bg3)}
.table tr:hover td{background:var(--card-h)}
.table td{font-size:13px}
.pill{display:inline-block;padding:2px 8px;border-radius:20px;font-size:11px;font-weight:600}
.pill-admin{background:rgba(229,160,13,.15);color:var(--accent)}
.pill-viewer{background:rgba(52,152,219,.15);color:var(--blue)}

.video-container{width:100%;max-width:1100px;margin:0 auto}
.video-player{width:100%;background:#000;border-radius:var(--rl);overflow:hidden;box-shadow:var(--shadow)}
.video-player video{width:100%;display:block;max-height:70vh}
.audio-wrapper{border-radius:var(--rl);overflow:hidden;box-shadow:var(--shadow)}
.audio-player{background:linear-gradient(135deg,#1e1e2e 0%,#2a2040 50%,#1a2030 100%);
  padding:48px 40px 36px;display:flex;flex-direction:column;align-items:center;gap:24px;
  min-height:320px;justify-content:center}
.audio-art{width:140px;height:140px;background:rgba(229,160,13,.15);border:2px solid rgba(229,160,13,.3);
  border-radius:50%;display:flex;align-items:center;justify-content:center;
  font-size:64px;box-shadow:0 0 40px rgba(229,160,13,.2);
  animation:spin 12s linear infinite}
.audio-art-img{width:160px;height:160px;border-radius:50%;object-fit:cover;
  border:3px solid rgba(229,160,13,.4);box-shadow:0 0 40px rgba(229,160,13,.25);
  animation:spin 12s linear infinite}
.audio-art-wrap{display:flex;align-items:center;justify-content:center}
@keyframes spin{from{transform:rotate(0deg)}to{transform:rotate(360deg)}}
.audio-info{text-align:center}
.audio-title{font-family:'Barlow Condensed',sans-serif;font-size:26px;font-weight:700;
  color:var(--text);margin-bottom:6px;line-height:1.2}
.audio-artist{font-size:15px;color:var(--accent);font-weight:600;margin-bottom:4px}
.audio-album{font-size:13px;color:var(--text2)}
.audio-ctrl{width:100%;max-width:680px;accent-color:var(--accent);
  height:48px;border-radius:8px;background:rgba(255,255,255,.05)}
.back-btn{display:inline-flex;align-items:center;gap:6px;color:var(--text2);
  font-size:14px;font-weight:600;margin-bottom:16px;padding:6px 10px;
  border-radius:var(--r);transition:var(--t)}
.back-btn:hover{color:var(--text);background:var(--bg3)}
.media-detail{background:var(--card);border:1px solid var(--border);
  border-radius:var(--rl);padding:24px;margin-top:20px}
.media-detail h2{font-family:'Barlow Condensed',sans-serif;font-size:28px;font-weight:700;margin-bottom:8px}
.tag{display:inline-block;background:var(--bg3);border-radius:4px;
  padding:3px 8px;font-size:11px;font-weight:600;color:var(--text2);margin-right:6px}

.profile-card{background:var(--card);border:1px solid var(--border);
  border-radius:var(--rl);padding:28px;max-width:520px}
.profile-avatar{font-size:56px;margin-bottom:16px}
.profile-name{font-family:'Barlow Condensed',sans-serif;font-size:28px;font-weight:700}
.profile-meta{font-size:13px;color:var(--text2);margin-top:4px}

.form-group{margin-bottom:16px}
.form-label{font-size:12px;font-weight:600;color:var(--text2);
  display:block;margin-bottom:6px;letter-spacing:.5px;text-transform:uppercase}
.form-input{width:100%;background:var(--bg3);border:1px solid var(--border);
  border-radius:var(--r);padding:10px 12px;color:var(--text);
  font-size:14px;font-family:'Barlow',sans-serif;transition:var(--t)}
.form-input:focus{outline:none;border-color:var(--accent)}
.form-select{width:100%;background:var(--bg3);border:1px solid var(--border);
  border-radius:var(--r);padding:10px 12px;color:var(--text);
  font-size:14px;font-family:'Barlow',sans-serif}
.form-hint{font-size:11px;color:var(--text3);margin-top:4px}
.form-error{color:var(--red);font-size:13px;margin-top:6px}
.modal-footer{display:flex;gap:10px;justify-content:flex-end;margin-top:24px}
.section-card{background:var(--card);border:1px solid var(--border);
  border-radius:var(--rl);padding:24px;margin-bottom:20px}
.section-card-title{font-size:15px;font-weight:700;margin-bottom:18px;
  padding-bottom:12px;border-bottom:1px solid var(--border)}

.modal-overlay{position:fixed;inset:0;background:rgba(0,0,0,.8);
  display:flex;align-items:center;justify-content:center;
  z-index:500;backdrop-filter:blur(4px)}
.modal{background:var(--bg2);border:1px solid var(--border);
  border-radius:var(--rl);padding:28px;width:100%;max-width:520px;
  box-shadow:0 24px 80px rgba(0,0,0,.6)}
.modal-title{font-family:'Barlow Condensed',sans-serif;font-size:22px;font-weight:700;margin-bottom:20px}

.divider{border:none;border-top:1px solid var(--border);margin:20px 0}

.empty-state{text-align:center;padding:80px 20px}
.empty-icon{font-size:64px;opacity:.2;margin-bottom:16px}
.empty-title{font-size:20px;font-weight:700;margin-bottom:8px}
.empty-text{font-size:14px;color:var(--text2);margin-bottom:24px}

.toast-container{position:fixed;bottom:20px;right:20px;
  display:flex;flex-direction:column;gap:8px;z-index:600}
.toast{background:var(--bg2);border:1px solid var(--border);border-radius:var(--r);
  padding:12px 16px;font-size:13px;box-shadow:var(--shadow);
  display:flex;align-items:center;gap:10px;animation:slideIn .3s ease;min-width:220px}
.toast-success{border-left:3px solid var(--green)}
.toast-error{border-left:3px solid var(--red)}
.toast-info{border-left:3px solid var(--blue)}
@keyframes slideIn{from{transform:translateX(120%);opacity:0}to{transform:translateX(0);opacity:1}}

::-webkit-scrollbar{width:5px;height:5px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:var(--border);border-radius:3px}
::-webkit-scrollbar-thumb:hover{background:var(--text3)}

.mobile-nav{display:none;position:fixed;bottom:0;left:0;right:0;
  background:var(--bg2);border-top:1px solid var(--border);
  z-index:300;justify-content:space-around;align-items:stretch;
  padding:6px 0;padding-bottom:calc(6px + env(safe-area-inset-bottom))}
.mob-nav-item{display:flex;flex-direction:column;align-items:center;justify-content:center;
  gap:2px;font-size:10px;font-weight:600;color:var(--text2);
  padding:4px 10px;border-radius:var(--r);min-width:52px;transition:var(--t)}
.mob-nav-item .mob-icon{font-size:20px;line-height:1}
.mob-nav-item.active{color:var(--accent)}
.mob-nav-item:hover{color:var(--text)}

.player-nav{display:flex;justify-content:space-between;align-items:center;
  margin-top:16px;gap:10px}
.player-nav-btn{display:inline-flex;align-items:center;gap:8px;
  padding:10px 20px;border-radius:var(--r);
  background:rgba(229,160,13,.15);
  border:1.5px solid var(--accent);
  color:var(--text);font-size:13px;
  font-weight:600;transition:var(--t);max-width:45%;letter-spacing:.3px}
.player-nav-btn:hover{background:var(--accent);color:#1a1a1f;
  box-shadow:0 0 16px rgba(229,160,13,.35)}
.player-nav-btn.disabled{opacity:.2;pointer-events:none;
  border-color:var(--border);color:var(--text3);background:transparent}
.player-nav-btn span.nav-label{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
  color:#e8e8f0;font-weight:600}
.player-nav-btn:hover span.nav-label{color:#1a1a1f}

.queue-panel{background:var(--card);border:1px solid var(--border);border-radius:var(--rl);
  padding:16px;margin-top:16px}
.queue-title{font-size:13px;font-weight:700;color:var(--text2);margin-bottom:10px}
.queue-list{display:flex;flex-direction:column;gap:2px;max-height:240px;overflow-y:auto}
.queue-item{display:flex;align-items:center;gap:10px;padding:7px 10px;border-radius:var(--r);
  font-size:13px;color:var(--text2);transition:var(--t);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.queue-item:hover{background:var(--bg3);color:var(--text)}
.queue-icon{flex-shrink:0}

.slideshow-controls{display:flex;align-items:center;gap:10px;margin-top:10px;padding:8px 0;flex-wrap:wrap}

/* List view */
.media-list{display:flex;flex-direction:column;gap:6px;margin-bottom:32px}
.list-item{background:var(--card);border:1px solid var(--border);border-radius:var(--r);
  display:flex;align-items:center;gap:14px;padding:10px 14px;transition:var(--t);cursor:pointer}
.list-item:hover{border-color:var(--accent);background:var(--card-h);transform:translateX(3px)}
.list-thumb{width:44px;height:44px;border-radius:6px;flex-shrink:0;background:var(--bg3);
  display:flex;align-items:center;justify-content:center;font-size:20px;overflow:hidden;position:relative}
.list-thumb img{width:100%;height:100%;object-fit:cover;position:absolute;inset:0;border-radius:6px}
.list-info{flex:1;min-width:0}
.list-title{font-size:14px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.list-meta{font-size:12px;color:var(--text2);margin-top:2px}
.list-prog{height:3px;background:var(--bg3);border-radius:2px;margin-top:5px;width:120px}
.list-prog-fill{height:3px;background:var(--accent);border-radius:2px}
.list-size{font-size:12px;color:var(--text3);flex-shrink:0;white-space:nowrap}
.list-track{font-size:12px;color:var(--text3);width:28px;text-align:right;flex-shrink:0}

/* Sort + view controls */
.lib-controls{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:20px}
.sort-btn{padding:5px 12px;border-radius:var(--r);background:var(--bg3);border:1px solid var(--border);
  font-size:12px;font-weight:600;color:var(--text2);transition:var(--t);cursor:pointer;text-decoration:none;display:inline-block}
.sort-btn:hover,.sort-btn.active{background:rgba(229,160,13,.15);border-color:var(--accent);color:var(--accent)}
.view-btn{padding:6px 10px;border-radius:var(--r);background:var(--bg3);border:1px solid var(--border);
  font-size:14px;color:var(--text2);transition:var(--t);cursor:pointer;text-decoration:none;display:inline-block}
.view-btn.active{background:rgba(229,160,13,.15);border-color:var(--accent)}
.sep{color:var(--border);font-size:18px;padding:0 2px}

/* Group headers */
.group-header{font-family:'Barlow Condensed',sans-serif;font-size:19px;font-weight:700;
  color:var(--accent);margin:28px 0 10px;padding-bottom:6px;border-bottom:1px solid var(--border)}

/* Artist / album cards */
.artist-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:16px;margin-bottom:32px}
.artist-card{background:var(--card);border:1px solid var(--border);border-radius:var(--rl);
  padding:20px 16px;text-align:center;cursor:pointer;transition:var(--t)}
.artist-card:hover{border-color:var(--accent);transform:translateY(-3px);background:var(--card-h)}
.artist-avatar{font-size:40px;margin-bottom:10px}
.artist-name{font-size:14px;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.artist-meta{font-size:11px;color:var(--text2);margin-top:3px}
.album-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:16px;margin-bottom:32px}
.album-card{background:var(--card);border:1px solid var(--border);border-radius:var(--rl);
  overflow:hidden;cursor:pointer;transition:var(--t)}
.album-card:hover{border-color:var(--accent);transform:translateY(-3px)}
.album-cover{width:100%;aspect-ratio:1;background:var(--bg3);
  display:flex;align-items:center;justify-content:center;font-size:48px;
  position:relative;overflow:hidden}
.album-cover img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover}
.album-info{padding:10px 12px}
.album-name{font-size:13px;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.album-artist{font-size:11px;color:var(--accent);margin-top:2px}
.album-cnt{font-size:11px;color:var(--text3);margin-top:1px}

/* Continue watching */
.continue-grid{display:flex;gap:12px;overflow-x:auto;padding-bottom:8px;margin-bottom:28px}
.continue-card{flex-shrink:0;width:160px;background:var(--card);border:1px solid var(--border);
  border-radius:var(--rl);overflow:hidden;cursor:pointer;transition:var(--t)}
.continue-card:hover{border-color:var(--accent);transform:translateY(-2px)}
.continue-thumb{width:160px;height:90px;background:var(--bg3);display:flex;
  align-items:center;justify-content:center;font-size:28px;position:relative;overflow:hidden}
.continue-thumb img{width:100%;height:100%;object-fit:cover;position:absolute;inset:0}
.continue-bar{position:absolute;bottom:0;left:0;right:0;height:3px;background:rgba(255,255,255,.1)}
.continue-fill{height:3px;background:var(--accent)}
.continue-info{padding:8px 10px}
.continue-title{font-size:12px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.continue-pct{font-size:11px;color:var(--accent);margin-top:2px}

/* Activity log */
.activity-row{display:flex;align-items:center;gap:12px;padding:10px 14px;
  border-bottom:1px solid var(--border);font-size:13px}
.activity-row:last-child{border-bottom:none}
.activity-icon{font-size:18px;flex-shrink:0}
.activity-info{flex:1;min-width:0}
.activity-title{font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.activity-meta{font-size:11px;color:var(--text3);margin-top:2px}
.activity-time{font-size:11px;color:var(--text3);flex-shrink:0;white-space:nowrap}

@media(max-width:768px){
  .sidebar{display:none}
  .mobile-nav{display:flex}
  .main{padding-bottom:66px}
  .media-grid{grid-template-columns:repeat(auto-fill,minmax(130px,1fr))}
  .player-nav-btn span.nav-label{display:none}
  .continue-grid{gap:8px}
  .continue-card{width:130px}
  .continue-thumb{width:130px;height:74px}
}

/* ── Mini Player ── */
#miniplayer{
  position:fixed;bottom:0;left:0;right:0;height:68px;
  background:var(--bg2);border-top:1px solid var(--border);
  display:none;align-items:center;gap:12px;padding:0 16px;
  z-index:290;box-shadow:0 -6px 30px rgba(0,0,0,.5);
  transition:transform .3s cubic-bezier(.4,0,.2,1)}
#miniplayer.visible{display:flex}
.mp-thumb{width:46px;height:46px;border-radius:6px;object-fit:cover;
  flex-shrink:0;background:var(--bg3)}
.mp-thumb-fallback{width:46px;height:46px;border-radius:6px;flex-shrink:0;
  background:var(--bg3);display:flex;align-items:center;justify-content:center;font-size:22px}
.mp-info{flex:1;min-width:0}
.mp-title{font-size:13px;font-weight:700;white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis}
.mp-artist{font-size:11px;color:var(--text2);margin-top:2px;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.mp-controls{display:flex;align-items:center;gap:4px;flex-shrink:0}
.mp-btn{width:36px;height:36px;border-radius:50%;background:transparent;
  border:none;color:var(--text2);font-size:16px;cursor:pointer;
  display:flex;align-items:center;justify-content:center;transition:var(--t)}
.mp-btn:hover{background:var(--bg3);color:var(--text)}
.mp-btn.mp-play{width:40px;height:40px;background:var(--accent);
  color:#1a1a1f;font-size:18px}
.mp-btn.mp-play:hover{background:var(--accent2)}
.mp-progress{flex:1;max-width:220px;flex-shrink:0}
.mp-prog-bar{height:4px;background:var(--bg3);border-radius:2px;
  cursor:pointer;position:relative;margin-bottom:4px}
.mp-prog-fill{height:4px;background:var(--accent);border-radius:2px;
  pointer-events:none}
.mp-time{font-size:10px;color:var(--text3);display:flex;justify-content:space-between}
.mp-close{width:28px;height:28px;border-radius:50%;background:transparent;
  border:none;color:var(--text3);font-size:14px;cursor:pointer;
  display:flex;align-items:center;justify-content:center;flex-shrink:0;transition:var(--t)}
.mp-close:hover{background:var(--bg3);color:var(--red)}
.mp-pip-btn{padding:4px 8px;border-radius:4px;background:var(--bg3);
  border:1px solid var(--border);color:var(--text2);font-size:11px;
  font-weight:600;cursor:pointer;flex-shrink:0;transition:var(--t)}
.mp-pip-btn:hover{border-color:var(--accent);color:var(--accent)}
body.has-miniplayer .main{padding-bottom:68px}
body.has-miniplayer .mobile-nav{bottom:68px}
@media(max-width:768px){
  #miniplayer{padding:0 10px;gap:8px}
  .mp-progress{display:none}
  body.has-miniplayer .main{padding-bottom:calc(66px + 68px)}
}
"""

BASE_HEAD = """<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Barlow:wght@300;400;500;600;700&family=Barlow+Condensed:wght@400;600;700&display=swap" rel="stylesheet">"""

BASE_JS = """
function toast(msg, type='info'){
  const t=document.createElement('div');
  t.className='toast toast-'+type;
  t.innerHTML=(type==='success'?'✅':type==='error'?'❌':'ℹ️')+' '+msg;
  document.getElementById('toasts').appendChild(t);
  setTimeout(()=>t.remove(),3500);
}
const si=document.querySelector('.search-input');
if(si) si.addEventListener('keydown',e=>{
  if(e.key==='Enter') window.location='/search?q='+encodeURIComponent(si.value);
});
function showModal(id){document.getElementById(id).style.display='flex'}
function hideModal(id){document.getElementById(id).style.display='none'}
"""

LICONS = {'movies':'🎬','shows':'📺','music':'🎵','photos':'🖼️','other':'📁'}

def render_shell(title: str, body: str, user: dict, active: str = '') -> str:
    role_cls = 'role-badge-admin' if user['role'] == 'admin' else 'role-badge-viewer'
    role_lbl = 'Admin' if user['role'] == 'admin' else 'Espectador'
    is_admin = user['role'] == 'admin'
    safe_title   = html_mod.escape(title)
    safe_display = html_mod.escape(user['display'])
    safe_avatar  = html_mod.escape(user['avatar'])

    db = get_db()
    libs = db.execute("SELECT id, name, type FROM libraries ORDER BY name COLLATE NOCASE").fetchall()
    db.close()

    lib_nav_items = ''
    mob_lib_items = ''
    for lib in libs:
        icon      = LICONS.get(lib['type'], '📁')
        lib_key   = f'lib_{lib["id"]}'
        is_active = active == lib_key
        safe_lname = html_mod.escape(lib['name'])
        lib_nav_items += f"""
        <a href="/library/{lib['id']}" class="nav-item {'active' if is_active else ''}">
          <span class="nav-icon">{icon}</span>
          <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{safe_lname}</span>
        </a>"""

    mob_libs_shown = list(libs)[:3]
    for lib in mob_libs_shown:
        icon      = LICONS.get(lib['type'], '📁')
        lib_key   = f'lib_{lib["id"]}'
        safe_lname = html_mod.escape(lib['name'][:8])
        mob_lib_items += f"""
      <a href="/library/{lib['id']}" class="mob-nav-item {'active' if active == lib_key else ''}">
        <span class="mob-icon">{icon}</span><span>{safe_lname}</span>
      </a>"""

    lib_section = ''
    if libs:
        lib_section = f"""
    <div class="nav-section">
      <div class="nav-label">Mis bibliotecas</div>
      {lib_nav_items}
    </div>"""

    admin_nav = ''
    if is_admin:
        admin_nav = f"""
      <div class="nav-section">
        <div class="nav-label">Administración</div>
        <a href="/libraries" class="nav-item {'active' if active=='libraries' else ''}">
          <span class="nav-icon">📚</span> Gestionar
        </a>
        <a href="/admin/users" class="nav-item {'active' if active=='users' else ''}">
          <span class="nav-icon">👥</span> Usuarios
        </a>
        <a href="/activity" class="nav-item {'active' if active=='activity' else ''}">
          <span class="nav-icon">📊</span> Actividad
        </a>
        <a href="/settings" class="nav-item {'active' if active=='settings' else ''}">
          <span class="nav-icon">⚙️</span> Ajustes
        </a>
      </div>"""

    return f"""<!DOCTYPE html>
<html lang="es">
<head>{BASE_HEAD}<title>{safe_title} — PyLex</title>
<style>{BASE_CSS}</style></head>
<body>
<div class="app">
  <nav class="sidebar">
    <div class="logo">
      <div class="logo-icon">P</div>
      <div><div class="logo-text">PyLex</div><div class="logo-sub">Media Server</div></div>
    </div>
    <div class="nav-section">
      <div class="nav-label">Navegar</div>
      <a href="/" class="nav-item {'active' if active=='home' else ''}">
        <span class="nav-icon">⊞</span> Inicio
      </a>
    </div>
    {lib_section}
    {admin_nav}
    <div class="sidebar-footer">
      <a href="/profile" class="user-chip">
        <div class="user-avatar">{safe_avatar}</div>
        <div>
          <div class="user-name">{safe_display}</div>
          <div class="user-role {role_cls}">{role_lbl}</div>
        </div>
      </a>
      <div class="server-status">
        <div class="status-dot"></div>
        Servidor activo · :{PORT}
      </div>
    </div>
  </nav>
  <div class="main">{body}</div>
</div>

<nav class="mobile-nav">
  <a href="/" class="mob-nav-item {'active' if active=='home' else ''}">
    <span class="mob-icon">⊞</span><span>Inicio</span>
  </a>
  {mob_lib_items}
  <a href="/profile" class="mob-nav-item {'active' if active=='profile' else ''}">
    <span class="mob-icon">{safe_avatar}</span><span>Perfil</span>
  </a>
</nav>

<div class="toast-container" id="toasts"></div>

<!-- ── Mini Player ── -->
<div id="miniplayer">
  <img id="mp-thumb" class="mp-thumb" src="" alt=""
    onerror="this.style.display='none';document.getElementById('mp-thumb-fb').style.display='flex'">
  <div id="mp-thumb-fb" class="mp-thumb-fallback" style="display:none">🎵</div>
  <div class="mp-info" style="cursor:pointer" onclick="PyLexMP.goToPage()">
    <div class="mp-title" id="mp-title">—</div>
    <div class="mp-artist" id="mp-artist"></div>
  </div>
  <div class="mp-controls">
    <button class="mp-btn" id="mp-prev-btn" onclick="PyLexMP.prev()" title="Anterior">⏮</button>
    <button class="mp-btn mp-play" id="mp-play-btn" onclick="PyLexMP.togglePlay()" title="Play/Pausa">▶</button>
    <button class="mp-btn" id="mp-next-btn" onclick="PyLexMP.next()" title="Siguiente">⏭</button>
  </div>
  <div class="mp-progress" id="mp-progress-wrap">
    <div class="mp-prog-bar" id="mp-prog-bar">
      <div class="mp-prog-fill" id="mp-prog-fill" style="width:0%"></div>
    </div>
    <div class="mp-time"><span id="mp-cur">0:00</span><span id="mp-dur">0:00</span></div>
  </div>
  <button class="mp-pip-btn" id="mp-pip-btn" style="display:none"
    onclick="PyLexMP.requestPiP()" title="Picture in Picture">⧉ PiP</button>
  <button class="mp-close" onclick="PyLexMP.close()" title="Cerrar">✕</button>
</div>
<audio id="mp-audio" style="display:none" preload="auto"></audio>

<script>{BASE_JS}</script>
<script>
/* ══════════════════════════════════════════════════════
   PyLexMP — Mini Player global
   ══════════════════════════════════════════════════════ */
const PyLexMP = (()=>{{
  const STORE_KEY = 'pylex_mp';
  const audio     = document.getElementById('mp-audio');
  const bar       = document.getElementById('miniplayer');

  let state = null;        // current track data
  let mainEl = null;       // reference to main player element (on play page)
  let mainIsVideo = false;

  // ── helpers ──────────────────────────────────────────
  function fmt(s){{
    if(!s || isNaN(s)) return '0:00';
    const m=Math.floor(s/60), sec=Math.floor(s%60);
    return m+':'+(sec<10?'0':'')+sec;
  }}

  function save(s){{
    try{{ sessionStorage.setItem(STORE_KEY, JSON.stringify(s)); }}catch(e){{}}
  }}

  function load(){{
    try{{ return JSON.parse(sessionStorage.getItem(STORE_KEY)); }}
    catch(e){{ return null; }}
  }}

  function show(){{
    bar.classList.add('visible');
    document.body.classList.add('has-miniplayer');
  }}

  function hide(){{
    bar.classList.remove('visible');
    document.body.classList.remove('has-miniplayer');
  }}

  function updateUI(){{
    if(!state) return;
    document.getElementById('mp-title').textContent  = state.title  || '—';
    document.getElementById('mp-artist').textContent = state.artist || state.album || '';
    const thumb = document.getElementById('mp-thumb');
    const fb    = document.getElementById('mp-thumb-fb');
    thumb.style.display = '';
    fb.style.display    = 'none';
    thumb.src = '/thumb/' + state.id;
    // PiP button only for video
    document.getElementById('mp-pip-btn').style.display = (state.type === 'video') ? '' : 'none';
    const icon = state.type === 'video' ? '🎬' : (state.type === 'image' ? '🖼️' : '🎵');
    fb.textContent = icon;
  }}

  function setPlayBtn(playing){{
    document.getElementById('mp-play-btn').textContent = playing ? '⏸' : '▶';
  }}

  // ── progress bar interaction ─────────────────────────
  document.getElementById('mp-prog-bar').addEventListener('click', e=>{{
    if(!audio.duration) return;
    const r = e.currentTarget.getBoundingClientRect();
    const pct = (e.clientX - r.left) / r.width;
    audio.currentTime = pct * audio.duration;
    if(state) {{ state.position = audio.currentTime; save(state); }}
  }});

  // ── audio events ─────────────────────────────────────
  audio.addEventListener('timeupdate', ()=>{{
    if(!audio.duration) return;
    const pct = (audio.currentTime / audio.duration) * 100;
    document.getElementById('mp-prog-fill').style.width = pct + '%';
    document.getElementById('mp-cur').textContent = fmt(audio.currentTime);
    document.getElementById('mp-dur').textContent = fmt(audio.duration);
    if(state){{
      state.position = audio.currentTime;
      state.progress = audio.currentTime / audio.duration;
    }}
  }});

  audio.addEventListener('play',  ()=> setPlayBtn(true));
  audio.addEventListener('pause', ()=> setPlayBtn(false));

  audio.addEventListener('ended', ()=>{{
    if(state && state.next_id){{
      window.location = '/play/' + state.next_id;
    }} else {{
      setPlayBtn(false);
    }}
  }});

  // Periodic save of position to sessionStorage
  setInterval(()=>{{
    if(state && !audio.paused && !audio.muted){{
      save(state);
      // Also persist to server every 15s
      fetch('/api/play/' + state.id, {{method:'POST',
        headers:{{'Content-Type':'application/json'}},
        body:JSON.stringify({{progress: state.progress||0, position: state.position||0}})}})
        .catch(()=>{{}});
    }}
  }}, 15000);

  // ── public API ────────────────────────────────────────

  /**
   * Called from a play page to register the current track.
   * @param {{id, title, artist, album, type, position, prev_id, next_id}} data
   * @param el  — the main <audio> or <video> element on the play page
   */
  function register(data, el){{
    state   = data;
    mainEl  = el;
    mainIsVideo = data.type === 'video';
    save(data);
    updateUI();
    show();
    setPlayBtn(!el.paused);

    // Sync progress from main player
    el.addEventListener('timeupdate', ()=>{{
      if(!el.duration) return;
      const pct = (el.currentTime / el.duration) * 100;
      document.getElementById('mp-prog-fill').style.width = pct + '%';
      document.getElementById('mp-cur').textContent = fmt(el.currentTime);
      document.getElementById('mp-dur').textContent = fmt(el.duration);
      state.position = el.currentTime;
      state.progress = el.currentTime / el.duration;
    }});
    el.addEventListener('play',  ()=> setPlayBtn(true));
    el.addEventListener('pause', ()=> setPlayBtn(false));

    // For video: enable PiP button
    if(mainIsVideo){{
      document.getElementById('mp-pip-btn').style.display = '';
    }}

    // For audio: mute the mini-player audio (main player is active)
    if(data.type === 'audio'){{
      audio.muted = true;
      // keep audio element loaded so we can resume after nav
      if(audio.src !== location.origin + '/stream/' + data.id){{
        audio.src = '/stream/' + data.id;
      }}
    }}
  }}

  /** Restore from sessionStorage after page navigation (not on play page) */
  function restore(){{
    const d = load();
    if(!d || !d.id) return;
    state = d;
    updateUI();
    show();

    if(d.type === 'audio'){{
      audio.muted = false;
      if(audio.src !== location.origin + '/stream/' + d.id){{
        audio.src = '/stream/' + d.id;
        audio.addEventListener('loadedmetadata', ()=>{{
          if(d.position > 2) audio.currentTime = d.position;
          audio.play().catch(()=>{{}});
        }}, {{once:true}});
      }} else if(audio.paused){{
        if(d.position > 2) audio.currentTime = d.position;
        audio.play().catch(()=>{{}});
      }}
    }} else if(d.type === 'video'){{
      // Can't resume video in mini player — just show "go back" state
      setPlayBtn(false);
    }}
  }}

  function togglePlay(){{
    if(mainEl && !mainEl.ended){{
      mainEl.paused ? mainEl.play() : mainEl.pause();
    }} else {{
      audio.paused ? audio.play() : audio.pause();
    }}
  }}

  function prev(){{
    if(state && state.prev_id) window.location = '/play/' + state.prev_id;
  }}

  function next(){{
    if(state && state.next_id) window.location = '/play/' + state.next_id;
  }}

  function close(){{
    audio.pause();
    if(mainEl) mainEl.pause();
    state = null;
    try{{ sessionStorage.removeItem(STORE_KEY); }}catch(e){{}}
    hide();
  }}

  function goToPage(){{
    if(state && state.id) window.location = '/play/' + state.id;
  }}

  function requestPiP(){{
    // Works for video on play page
    if(mainEl && mainEl.tagName === 'VIDEO' && document.pictureInPictureEnabled){{
      mainEl.requestPictureInPicture().catch(e => toast('PiP no disponible', 'error'));
    }} else {{
      toast('Picture in Picture solo disponible con vídeo', 'info');
    }}
  }}

  // ── init ─────────────────────────────────────────────
  // On non-play pages: restore mini player from sessionStorage
  if(!window.location.pathname.startsWith('/play/')){{
    restore();
  }}

  return {{ register, togglePlay, prev, next, close, goToPage, requestPiP }};
}})();
</script>
</body></html>"""

# ── Page: Setup ────────────────────────────────────────────────────────────────

def page_setup(error: str = '') -> str:
    err_html = f'<p class="form-error">{html_mod.escape(error)}</p>' if error else ''
    return f"""<!DOCTYPE html>
<html lang="es"><head>{BASE_HEAD}<title>Configuración inicial — PyLex</title>
<style>
{BASE_CSS}
.setup-wrap{{min-height:100vh;display:flex;align-items:center;justify-content:center;
  background:var(--bg);padding:24px}}
.setup-card{{background:var(--bg2);border:1px solid var(--border);border-radius:var(--rl);
  padding:40px;width:100%;max-width:440px;box-shadow:var(--shadow)}}
.setup-logo{{display:flex;align-items:center;gap:12px;margin-bottom:28px}}
.setup-icon{{width:48px;height:48px;background:var(--accent);border-radius:10px;
  display:flex;align-items:center;justify-content:center;
  font-family:'Barlow Condensed',sans-serif;font-size:24px;font-weight:700;color:#1a1a1f}}
.setup-title{{font-family:'Barlow Condensed',sans-serif;font-size:26px;font-weight:700}}
.setup-sub{{font-size:13px;color:var(--text2);margin-bottom:28px;line-height:1.7;
  border-left:3px solid var(--accent);padding-left:12px;background:rgba(229,160,13,.06);
  padding:10px 12px;border-radius:0 var(--r) var(--r) 0}}
</style></head>
<body>
<div class="setup-wrap">
  <div class="setup-card">
    <div class="setup-logo">
      <div class="setup-icon">P</div>
      <div><div class="setup-title">PyLex</div>
      <div style="font-size:12px;color:var(--text3)">Media Server · Primera configuración</div></div>
    </div>
    <p class="setup-sub">
      👋 Bienvenido. Crea la cuenta de <strong>administrador</strong>.<br>
      Solo este usuario podrá gestionar bibliotecas y otros usuarios.
    </p>
    {err_html}
    <div class="form-group">
      <label class="form-label">Nombre de usuario</label>
      <input class="form-input" id="su" placeholder="admin" autocomplete="username"/>
    </div>
    <div class="form-group">
      <label class="form-label">Nombre para mostrar</label>
      <input class="form-input" id="sd" placeholder="Tu nombre"/>
    </div>
    <div class="form-group">
      <label class="form-label">Contraseña</label>
      <input class="form-input" type="password" id="sp" autocomplete="new-password"/>
      <div class="form-hint">Mínimo 8 caracteres</div>
    </div>
    <div class="form-group">
      <label class="form-label">Confirmar contraseña</label>
      <input class="form-input" type="password" id="sp2" autocomplete="new-password"/>
    </div>
    <button class="btn btn-primary" style="width:100%;justify-content:center;padding:12px"
      onclick="doSetup()">Crear cuenta de administrador →</button>
  </div>
</div>
<div class="toast-container" id="toasts"></div>
<script>
{BASE_JS}
function doSetup(){{
  const u=document.getElementById('su').value.trim();
  const d=document.getElementById('sd').value.trim();
  const p=document.getElementById('sp').value;
  const p2=document.getElementById('sp2').value;
  if(!u||!d||!p){{toast('Rellena todos los campos','error');return;}}
  if(p.length<8){{toast('La contraseña debe tener 8+ caracteres','error');return;}}
  if(p!==p2){{toast('Las contraseñas no coinciden','error');return;}}
  fetch('/api/setup',{{method:'POST',headers:{{'Content-Type':'application/json'}},
    body:JSON.stringify({{username:u,display:d,password:p}})}})
    .then(r=>r.json()).then(d=>{{
      if(d.ok) window.location='/';
      else toast('Error: '+d.error,'error');
    }});
}}
document.querySelectorAll('.form-input').forEach(el=>
  el.addEventListener('keydown',e=>{{if(e.key==='Enter')doSetup();}}));
</script>
</body></html>"""

# ── Page: Login ────────────────────────────────────────────────────────────────

def page_login(next_url: str = '/', error: str = '') -> str:
    err_html = f'<p class="form-error">{html_mod.escape(error)}</p>' if error else ''
    return f"""<!DOCTYPE html>
<html lang="es"><head>{BASE_HEAD}<title>Iniciar sesión — PyLex</title>
<style>
{BASE_CSS}
.login-wrap{{min-height:100vh;display:flex;align-items:center;justify-content:center;
  background:var(--bg);padding:24px}}
.login-card{{background:var(--bg2);border:1px solid var(--border);border-radius:var(--rl);
  padding:40px;width:100%;max-width:400px;box-shadow:var(--shadow)}}
.login-logo{{text-align:center;margin-bottom:32px}}
.login-icon{{width:60px;height:60px;background:var(--accent);border-radius:14px;
  display:inline-flex;align-items:center;justify-content:center;
  font-family:'Barlow Condensed',sans-serif;font-size:30px;font-weight:700;
  color:#1a1a1f;margin-bottom:12px}}
.login-title{{font-family:'Barlow Condensed',sans-serif;font-size:30px;font-weight:700}}
.login-sub{{font-size:13px;color:var(--text2);margin-top:4px}}
</style></head>
<body>
<div class="login-wrap">
  <div class="login-card">
    <div class="login-logo">
      <div class="login-icon">P</div>
      <div class="login-title">PyLex</div>
      <div class="login-sub">Introduce tus credenciales para continuar</div>
    </div>
    {err_html}
    <div class="form-group">
      <label class="form-label">Usuario</label>
      <input class="form-input" id="lu" placeholder="nombre de usuario" autocomplete="username"/>
    </div>
    <div class="form-group">
      <label class="form-label">Contraseña</label>
      <input class="form-input" type="password" id="lp" autocomplete="current-password"/>
    </div>
    <label style="display:flex;align-items:center;gap:8px;font-size:13px;
      color:var(--text2);margin-bottom:20px;cursor:pointer">
      <input type="checkbox" id="lr" style="accent-color:var(--accent)">
      Recordarme {SESSION_DAYS} días
    </label>
    <button class="btn btn-primary" style="width:100%;justify-content:center;padding:12px"
      onclick="doLogin()">Iniciar sesión</button>
  </div>
</div>
<div class="toast-container" id="toasts"></div>
<script>
{BASE_JS}
const NEXT='{next_url}';
function doLogin(){{
  const u=document.getElementById('lu').value.trim();
  const p=document.getElementById('lp').value;
  const r=document.getElementById('lr').checked;
  if(!u||!p){{toast('Introduce usuario y contraseña','error');return;}}
  fetch('/api/login',{{method:'POST',headers:{{'Content-Type':'application/json'}},
    body:JSON.stringify({{username:u,password:p,remember:r}})}})
    .then(r=>r.json()).then(d=>{{
      if(d.ok) window.location=NEXT;
      else toast('Usuario o contraseña incorrectos','error');
    }});
}}
document.querySelectorAll('.form-input').forEach(el=>
  el.addEventListener('keydown',e=>{{if(e.key==='Enter')doLogin();}}));
</script>
</body></html>"""

# ── Page: Home ─────────────────────────────────────────────────────────────────

def page_home(user: dict) -> str:
    db = get_db()
    libs   = db.execute("SELECT * FROM libraries ORDER BY name COLLATE NOCASE").fetchall()
    counts = {r['library_id']: r for r in
              db.execute("SELECT library_id, COUNT(*) cnt, SUM(size) sz FROM media GROUP BY library_id").fetchall()}
    recent = db.execute("SELECT * FROM media ORDER BY added_at DESC LIMIT 24").fetchall()
    total_files = db.execute("SELECT COUNT(*) FROM media").fetchone()[0]
    total_size  = db.execute("SELECT SUM(size) FROM media").fetchone()[0] or 0
    db.close()

    lib_cards_html = ''
    for lib in libs:
        icon  = LICONS.get(lib['type'], '📁')
        info  = counts.get(lib['id'])
        cnt   = info['cnt'] if info else 0
        sz    = human_size(info['sz'] or 0) if info else '0 B'
        last  = lib['last_scan'] or 'Nunca'
        if last != 'Nunca':
            try: last = datetime.fromisoformat(last).strftime('%d/%m/%Y')
            except: pass
        safe_lname = html_mod.escape(lib['name'])
        lib_cards_html += f"""
        <a href="/library/{lib['id']}" class="lib-home-card">
          <div class="lib-home-icon">{icon}</div>
          <div class="lib-home-name">{safe_lname}</div>
          <div class="lib-home-meta">{cnt} archivos · {sz}</div>
          <div class="lib-home-scan">Escaneada {last}</div>
        </a>"""

    empty_libs = ''
    if not libs:
        empty_libs = _empty('📚', 'Sin bibliotecas',
            'Añade tu primera biblioteca para empezar',
            '<a href="/libraries" class="btn btn-primary">+ Añadir biblioteca</a>' if user['role']=='admin' else '')

    add_btn = '<a href="/libraries" class="btn btn-primary">+ Biblioteca</a>' if user['role']=='admin' else ''

    recent_html = ''
    if recent:
        recent_html = f"""
      <div class="section-header" style="margin-top:32px">
        <div class="section-title">Añadido recientemente</div>
      </div>
      <div class="media-grid">{''.join(_media_card(m) for m in recent)}</div>"""

    db2 = get_db()
    in_progress = db2.execute(
        "SELECT * FROM media WHERE progress>0.05 AND progress<0.95 ORDER BY last_played DESC LIMIT 12"
    ).fetchall()
    db2.close()

    continue_html = ''
    if in_progress:
        cards_cont = ''
        for cp in in_progress:
            icon  = {'video':'🎬','audio':'🎵','image':'🖼️'}.get(cp['type'],'📄')
            pct   = int((cp['progress'] or 0)*100)
            safe_ct = html_mod.escape(cp['title'])
            cards_cont += f"""
        <div class="continue-card" onclick="window.location='/play/{cp['id']}'">
          <div class="continue-thumb">
            <img src="/thumb/{cp['id']}" onerror="this.remove()">{icon}
            <div class="continue-bar"><div class="continue-fill" style="width:{pct}%"></div></div>
          </div>
          <div class="continue-info">
            <div class="continue-title" title="{safe_ct}">{safe_ct}</div>
            <div class="continue-pct">{pct}% completado</div>
          </div>
        </div>"""
        continue_html = f"""
      <div class="section-header">
        <div class="section-title">⏯ Continuar</div>
      </div>
      <div class="continue-grid">{cards_cont}</div>"""

    body = f"""
    <div class="topbar">
      <div class="page-title">⊞ Inicio</div>
      <div class="search-wrap"><span class="search-icon">🔍</span>
        <input class="search-input" placeholder="Buscar medios..."/></div>
      <div class="topbar-actions">{add_btn}</div>
    </div>
    <div class="content">
      <div class="stats-bar">
        <div class="stat-card"><div class="stat-value">{len(libs)}</div><div class="stat-label">📚 Bibliotecas</div></div>
        <div class="stat-card"><div class="stat-value">{total_files}</div><div class="stat-label">🗂 Archivos totales</div></div>
        <div class="stat-card"><div class="stat-value">{human_size(total_size)}</div><div class="stat-label">💾 Espacio</div></div>
      </div>
      {"<div class='section-header'><div class='section-title'>Mis bibliotecas</div></div>" if libs else ""}
      <div class="lib-home-grid">{lib_cards_html}</div>
      {empty_libs}
      {continue_html}
      {recent_html}
    </div>"""
    return render_shell('Inicio', body, user, 'home')

def _media_card(m) -> str:
    icons = {'video':'🎬','audio':'🎵','image':'🖼️'}
    bc    = {'video':'badge-video','audio':'badge-audio','image':'badge-image'}
    icon  = icons.get(m['type'],'📄')
    prog  = m['progress'] or 0
    ph    = f'<div class="progress-bar" style="z-index:2"><div class="progress-fill" style="width:{min(100,prog*100):.0f}%"></div></div>' if prog > 0 else ''
    year  = f' · {m["year"]}' if m['year'] else ''
    land  = 'land' if m['type']=='video' else ''
    safe_title = html_mod.escape(m['title'])
    thumb = f'<img src="/thumb/{m["id"]}" loading="lazy" class="thumb-img" onerror="this.remove()">'
    return f"""
    <div class="media-card" onclick="window.location='/play/{m['id']}'">
      <div class="media-thumb {land}">
        {thumb}
        <div class="media-thumb-icon">{icon}</div>
        <div class="media-type-badge {bc.get(m['type'],'')} " style="z-index:2"> {m['type']}</div>{ph}
      </div>
      <div class="media-info">
        <div class="media-title" title="{safe_title}">{safe_title}</div>
        <div class="media-meta">{human_size(m['size'] or 0)}{year}</div>
      </div>
    </div>"""

def _media_list_item(m) -> str:
    """Render a single media item as a compact list row."""
    icons = {'video':'🎬','audio':'🎵','image':'🖼️'}
    icon  = icons.get(m['type'], '📄')
    prog  = m['progress'] or 0
    safe_title  = html_mod.escape(m['title'])
    artist = html_mod.escape(m['artist'] or '') if m['artist'] else ''
    album  = html_mod.escape(m['album'] or '') if m['album'] else ''
    meta_parts = []
    if artist: meta_parts.append(artist)
    if album:  meta_parts.append(album)
    if m['year']: meta_parts.append(str(m['year']))
    meta = ' · '.join(meta_parts) if meta_parts else human_size(m['size'] or 0)
    prog_bar = f'<div class="list-prog"><div class="list-prog-fill" style="width:{min(100,prog*100):.0f}%"></div></div>' if prog > 0.01 else ''
    track_html = f'<div class="list-track">{m["track"]}</div>' if m['track'] else ''
    return f"""
    <div class="list-item" onclick="window.location='/play/{m["id"]}'">
      <div class="list-thumb">
        <img src="/thumb/{m['id']}" onerror="this.remove()">{icon}
      </div>
      {track_html}
      <div class="list-info">
        <div class="list-title" title="{safe_title}">{safe_title}</div>
        <div class="list-meta">{meta}</div>
        {prog_bar}
      </div>
      <div class="list-size">{human_size(m['size'] or 0)}</div>
    </div>"""

def _empty(icon, title, text, action='') -> str:
    return f'<div class="empty-state"><div class="empty-icon">{icon}</div><div class="empty-title">{title}</div><div class="empty-text">{text}</div>{action}</div>'

# ── Page: Library (individual) ────────────────────────────────────────────────

SORT_OPTIONS = {
    'name':       ('title COLLATE NOCASE', 'A–Z'),
    'name_desc':  ('title COLLATE NOCASE DESC', 'Z–A'),
    'date':       ('added_at DESC', 'Recientes'),
    'date_asc':   ('added_at ASC', 'Antiguos'),
    'size':       ('size DESC', 'Tamaño ↓'),
    'size_asc':   ('size ASC', 'Tamaño ↑'),
    'plays':      ('play_count DESC', 'Más vistos'),
    'year':       ('year DESC NULLS LAST', 'Año ↓'),
    'year_asc':   ('year ASC NULLS LAST', 'Año ↑'),
}

def page_library(user: dict, lib_id_str: str,
                 sort: str = 'name', view: str = 'grid',
                 group_by: str = '') -> tuple:
    try:
        lib_id = int(lib_id_str)
    except (ValueError, TypeError):
        return render_shell('Error', _empty('🔍', 'Biblioteca no encontrada', ''), user), 404

    db = get_db()
    lib = db.execute("SELECT * FROM libraries WHERE id=?", (lib_id,)).fetchone()
    if not lib:
        db.close()
        return render_shell('Error', _empty('🔍', 'Biblioteca no encontrada', ''), user), 404

    sort_sql, _ = SORT_OPTIONS.get(sort, SORT_OPTIONS['name'])
    items = db.execute(
        f"SELECT * FROM media WHERE library_id=? ORDER BY {sort_sql}", (lib_id,)
    ).fetchall()
    db.close()

    icon = LICONS.get(lib['type'], '📁')
    safe_lname = html_mod.escape(lib['name'])
    is_music = lib['type'] == 'music'

    def slink(s, lbl):
        active = 'active' if sort == s else ''
        return f'<a href="/library/{lib_id}?sort={s}&view={view}" class="sort-btn {active}">{lbl}</a>'

    def vlink(v, lbl):
        active = 'active' if view == v else ''
        return f'<a href="/library/{lib_id}?sort={sort}&view={v}" class="view-btn {active}" title="{lbl}">{lbl}</a>'

    # Build sort controls
    sort_html = f"""
    <div class="lib-controls">
      <span style="font-size:12px;color:var(--text3)">Ordenar:</span>
      {slink('name','A–Z')}
      {slink('date','Recientes')}
      {slink('plays','Más vistos')}
      {slink('size','Tamaño')}
      {slink('year','Año')}
      <span class="sep">|</span>
      {vlink('grid','▦')}
      {vlink('list','☰')}
      {('<span class="sep">|</span>' + vlink('artist','🎤') + vlink('album','💿')) if is_music else ''}
    </div>"""

    scan_btn = f'<button class="btn btn-ghost btn-sm" onclick="scanLib({lib_id})">↻ Escanear</button>' if user['role'] == 'admin' else ''
    settings_btn = f'<a href="/libraries" class="btn btn-ghost btn-sm">⚙ Gestionar</a>' if user['role'] == 'admin' else ''

    # ── Artist view ──
    if view == 'artist' and is_music:
        artists = {}
        for m in items:
            a = m['artist'] or '— Desconocido —'
            if a not in artists:
                artists[a] = {'count': 0, 'albums': set(), 'sample_id': m['id']}
            artists[a]['count'] += 1
            if m['album']:
                artists[a]['albums'].add(m['album'])
        cards = ''
        for aname in sorted(artists.keys(), key=str.casefold):
            info = artists[aname]
            safe_a = html_mod.escape(aname)
            alb_cnt = len(info['albums'])
            cards += f"""
            <div class="artist-card"
              onclick="window.location='/library/{lib_id}?sort={sort}&view=album&artist={quote(aname)}'">
              <div class="artist-avatar">🎤</div>
              <div class="artist-name" title="{safe_a}">{safe_a}</div>
              <div class="artist-meta">{info['count']} canciones · {alb_cnt} álbumes</div>
            </div>"""
        content = f'<div class="artist-grid">{cards}</div>' if cards else _empty('🎤','Sin artistas','Escanea la biblioteca para detectar metadatos')

    # ── Album view ──
    elif view == 'album' and is_music:
        artist_filter = ''  # filled by query param if coming from artist view
        albums = {}
        for m in items:
            alb = m['album'] or '— Sin álbum —'
            art = m['artist'] or '— Desconocido —'
            key = (art, alb)
            if key not in albums:
                albums[key] = {'count': 0, 'sample_id': m['id']}
            albums[key]['count'] += 1
        cards = ''
        for (art, alb) in sorted(albums.keys(), key=lambda x: (x[0].casefold(), x[1].casefold())):
            info = albums[(art, alb)]
            safe_alb = html_mod.escape(alb)
            safe_art = html_mod.escape(art)
            cards += f"""
            <div class="album-card"
              onclick="window.location='/library/{lib_id}?sort=track&view=list&album={quote(alb)}'">
              <div class="album-cover">
                <img src="/thumb/{info['sample_id']}" onerror="this.remove()">💿
              </div>
              <div class="album-info">
                <div class="album-name" title="{safe_alb}">{safe_alb}</div>
                <div class="album-artist">{safe_art}</div>
                <div class="album-cnt">{info['count']} canciones</div>
              </div>
            </div>"""
        content = f'<div class="album-grid">{cards}</div>' if cards else _empty('💿','Sin álbumes','Escanea la biblioteca para detectar metadatos')

    # ── List view ──
    elif view == 'list':
        if items:
            content = f'<div class="media-list">{"".join(_media_list_item(m) for m in items)}</div>'
        else:
            content = _empty(icon, 'Biblioteca vacía', 'Pulsa Escanear para indexar',
                f'<button class="btn btn-primary" onclick="scanLib({lib_id})">↻ Escanear ahora</button>' if user['role']=='admin' else '')

    # ── Grid view (default) ──
    else:
        if items:
            content = f'<div class="media-grid">{"".join(_media_card(m) for m in items)}</div>'
        else:
            content = _empty(icon, 'Biblioteca vacía', 'Pulsa Escanear para indexar',
                f'<button class="btn btn-primary" onclick="scanLib({lib_id})">↻ Escanear ahora</button>' if user['role']=='admin' else '')

    body = f"""
    <div class="topbar">
      <div class="page-title">{icon} {safe_lname}</div>
      <div class="search-wrap"><span class="search-icon">🔍</span>
        <input class="search-input" placeholder="Buscar en {safe_lname}..."/></div>
      <div class="topbar-actions">
        <span style="font-size:13px;color:var(--text2)">{len(items)} elementos</span>
        {scan_btn}{settings_btn}
      </div>
    </div>
    <div class="content">
      {sort_html}
      {content}
    </div>
    <script>
    function scanLib(id){{
      fetch('/api/scan/'+id,{{method:'POST'}})
        .then(r=>r.json()).then(d=>{{
          if(d.ok) {{ toast('✅ Escaneo iniciado, recarga en unos segundos','success'); setTimeout(()=>location.reload(),4000); }}
          else toast('❌ Error: '+d.error,'error');
        }});
    }}
    </script>"""
    return render_shell(lib['name'], body, user, f'lib_{lib_id}'), 200

# ── Page: Libraries (admin management) ────────────────────────────────────────

def page_libraries(user: dict) -> str:
    db = get_db()
    libs   = db.execute("SELECT * FROM libraries ORDER BY name").fetchall()
    counts = {r['library_id']: r['cnt'] for r in
              db.execute("SELECT library_id,COUNT(*) cnt FROM media GROUP BY library_id").fetchall()}
    db.close()

    lib_cards = ''
    for lib in libs:
        icon = LICONS.get(lib['type'],'📁')
        cnt  = counts.get(lib['id'], 0)
        last = lib['last_scan'] or 'Nunca'
        if last != 'Nunca':
            try:
                last = datetime.fromisoformat(last).strftime('%d/%m/%Y %H:%M')
            except (ValueError, TypeError):
                pass
        safe_lname = html_mod.escape(lib['name'])
        safe_lpath = html_mod.escape(lib['path'])
        lib_cards += f"""
        <div class="lib-card">
          <div class="lib-icon">{icon}</div>
          <div class="lib-name">{safe_lname}</div>
          <div class="lib-path">{safe_lpath}</div>
          <div class="lib-stats">
            <div class="lib-stat"><strong>{cnt}</strong> archivos</div>
            <div class="lib-stat">Escaneada: <strong>{last}</strong></div>
          </div>
          <div class="lib-footer">
            <button class="btn btn-ghost btn-sm" onclick="scanLib({lib['id']})">↻ Escanear</button>
            <button class="btn btn-danger btn-sm" onclick="delLib({lib['id']},'{safe_lname}')">🗑 Eliminar</button>
          </div>
        </div>"""

    empty = '' if libs else _empty('📚','Sin bibliotecas','Añade una carpeta para empezar a organizar tu colección')
    body = f"""
    <div class="topbar">
      <div class="page-title">📚 Bibliotecas</div>
      <div class="topbar-actions">
        <button class="btn btn-primary" onclick="showModal('add-lib-modal')">+ Añadir biblioteca</button>
      </div>
    </div>
    <div class="content">
      <div class="lib-grid">{lib_cards}</div>{empty}
    </div>
    <div class="modal-overlay" id="add-lib-modal" style="display:none"
      onclick="if(event.target===this)hideModal('add-lib-modal')">
      <div class="modal">
        <div class="modal-title">+ Nueva Biblioteca</div>
        <div class="form-group">
          <label class="form-label">Nombre</label>
          <input class="form-input" id="lib-name" placeholder="Mi Colección"/>
        </div>
        <div class="form-group">
          <label class="form-label">Tipo</label>
          <select class="form-select" id="lib-type">
            <option value="movies">🎬 Películas</option>
            <option value="shows">📺 Series</option>
            <option value="music">🎵 Música</option>
            <option value="photos">🖼️ Fotos</option>
            <option value="other">📁 Otros</option>
          </select>
        </div>
        <div class="form-group">
          <label class="form-label">Ruta de la carpeta</label>
          <input class="form-input" id="lib-path" placeholder="Linux: /home/usuario/Videos  |  Windows: C:\\Users\\usuario\\Videos"/>
          <div class="form-hint">Ruta completa en este servidor donde están los archivos</div>
        </div>
        <div class="modal-footer">
          <button class="btn btn-ghost" onclick="hideModal('add-lib-modal')">Cancelar</button>
          <button id="add-lib-btn" class="btn btn-primary" onclick="addLib()">Crear y Escanear</button>
        </div>
      </div>
    </div>
    <script>{_LIBRARIES_JS}</script>"""
    return render_shell('Bibliotecas', body, user, 'libraries')

# ── Page: Admin Users ──────────────────────────────────────────────────────────

def page_users(user: dict) -> str:
    db  = get_db()
    all_users = db.execute("SELECT * FROM users ORDER BY role DESC, username").fetchall()
    db.close()

    rows = ''
    for u in all_users:
        last = u['last_login'] or '—'
        if last != '—':
            try:
                last = datetime.fromisoformat(last).strftime('%d/%m/%Y %H:%M')
            except (ValueError, TypeError):
                pass
        safe_uname   = html_mod.escape(u['username'])
        safe_display = html_mod.escape(u['display'])
        safe_avatar  = html_mod.escape(u['avatar'])
        pill = f'<span class="pill pill-{u["role"]}">{("Admin" if u["role"]=="admin" else "Espectador")}</span>'
        del_btn = '' if u['id'] == user['id'] else f'<button class="btn btn-danger btn-sm" onclick="delUser({u["id"]},\'{safe_uname}\')">Eliminar</button>'
        role_btn = '' if u['id'] == user['id'] else (
            f'<button class="btn btn-ghost btn-sm" onclick="toggleRole({u["id"]},\'viewer\')">→ Espectador</button>'
            if u['role'] == 'admin' else
            f'<button class="btn btn-ghost btn-sm" onclick="toggleRole({u["id"]},\'admin\')">→ Admin</button>'
        )
        rows += f"""<tr>
          <td>{safe_avatar} {safe_display}</td>
          <td><code style="font-size:12px;color:var(--text2)">@{safe_uname}</code></td>
          <td>{pill}</td>
          <td>{last}</td>
          <td style="display:flex;gap:6px">{role_btn}{del_btn}</td>
        </tr>"""

    avatars = '🎬 🎵 🖼️ 🎮 🍿 🎙️ 📺 🦊 🐉 🌟 👤 🎭'.split()
    av_btns = ''.join(f'<button class="btn-icon" onclick="pickAv(\'{a}\')" title="{a}">{a}</button>' for a in avatars)

    body = f"""
    <div class="topbar">
      <div class="page-title">👥 Usuarios</div>
      <div class="topbar-actions">
        <button class="btn btn-primary" onclick="showModal('add-user-modal')">+ Nuevo usuario</button>
      </div>
    </div>
    <div class="content">
      <div class="section-card">
        <div class="section-card-title">Usuarios registrados ({len(all_users)})</div>
        <table class="table">
          <thead><tr><th>Nombre</th><th>Usuario</th><th>Rol</th><th>Último acceso</th><th>Acciones</th></tr></thead>
          <tbody>{rows}</tbody>
        </table>
      </div>
    </div>

    <div class="modal-overlay" id="add-user-modal" style="display:none"
      onclick="if(event.target===this)hideModal('add-user-modal')">
      <div class="modal">
        <div class="modal-title">+ Nuevo Usuario</div>
        <div class="form-group">
          <label class="form-label">Avatar</label>
          <div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:4px">{av_btns}</div>
          <input class="form-input" id="nu-avatar" value="👤" style="width:60px;text-align:center"/>
        </div>
        <div class="form-group">
          <label class="form-label">Nombre para mostrar</label>
          <input class="form-input" id="nu-display" placeholder="Nombre"/>
        </div>
        <div class="form-group">
          <label class="form-label">Nombre de usuario</label>
          <input class="form-input" id="nu-username" placeholder="usuario"/>
        </div>
        <div class="form-group">
          <label class="form-label">Contraseña</label>
          <input class="form-input" type="password" id="nu-pass" placeholder="Mínimo 8 caracteres"/>
        </div>
        <div class="form-group">
          <label class="form-label">Rol</label>
          <select class="form-select" id="nu-role">
            <option value="viewer">👁 Espectador (solo ver)</option>
            <option value="admin">⚙️ Administrador (acceso total)</option>
          </select>
        </div>
        <div class="modal-footer">
          <button class="btn btn-ghost" onclick="hideModal('add-user-modal')">Cancelar</button>
          <button class="btn btn-primary" onclick="addUser()">Crear usuario</button>
        </div>
      </div>
    </div>
    <script>
    function pickAv(a){{document.getElementById('nu-avatar').value=a;}}
    function addUser(){{
      const d={{
        display:document.getElementById('nu-display').value.trim(),
        username:document.getElementById('nu-username').value.trim(),
        password:document.getElementById('nu-pass').value,
        role:document.getElementById('nu-role').value,
        avatar:document.getElementById('nu-avatar').value
      }};
      if(!d.display||!d.username||!d.password){{toast('Rellena todos los campos','error');return;}}
      if(d.password.length<8){{toast('Contraseña mínimo 8 caracteres','error');return;}}
      fetch('/api/users',{{method:'POST',headers:{{'Content-Type':'application/json'}},
        body:JSON.stringify(d)}})
        .then(r=>r.json()).then(r=>{{
          if(r.ok){{toast('Usuario creado','success');setTimeout(()=>location.reload(),800);}}
          else toast('Error: '+r.error,'error');
        }});
    }}
    function delUser(id,name){{
      if(!confirm('¿Eliminar al usuario "'+name+'"? Se cerrarán todas sus sesiones.'))return;
      fetch('/api/users/'+id,{{method:'DELETE'}})
        .then(r=>r.json()).then(d=>{{
          if(d.ok){{toast('Usuario eliminado','success');setTimeout(()=>location.reload(),800);}}
          else toast('Error: '+d.error,'error');
        }});
    }}
    function toggleRole(id,role){{
      fetch('/api/users/'+id+'/role',{{method:'POST',
        headers:{{'Content-Type':'application/json'}},
        body:JSON.stringify({{role}})}})
        .then(r=>r.json()).then(d=>{{
          if(d.ok){{toast('Rol actualizado','success');setTimeout(()=>location.reload(),800);}}
          else toast('Error: '+d.error,'error');
        }});
    }}
    </script>"""
    return render_shell('Usuarios', body, user, 'users')

# ── Page: Activity Log ─────────────────────────────────────────────────────────

def page_activity(user: dict) -> str:
    db = get_db()
    rows = db.execute("""
        SELECT al.id, al.action, al.created_at,
               u.display AS user_display, u.avatar AS user_avatar,
               m.title AS media_title, m.type AS media_type, m.id AS media_id
        FROM activity_log al
        JOIN users u ON al.user_id = u.id
        JOIN media m ON al.media_id = m.id
        ORDER BY al.created_at DESC
        LIMIT 200
    """).fetchall()
    # Stats
    total_plays = db.execute("SELECT COUNT(*) FROM activity_log").fetchone()[0]
    most_played = db.execute("""
        SELECT m.title, m.type, COUNT(*) cnt
        FROM activity_log al JOIN media m ON al.media_id=m.id
        GROUP BY al.media_id ORDER BY cnt DESC LIMIT 5
    """).fetchall()
    active_users = db.execute("""
        SELECT u.display, u.avatar, COUNT(*) cnt
        FROM activity_log al JOIN users u ON al.user_id=u.id
        GROUP BY al.user_id ORDER BY cnt DESC LIMIT 5
    """).fetchall()
    db.close()

    type_icons = {'video':'🎬','audio':'🎵','image':'🖼️'}

    activity_html = ''
    for r in rows:
        icon = type_icons.get(r['media_type'], '📄')
        try:
            ts = datetime.fromisoformat(r['created_at']).strftime('%d/%m/%Y %H:%M')
        except Exception:
            ts = r['created_at'] or '—'
        safe_title   = html_mod.escape(r['media_title'])
        safe_display = html_mod.escape(r['user_display'])
        safe_avatar  = html_mod.escape(r['user_avatar'])
        activity_html += f"""
        <div class="activity-row">
          <div class="activity-icon">{icon}</div>
          <div class="activity-info">
            <div class="activity-title">
              <a href="/play/{r['media_id']}" style="color:var(--text)">{safe_title}</a>
            </div>
            <div class="activity-meta">{safe_avatar} {safe_display}</div>
          </div>
          <div class="activity-time">{ts}</div>
        </div>"""

    # Top media
    top_media_html = ''
    for i, mp in enumerate(most_played, 1):
        icon = type_icons.get(mp['type'], '📄')
        safe_t = html_mod.escape(mp['title'])
        top_media_html += f"""
        <tr><td style="color:var(--accent);font-weight:700">{i}</td>
        <td>{icon} {safe_t}</td>
        <td style="color:var(--accent);font-weight:600">{mp['cnt']}</td></tr>"""

    # Top users
    top_users_html = ''
    for i, au in enumerate(active_users, 1):
        safe_d = html_mod.escape(au['display'])
        safe_av = html_mod.escape(au['avatar'])
        top_users_html += f"""
        <tr><td style="color:var(--accent);font-weight:700">{i}</td>
        <td>{safe_av} {safe_d}</td>
        <td style="color:var(--accent);font-weight:600">{au['cnt']}</td></tr>"""

    body = f"""
    <div class="topbar">
      <div class="page-title">📊 Actividad</div>
    </div>
    <div class="content">
      <div class="stats-bar" style="margin-bottom:24px">
        <div class="stat-card"><div class="stat-value">{total_plays}</div><div class="stat-label">▶ Reproducciones totales</div></div>
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-bottom:24px">
        <div class="section-card">
          <div class="section-card-title">🏆 Más reproducidos</div>
          <table class="table">
            <thead><tr><th>#</th><th>Título</th><th>Veces</th></tr></thead>
            <tbody>{top_media_html or '<tr><td colspan=3 style="color:var(--text3)">Sin datos</td></tr>'}</tbody>
          </table>
        </div>
        <div class="section-card">
          <div class="section-card-title">👥 Usuarios más activos</div>
          <table class="table">
            <thead><tr><th>#</th><th>Usuario</th><th>Plays</th></tr></thead>
            <tbody>{top_users_html or '<tr><td colspan=3 style="color:var(--text3)">Sin datos</td></tr>'}</tbody>
          </table>
        </div>
      </div>
      <div class="section-card">
        <div class="section-card-title">📋 Historial reciente (últimas 200 acciones)</div>
        {activity_html or _empty('📋','Sin actividad','Nada reproducido aún')}
      </div>
    </div>"""
    return render_shell('Actividad', body, user, 'activity')

# ── Page: Profile ──────────────────────────────────────────────────────────────

def page_profile(user: dict) -> str:
    db = get_db()
    sessions = db.execute("""
        SELECT token, created_at, expires_at, ip, ua
        FROM sessions WHERE user_id=? ORDER BY created_at DESC
    """, (user['id'],)).fetchall()
    db.close()

    sess_rows = ''
    for s in sessions:
        try:
            ca = datetime.fromisoformat(s['created_at']).strftime('%d/%m/%Y %H:%M')
        except (ValueError, TypeError):
            ca = s['created_at'] or '—'
        safe_ip = html_mod.escape(s['ip'] or '—')
        safe_ua = html_mod.escape((s['ua'] or '—')[:60])
        sess_rows += f"""<tr>
          <td>🖥️ {safe_ip}</td>
          <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
            font-size:11px;color:var(--text3)">{safe_ua}</td>
          <td>{ca}</td>
          <td><button class="btn btn-ghost btn-sm"
            onclick="revokeSession('{s['token']}')">Cerrar</button></td>
        </tr>"""

    safe_display  = html_mod.escape(user['display'])
    safe_avatar   = html_mod.escape(user['avatar'])
    safe_username = html_mod.escape(user['username'])
    body = f"""
    <div class="topbar"><div class="page-title">👤 Mi perfil</div></div>
    <div class="content" style="max-width:680px">
      <div class="section-card">
        <div class="section-card-title">Información</div>
        <div style="display:flex;align-items:center;gap:20px;margin-bottom:24px">
          <div style="font-size:56px">{safe_avatar}</div>
          <div>
            <div style="font-size:22px;font-weight:700">{safe_display}</div>
            <div style="font-size:13px;color:var(--text2)">@{safe_username}</div>
            <div style="margin-top:6px">
              <span class="pill {'pill-admin' if user['role']=='admin' else 'pill-viewer'}">
                {'Administrador' if user['role']=='admin' else 'Espectador'}
              </span>
            </div>
          </div>
        </div>
        <div class="form-group">
          <label class="form-label">Nombre para mostrar</label>
          <input class="form-input" id="p-display" value="{safe_display}"/>
        </div>
        <div class="form-group">
          <label class="form-label">Avatar</label>
          <input class="form-input" id="p-avatar" value="{safe_avatar}" style="width:60px;text-align:center"/>
        </div>
        <button class="btn btn-primary" onclick="saveProfile()">Guardar cambios</button>
      </div>

      <div class="section-card">
        <div class="section-card-title">Cambiar contraseña</div>
        <div class="form-group">
          <label class="form-label">Contraseña actual</label>
          <input class="form-input" type="password" id="p-old"/>
        </div>
        <div class="form-group">
          <label class="form-label">Nueva contraseña</label>
          <input class="form-input" type="password" id="p-new"/>
        </div>
        <div class="form-group">
          <label class="form-label">Confirmar nueva contraseña</label>
          <input class="form-input" type="password" id="p-new2"/>
        </div>
        <button class="btn btn-primary" onclick="changePass()">Cambiar contraseña</button>
      </div>

      <div class="section-card">
        <div class="section-card-title">Sesiones activas ({len(sessions)})</div>
        <table class="table">
          <thead><tr><th>IP</th><th>Navegador</th><th>Inicio</th><th></th></tr></thead>
          <tbody>{sess_rows}</tbody>
        </table>
        <div style="margin-top:12px">
          <button class="btn btn-danger btn-sm" onclick="revokeAll()">Cerrar todas las sesiones</button>
        </div>
      </div>

      <div style="text-align:right;margin-bottom:24px">
        <a href="/logout" class="btn btn-ghost">🚪 Cerrar sesión</a>
      </div>
    </div>
    <script>
    function saveProfile(){{
      fetch('/api/profile',{{method:'POST',headers:{{'Content-Type':'application/json'}},
        body:JSON.stringify({{
          display:document.getElementById('p-display').value.trim(),
          avatar:document.getElementById('p-avatar').value.trim()
        }})}})
        .then(r=>r.json()).then(d=>{{
          if(d.ok){{toast('Perfil actualizado','success');setTimeout(()=>location.reload(),900);}}
          else toast('Error: '+d.error,'error');
        }});
    }}
    function changePass(){{
      const old=document.getElementById('p-old').value;
      const n=document.getElementById('p-new').value;
      const n2=document.getElementById('p-new2').value;
      if(!old||!n){{toast('Rellena los campos','error');return;}}
      if(n.length<8){{toast('La contraseña debe tener 8+ caracteres','error');return;}}
      if(n!==n2){{toast('Las contraseñas no coinciden','error');return;}}
      fetch('/api/profile/password',{{method:'POST',headers:{{'Content-Type':'application/json'}},
        body:JSON.stringify({{old_password:old,new_password:n}})}})
        .then(r=>r.json()).then(d=>{{
          if(d.ok) toast('Contraseña actualizada','success');
          else toast('Error: '+d.error,'error');
        }});
    }}
    function revokeSession(token){{
      fetch('/api/sessions/'+token,{{method:'DELETE'}})
        .then(r=>r.json()).then(d=>{{
          if(d.ok){{toast('Sesión cerrada','success');setTimeout(()=>location.reload(),800);}}
        }});
    }}
    function revokeAll(){{
      if(!confirm('¿Cerrar todas las sesiones? Tendrás que volver a iniciar sesión.'))return;
      fetch('/api/sessions',{{method:'DELETE'}})
        .then(r=>r.json()).then(d=>{{
          if(d.ok) window.location='/login';
        }});
    }}
    </script>"""
    return render_shell('Mi perfil', body, user)

# ── Page: Play ─────────────────────────────────────────────────────────────────

def page_play(user: dict, media_id: str) -> tuple:
    db = get_db()
    m  = db.execute("SELECT * FROM media WHERE id=?", (media_id,)).fetchone()
    if not m:
        db.close()
        return render_shell('No encontrado',
            _empty('🔍','Archivo no encontrado','<a href="/" class="btn btn-ghost">← Volver</a>'),
            user), 404

    if m['library_id']:
        siblings = db.execute(
            "SELECT id, title, type FROM media WHERE library_id=? ORDER BY title COLLATE NOCASE",
            (m['library_id'],)
        ).fetchall()
    else:
        siblings = db.execute(
            "SELECT id, title, type FROM media WHERE type=? ORDER BY title COLLATE NOCASE", (m['type'],)
        ).fetchall()
    db.close()

    sib_ids = [r['id'] for r in siblings]
    try:
        idx = sib_ids.index(media_id)
    except ValueError:
        idx = -1

    prev_item = siblings[idx - 1] if idx > 0 else None
    next_item = siblings[idx + 1] if idx >= 0 and idx < len(sib_ids) - 1 else None

    stream     = f'/stream/{media_id}'
    mime       = get_mime(m['path'])
    safe_title = html_mod.escape(m['title'])
    safe_path  = html_mod.escape(m['path'])
    safe_artist = html_mod.escape(m['artist'] or '')
    safe_album  = html_mod.escape(m['album'] or '')
    saved_pos  = float(m['position'] or 0)
    saved_prog = float(m['progress'] or 0)

    extra_js = ''
    queue_panel = ''

    if m['type'] == 'video':
        player = f'''<video id="media-el" controls autoplay preload="auto" style="width:100%;display:block">
  <source src="{stream}" type="{html_mod.escape(mime)}">
  Tu navegador no soporta este vídeo.
</video>'''
        player_class = 'video-player'
        next_url = f'/play/{next_item["id"]}' if next_item else 'null'
        prev_url = f'/play/{prev_item["id"]}' if prev_item else 'null'
        extra_js = f"""
const el = document.getElementById('media-el');
const SAVE_INT = 8000;
let lastSave = 0;
if ({saved_pos} > 5) el.addEventListener('loadedmetadata', ()=>{{ el.currentTime={saved_pos}; }});
el.addEventListener('loadedmetadata', ()=>{{
  // Register with mini player
  PyLexMP.register({{
    id:      '{media_id}',
    title:   {json.dumps(m['title'])},
    artist:  {json.dumps(m['artist'] or '')},
    album:   {json.dumps(m['album'] or '')},
    type:    'video',
    position: {saved_pos},
    prev_id: {json.dumps(prev_item['id'] if prev_item else None)},
    next_id: {json.dumps(next_item['id'] if next_item else None)},
  }}, el);
}});
el.addEventListener('timeupdate', ()=>{{
  const now = Date.now();
  if(now - lastSave < SAVE_INT) return;
  lastSave = now;
  const prog = el.duration ? el.currentTime/el.duration : 0;
  fetch('/api/play/{media_id}',{{method:'POST',headers:{{'Content-Type':'application/json'}},
    body:JSON.stringify({{progress:prog,position:el.currentTime}})}});
}});
el.addEventListener('ended', ()=>{{
  const nx = '{next_url}';
  if(nx && nx!='null') window.location=nx;
}});
// Auto Picture-in-Picture when user navigates away
document.addEventListener('visibilitychange', ()=>{{
  if(document.visibilityState === 'hidden' && !el.paused &&
     document.pictureInPictureEnabled && !document.pictureInPictureElement){{
    el.requestPictureInPicture().catch(()=>{{}});
  }}
}});"""

    elif m['type'] == 'audio':
        meta_line = ''
        if safe_artist: meta_line += f'<div class="audio-artist">{safe_artist}</div>'
        if safe_album:  meta_line += f'<div class="audio-album">💿 {safe_album}</div>'
        art_html = f'''<img src="/thumb/{media_id}" class="audio-art-img"
          onerror="this.style.display='none';document.getElementById('audio-art-fallback').style.display='flex'">
        <div class="audio-art" id="audio-art-fallback" style="display:none">🎵</div>'''
        player = f'''<div class="audio-player">
  <div class="audio-art-wrap">{art_html}</div>
  <div class="audio-info">
    <div class="audio-title">{safe_title}</div>
    {meta_line}
  </div>
  <audio id="media-el" controls autoplay preload="auto" class="audio-ctrl">
    <source src="{stream}" type="{html_mod.escape(mime)}">
  </audio>
</div>'''
        player_class = 'audio-wrapper'

        queue_items = siblings[idx+1:idx+21] if idx >= 0 else []
        queue_html = ''
        for qi in queue_items:
            qt = html_mod.escape(qi['title'][:40])
            queue_html += f'<a href="/play/{qi["id"]}" class="queue-item"><span class="queue-icon">🎵</span>{qt}</a>'
        queue_panel = f'''<div class="queue-panel">
  <div class="queue-title">🎵 A continuación ({len(queue_items)})</div>
  <div class="queue-list">{queue_html if queue_html else '<span style="color:var(--text3);font-size:13px">No hay más pistas</span>'}</div>
</div>'''

        next_url = f'/play/{next_item["id"]}' if next_item else 'null'
        extra_js = f"""
const el = document.getElementById('media-el');
if({saved_pos} > 2) el.addEventListener('loadedmetadata',()=>{{el.currentTime={saved_pos};}});
// Register with mini player once metadata is ready
el.addEventListener('loadedmetadata', ()=>{{
  PyLexMP.register({{
    id:      '{media_id}',
    title:   {json.dumps(m['title'])},
    artist:  {json.dumps(m['artist'] or '')},
    album:   {json.dumps(m['album'] or '')},
    type:    'audio',
    position: {saved_pos},
    prev_id: {json.dumps(prev_item['id'] if prev_item else None)},
    next_id: {json.dumps(next_item['id'] if next_item else None)},
  }}, el);
}});
let lastSave=0;
el.addEventListener('timeupdate',()=>{{
  const now=Date.now();
  if(now-lastSave<10000)return;
  lastSave=now;
  const prog=el.duration?el.currentTime/el.duration:0;
  fetch('/api/play/{media_id}',{{method:'POST',headers:{{'Content-Type':'application/json'}},
    body:JSON.stringify({{progress:prog,position:el.currentTime}})}});
}});
el.addEventListener('ended',()=>{{
  const nx='{next_url}';
  if(nx&&nx!='null')window.location=nx;
}});"""

    elif m['type'] == 'image':
        img_siblings = [s for s in siblings if s['type'] == 'image']
        img_ids = [s['id'] for s in img_siblings]
        try:
            img_idx = img_ids.index(media_id)
        except ValueError:
            img_idx = -1

        player = f'''<div id="slideshow-wrap" style="position:relative;text-align:center">
  <img id="slide-img" src="{stream}"
    style="max-width:100%;max-height:75vh;border-radius:var(--rl);display:block;margin:0 auto;
           cursor:zoom-in;transition:transform .2s ease;transform-origin:center center"
    alt="{safe_title}">
</div>
<div class="slideshow-controls">
  <button class="btn btn-ghost btn-sm" id="ss-btn" onclick="toggleSlideshow()">▶ Presentación</button>
  <select class="form-select" id="ss-speed" style="width:auto;padding:5px 10px;font-size:12px">
    <option value="3000">3 seg</option>
    <option value="5000" selected>5 seg</option>
    <option value="10000">10 seg</option>
  </select>
  <span style="font-size:12px;color:var(--text3)">{img_idx+1 if img_idx>=0 else '?'} / {len(img_ids)}</span>
</div>'''
        player_class = 'video-player'

        img_ids_json = json.dumps(img_ids)
        extra_js = f"""
const IMG_IDS={img_ids_json};
let ssTimer=null, ssRunning=false;
function toggleSlideshow(){{
  ssRunning=!ssRunning;
  document.getElementById('ss-btn').textContent=ssRunning?'⏹ Detener':'▶ Presentación';
  if(ssRunning) advanceSS(); else clearTimeout(ssTimer);
}}
function advanceSS(){{
  const spd=parseInt(document.getElementById('ss-speed').value)||5000;
  ssTimer=setTimeout(()=>{{
    const cur='{media_id}';
    const idx=IMG_IDS.indexOf(cur);
    const nxt=IMG_IDS[(idx+1)%IMG_IDS.length];
    window.location='/play/'+nxt;
  }},spd);
}}
const img=document.getElementById('slide-img');
let scale=1,ox=0,oy=0;
img.addEventListener('wheel',e=>{{
  e.preventDefault();
  scale=Math.min(4,Math.max(1,scale+(e.deltaY<0?.2:-.2)));
  img.style.transform=scale>1?`scale(${{scale}}) translate(${{ox}}px,${{oy}}px)`:'scale(1)';
  img.style.cursor=scale>1?'grab':'zoom-in';
}},{{passive:false}});
let initDist=0,initScale=1;
img.addEventListener('touchstart',e=>{{if(e.touches.length===2){{
  initDist=Math.hypot(e.touches[0].clientX-e.touches[1].clientX,
                     e.touches[0].clientY-e.touches[1].clientY);
  initScale=scale;
}}}});
img.addEventListener('touchmove',e=>{{if(e.touches.length===2){{
  e.preventDefault();
  const d=Math.hypot(e.touches[0].clientX-e.touches[1].clientX,
                    e.touches[0].clientY-e.touches[1].clientY);
  scale=Math.min(4,Math.max(1,initScale*(d/initDist)));
  img.style.transform=scale>1?`scale(${{scale}})`:'scale(1)';
}}}},{{passive:false}});
img.addEventListener('dblclick',()=>{{scale=1;img.style.transform='scale(1)';img.style.cursor='zoom-in';}});
fetch('/api/play/{media_id}',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{progress:1,position:0}})}});"""
    else:
        player = '<p style="padding:24px;color:var(--text2)">Formato no soportado.</p>'
        player_class = 'video-player'

    back_url = f'/library/{m["library_id"]}' if m['library_id'] else '/'
    prev_btn = (f'<a href="/play/{prev_item["id"]}" class="player-nav-btn">← <span class="nav-label">{html_mod.escape(prev_item["title"][:30])}</span></a>'
                if prev_item else '<span class="player-nav-btn disabled">←</span>')
    next_btn = (f'<a href="/play/{next_item["id"]}" class="player-nav-btn"><span class="nav-label">{html_mod.escape(next_item["title"][:30])}</span> →</a>'
                if next_item else '<span class="player-nav-btn disabled">→</span>')
    pos_label = f'{idx + 1} / {len(sib_ids)}' if idx >= 0 else ''

    ext  = html_mod.escape(Path(m['path']).suffix[1:].upper())
    year = f' ({m["year"]})' if m['year'] else ''
    prog_pct = int((saved_prog or 0) * 100)
    prog_info = f'<div style="margin-top:10px"><div style="font-size:11px;color:var(--text3);margin-bottom:4px">Progreso: {prog_pct}%</div><div style="background:var(--bg3);border-radius:4px;height:4px"><div style="width:{prog_pct}%;height:4px;background:var(--accent);border-radius:4px"></div></div></div>' if prog_pct > 0 else ''

    body = f"""
    <div class="topbar"><div class="page-title">▶ Reproduciendo</div></div>
    <div class="content">
      <a href="{back_url}" class="back-btn">← Volver a la biblioteca</a>
      <div class="video-container">
        <div class="{player_class}">{player}</div>
        <div class="player-nav">
          {prev_btn}
          <span style="font-size:12px;color:var(--text3);white-space:nowrap">{pos_label}</span>
          {next_btn}
        </div>
        {queue_panel}
        <div class="media-detail">
          <h2>{safe_title}{year}</h2>
          <div style="margin-bottom:16px">
            <span class="tag">{m['type'].capitalize()}</span>
            <span class="tag">{human_size(m['size'] or 0)}</span>
            <span class="tag">{ext}</span>
            {f'<span class="tag">{html_mod.escape(safe_artist)}</span>' if safe_artist else ''}
            {f'<span class="tag">💿 {html_mod.escape(safe_album)}</span>' if safe_album else ''}
          </div>
          {prog_info}
          <div style="font-size:12px;color:var(--text3);word-break:break-all;margin-top:10px">📁 {safe_path}</div>
          <div style="margin-top:16px">
            <a href="{stream}" download class="btn btn-ghost btn-sm">⬇ Descargar</a>
          </div>
        </div>
      </div>
    </div>
    <script>
    {extra_js}
    </script>"""
    return render_shell(m['title'], body, user), 200

# ── Page: Search ───────────────────────────────────────────────────────────────

def page_search(user: dict, query: str, filter_type: str = '',
                filter_year: str = '', filter_lib: str = '') -> str:
    if not query:
        return render_shell('Búsqueda', f"""
    <div class="topbar">
      <div class="page-title">🔍 Búsqueda</div>
      <div class="search-wrap"><span class="search-icon">🔍</span>
        <input class="search-input" placeholder="Buscar medios..."/></div>
    </div>
    <div class="content">{_empty('🔍','Busca algo','Introduce un término en la barra superior')}</div>""", user)

    db = get_db()
    like = f'%{query}%'
    base_q = """SELECT m.*, l.name AS lib_name FROM media m
        LEFT JOIN libraries l ON m.library_id = l.id
        WHERE (m.title LIKE ? OR m.artist LIKE ? OR m.album LIKE ?
               OR m.genre LIKE ? OR m.path LIKE ?)"""
    params = [like, like, like, like, like]
    if filter_type:
        base_q += " AND m.type=?"
        params.append(filter_type)
    if filter_year:
        try:
            base_q += " AND m.year=?"
            params.append(int(filter_year))
        except (ValueError, TypeError):
            pass
    if filter_lib:
        try:
            base_q += " AND m.library_id=?"
            params.append(int(filter_lib))
        except (ValueError, TypeError):
            pass
    base_q += " ORDER BY m.title COLLATE NOCASE LIMIT 200"
    results = db.execute(base_q, params).fetchall()

    # Libraries for filter dropdown
    all_libs = db.execute("SELECT id, name FROM libraries ORDER BY name COLLATE NOCASE").fetchall()
    # Available years in results
    all_years = sorted(set(r['year'] for r in results if r['year']), reverse=True)
    db.close()

    safe_q = html_mod.escape(query)
    base_url = f'/search?q={quote(query)}'

    def tlink(t, lbl, cnt=None):
        active = 'active' if filter_type == t else ''
        label = f'{lbl} ({cnt})' if cnt is not None else lbl
        return f'<a href="{base_url}&type={t}&year={filter_year}&lib={filter_lib}" class="sort-btn {active}">{label}</a>'

    by_type = {'video': [], 'audio': [], 'image': []}
    for r in results:
        by_type.get(r['type'], []).append(r)

    # Build type filters
    type_labels = {'video': '🎬 Vídeos', 'audio': '🎵 Música', 'image': '🖼️ Fotos'}
    type_filters = f'<a href="{base_url}&year={filter_year}&lib={filter_lib}" class="sort-btn {"active" if not filter_type else ""}">Todos ({len(results)})</a>'
    for t, lbl in type_labels.items():
        cnt = len(by_type[t])
        if cnt:
            type_filters += tlink(t, lbl, cnt)

    # Year filter dropdown
    year_options = '<option value="">Todos los años</option>'
    for y in all_years:
        sel = 'selected' if str(y) == filter_year else ''
        year_options += f'<option value="{y}" {sel}>{y}</option>'

    # Library filter dropdown
    lib_options = '<option value="">Todas las bibliotecas</option>'
    for lb in all_libs:
        sel = 'selected' if str(lb['id']) == filter_lib else ''
        lib_options += f'<option value="{lb["id"]}" {sel}>{html_mod.escape(lb["name"])}</option>'

    filter_bar = f"""
    <div class="lib-controls" style="margin-bottom:16px;flex-wrap:wrap">
      <span style="font-size:12px;color:var(--text3)">Tipo:</span>
      {type_filters}
      <span class="sep">|</span>
      <select class="form-select" style="width:auto;padding:5px 10px;font-size:12px"
        onchange="location='/search?q={quote(query)}&type={filter_type}&year='+this.value+'&lib={filter_lib}'">
        {year_options}
      </select>
      <select class="form-select" style="width:auto;padding:5px 10px;font-size:12px"
        onchange="location='/search?q={quote(query)}&type={filter_type}&year={filter_year}&lib='+this.value">
        {lib_options}
      </select>
    </div>"""

    # Results grouped by type
    sections_html = ''
    if not results:
        sections_html = _empty('🔍', f'Sin resultados para "{safe_q}"', 'Prueba con otro término o ajusta los filtros')
    else:
        for t, label in type_labels.items():
            items = by_type[t]
            if not items: continue
            if filter_type and t != filter_type: continue
            sections_html += f'<div class="group-header">{label} ({len(items)})</div>'
            sections_html += f'<div class="media-grid">{"".join(_media_card(m) for m in items[:48])}</div>'

    body = f"""
    <div class="topbar">
      <div class="page-title">🔍 Búsqueda</div>
      <div class="search-wrap"><span class="search-icon">🔍</span>
        <input class="search-input" value="{safe_q}" placeholder="Buscar medios..."/></div>
    </div>
    <div class="content">
      <div class="section-header">
        <div class="section-title">Resultados para &ldquo;{safe_q}&rdquo;</div>
      </div>
      {filter_bar}
      {sections_html}
    </div>"""
    return render_shell(f'Buscar: {safe_q}', body, user)

# ── Page: Settings ─────────────────────────────────────────────────────────────

def page_settings(user: dict) -> str:
    db = get_db()
    sname = db.execute("SELECT value FROM settings WHERE key='server_name'").fetchone()
    autoscan = db.execute("SELECT value FROM settings WHERE key='auto_scan_hours'").fetchone()
    db.close()
    svname = sname['value'] if sname else 'PyLex Media Server'
    asval  = autoscan['value'] if autoscan else str(AUTO_SCAN_HOURS)
    safe_svname = html_mod.escape(svname)
    body = f"""
    <div class="topbar">
      <div class="page-title">⚙️ Ajustes</div>
      <div class="topbar-actions">
        <button class="btn btn-primary" onclick="saveSettings()">Guardar</button>
      </div>
    </div>
    <div class="content" style="max-width:600px">
      <div class="section-card">
        <div class="section-card-title">Servidor</div>
        <div class="form-group">
          <label class="form-label">Nombre del servidor</label>
          <input class="form-input" id="sname" value="{safe_svname}"/>
        </div>
        <div class="form-group">
          <label class="form-label">Puerto</label>
          <input class="form-input" value="{PORT}" disabled/>
          <div class="form-hint">Cambia PORT en el código fuente para modificarlo</div>
        </div>
        <div class="form-group">
          <label class="form-label">Auto-escaneo (horas entre cada escaneo automático, 0 = desactivado)</label>
          <input class="form-input" id="autoscan" value="{html_mod.escape(asval)}" type="number" min="0" max="168"/>
          <div class="form-hint">Requiere reiniciar el servidor para aplicar el cambio</div>
        </div>
        <div class="form-group">
          <label class="form-label">Versión</label>
          <input class="form-input" value="PyLex 1.2.0" disabled/>
        </div>
      </div>
    </div>
    <script>
    function saveSettings(){{
      fetch('/api/settings',{{method:'POST',headers:{{'Content-Type':'application/json'}},
        body:JSON.stringify({{
          server_name:document.getElementById('sname').value,
          auto_scan_hours:document.getElementById('autoscan').value
        }})}})
        .then(r=>r.json()).then(d=>{{
          if(d.ok) toast('Ajustes guardados','success');
          else toast('Error','error');
        }});
    }}
    </script>"""
    return render_shell('Ajustes', body, user, 'settings')

# ── Library page JS ────────────────────────────────────────────────────────────
_LIBRARIES_JS = """
function addLib() {
  const name = document.getElementById('lib-name').value.trim();
  const path = document.getElementById('lib-path').value.trim();
  const type = document.getElementById('lib-type').value;
  if (!name || !path) { toast('Rellena todos los campos', 'error'); return; }
  const btn = document.getElementById('add-lib-btn');
  btn.disabled = true; btn.textContent = 'Escaneando\u2026';
  fetch('/api/libraries', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({name, path, type})
  })
  .then(r => r.json().then(d => {
    btn.disabled = false; btn.textContent = 'Crear y Escanear';
    if (d.ok) {
      toast('\u2705 Biblioteca creada \u00b7 ' + d.count + ' archivos encontrados', 'success');
      setTimeout(() => location.reload(), 1200);
    } else {
      toast('\u274c Error: ' + d.error, 'error');
    }
  }))
  .catch(err => {
    btn.disabled = false; btn.textContent = 'Crear y Escanear';
    toast('\u274c Error de red: ' + err, 'error');
  });
}
function scanLib(id) {
  toast('Escaneo iniciado\u2026', 'info');
  fetch('/api/scan/' + id, {method: 'POST'})
    .then(r => r.json()).then(d => {
      if (d.ok) {
        toast('\u2705 Escaneo en curso, recarga en unos segundos', 'success');
        setTimeout(() => location.reload(), 4000);
      } else {
        toast('\u274c Error: ' + d.error, 'error');
      }
    });
}
function delLib(id, name) {
  if (!confirm('\u00bfEliminar la biblioteca "' + name + '"?\\nSe eliminar\u00e1n todos sus registros de medios.')) return;
  fetch('/api/libraries/' + id, {method: 'DELETE'})
    .then(r => r.json().then(d => {
      if (d.ok) {
        toast('\u2705 Biblioteca eliminada', 'success');
        setTimeout(() => location.reload(), 800);
      } else {
        toast('\u274c Error: ' + d.error, 'error');
      }
    }))
    .catch(err => {
      toast('\u274c Error de red: ' + err, 'error');
    });
}
"""

# ── Auto-scan worker ────────────────────────────────────────────────────────────

def _auto_scan_worker(interval_hours: int):
    """Daemon thread: re-scans all libraries every interval_hours hours."""
    log.info("Auto-scan activado cada %d horas", interval_hours)
    while True:
        time.sleep(interval_hours * 3600)
        try:
            db = get_db()
            libs = db.execute("SELECT id, path, type FROM libraries").fetchall()
            db.close()
            for lib in libs:
                log.info("Auto-scan biblioteca %d: %s", lib['id'], lib['path'])
                scan_library(lib['id'], lib['path'], lib['type'])
        except Exception as e:
            log.error("Error en auto-scan: %s", e)


class PyLexHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        method = self.command if hasattr(self, 'command') else '?'
        if method in ('POST', 'DELETE', 'PUT') or (args and str(args[1]) >= '400'):
            log.info("[%s] %s %s %s", self.client_address[0], method,
                     self.path, args[1] if len(args) > 1 else '')

    def get_token(self) -> str:
        return parse_cookie(self.headers.get('Cookie', '')).get(COOKIE_NAME, '')

    def current_user(self):
        return get_session_user(self.get_token())

    def send_html(self, html: str, status: int = 200, extra_headers: list = None):
        body = html.encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        for h, v in (extra_headers or []):
            self.send_header(h, v)
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, data: dict, status: int = 200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def redirect(self, url: str, extra_headers: list = None):
        self.send_response(302)
        self.send_header('Location', url)
        for h, v in (extra_headers or []):
            self.send_header(h, v)
        self.end_headers()

    def require_admin(self, user: dict) -> bool:
        if not user or user['role'] != 'admin':
            self.send_json({'ok': False, 'error': 'Solo administradores'}, 403)
            return False
        return True

    def read_json(self) -> dict:
        try:
            length = int(self.headers.get('Content-Length', 0))
            if length > 1_048_576:
                return {}
            return json.loads(self.rfile.read(length)) if length else {}
        except (json.JSONDecodeError, ValueError):
            return {}

    def get_ip(self) -> str:
        real_ip = self.client_address[0]
        fwd = self.headers.get('X-Forwarded-For', '').split(',')[0].strip()
        real_ip_obj = None
        try:
            real_ip_obj = ipaddress.ip_address(real_ip)
        except ValueError:
            pass
        if fwd and real_ip_obj and real_ip_obj.is_private:
            try:
                return str(ipaddress.ip_address(fwd))
            except ValueError:
                pass
        return real_ip

    # ── GET ────────────────────────────────────────────────────────────────────

    def do_GET(self):
        parsed = urlparse(self.path)
        path   = parsed.path.rstrip('/') or '/'
        qs     = parse_qs(parsed.query)

        if needs_setup() and path != '/api/setup':
            self.send_html(page_setup())
            return

        if path == '/login':
            next_url = qs.get('next', ['/'])[0]
            self.send_html(page_login(next_url))
            return
        if path == '/logout':
            token = self.get_token()
            if token:
                revoke_session(token)
            self.redirect('/login', [('Set-Cookie', clear_session_cookie())])
            return

        user = self.current_user()
        if not user:
            self.redirect(f'/login?next={quote(path)}')
            return

        try:
            if path == '/':
                self.send_html(page_home(user))
            elif path in ('/movies', '/music', '/photos'):
                self.redirect('/')
            elif path.startswith('/library/'):
                sort = qs.get('sort', ['name'])[0]
                view = qs.get('view', ['grid'])[0]
                html, status = page_library(user, path[9:], sort=sort, view=view)
                self.send_html(html, status)
            elif path == '/libraries':
                if user['role'] != 'admin':
                    self.redirect('/'); return
                self.send_html(page_libraries(user))
            elif path == '/admin/users':
                if user['role'] != 'admin':
                    self.redirect('/'); return
                self.send_html(page_users(user))
            elif path == '/activity':
                if user['role'] != 'admin':
                    self.redirect('/'); return
                self.send_html(page_activity(user))
            elif path == '/profile':
                self.send_html(page_profile(user))
            elif path == '/settings':
                if user['role'] != 'admin':
                    self.redirect('/'); return
                self.send_html(page_settings(user))
            elif path.startswith('/play/'):
                html, status = page_play(user, path[6:])
                self.send_html(html, status)
            elif path.startswith('/thumb/'):
                self.handle_thumb(path[7:])
            elif path.startswith('/stream/'):
                self.handle_stream(path[8:])
            elif path == '/search':
                self.send_html(page_search(
                    user,
                    qs.get('q', [''])[0],
                    qs.get('type', [''])[0],
                    qs.get('year', [''])[0],
                    qs.get('lib',  [''])[0],
                ))
            elif path == '/api/debug':
                if user['role'] != 'admin':
                    self.send_json({'ok': False, 'error': 'Solo admin'}, 403); return
                db = get_db()
                lib_cols  = [r[1] for r in db.execute("PRAGMA table_info(libraries)").fetchall()]
                med_cols  = [r[1] for r in db.execute("PRAGMA table_info(media)").fetchall()]
                lib_count = db.execute("SELECT COUNT(*) FROM libraries").fetchone()[0]
                med_count = db.execute("SELECT COUNT(*) FROM media").fetchone()[0]
                libs      = [dict(r) for r in db.execute("SELECT id,name,path,type FROM libraries").fetchall()]
                db.close()
                self.send_json({
                    'ok': True, 'user': user['username'], 'role': user['role'],
                    'libraries_count': lib_count, 'media_count': med_count,
                    'libraries': libs,
                    'libraries_columns': lib_cols, 'media_columns': med_cols,
                    'db_path': DB_PATH, 'db_exists': os.path.isfile(DB_PATH),
                    'db_size_bytes': os.path.getsize(DB_PATH) if os.path.isfile(DB_PATH) else 0,
                    'python_version': sys.version,
                    'cwd': os.getcwd(),
                })
            else:
                self.send_html('<h1 style="font-family:sans-serif;padding:40px">404 — Página no encontrada</h1>', 404)
        except Exception as e:
            log.error("Error en GET %s: %s", path, e, exc_info=True)
            self.send_html(f'<pre style="padding:24px;color:red">Error: {html_mod.escape(str(e))}</pre>', 500)

    # ── POST ───────────────────────────────────────────────────────────────────

    def do_POST(self):
        path = urlparse(self.path).path.rstrip('/')
        body = self.read_json()

        if path == '/api/setup':
            if not needs_setup():
                self.send_json({'ok': False, 'error': 'Ya configurado'})
                return
            un  = body.get('username', '').strip().lower()
            dis = body.get('display', '').strip()
            pw  = body.get('password', '')
            if not un or not dis or len(pw) < 8:
                self.send_json({'ok': False, 'error': 'Datos insuficientes'})
                return
            h, s = hash_password(pw)
            db   = get_db()
            try:
                db.execute("INSERT INTO users(username,display,pw_hash,pw_salt,role,avatar) VALUES(?,?,?,?,?,?)",
                           (un, dis, h, s, 'admin', '🎬'))
                db.commit()
                uid = db.execute("SELECT id FROM users WHERE username=?", (un,)).fetchone()['id']
                db.close()
                token = create_session(uid, self.get_ip(), self.headers.get('User-Agent', ''))
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Set-Cookie', make_session_cookie(token))
                body_b = json.dumps({'ok': True}).encode()
                self.send_header('Content-Length', str(len(body_b)))
                self.end_headers()
                self.wfile.write(body_b)
            except sqlite3.IntegrityError:
                db.close()
                self.send_json({'ok': False, 'error': 'El usuario ya existe'})
            return

        if path == '/api/login':
            un = body.get('username', '').strip().lower()
            pw = body.get('password', '')
            db = get_db()
            u  = db.execute("SELECT * FROM users WHERE username=?", (un,)).fetchone()
            db.close()
            if not u or not verify_password(pw, u['pw_hash'], u['pw_salt']):
                self.send_json({'ok': False, 'error': 'Credenciales incorrectas'})
                return
            token = create_session(u['id'], self.get_ip(), self.headers.get('User-Agent', ''))
            days  = SESSION_DAYS if body.get('remember') else 1
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Set-Cookie', make_session_cookie(token, days * 86400))
            resp = json.dumps({'ok': True}).encode()
            self.send_header('Content-Length', str(len(resp)))
            self.end_headers()
            self.wfile.write(resp)
            return

        user = self.current_user()
        if not user:
            self.send_json({'ok': False, 'error': 'No autenticado'}, 401)
            return

        try:
            if path == '/api/libraries':
                if not self.require_admin(user): return
                name  = body.get('name', '').strip()
                fpath = body.get('path', '').strip()
                ltype = body.get('type', 'other')
                if not name or not fpath:
                    self.send_json({'ok': False, 'error': 'Faltan datos'}); return

                fpath = os.path.normpath(fpath)
                fpath = os.path.realpath(fpath)

                if not os.path.isdir(fpath):
                    self.send_json({'ok': False, 'error': f'Ruta no encontrada: {fpath}'}); return

                fpath_lower = fpath.lower()
                BLOCKED = {'/', '/etc', '/bin', '/sbin', '/usr', '/usr/bin',
                           '/usr/sbin', '/boot', '/sys', '/proc', '/dev', '/run'}
                BLOCKED_PREFIXES_UNIX = ('/etc/', '/proc/', '/sys/', '/dev/')
                BLOCKED_PREFIXES_WIN  = ('c:\\windows', 'c:\\program files', 'c:\\users\\default')
                is_blocked = (
                    fpath in BLOCKED
                    or any(fpath.startswith(p) for p in BLOCKED_PREFIXES_UNIX)
                    or any(fpath_lower.startswith(p) for p in BLOCKED_PREFIXES_WIN)
                )
                if is_blocked:
                    self.send_json({'ok': False, 'error': 'Ruta de sistema no permitida'}); return

                db = get_db()
                c  = db.cursor()
                c.execute("INSERT INTO libraries(name,path,type,created_by) VALUES(?,?,?,?)",
                          (name, fpath, ltype, user['id']))
                lid = c.lastrowid
                db.commit(); db.close()

                count = scan_library(lid, fpath, ltype)
                self.send_json({'ok': True, 'id': lid, 'count': count})

            elif path.startswith('/api/scan/'):
                if not self.require_admin(user): return
                lid = int(path[10:])
                db  = get_db()
                lib = db.execute("SELECT * FROM libraries WHERE id=?", (lid,)).fetchone()
                db.close()
                if not lib: self.send_json({'ok': False, 'error': 'No encontrada'}); return
                threading.Thread(target=scan_library,
                                 args=(lid, lib['path'], lib['type']), daemon=True).start()
                self.send_json({'ok': True, 'message': 'Escaneo iniciado en segundo plano'})

            elif path == '/api/users':
                if not self.require_admin(user): return
                un   = body.get('username', '').strip().lower()
                dis  = body.get('display', '').strip()
                pw   = body.get('password', '')
                role = body.get('role', 'viewer')
                av   = body.get('avatar', '👤')
                if not un or not dis or len(pw) < 8:
                    self.send_json({'ok': False, 'error': 'Datos insuficientes'}); return
                h, s = hash_password(pw)
                db = get_db()
                try:
                    db.execute("INSERT INTO users(username,display,pw_hash,pw_salt,role,avatar) VALUES(?,?,?,?,?,?)",
                               (un, dis, h, s, role, av))
                    db.commit(); db.close()
                    self.send_json({'ok': True})
                except sqlite3.IntegrityError:
                    db.close()
                    self.send_json({'ok': False, 'error': 'El usuario ya existe'})

            elif re.match(r'^/api/users/\d+/role$', path):
                if not self.require_admin(user): return
                uid  = int(path.split('/')[3])
                role = body.get('role', 'viewer')
                if uid == user['id']:
                    self.send_json({'ok': False, 'error': 'No puedes cambiar tu propio rol'}); return
                db = get_db()
                db.execute("UPDATE users SET role=? WHERE id=?", (role, uid))
                db.commit(); db.close()
                self.send_json({'ok': True})

            elif path.startswith('/api/play/'):
                mid      = path[10:]
                progress = body.get('progress')
                position = body.get('position')
                db  = get_db()
                if progress is not None:
                    db.execute("""UPDATE media SET play_count=play_count+1,
                        last_played=?, progress=?, position=? WHERE id=?""",
                        (datetime.now().isoformat(), float(progress),
                         float(position or 0), mid))
                else:
                    db.execute("UPDATE media SET play_count=play_count+1, last_played=? WHERE id=?",
                               (datetime.now().isoformat(), mid))
                db.execute("INSERT INTO activity_log(user_id, media_id, action) VALUES(?,?,?)",
                           (user['id'], mid, 'play'))
                db.commit(); db.close()
                self.send_json({'ok': True})

            elif path == '/api/profile':
                dis = body.get('display', '').strip()
                av  = body.get('avatar', '').strip()
                if not dis: self.send_json({'ok': False, 'error': 'Nombre vacío'}); return
                db = get_db()
                db.execute("UPDATE users SET display=?, avatar=? WHERE id=?",
                           (dis, av or user['avatar'], user['id']))
                db.commit(); db.close()
                self.send_json({'ok': True})

            elif path == '/api/profile/password':
                old = body.get('old_password', '')
                new = body.get('new_password', '')
                db  = get_db()
                u   = db.execute("SELECT * FROM users WHERE id=?", (user['id'],)).fetchone()
                if not verify_password(old, u['pw_hash'], u['pw_salt']):
                    db.close()
                    self.send_json({'ok': False, 'error': 'Contraseña actual incorrecta'}); return
                if len(new) < 8:
                    db.close()
                    self.send_json({'ok': False, 'error': 'Mínimo 8 caracteres'}); return
                h, s = hash_password(new)
                db.execute("UPDATE users SET pw_hash=?, pw_salt=? WHERE id=?", (h, s, user['id']))
                db.commit(); db.close()
                self.send_json({'ok': True})

            elif path == '/api/settings':
                if not self.require_admin(user): return
                sn = body.get('server_name', '').strip()
                asc = body.get('auto_scan_hours', '').strip()
                db = get_db()
                if sn:
                    db.execute("INSERT OR REPLACE INTO settings VALUES('server_name',?)", (sn,))
                if asc:
                    db.execute("INSERT OR REPLACE INTO settings VALUES('auto_scan_hours',?)", (asc,))
                db.commit(); db.close()
                self.send_json({'ok': True})

            else:
                self.send_json({'ok': False, 'error': 'Not found'}, 404)
        except Exception as e:
            log.error("Error en POST %s: %s", path, e, exc_info=True)
            self.send_json({'ok': False, 'error': str(e)}, 500)

    # ── DELETE ─────────────────────────────────────────────────────────────────

    def do_DELETE(self):
        path = urlparse(self.path).path.rstrip('/')
        user = self.current_user()
        if not user:
            self.send_json({'ok': False, 'error': 'No autenticado'}, 401)
            return
        try:
            if path.startswith('/api/libraries/'):
                if not self.require_admin(user): return
                lid = int(path[15:])
                db  = get_db()
                db.execute("DELETE FROM media WHERE library_id=?", (lid,))
                db.execute("DELETE FROM libraries WHERE id=?", (lid,))
                db.commit(); db.close()
                self.send_json({'ok': True})

            elif path.startswith('/api/users/') and not path.endswith('/role'):
                if not self.require_admin(user): return
                uid = int(path[11:])
                if uid == user['id']:
                    self.send_json({'ok': False, 'error': 'No puedes eliminarte a ti mismo'}); return
                db = get_db()
                db.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
                db.execute("DELETE FROM users WHERE id=?", (uid,))
                db.commit(); db.close()
                self.send_json({'ok': True})

            elif path.startswith('/api/sessions/') and path != '/api/sessions':
                token = path[14:]
                db = get_db()
                s  = db.execute("SELECT user_id FROM sessions WHERE token=?", (token,)).fetchone()
                db.close()
                if s and s['user_id'] == user['id']:
                    revoke_session(token)
                    self.send_json({'ok': True})
                else:
                    self.send_json({'ok': False, 'error': 'No autorizado'}, 403)

            elif path == '/api/sessions':
                db = get_db()
                db.execute("DELETE FROM sessions WHERE user_id=?", (user['id'],))
                db.commit(); db.close()
                self.send_json({'ok': True})

            else:
                self.send_json({'ok': False, 'error': 'Not found'}, 404)
        except Exception as e:
            log.error("Error en DELETE %s: %s", path, e, exc_info=True)
            self.send_json({'ok': False, 'error': str(e)}, 500)

    # ── Thumbnails ─────────────────────────────────────────────────────────────

    def handle_thumb(self, media_id: str):
        if not re.fullmatch(r'[0-9a-f]{32}', media_id):
            self.send_response(400); self.end_headers(); return

        user = self.current_user()
        if not user:
            self.send_response(401); self.end_headers(); return

        db = get_db()
        m  = db.execute("SELECT id, path, type FROM media WHERE id=?", (media_id,)).fetchone()
        db.close()

        if not m or not os.path.isfile(m['path']):
            self.send_response(404); self.end_headers(); return

        thumb = get_or_make_thumb(m['id'], m['path'], m['type'])

        if not thumb or not os.path.isfile(thumb):
            self.send_response(404); self.end_headers(); return

        fsize = os.path.getsize(thumb)
        mime = get_mime(thumb) if m['type'] == 'image' else 'image/jpeg'

        self.send_response(200)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(fsize))
        self.send_header('Cache-Control', 'public, max-age=86400')
        self.end_headers()
        try:
            with open(thumb, 'rb') as f:
                while True:
                    chunk = f.read(CHUNK_SIZE)
                    if not chunk: break
                    self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass

    # ── Streaming ──────────────────────────────────────────────────────────────

    def handle_stream(self, media_id: str):
        if not re.fullmatch(r'[0-9a-f]{32}', media_id):
            self.send_response(400); self.end_headers(); return

        user = self.current_user()
        if not user:
            self.send_response(401); self.end_headers(); return

        db = get_db()
        m  = db.execute("SELECT * FROM media WHERE id=?", (media_id,)).fetchone()
        db.close()
        if not m or not os.path.isfile(m['path']):
            self.send_response(404); self.end_headers(); return

        fpath = m['path']
        fsize = os.path.getsize(fpath)
        mime  = get_mime(fpath)
        rh    = self.headers.get('Range')

        try:
            if rh:
                try:
                    parts = rh.replace('bytes=', '').split('-')
                    start = int(parts[0]) if parts[0] else 0
                    end   = int(parts[1]) if parts[1] else fsize - 1
                except (ValueError, IndexError):
                    self.send_response(416); self.end_headers(); return
                if start < 0 or start >= fsize or end < start:
                    self.send_response(416); self.end_headers(); return
                end    = min(end, fsize - 1)
                length = end - start + 1
                self.send_response(206)
                self.send_header('Content-Type', mime)
                self.send_header('Content-Range', f'bytes {start}-{end}/{fsize}')
                self.send_header('Content-Length', str(length))
                self.send_header('Accept-Ranges', 'bytes')
                self.end_headers()
                with open(fpath, 'rb') as f:
                    f.seek(start)
                    rem = length
                    while rem > 0:
                        chunk = f.read(min(CHUNK_SIZE, rem))
                        if not chunk: break
                        self.wfile.write(chunk)
                        rem -= len(chunk)
            else:
                self.send_response(200)
                self.send_header('Content-Type', mime)
                self.send_header('Content-Length', str(fsize))
                self.send_header('Accept-Ranges', 'bytes')
                self.end_headers()
                with open(fpath, 'rb') as f:
                    while True:
                        chunk = f.read(CHUNK_SIZE)
                        if not chunk: break
                        self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass

# ── Network helpers ────────────────────────────────────────────────────────────

def _ip_score(ip: str) -> int:
    try:
        addr = ipaddress.ip_address(ip)
        VIRTUAL_NETS = [
            ipaddress.ip_network('192.168.56.0/24'),
            ipaddress.ip_network('192.168.99.0/24'),
            ipaddress.ip_network('192.168.100.0/24'),
            ipaddress.ip_network('172.16.0.0/12'),
            ipaddress.ip_network('10.0.2.0/24'),
        ]
        VMWARE_PREFIXES = ('192.168.10.', '192.168.137.', '192.168.110.')
        for net in VIRTUAL_NETS:
            if addr in net:
                return 100
        for prefix in VMWARE_PREFIXES:
            if ip.startswith(prefix):
                return 90
        if ip.startswith('192.168.'): return 10
        if ip.startswith('10.'):      return 20
        if ip.startswith('172.'):     return 50
        return 30
    except ValueError:
        return 200

def _is_usable_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
        if addr.is_loopback:    return False
        if addr.is_link_local:  return False
        if addr.is_multicast:   return False
        if not addr.is_private: return False
        if addr in ipaddress.ip_network('172.16.0.0/12'): return False
        return True
    except ValueError:
        return False

def get_local_ip() -> str:
    candidates = []
    for target in [('8.8.8.8', 80), ('1.1.1.1', 80)]:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(1)
            s.connect(target)
            ip = s.getsockname()[0]
            s.close()
            candidates.append(ip)
        except Exception:
            pass
    try:
        hostname = socket.gethostname()
        for addr_info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            candidates.append(addr_info[4][0])
    except Exception:
        pass
    usable = [ip for ip in candidates if _is_usable_ip(ip)]
    if usable:
        usable.sort(key=_ip_score)
        return usable[0]
    for ip in candidates:
        try:
            addr = ipaddress.ip_address(ip)
            if not addr.is_loopback:
                return ip
        except ValueError:
            pass
    return 'localhost'

import socketserver

class ThreadedHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    daemon_threads = True
    timeout = 300

def confirm_ip(detected_ip: str) -> str:
    SEP = "═"*52
    print("\n" + SEP)
    print("  🌐  Configuración de red")
    print(SEP)

    if detected_ip != 'localhost':
        candidates = []
        for target in [('8.8.8.8', 80), ('1.1.1.1', 80)]:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.settimeout(1); s.connect(target)
                candidates.append(s.getsockname()[0]); s.close()
            except Exception:
                pass
        try:
            for addr_info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
                ip = addr_info[4][0]
                if ip not in candidates:
                    candidates.append(ip)
        except Exception:
            pass
        usable = [ip for ip in candidates if _is_usable_ip(ip)]

        print(f"  IP detectada automáticamente: \033[1;33m{detected_ip}\033[0m")
        if len(usable) > 1:
            print(f"  Otras IPs disponibles:")
            for ip in usable:
                marker = "  ◀ (seleccionada)" if ip == detected_ip else ""
                print(f"    · {ip}{marker}")
        print()
        print(f"  Los dispositivos de tu red podrán acceder en:")
        print(f"  \033[1;36mhttp://{detected_ip}:{PORT}\033[0m")
        print()

        while True:
            try:
                resp = input("  ¿La IP es correcta? [S/n]: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print()
                return detected_ip
            if resp in ('', 's', 'si', 'sí', 'y', 'yes'):
                print(f"  ✅  Usando IP: {detected_ip}\n")
                return detected_ip
            elif resp in ('n', 'no'):
                break
            else:
                print("  Responde S (sí) o N (no)")
    else:
        print("  ⚠️  No se pudo detectar una IP de red automáticamente.")
        print()

    print("  Escribe la IP de este equipo en tu red local.")
    print("  Puedes consultarla con: ipconfig (Windows) / ip a (Linux/Mac)")
    print()
    while True:
        try:
            manual = input("  IP manual (o Enter para usar localhost): ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 'localhost'
        if manual == '':
            print("  ⚠️  Se usará localhost. Solo tú podrás acceder desde este equipo.\n")
            return 'localhost'
        try:
            ipaddress.ip_address(manual)
            print(f"  ✅  Usando IP manual: {manual}\n")
            return manual
        except ValueError:
            print(f"  ❌  '{manual}' no es una IP válida. Ejemplo: 192.168.1.10")


def main():
    db_existed = os.path.isfile(DB_PATH)
    init_db()
    setup = needs_setup()

    try:
        conn = sqlite3.connect(DB_PATH)
        lib_cols = [r[1] for r in conn.execute("PRAGMA table_info(libraries)").fetchall()]
        conn.close()
        migrated_ok = 'created_by' in lib_cols
    except Exception:
        migrated_ok = False

    detected = get_local_ip()
    ip = confirm_ip(detected)

    SEP = "═"*52
    print("\n" + SEP)
    print("  🎬  PyLex Media Server v1.2.0")
    print(SEP)
    print(f"  Local:   http://localhost:{PORT}")
    if ip != 'localhost':
        print(f"  Red:     \033[1;36mhttp://{ip}:{PORT}\033[0m")
    else:
        print(f"  Red:     (sin IP de red — solo acceso local)")
    if db_existed:
        status = "✅ migrada" if migrated_ok else "⚠️  ERROR DE MIGRACIÓN"
        print(f"  BD:      pylex.db existente — {status}")
    else:
        print(f"  BD:      pylex.db creada nueva")
    if setup:
        print("  ⚠️  Primera ejecución: crea tu cuenta admin")
    print(SEP)
    print(f"  Diagnóstico: http://localhost:{PORT}/api/debug  (solo admin)")
    print("  Presiona Ctrl+C para detener")
    print(SEP + "\n")

    # ── Auto-scan thread ────────────────────────────────────────────────────────
    try:
        db = get_db()
        asrow = db.execute("SELECT value FROM settings WHERE key='auto_scan_hours'").fetchone()
        db.close()
        auto_h = int(asrow['value']) if asrow else AUTO_SCAN_HOURS
    except Exception:
        auto_h = AUTO_SCAN_HOURS

    if auto_h > 0:
        t = threading.Thread(target=_auto_scan_worker, args=(auto_h,), daemon=True)
        t.start()
        log.info("Auto-scan thread started (every %d hours)", auto_h)

    server = ThreadedHTTPServer(('0.0.0.0', PORT), PyLexHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  ⏹  Servidor detenido\n")
        server.server_close()

if __name__ == '__main__':
    main()