#!/usr/bin/env python3
"""
PyLex Desktop — Cliente nativo para PyLex Media Server
======================================================
v1.0.0

Requisitos:
    pip install PyQt6 requests

Uso:
    python pylex_desktop.py

El servidor debe tener pylex_api.py aplicado (import pylex_api; pylex_api.patch(PyLexHandler))
para que los endpoints /api/* devuelvan JSON con el token en el body.
"""

import sys
import os
import json
import socket
import threading
import time
import urllib.request
import urllib.error
import http.client
import urllib.parse
from pathlib import Path
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler

import requests

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QScrollArea, QFrame, QGridLayout, QSplitter,
    QLineEdit, QDialog, QDialogButtonBox, QFormLayout, QSlider,
    QMessageBox, QStackedWidget, QProgressBar, QSizePolicy,
    QComboBox, QSpacerItem, QScrollBar, QToolTip
)
from PyQt6.QtCore import (
    Qt, QThread, pyqtSignal, QTimer, QUrl, QSize, QPoint,
    QPropertyAnimation, QEasingCurve
)
from PyQt6.QtGui import (
    QFont, QColor, QPalette, QPixmap, QIcon, QPainter,
    QBrush, QPen, QLinearGradient, QCursor
)
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from PyQt6.QtMultimediaWidgets import QVideoWidget

# ══════════════════════════════════════════════════════════════════════════════
# Constantes y helpers
# ══════════════════════════════════════════════════════════════════════════════

APP_NAME    = "PyLex Desktop"
APP_VERSION = "1.0.0"
CONFIG_FILE = Path.home() / ".config" / "pylex_desktop" / "config.json"

C = {
    'bg':      '#1a1a1f',
    'bg2':     '#222228',
    'bg3':     '#2a2a32',
    'card':    '#252530',
    'card_h':  '#2e2e3c',
    'border':  '#333344',
    'accent':  '#e5a00d',
    'accent2': '#cc8800',
    'text':    '#e8e8f0',
    'text2':   '#9898b0',
    'text3':   '#5a5a72',
    'green':   '#2ecc71',
    'red':     '#e74c3c',
    'blue':    '#3498db',
}

STYLESHEET = f"""
QMainWindow, QDialog, QWidget {{
    background: {C['bg']};
    color: {C['text']};
    font-family: 'Segoe UI', 'SF Pro Text', 'Helvetica Neue', sans-serif;
    font-size: 13px;
}}
QScrollArea, QScrollArea > QWidget > QWidget {{
    background: transparent;
    border: none;
}}
QScrollBar:vertical {{
    background: {C['bg2']}; width: 5px; border-radius: 3px; margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {C['border']}; border-radius: 3px; min-height: 20px;
}}
QScrollBar::handle:vertical:hover {{ background: {C['text3']}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{
    background: {C['bg2']}; height: 5px; border-radius: 3px; margin: 0;
}}
QScrollBar::handle:horizontal {{ background: {C['border']}; border-radius: 3px; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
QPushButton {{
    background: {C['bg3']}; color: {C['text2']};
    border: 1px solid {C['border']}; border-radius: 7px;
    padding: 7px 16px; font-weight: 600; font-size: 13px;
}}
QPushButton:hover {{
    background: {C['border']}; color: {C['text']};
}}
QPushButton:pressed {{ background: {C['bg2']}; }}
QPushButton[role="accent"] {{
    background: {C['accent']}; color: #1a1a1f; border: none;
}}
QPushButton[role="accent"]:hover {{ background: {C['accent2']}; }}
QPushButton[role="danger"] {{
    background: {C['red']}; color: #fff; border: none;
}}
QPushButton[role="icon"] {{
    background: transparent; border: none;
    padding: 5px; border-radius: 5px; font-size: 16px;
}}
QPushButton[role="icon"]:hover {{ background: {C['bg3']}; }}
QPushButton[role="nav"] {{
    background: transparent; color: {C['text2']}; border: none;
    border-radius: 8px; padding: 9px 14px;
    text-align: left; font-size: 13px; font-weight: 500;
}}
QPushButton[role="nav"]:hover {{ background: {C['bg3']}; color: {C['text']}; }}
QPushButton[role="nav"][active="true"] {{
    background: rgba(229,160,13, 0.12); color: {C['accent']};
}}
QLineEdit {{
    background: {C['bg3']}; color: {C['text']};
    border: 1px solid {C['border']}; border-radius: 8px;
    padding: 9px 12px; font-size: 13px;
}}
QLineEdit:focus {{ border-color: {C['accent']}; }}
QLabel {{ color: {C['text']}; background: transparent; }}
QSlider::groove:horizontal {{
    height: 4px; background: {C['bg3']}; border-radius: 2px;
}}
QSlider::handle:horizontal {{
    background: {C['accent']}; width: 14px; height: 14px;
    border-radius: 7px; margin: -5px 0;
}}
QSlider::sub-page:horizontal {{ background: {C['accent']}; border-radius: 2px; }}
QComboBox {{
    background: {C['bg3']}; color: {C['text']};
    border: 1px solid {C['border']}; border-radius: 7px; padding: 6px 12px;
}}
QComboBox::drop-down {{ border: none; }}
QComboBox QAbstractItemView {{
    background: {C['bg2']}; color: {C['text']};
    border: 1px solid {C['border']};
    selection-background-color: {C['bg3']};
}}
QFrame[role="card"] {{
    background: {C['card']}; border: 1px solid {C['border']}; border-radius: 12px;
}}
QFrame[role="card"]:hover {{
    background: {C['card_h']}; border-color: {C['accent']};
}}
QFrame[role="sidebar"] {{
    background: {C['bg2']}; border-right: 1px solid {C['border']};
}}
QFrame[role="topbar"] {{
    background: {C['bg2']}; border-bottom: 1px solid {C['border']};
}}
QFrame[role="player"] {{
    background: {C['bg2']}; border-top: 1px solid {C['border']};
}}
QProgressBar {{
    background: {C['bg3']}; border: none; border-radius: 2px; height: 3px;
}}
QProgressBar::chunk {{ background: {C['accent']}; border-radius: 2px; }}
QMessageBox {{ background: {C['bg2']}; }}
"""


def _human_size(n: int) -> str:
    if not n:
        return '0 B'
    for unit in ('B', 'KB', 'MB', 'GB'):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _fmt_time(s) -> str:
    if not s or s != s:
        return '0:00'
    s = int(s)
    m, sec = divmod(s, 60)
    h, m   = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m}:{sec:02d}"


def _elide(text: str, fm, width: int) -> str:
    from PyQt6.QtCore import Qt
    return fm.elidedText(text, Qt.TextElideMode.ElideRight, width)


# ══════════════════════════════════════════════════════════════════════════════
# Config
# ══════════════════════════════════════════════════════════════════════════════

class Config:
    def __init__(self):
        self._d = {'servers': [], 'active': -1, 'volume': 80, 'view': 'grid'}
        self.load()

    def load(self):
        if CONFIG_FILE.exists():
            try:
                self._d = json.loads(CONFIG_FILE.read_text('utf-8'))
            except Exception:
                pass

    def save(self):
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(self._d, indent=2), 'utf-8')

    @property
    def active(self):
        idx = self._d.get('active', -1)
        srv = self._d.get('servers', [])
        return srv[idx] if 0 <= idx < len(srv) else None

    def set_server(self, url: str, token: str, user: dict):
        url = url.rstrip('/')
        entry = {'url': url, 'token': token,
                 'username': user.get('username', ''),
                 'display':  user.get('display', ''),
                 'role':     user.get('role', 'viewer'),
                 'avatar':   user.get('avatar', '🎬')}
        for i, s in enumerate(self._d.get('servers', [])):
            if s.get('url') == url:
                self._d['servers'][i] = entry
                self._d['active'] = i
                self.save()
                return
        self._d.setdefault('servers', []).append(entry)
        self._d['active'] = len(self._d['servers']) - 1
        self.save()

    def logout(self):
        idx = self._d.get('active', -1)
        srv = self._d.get('servers', [])
        if 0 <= idx < len(srv):
            srv[idx]['token'] = ''
        self.save()

    @property
    def volume(self): return self._d.get('volume', 80)
    @volume.setter
    def volume(self, v): self._d['volume'] = v; self.save()

    @property
    def view(self): return self._d.get('view', 'grid')
    @view.setter
    def view(self, v): self._d['view'] = v; self.save()


# ══════════════════════════════════════════════════════════════════════════════
# Lenient HTTP — salta cabeceras espurias enviadas antes del status HTTP
# ══════════════════════════════════════════════════════════════════════════════

class _LenientHTTPResponse(http.client.HTTPResponse):
    def _read_status(self):
        while True:
            line = str(self.fp.readline(http.client._MAXLINE + 1), 'iso-8859-1')
            if not line:
                raise http.client.RemoteDisconnected(
                    'Remote end closed connection without response')
            line = line.rstrip('\r\n')
            if not line:
                continue
            if line.startswith('HTTP/'):
                parts = line.split(None, 2)
                try:
                    version, status = parts[0], int(parts[1])
                    reason = parts[2].strip() if len(parts) > 2 else ''
                    return version, status, reason
                except (ValueError, IndexError):
                    continue


class _LenientHTTPConnection(http.client.HTTPConnection):
    response_class = _LenientHTTPResponse


def _lenient_request(method: str, url: str, payload: dict | None = None,
                     cookie: str = '', timeout: int = 12,
                     extra_headers: dict | None = None):
    """Petición HTTP tolerante. Devuelve (status, headers_dict, body_bytes)."""
    parsed = urllib.parse.urlparse(url)
    host   = parsed.hostname
    port   = parsed.port or 80
    path   = parsed.path or '/'
    if parsed.query:
        path += '?' + parsed.query
    body   = json.dumps(payload).encode() if payload is not None else b''
    hdrs   = {
        'Content-Type':   'application/json',
        'Content-Length': str(len(body)),
        'Connection':     'close',
        'User-Agent':     f'{APP_NAME}/{APP_VERSION}',
    }
    if cookie:
        hdrs['Cookie'] = f'pylex_session={cookie}'
    if extra_headers:
        hdrs.update(extra_headers)
    conn = _LenientHTTPConnection(host, port, timeout=timeout)
    try:
        conn.request(method, path, body=body or None, headers=hdrs)
        resp   = conn.getresponse()
        status = resp.status
        rheads = dict(resp.getheaders())
        raw    = resp.read()
    finally:
        conn.close()
    return status, rheads, raw


# ══════════════════════════════════════════════════════════════════════════════
# API Client
# ══════════════════════════════════════════════════════════════════════════════

class APIClient:
    def __init__(self):
        self.base_url = ''
        self.token    = ''

    def connect(self, url: str, token: str):
        self.base_url = url.rstrip('/')
        self.token    = token

    def _u(self, p): return self.base_url + p

    def _parse(self, status: int, raw: bytes) -> dict:
        if status >= 400:
            try:
                msg = json.loads(raw).get('error', f'HTTP {status}')
            except Exception:
                msg = f'HTTP {status}'
            raise Exception(msg)
        return json.loads(raw)

    def get(self, path, **_):
        status, _, raw = _lenient_request('GET', self._u(path), cookie=self.token)
        return self._parse(status, raw)

    def post(self, path, data=None, **_):
        status, _, raw = _lenient_request('POST', self._u(path),
                                          payload=data, cookie=self.token)
        return self._parse(status, raw)

    def thumb_bytes(self, mid: str) -> bytes | None:
        try:
            status, _, raw = _lenient_request('GET', self._u(f'/thumb/{mid}'),
                                              cookie=self.token, timeout=8)
            return raw if status == 200 and raw else None
        except Exception:
            return None

    def login(self, url: str, username: str, password: str) -> dict:
        _, _, raw = _lenient_request(
            'POST', url.rstrip('/') + '/api/login',
            payload={'username': username, 'password': password, 'remember': True},
        )
        return json.loads(raw)


# ══════════════════════════════════════════════════════════════════════════════
# Auth Proxy  (inyecta la cookie en cada petición de streaming)
# ══════════════════════════════════════════════════════════════════════════════

class _ProxyHandler(BaseHTTPRequestHandler):
    cfg = {}  # {'server_url': ..., 'token': ...}

    def do_GET(self):
        server_url = self.cfg.get('server_url', '').rstrip('/')
        token      = self.cfg.get('token', '')
        target     = server_url + self.path
        print(f'[Proxy] → {target}', flush=True)

        parsed = urllib.parse.urlparse(target)
        host   = parsed.hostname
        port   = parsed.port or 80
        path   = parsed.path or '/'
        if parsed.query:
            path += '?' + parsed.query

        req_headers = {
            'Cookie':     f'pylex_session={token}',
            'Connection': 'close',
            'User-Agent': f'{APP_NAME}/{APP_VERSION}',
        }
        rh = self.headers.get('Range')
        if rh:
            req_headers['Range'] = rh

        try:
            conn = _LenientHTTPConnection(host, port, timeout=60)
            conn.request('GET', path, headers=req_headers)
            resp = conn.getresponse()
            print(f'[Proxy] ← {resp.status} {resp.reason} ({target})', flush=True)

            self.send_response(resp.status)
            for k, v in resp.getheaders():
                if k.lower() in ('content-type', 'content-length',
                                 'content-range', 'accept-ranges'):
                    self.send_header(k, v)
            self.end_headers()

            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    break
            conn.close()

        except Exception as e:
            print(f'[Proxy] ✗ Error: {e} ({target})', flush=True)
            code = getattr(e, 'code', 502)
            try:
                self.send_error(code)
            except Exception:
                pass

    def log_message(self, *a):
        pass  # silenciar log HTTP estándar (usamos print arriba)


class AuthProxy:
    def __init__(self):
        self._srv  = None
        self._port = 0

    @property
    def port(self):
        return self._port

    def start(self, server_url: str, token: str):
        self.stop()
        _ProxyHandler.cfg = {'server_url': server_url, 'token': token}
        s = socket.socket(); s.bind(('', 0)); self._port = s.getsockname()[1]; s.close()
        self._srv = HTTPServer(('127.0.0.1', self._port), _ProxyHandler)
        t = threading.Thread(target=self._srv.serve_forever, daemon=True, name='PyLexProxy')
        t.start()

    def update(self, server_url: str, token: str):
        _ProxyHandler.cfg = {'server_url': server_url, 'token': token}

    def stop(self):
        if self._srv:
            self._srv.shutdown()
            self._srv  = None
            self._port = 0

    def stream_url(self, mid: str) -> str:
        return f'http://127.0.0.1:{self._port}/stream/{mid}'


# ══════════════════════════════════════════════════════════════════════════════
# Workers
# ══════════════════════════════════════════════════════════════════════════════

class Worker(QThread):
    result = pyqtSignal(object)
    error  = pyqtSignal(str)

    def __init__(self, fn, *a, **kw):
        super().__init__()
        self._fn, self._a, self._kw = fn, a, kw

    def run(self):
        try:
            self.result.emit(self._fn(*self._a, **self._kw))
        except Exception as e:
            self.error.emit(str(e))



# Contenedor padre para todos los ThumbWorkers.
# Al asignar un QObject padre, Qt posee el objeto C++ y el GC de Python
# no lo destruirá mientras el hilo del OS todavía está activo.
# Se inicializa de forma perezosa en el primer uso (necesita QApplication activa).
_TW_CONTAINER: 'QObject | None' = None

def _tw_container() -> 'QObject':
    global _TW_CONTAINER
    if _TW_CONTAINER is None:
        from PyQt6.QtCore import QObject as _QObject
        _TW_CONTAINER = _QObject()
    return _TW_CONTAINER


class ThumbWorker(QThread):
    done = pyqtSignal(str, QPixmap)

    def __init__(self, api: APIClient, mid: str):
        # El contenedor es el padre Qt → Qt gestiona el ciclo de vida C++
        super().__init__(_tw_container())
        self._api = api
        self._mid = mid
        self.setTerminationEnabled(True)
        # deleteLater programa la destrucción C++ para el siguiente ciclo del
        # event loop, momento en que el hilo del OS ya habrá salido del todo.
        self.finished.connect(self.deleteLater)

    def run(self):
        if self.isInterruptionRequested():
            return
        data = self._api.thumb_bytes(self._mid)
        if self.isInterruptionRequested():
            return
        if data:
            px = QPixmap()
            px.loadFromData(data)
            if not px.isNull() and not self.isInterruptionRequested():
                self.done.emit(self._mid, px)


# ══════════════════════════════════════════════════════════════════════════════
# Rounded pixmap helper
# ══════════════════════════════════════════════════════════════════════════════

def _rounded_pixmap(src: QPixmap, w: int, h: int, r: int = 10) -> QPixmap:
    scaled = src.scaled(w, h,
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation)
    x = (scaled.width()  - w) // 2
    y = (scaled.height() - h) // 2
    cropped = scaled.copy(x, y, w, h)
    out = QPixmap(w, h)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QBrush(cropped))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawRoundedRect(0, 0, w, h, r, r)
    p.end()
    return out


# ══════════════════════════════════════════════════════════════════════════════
# Media Card (grid view)
# ══════════════════════════════════════════════════════════════════════════════

class MediaCard(QFrame):
    clicked = pyqtSignal(dict)

    W = 164
    THUMB_H_VIDEO = 96
    THUMB_H_OTHER = 130

    def __init__(self, media: dict, api: APIClient):
        super().__init__()
        self.media      = media
        self._api       = api
        self._tw        = None
        self._thumb_h   = self.THUMB_H_VIDEO if media.get('type') == 'video' else self.THUMB_H_OTHER
        self._card_h    = self._thumb_h + 74
        self.setProperty('role', 'card')
        self.setFixedSize(self.W, self._card_h)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._build()
        self._load_thumb()

    def _build(self):
        m = self.media
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # Thumb
        self._tl = QLabel()
        self._tl.setFixedSize(self.W - 2, self._thumb_h)
        self._tl.setStyleSheet(f"background:{C['bg3']};border-radius:11px 11px 0 0;")
        self._tl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icons = {'video': '🎬', 'audio': '🎵', 'image': '🖼️'}
        self._tl.setText(icons.get(m.get('type', ''), '📄'))
        self._tl.setFont(QFont('', 28))
        lay.addWidget(self._tl)

        # Info
        info = QWidget()
        info.setStyleSheet('background:transparent')
        il = QVBoxLayout(info)
        il.setContentsMargins(10, 8, 10, 6)
        il.setSpacing(3)

        title = QLabel()
        title.setFont(QFont('', 11, QFont.Weight.Bold))
        fm = title.fontMetrics()
        title.setText(_elide(m.get('title', '—'), fm, self.W - 22))
        il.addWidget(title)

        meta_parts = []
        if m.get('artist'):    meta_parts.append(m['artist'])
        elif m.get('year'):    meta_parts.append(str(m['year']))
        if not meta_parts:     meta_parts.append(_human_size(m.get('size', 0)))
        meta = QLabel()
        meta.setFont(QFont('', 10))
        meta.setStyleSheet(f'color:{C["text2"]}')
        meta.setText(_elide(' · '.join(meta_parts), meta.fontMetrics(), self.W - 22))
        il.addWidget(meta)

        prog = float(m.get('progress') or 0)
        if prog > 0.02:
            pb = QProgressBar()
            pb.setMaximumHeight(3)
            pb.setRange(0, 100)
            pb.setValue(int(prog * 100))
            pb.setTextVisible(False)
            il.addWidget(pb)

        lay.addWidget(info)

    def _load_thumb(self):
        if self._tw is not None:
            try:
                self._tw.requestInterruption()
            except RuntimeError:
                pass   # deleteLater ya ejecutó — ignorar
            self._tw = None   # Qt (padre) mantiene vivo el C++; GC solo libera el wrapper Python
        self._tw = ThumbWorker(self._api, self.media['id'])
        self._tw.done.connect(self._on_thumb)
        self._tw.start()

    def _on_thumb(self, mid: str, px: QPixmap):
        if mid != self.media['id']:
            return
        w, h = self.W - 2, self._thumb_h
        self._tl.setPixmap(_rounded_pixmap(px, w, h, 11))

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.media)
        super().mousePressEvent(e)


# ══════════════════════════════════════════════════════════════════════════════
# List row
# ══════════════════════════════════════════════════════════════════════════════

class ListRow(QFrame):
    clicked = pyqtSignal(dict)

    def __init__(self, media: dict, api: APIClient):
        super().__init__()
        self.media = media
        self._api  = api
        self._tw   = None
        self.setProperty('role', 'card')
        self.setFixedHeight(56)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._build()
        self._load_thumb()

    def _build(self):
        m = self.media
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 0, 16, 0)
        row.setSpacing(10)

        self._tl = QLabel()
        self._tl.setFixedSize(40, 40)
        self._tl.setStyleSheet(f'background:{C["bg3"]};border-radius:6px;')
        self._tl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icons = {'video': '🎬', 'audio': '🎵', 'image': '🖼️'}
        self._tl.setText(icons.get(m.get('type', ''), '📄'))
        row.addWidget(self._tl)

        if m.get('track'):
            t = QLabel(str(m['track']))
            t.setFixedWidth(24)
            t.setStyleSheet(f'color:{C["text3"]};font-size:11px;')
            t.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            row.addWidget(t)

        info = QWidget(); info.setStyleSheet('background:transparent')
        il = QVBoxLayout(info); il.setContentsMargins(0,0,0,0); il.setSpacing(2)

        title = QLabel(m.get('title', '—'))
        title.setFont(QFont('', 12, QFont.Weight.Bold))
        il.addWidget(title)

        parts = []
        if m.get('artist'): parts.append(m['artist'])
        if m.get('album'):  parts.append(m['album'])
        if m.get('year'):   parts.append(str(m['year']))
        meta_text = ' · '.join(parts) if parts else _human_size(m.get('size', 0))
        meta = QLabel(meta_text)
        meta.setFont(QFont('', 10))
        meta.setStyleSheet(f'color:{C["text2"]}')
        il.addWidget(meta)
        row.addWidget(info, 1)

        sz = QLabel(_human_size(m.get('size', 0)))
        sz.setStyleSheet(f'color:{C["text3"]};font-size:11px;')
        row.addWidget(sz)

    def _load_thumb(self):
        if self._tw is not None:
            try:
                self._tw.requestInterruption()
            except RuntimeError:
                pass
            self._tw = None
        self._tw = ThumbWorker(self._api, self.media['id'])
        self._tw.done.connect(self._on_thumb)
        self._tw.start()

    def _on_thumb(self, mid: str, px: QPixmap):
        if mid != self.media['id']:
            return
        self._tl.setPixmap(_rounded_pixmap(px, 40, 40, 6))

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.media)
        super().mousePressEvent(e)


# ══════════════════════════════════════════════════════════════════════════════
# Video Window  (ventana flotante de vídeo, puede fullscreen)
# ══════════════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════════════
# Image loader worker
# ══════════════════════════════════════════════════════════════════════════════

class _ImageLoadWorker(QThread):
    done      = pyqtSignal(QPixmap)
    error_msg = pyqtSignal(str)

    def __init__(self, proxy: 'AuthProxy', mid: str):
        super().__init__()
        self._proxy = proxy
        self._mid   = mid
        self.setTerminationEnabled(True)

    def run(self):
        try:
            if self.isInterruptionRequested():
                return
            url = self._proxy.stream_url(self._mid)
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=30) as r:
                data = r.read()
            if self.isInterruptionRequested():
                return
            px = QPixmap()
            if data and px.loadFromData(data) and not px.isNull():
                self.done.emit(px)
            else:
                self.error_msg.emit('Formato de imagen no compatible')
        except Exception as exc:
            if not self.isInterruptionRequested():
                self.error_msg.emit(str(exc))


# ══════════════════════════════════════════════════════════════════════════════
# MediaViewer — visor embebido para vídeo e imágenes (dentro de la ventana)
# ══════════════════════════════════════════════════════════════════════════════

class MediaViewer(QWidget):
    """Panel embebido que sustituye al ContentView durante la reproducción de
    vídeo o al visualizar imágenes. No abre ninguna ventana externa."""

    closed         = pyqtSignal()
    position_saved = pyqtSignal(float, float)   # progress, position_secs

    def __init__(self, player: QMediaPlayer, proxy: 'AuthProxy'):
        super().__init__()
        self._player     = player
        self._proxy      = proxy
        self._media      = None
        self._mode       = None   # 'video' | 'image'
        self._dur        = 0
        self._dragging   = False
        self._fullscreen = False
        self._img_worker = None
        self._build()

        self._player.positionChanged.connect(self._on_pos)
        self._player.durationChanged.connect(self._on_dur)
        self._player.playbackStateChanged.connect(self._on_state)

    # ── Build UI ───────────────────────────────────────────────────────────────

    def _build(self):
        vl = QVBoxLayout(self)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.setSpacing(0)

        # ── Header ──────────────────────────────────────────────────────
        header = QWidget()
        header.setFixedHeight(46)
        header.setStyleSheet(
            f'background:{C["bg2"]};border-bottom:1px solid {C["border"]};')
        hl = QHBoxLayout(header)
        hl.setContentsMargins(14, 0, 14, 0)
        hl.setSpacing(10)

        back = QPushButton('← Volver')
        back.setProperty('role', 'icon')
        back.setFixedHeight(30)
        back.setStyleSheet(
            f'font-size:13px;color:{C["text2"]};background:transparent;border:none;'
            f'padding:0 8px;')
        back.clicked.connect(self._close)
        hl.addWidget(back)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet(f'color:{C["border"]};')
        hl.addWidget(sep)

        self._title_lbl = QLabel()
        self._title_lbl.setFont(QFont('', 13, QFont.Weight.Bold))
        hl.addWidget(self._title_lbl, 1)

        self._fs_btn = QPushButton('⛶')
        self._fs_btn.setProperty('role', 'icon')
        self._fs_btn.setFixedSize(32, 32)
        self._fs_btn.setToolTip('Pantalla completa (F)')
        self._fs_btn.clicked.connect(self._toggle_fs)
        hl.addWidget(self._fs_btn)

        vl.addWidget(header)

        # ── Content stack ────────────────────────────────────────────────
        self._stack = QStackedWidget()

        # Page 0: vídeo
        self._video_w = QVideoWidget()
        self._video_w.setStyleSheet('background:black;')
        # WA_NativeWindow es necesario en Windows para que Qt cree el HWND
        # antes de asignarlo como salida del pipeline multimedia
        self._video_w.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        self._video_w.mouseDoubleClickEvent = lambda e: self._toggle_fs()
        self._stack.addWidget(self._video_w)

        # Page 1: imagen
        img_scroll = QScrollArea()
        img_scroll.setWidgetResizable(True)
        img_scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        img_scroll.setStyleSheet(f'background:#0a0a0f;border:none;')
        self._img_lbl = QLabel()
        self._img_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._img_lbl.setStyleSheet('background:transparent;')
        self._img_lbl.setScaledContents(False)
        img_scroll.setWidget(self._img_lbl)
        self._stack.addWidget(img_scroll)

        vl.addWidget(self._stack, 1)

        # ── Barra de controles de vídeo ──────────────────────────────────
        self._ctrl = QWidget()
        self._ctrl.setFixedHeight(54)
        self._ctrl.setStyleSheet(
            f'background:{C["bg2"]};border-top:1px solid {C["border"]};')
        cl = QHBoxLayout(self._ctrl)
        cl.setContentsMargins(14, 0, 14, 0)
        cl.setSpacing(10)

        self._restart_btn = self._mk_ctrl_btn('⏮', self._restart, 'Reiniciar')
        cl.addWidget(self._restart_btn)

        self._play_btn = self._mk_ctrl_btn('⏸', self._play_pause, 'Play / Pausa')
        cl.addWidget(self._play_btn)

        self._end_btn = self._mk_ctrl_btn('⏭', self._skip_end, 'Final')
        cl.addWidget(self._end_btn)

        self._pos_slider = QSlider(Qt.Orientation.Horizontal)
        self._pos_slider.setRange(0, 1000)
        self._pos_slider.sliderPressed.connect(lambda: setattr(self, '_dragging', True))
        self._pos_slider.sliderReleased.connect(self._do_seek)
        self._pos_slider.sliderMoved.connect(self._seek_preview)
        cl.addWidget(self._pos_slider, 1)

        self._time_lbl = QLabel('0:00 / 0:00')
        self._time_lbl.setStyleSheet(f'color:{C["text2"]};font-size:11px;')
        cl.addWidget(self._time_lbl)

        vl.addWidget(self._ctrl)

    def _mk_ctrl_btn(self, icon: str, fn, tip: str) -> QPushButton:
        b = QPushButton(icon)
        b.setProperty('role', 'icon')
        b.setToolTip(tip)
        b.setFixedSize(34, 34)
        b.clicked.connect(fn)
        return b

    # ── Public API ─────────────────────────────────────────────────────────────

    def show_video(self, media: dict):
        """Configura el panel para reproducir vídeo."""
        self._media = media
        self._mode  = 'video'
        self._title_lbl.setText(media.get('title', 'Vídeo'))
        self._stack.setCurrentIndex(0)
        # La barra de controles interna se oculta: el PlayerBar de abajo
        # tiene play/pause/seek/volumen. Evita la apariencia duplicada.
        self._ctrl.hide()
        self._fs_btn.show()
        self._video_w.show()
        self._video_w.raise_()
        # Forzar creación del HWND en Windows
        self._video_w.winId()
        # Diferir setVideoOutput al siguiente ciclo del event loop para que
        # Windows haya procesado la visibilidad del widget antes de asignarlo.
        QTimer.singleShot(0, lambda: self._player.setVideoOutput(self._video_w))

    def show_image(self, media: dict):
        """Carga y muestra una imagen completa."""
        self._media = media
        self._mode  = 'image'
        self._title_lbl.setText(media.get('title', 'Imagen'))
        self._stack.setCurrentIndex(1)
        self._ctrl.hide()
        self._fs_btn.hide()

        self._img_lbl.setText('⏳ Cargando imagen…')
        self._img_lbl.setFont(QFont('', 18))
        self._img_lbl.setPixmap(QPixmap())

        if self._img_worker is not None:
            try:
                if self._img_worker.isRunning():
                    self._img_worker.requestInterruption()
                    if not self._img_worker.wait(500):
                        self._img_worker.terminate()
                        self._img_worker.wait(200)
            except RuntimeError:
                pass
            self._img_worker = None
        self._img_worker = _ImageLoadWorker(self._proxy, media['id'])
        self._img_worker.setParent(self)           # MediaViewer posee el objeto C++
        self._img_worker.finished.connect(self._img_worker.deleteLater)
        self._img_worker.done.connect(self._on_image_loaded)
        self._img_worker.error_msg.connect(
            lambda msg: self._img_lbl.setText(f'❌ {msg}'))
        self._img_worker.start()

    def _on_image_loaded(self, px: QPixmap):
        if px.isNull():
            self._img_lbl.setText('❌ No se pudo cargar la imagen')
            return
        # Ajustar al tamaño del panel manteniendo proporciones
        area = self._stack.currentWidget()
        aw   = area.width()  - 32
        ah   = area.height() - 32
        if aw > 0 and ah > 0:
            px = px.scaled(aw, ah,
                           Qt.AspectRatioMode.KeepAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)
        self._img_lbl.setPixmap(px)
        self._img_lbl.setText('')

    def resizeEvent(self, e):
        """Re-escala la imagen al redimensionar la ventana."""
        super().resizeEvent(e)
        if (self._mode == 'image' and self._img_worker
                and not self._img_worker.isRunning()
                and not self._img_lbl.pixmap().isNull()):
            area = self._stack.currentWidget()
            aw   = area.width()  - 32
            ah   = area.height() - 32
            if aw > 0 and ah > 0:
                # Guardar el pixmap original en el worker para re-escalar sin pérdida
                pass   # El escalado ya se hizo; suficiente para el uso normal

    # ── Slots de reproductor ──────────────────────────────────────────────────

    def _on_pos(self, ms: int):
        if self._mode != 'video' or self._dragging or not self._dur:
            return
        self._pos_slider.setValue(int(ms / self._dur * 1000))
        self._time_lbl.setText(f'{_fmt_time(ms/1000)} / {_fmt_time(self._dur/1000)}')

    def _on_dur(self, ms: int):
        self._dur = ms

    def _on_state(self, state):
        if self._mode == 'video':
            playing = state == QMediaPlayer.PlaybackState.PlayingState
            self._play_btn.setText('⏸' if playing else '▶')

    # ── Controles de vídeo ────────────────────────────────────────────────────

    def _play_pause(self):
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
        else:
            self._player.play()

    def _restart(self):
        self._player.setPosition(0)

    def _skip_end(self):
        if self._dur:
            self._player.setPosition(self._dur - 1000)

    def _seek_preview(self, val: int):
        if self._dur:
            self._time_lbl.setText(
                f'{_fmt_time(val/1000*self._dur/1000)} / {_fmt_time(self._dur/1000)}')

    def _do_seek(self):
        self._dragging = False
        if self._dur:
            self._player.setPosition(int(self._pos_slider.value() / 1000 * self._dur))

    def _toggle_fs(self):
        parent_win = self.window()
        if self._fullscreen:
            parent_win.showNormal()
        else:
            parent_win.showFullScreen()
        self._fullscreen = not self._fullscreen

    # ── Cierre ────────────────────────────────────────────────────────────────

    def _close(self):
        """Guarda posición (vídeo) y emite señal para volver al ContentView."""
        if self._mode == 'video' and self._media:
            pos_ms = self._player.position()
            dur_ms = self._player.duration() or 1
            prog   = pos_ms / dur_ms
            self.position_saved.emit(prog, pos_ms / 1000)
            self._player.setVideoOutput(None)
        if self._img_worker is not None:
            try:
                if self._img_worker.isRunning():
                    self._img_worker.requestInterruption()
                    if not self._img_worker.wait(500):
                        self._img_worker.terminate()
                        self._img_worker.wait(200)
            except RuntimeError:
                pass
        self.closed.emit()

    # ── Teclado ───────────────────────────────────────────────────────────────

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape and self._fullscreen:
            self._toggle_fs()
        elif e.key() == Qt.Key.Key_Space and self._mode == 'video':
            self._play_pause()
        elif e.key() == Qt.Key.Key_F and self._mode == 'video':
            self._toggle_fs()
        else:
            super().keyPressEvent(e)


# ══════════════════════════════════════════════════════════════════════════════
# Player Bar (bottom, persistent)
# ══════════════════════════════════════════════════════════════════════════════

class PlayerBar(QFrame):
    play_media  = pyqtSignal(dict)   # cuando el usuario hace clic en el título
    open_viewer = pyqtSignal(str, dict)  # mode ('video'), media

    def __init__(self, proxy: AuthProxy, cfg: Config, api: APIClient):
        super().__init__()
        self.setProperty('role', 'player')
        self.setFixedHeight(72)

        self._proxy   = proxy
        self._cfg     = cfg
        self._api     = api
        self._current = None
        self._queue   = []
        self._q_idx   = 0
        self._tw       = None
        self._dragging = False

        # Media player
        self._player = QMediaPlayer()
        self._audio  = QAudioOutput()
        self._player.setAudioOutput(self._audio)
        self._audio.setVolume(cfg.volume / 100)

        self._build()
        self._connect()
        self.hide()

    def _build(self):
        row = QHBoxLayout(self)
        row.setContentsMargins(16, 0, 16, 0)
        row.setSpacing(12)

        # Thumb
        self._thumb = QLabel()
        self._thumb.setFixedSize(48, 48)
        self._thumb.setStyleSheet(f'background:{C["bg3"]};border-radius:8px;')
        self._thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._thumb.setText('🎵')
        self._thumb.setFont(QFont('', 20))
        row.addWidget(self._thumb)

        # Track info (clickable)
        info = QWidget(); info.setStyleSheet('background:transparent')
        info.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        info.setFixedWidth(200)
        il = QVBoxLayout(info); il.setContentsMargins(0,0,0,0); il.setSpacing(2)

        self._title_lbl = QLabel('—')
        self._title_lbl.setFont(QFont('', 12, QFont.Weight.Bold))
        il.addWidget(self._title_lbl)

        self._artist_lbl = QLabel('')
        self._artist_lbl.setFont(QFont('', 10))
        self._artist_lbl.setStyleSheet(f'color:{C["text2"]}')
        il.addWidget(self._artist_lbl)
        info.mousePressEvent = lambda e: self._on_info_click()
        row.addWidget(info)

        # Controls
        ctrl = QWidget(); ctrl.setStyleSheet('background:transparent')
        cl = QHBoxLayout(ctrl); cl.setContentsMargins(0,0,0,0); cl.setSpacing(4)

        self._btn_prev = self._mk_btn('⏮', self._prev)
        self._btn_play = self._mk_btn('▶', self._toggle_play)
        self._btn_play.setFixedSize(42, 42)
        self._btn_play.setStyleSheet(f'''
            QPushButton {{
                background:{C["accent"]};color:#1a1a1f;
                border:none;border-radius:21px;font-size:17px;
            }}
            QPushButton:hover {{ background:{C["accent2"]}; }}
        ''')
        self._btn_next = self._mk_btn('⏭', self._next)
        for b in [self._btn_prev, self._btn_play, self._btn_next]:
            cl.addWidget(b)
        row.addWidget(ctrl)

        # Progress
        prog = QWidget(); prog.setStyleSheet('background:transparent')
        pl = QVBoxLayout(prog); pl.setContentsMargins(0,0,0,0); pl.setSpacing(4)
        pl.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        self._seek = QSlider(Qt.Orientation.Horizontal)
        self._seek.setRange(0, 1000)
        self._seek.sliderPressed.connect(lambda: setattr(self, '_dragging', True))
        self._seek.sliderReleased.connect(self._do_seek)
        pl.addWidget(self._seek)

        times = QWidget(); times.setStyleSheet('background:transparent')
        tl = QHBoxLayout(times); tl.setContentsMargins(0,0,0,0)
        self._cur_lbl = QLabel('0:00')
        self._dur_lbl = QLabel('0:00')
        for lb in [self._cur_lbl, self._dur_lbl]:
            lb.setStyleSheet(f'color:{C["text3"]};font-size:10px;')
        tl.addWidget(self._cur_lbl)
        tl.addStretch()
        tl.addWidget(self._dur_lbl)
        pl.addWidget(times)
        row.addWidget(prog, 1)

        # Volume
        vol_w = QWidget(); vol_w.setStyleSheet('background:transparent')
        vl = QHBoxLayout(vol_w); vl.setContentsMargins(0,0,0,0); vl.setSpacing(6)
        vol_icon = QLabel('🔊')
        vol_icon.setFont(QFont('', 14))
        vl.addWidget(vol_icon)
        self._vol_slider = QSlider(Qt.Orientation.Horizontal)
        self._vol_slider.setRange(0, 100)
        self._vol_slider.setValue(self._cfg.volume)
        self._vol_slider.setFixedWidth(80)
        self._vol_slider.valueChanged.connect(self._on_vol)
        vl.addWidget(self._vol_slider)
        row.addWidget(vol_w)

        # Close
        close_btn = self._mk_btn('✕', self.stop_and_hide)
        close_btn.setToolTip('Cerrar reproductor')
        row.addWidget(close_btn)

    def _mk_btn(self, text: str, fn) -> QPushButton:
        b = QPushButton(text)
        b.setProperty('role', 'icon')
        b.setFixedSize(36, 36)
        b.clicked.connect(fn)
        return b

    def _connect(self):
        self._player.positionChanged.connect(self._on_pos)
        self._player.durationChanged.connect(self._on_dur)
        self._player.playbackStateChanged.connect(self._on_state)
        self._player.mediaStatusChanged.connect(self._on_status)

    # ── Public API ─────────────────────────────────────────────────────────────

    def load(self, media: dict, queue: list = None, queue_idx: int = 0):
        """Load media and optional playback queue."""
        self._current = media
        self._queue   = queue or [media]
        self._q_idx   = queue_idx

        mid = media['id']
        mtype = media.get('type', 'audio')

        # Update UI
        fm = self._title_lbl.fontMetrics()
        self._title_lbl.setText(_elide(media.get('title', '—'), fm, 200))
        self._artist_lbl.setText(
            media.get('artist') or media.get('album') or ''
        )
        self._load_thumb(mid)
        self.show()

        # Para vídeo: mostrar el visor. setVideoOutput se llama con QTimer.singleShot(0)
        # dentro de show_video() para que Windows cree el HWND antes de asignarlo.
        # setSource + play también se difieren para ejecutarse DESPUÉS de setVideoOutput.
        if mtype == 'video':
            self.open_viewer.emit('video', media)
            url  = QUrl(self._proxy.stream_url(mid))
            pos  = float(media.get('position') or 0)
            # Restaurar posición al conectar
            try:
                self._player.positionChanged.disconnect(self._restore_pos)
            except (TypeError, RuntimeError):
                pass
            self._restore_target = pos if pos > 3 else 0
            if pos > 3:
                self._player.positionChanged.connect(self._restore_pos)
            # Diferimos setSource+play un ciclo más tarde que setVideoOutput (timer=0)
            # para que el pipeline tenga el output asignado antes de cargar el medio.
            QTimer.singleShot(20, lambda: (
                self._player.setSource(url),
                self._player.play(),
            ))
        else:
            # Audio: no necesita visor, cargar directamente
            url = QUrl(self._proxy.stream_url(mid))
            self._player.setSource(url)
            try:
                self._player.positionChanged.disconnect(self._restore_pos)
            except (TypeError, RuntimeError):
                pass
            self._restore_target = 0
            pos = float(media.get('position') or 0)
            if pos > 3:
                self._player.positionChanged.connect(self._restore_pos)
                self._restore_target = pos
            self._player.play()

    def stop_and_hide(self):
        self._player.stop()
        self._player.setSource(QUrl())
        self._player.setVideoOutput(None)
        self.hide()
        self._current = None

    # ── Slots ──────────────────────────────────────────────────────────────────

    def _on_pos(self, ms: int):
        if self._dragging:
            return
        dur = self._player.duration()
        if dur > 0:
            self._seek.setValue(int(ms / dur * 1000))
        self._cur_lbl.setText(_fmt_time(ms / 1000))

    def _on_dur(self, ms: int):
        self._dur_lbl.setText(_fmt_time(ms / 1000))

    def _on_state(self, state):
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self._btn_play.setText('⏸' if playing else '▶')

    def _on_status(self, status):
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self._next()

    def _on_vol(self, val: int):
        self._audio.setVolume(val / 100)
        self._cfg.volume = val

    def _do_seek(self):
        self._dragging = False
        dur = self._player.duration()
        if dur:
            self._player.setPosition(int(self._seek.value() / 1000 * dur))

    def _restore_pos(self, ms: int):
        target = getattr(self, '_restore_target', 0)
        if target and ms > 0 and self._player.duration() > 0:
            self._player.setPosition(int(target * 1000))
            self._player.positionChanged.disconnect(self._restore_pos)
            self._restore_target = 0

    def _toggle_play(self):
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
        else:
            self._player.play()

    def _prev(self):
        if self._queue and self._q_idx > 0:
            self._q_idx -= 1
            self.load(self._queue[self._q_idx], self._queue, self._q_idx)

    def _next(self):
        if self._queue and self._q_idx < len(self._queue) - 1:
            self._q_idx += 1
            self.load(self._queue[self._q_idx], self._queue, self._q_idx)

    def _on_info_click(self):
        if self._current:
            self.play_media.emit(self._current)

    def _load_thumb(self, mid: str):
        if self._tw is not None:
            try:
                self._tw.requestInterruption()
            except RuntimeError:
                pass
            self._tw = None
        self._tw = ThumbWorker(self._api, mid)
        self._tw.done.connect(self._on_thumb)
        self._tw.start()

    def _on_thumb(self, mid: str, px: QPixmap):
        if self._current and mid == self._current['id']:
            self._thumb.setPixmap(_rounded_pixmap(px, 48, 48, 8))
            self._thumb.setText('')

    def _save_progress(self, prog: float, pos_secs: float):
        if self._current:
            w = Worker(
                self._api.post,
                f'/api/media/{self._current["id"]}/play',
                {'progress': prog, 'position': pos_secs}
            )
            w.setParent(self)            # PlayerBar posee el objeto C++
            w.finished.connect(w.deleteLater)
            w.start()

    @property
    def player(self):
        return self._player


# ══════════════════════════════════════════════════════════════════════════════
# Content view  (scroll area with grid or list)
# ══════════════════════════════════════════════════════════════════════════════

class ContentView(QScrollArea):
    media_clicked = pyqtSignal(dict)

    def __init__(self, api: APIClient, cfg: Config):
        super().__init__()
        self._api   = api
        self._cfg   = cfg
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._container = QWidget()
        self._container.setStyleSheet('background:transparent')
        self.setWidget(self._container)

        self._main_lay = QVBoxLayout(self._container)
        self._main_lay.setContentsMargins(24, 20, 24, 24)
        self._main_lay.setSpacing(6)
        self._main_lay.addStretch()

    def clear(self):
        while self._main_lay.count() > 1:   # keep the trailing stretch
            item = self._main_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def show_loading(self):
        self.clear()
        lbl = QLabel('⏳ Cargando…')
        lbl.setStyleSheet(f'color:{C["text3"]};font-size:15px;')
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._main_lay.insertWidget(0, lbl)

    def show_empty(self, msg: str = 'Sin resultados'):
        self.clear()
        lbl = QLabel(f'🔍  {msg}')
        lbl.setStyleSheet(f'color:{C["text3"]};font-size:15px;')
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._main_lay.insertWidget(0, lbl)

    def show_error(self, err: str):
        self.clear()
        lbl = QLabel(f'❌  {err}')
        lbl.setStyleSheet(f'color:{C["red"]};font-size:14px;')
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._main_lay.insertWidget(0, lbl)

    def show_media(self, items: list, title: str = ''):
        """Render a list of media items in grid or list view."""
        self.clear()
        if not items:
            self.show_empty()
            return

        insert_pos = 0

        if title:
            lbl = QLabel(title)
            lbl.setFont(QFont('', 16, QFont.Weight.Bold))
            lbl.setStyleSheet(f'color:{C["accent"]};margin-bottom:4px;')
            self._main_lay.insertWidget(insert_pos, lbl)
            insert_pos += 1

        if self._cfg.view == 'grid':
            wrap = QWidget(); wrap.setStyleSheet('background:transparent')
            flow = FlowLayout(wrap, hspacing=12, vspacing=12)
            for m in items:
                card = MediaCard(m, self._api)
                card.clicked.connect(self.media_clicked)
                flow.addWidget(card)
            self._main_lay.insertWidget(insert_pos, wrap)
        else:
            wrap = QWidget(); wrap.setStyleSheet('background:transparent')
            col  = QVBoxLayout(wrap); col.setContentsMargins(0,0,0,0); col.setSpacing(5)
            for m in items:
                row = ListRow(m, self._api)
                row.clicked.connect(self.media_clicked)
                col.addWidget(row)
            self._main_lay.insertWidget(insert_pos, wrap)

    def show_sections(self, sections: list):
        """sections = [{'title': ..., 'items': [...]}, ...]"""
        self.clear()
        for i, sec in enumerate(sections):
            if not sec.get('items'):
                continue
            lbl = QLabel(sec['title'])
            lbl.setFont(QFont('', 15, QFont.Weight.Bold))
            lbl.setStyleSheet(f'color:{C["accent"]};margin:{"16px 0 4px" if i else "0 0 4px"};')
            self._main_lay.insertWidget(self._main_lay.count() - 1, lbl)

            wrap = QWidget(); wrap.setStyleSheet('background:transparent')
            flow = FlowLayout(wrap, hspacing=12, vspacing=12)
            for m in sec['items'][:24]:
                card = MediaCard(m, self._api)
                card.clicked.connect(self.media_clicked)
                flow.addWidget(card)
            self._main_lay.insertWidget(self._main_lay.count() - 1, wrap)


# ══════════════════════════════════════════════════════════════════════════════
# Flow layout (wraps cards like CSS flex-wrap)
# ══════════════════════════════════════════════════════════════════════════════

from PyQt6.QtCore import QRect

class FlowLayout(object):
    """Minimal flow layout that wraps widgets."""

    def __init__(self, parent: QWidget, hspacing: int = 10, vspacing: int = 10):
        self._parent   = parent
        self._items    = []
        self._hs       = hspacing
        self._vs       = vspacing
        parent.resizeEvent = self._on_resize
        parent.sizeHint    = self._size_hint

    def addWidget(self, w: QWidget):
        w.setParent(self._parent)
        self._items.append(w)
        w.show()
        self._relayout(self._parent.width())

    def _on_resize(self, e):
        self._relayout(e.size().width())

    def _size_hint(self):
        if not self._items:
            return QSize(0, 0)
        return QSize(self._parent.width(), self._calc_height(self._parent.width()))

    def _calc_height(self, total_w: int) -> int:
        if not self._items:
            return 0
        w_item = self._items[0].width()
        if w_item <= 0:
            w_item = 164
        cols = max(1, (total_w + self._hs) // (w_item + self._hs))
        rows = (len(self._items) + cols - 1) // cols
        h_item = self._items[0].height() if self._items else 230
        return rows * (h_item + self._vs) - self._vs

    def _relayout(self, total_w: int):
        if not self._items:
            return
        w_item = self._items[0].width()
        if w_item <= 0:
            w_item = 164
        h_item = self._items[0].height()

        cols = max(1, (total_w + self._hs) // (w_item + self._hs))
        x = y = 0
        for i, item in enumerate(self._items):
            col = i % cols
            row = i // cols
            ix  = col * (w_item + self._hs)
            iy  = row * (h_item + self._vs)
            item.move(ix, iy)

        rows = (len(self._items) + cols - 1) // cols
        total_h = rows * (h_item + self._vs) - self._vs
        self._parent.setMinimumHeight(max(total_h, 1))


# ══════════════════════════════════════════════════════════════════════════════
# Sidebar
# ══════════════════════════════════════════════════════════════════════════════

class Sidebar(QFrame):
    nav_home      = pyqtSignal()
    nav_library   = pyqtSignal(dict)    # library dict
    nav_search    = pyqtSignal()
    nav_settings  = pyqtSignal()
    nav_logout    = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setProperty('role', 'sidebar')
        self.setFixedWidth(220)
        self._btns = {}

        vl = QVBoxLayout(self)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.setSpacing(0)

        # Logo
        logo = QWidget()
        logo.setFixedHeight(64)
        logo.setStyleSheet(f'background:transparent;border-bottom:1px solid {C["border"]};')
        ll = QHBoxLayout(logo); ll.setContentsMargins(18, 0, 18, 0); ll.setSpacing(10)
        icon_lbl = QLabel('P')
        icon_lbl.setFixedSize(36, 36)
        icon_lbl.setStyleSheet(f'''
            background:{C["accent"]};color:#1a1a1f;border-radius:8px;
            font-family:'Barlow Condensed',sans-serif;font-size:20px;font-weight:700;
        ''')
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ll.addWidget(icon_lbl)
        title_l = QLabel('<b>PyLex</b>')
        title_l.setStyleSheet(f'font-size:17px;color:{C["text"]};')
        ll.addWidget(title_l)
        vl.addWidget(logo)

        # Nav buttons
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet('background:transparent;border:none;')
        nav_w = QWidget(); nav_w.setStyleSheet('background:transparent;')
        self._nav_lay = QVBoxLayout(nav_w)
        self._nav_lay.setContentsMargins(8, 12, 8, 12)
        self._nav_lay.setSpacing(2)

        self._add_section('Navegar')
        self._btns['home'] = self._nav_btn('⊞  Inicio', lambda: self.nav_home.emit(), 'home')
        self._btns['search'] = self._nav_btn('🔍  Buscar', lambda: self.nav_search.emit(), 'search')

        self._lib_label_idx = None     # will insert library buttons here
        self._lib_sep_added = False

        self._nav_lay.addStretch()
        scroll.setWidget(nav_w)
        vl.addWidget(scroll, 1)

        # Footer
        footer = QWidget()
        footer.setFixedHeight(110)
        footer.setStyleSheet(f'background:transparent;border-top:1px solid {C["border"]};')
        fl = QVBoxLayout(footer); fl.setContentsMargins(8, 8, 8, 8); fl.setSpacing(4)

        self._user_btn = QPushButton('👤  —')
        self._user_btn.setProperty('role', 'nav')
        self._user_btn.clicked.connect(lambda: self.nav_settings.emit())
        fl.addWidget(self._user_btn)

        logout_btn = QPushButton('🚪  Cerrar sesión')
        logout_btn.setProperty('role', 'nav')
        logout_btn.clicked.connect(lambda: self.nav_logout.emit())
        fl.addWidget(logout_btn)

        dot = QLabel('● Conectado')
        dot.setStyleSheet(f'color:{C["green"]};font-size:11px;padding:2px 6px;')
        fl.addWidget(dot)
        vl.addWidget(footer)

    def _add_section(self, text: str):
        lbl = QLabel(text.upper())
        lbl.setStyleSheet(f'color:{C["text3"]};font-size:10px;font-weight:600;'
                          f'letter-spacing:1.5px;padding:8px 10px 4px;')
        self._nav_lay.addWidget(lbl)

    def _nav_btn(self, text: str, fn, key: str = '') -> QPushButton:
        b = QPushButton(text)
        b.setProperty('role', 'nav')
        b.setCheckable(False)
        b.clicked.connect(fn)
        b.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._nav_lay.addWidget(b)
        return b

    def set_libraries(self, libs: list):
        # Remove old library buttons
        for key in [k for k in self._btns if k.startswith('lib_')]:
            btn = self._btns.pop(key)
            btn.deleteLater()

        if not self._lib_sep_added and libs:
            # Insert section label before stretch
            stretch_idx = self._nav_lay.count() - 1
            lbl = QLabel('MIS BIBLIOTECAS')
            lbl.setStyleSheet(f'color:{C["text3"]};font-size:10px;font-weight:600;'
                              f'letter-spacing:1.5px;padding:12px 10px 4px;')
            self._nav_lay.insertWidget(stretch_idx, lbl)
            self._lib_sep_added = True

        icons = {'movies': '🎬', 'shows': '📺', 'music': '🎵',
                 'photos': '🖼️', 'other': '📁'}
        stretch_idx = self._nav_lay.count() - 1
        for lib in libs:
            icon = icons.get(lib.get('type', 'other'), '📁')
            key  = f'lib_{lib["id"]}'
            btn  = QPushButton(f'{icon}  {lib["name"]}')
            btn.setProperty('role', 'nav')
            btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            lib_copy = dict(lib)
            btn.clicked.connect(lambda _, l=lib_copy: self.nav_library.emit(l))
            self._nav_lay.insertWidget(stretch_idx, btn)
            self._btns[key] = btn
            stretch_idx += 1

    def set_active(self, key: str):
        for k, b in self._btns.items():
            b.setProperty('active', 'true' if k == key else 'false')
            b.style().unpolish(b)
            b.style().polish(b)

    def set_user(self, display: str, avatar: str = '🎬'):
        self._user_btn.setText(f'{avatar}  {display}')


# ══════════════════════════════════════════════════════════════════════════════
# Top bar
# ══════════════════════════════════════════════════════════════════════════════

class TopBar(QFrame):
    search_submitted = pyqtSignal(str)
    view_toggled     = pyqtSignal(str)     # 'grid' or 'list'
    sort_changed     = pyqtSignal(str)

    def __init__(self, cfg: Config):
        super().__init__()
        self.setProperty('role', 'topbar')
        self.setFixedHeight(56)
        self._cfg = cfg

        row = QHBoxLayout(self)
        row.setContentsMargins(20, 0, 20, 0)
        row.setSpacing(10)

        self._title = QLabel('Inicio')
        self._title.setFont(QFont('', 18, QFont.Weight.Bold))
        row.addWidget(self._title)

        row.addStretch()

        # Search
        self._search = QLineEdit()
        self._search.setPlaceholderText('🔍  Buscar…')
        self._search.setFixedWidth(260)
        self._search.returnPressed.connect(
            lambda: self.search_submitted.emit(self._search.text().strip())
        )
        row.addWidget(self._search)

        # Sort
        self._sort = QComboBox()
        self._sort.setFixedWidth(120)
        self._sort.addItems(['A – Z', 'Recientes', 'Más vistos', 'Tamaño', 'Año'])
        self._sort.currentTextChanged.connect(self._on_sort)
        self._sort.hide()
        row.addWidget(self._sort)

        # View toggle
        self._grid_btn = self._mk_view_btn('▦', 'grid')
        self._list_btn = self._mk_view_btn('☰', 'list')
        row.addWidget(self._grid_btn)
        row.addWidget(self._list_btn)
        self._update_view_btns()

    def _mk_view_btn(self, icon: str, mode: str) -> QPushButton:
        b = QPushButton(icon)
        b.setProperty('role', 'icon')
        b.setFixedSize(32, 32)
        b.clicked.connect(lambda: self._set_view(mode))
        return b

    def _set_view(self, mode: str):
        self._cfg.view = mode
        self._update_view_btns()
        self.view_toggled.emit(mode)

    def _update_view_btns(self):
        gv = self._cfg.view == 'grid'
        self._grid_btn.setStyleSheet(
            f'background:{C["accent"] if gv else C["bg3"]};color:{"#1a1a1f" if gv else C["text2"]};'
            f'border:none;border-radius:6px;font-size:15px;')
        self._list_btn.setStyleSheet(
            f'background:{C["accent"] if not gv else C["bg3"]};color:{"#1a1a1f" if not gv else C["text2"]};'
            f'border:none;border-radius:6px;font-size:15px;')

    def _on_sort(self, text: str):
        mapping = {'A – Z': 'name', 'Recientes': 'date',
                   'Más vistos': 'play_count', 'Tamaño': 'size', 'Año': 'year'}
        self.sort_changed.emit(mapping.get(text, 'name'))

    def set_title(self, t: str):
        self._title.setText(t)

    def show_sort(self, v: bool):
        self._sort.setVisible(v)


# ══════════════════════════════════════════════════════════════════════════════
# Login Dialog
# ══════════════════════════════════════════════════════════════════════════════

class LoginDialog(QDialog):
    logged_in = pyqtSignal(str, str, dict)   # url, token, user_info

    def __init__(self, api: APIClient, cfg: Config, parent=None):
        super().__init__(parent)
        self._api = api
        self._cfg = cfg
        self.setWindowTitle('PyLex Desktop — Iniciar sesión')
        self.setMinimumSize(400, 460)
        self.resize(460, 500)
        self.setStyleSheet(STYLESHEET)

        vl = QVBoxLayout(self)
        vl.setContentsMargins(40, 36, 40, 36)
        vl.setSpacing(16)

        # Logo
        logo_row = QHBoxLayout()
        icon = QLabel('P')
        icon.setFixedSize(52, 52)
        icon.setStyleSheet(f'background:{C["accent"]};color:#1a1a1f;border-radius:12px;'
                           f'font-size:28px;font-weight:800;')
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo_row.addWidget(icon)
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        title_lbl = QLabel('<b style="font-size:22px">PyLex Desktop</b>')
        title_lbl.setMinimumWidth(200)
        title_col.addWidget(title_lbl)
        sub = QLabel('Conecta con tu servidor PyLex')
        sub.setStyleSheet(f'color:{C["text2"]};font-size:12px;')
        title_col.addWidget(sub)
        logo_row.addLayout(title_col)
        logo_row.addStretch()
        vl.addLayout(logo_row)

        # Fields
        self._url = QLineEdit()
        self._url.setPlaceholderText('http://192.168.1.x:7777')

        self._user = QLineEdit()
        self._user.setPlaceholderText('Usuario')
        self._user.setFocus()

        self._pass = QLineEdit()
        self._pass.setPlaceholderText('Contraseña')
        self._pass.setEchoMode(QLineEdit.EchoMode.Password)

        for label, widget in [
            ('Dirección del servidor', self._url),
            ('Usuario', self._user),
            ('Contraseña', self._pass),
        ]:
            lbl = QLabel(label)
            lbl.setStyleSheet(f'color:{C["text2"]};font-size:11px;font-weight:600;'
                              f'text-transform:uppercase;letter-spacing:0.5px;')
            vl.addWidget(lbl)
            vl.addWidget(widget)

        # Pre-fill last server
        if cfg.active:
            self._url.setText(cfg.active.get('url', ''))
            self._user.setText(cfg.active.get('username', ''))

        self._err = QLabel('')
        self._err.setStyleSheet(f'color:{C["red"]};font-size:12px;')
        self._err.setWordWrap(True)
        vl.addWidget(self._err)

        vl.addStretch()

        self._btn = QPushButton('Iniciar sesión')
        self._btn.setProperty('role', 'accent')
        self._btn.setFixedHeight(44)
        self._btn.clicked.connect(self._do_login)
        vl.addWidget(self._btn)

        self._pass.returnPressed.connect(self._do_login)
        self._user.returnPressed.connect(self._pass.setFocus)

    def _do_login(self):
        url  = self._url.text().strip()
        user = self._user.text().strip()
        pw   = self._pass.text()
        if not url or not user or not pw:
            self._err.setText('Rellena todos los campos.')
            return
        if not url.startswith('http'):
            url = 'http://' + url

        self._btn.setEnabled(False)
        self._btn.setText('Conectando…')
        self._err.setText('')

        def _login():
            return self._api.login(url, user, pw)

        w = Worker(_login)
        w.result.connect(self._on_result)
        w.error.connect(self._on_error)
        w.setParent(self)
        w.start()
        self._worker = w

    def _on_result(self, data: dict):
        self._btn.setEnabled(True)
        self._btn.setText('Iniciar sesión')
        if data.get('ok'):
            self.logged_in.emit(
                self._url.text().strip().rstrip('/'),
                data.get('token', ''),
                data.get('user', {})
            )
            self.accept()
        else:
            self._err.setText(data.get('error', 'Error desconocido'))

    def _on_error(self, err: str):
        self._btn.setEnabled(True)
        self._btn.setText('Iniciar sesión')
        self._err.setText(f'No se pudo conectar: {err}')


# ══════════════════════════════════════════════════════════════════════════════
# Settings Dialog
# ══════════════════════════════════════════════════════════════════════════════

class SettingsDialog(QDialog):
    server_changed = pyqtSignal(str)    # new server url

    def __init__(self, cfg: Config, parent=None):
        super().__init__(parent)
        self._cfg = cfg
        self.setWindowTitle('Ajustes')
        self.setFixedSize(440, 280)
        self.setStyleSheet(STYLESHEET)

        vl = QVBoxLayout(self)
        vl.setContentsMargins(32, 28, 32, 28)
        vl.setSpacing(14)

        title = QLabel('<b style="font-size:16px">Ajustes del cliente</b>')
        vl.addWidget(title)

        fl = QFormLayout(); fl.setSpacing(12)
        self._url = QLineEdit(cfg.active.get('url', '') if cfg.active else '')
        self._url.setPlaceholderText('http://192.168.1.x:7777')
        fl.addRow('Servidor:', self._url)
        vl.addLayout(fl)

        info = QLabel(f'Usuario: <b>{cfg.active.get("display", "—")}</b>'
                      f'  ·  Rol: <b>{cfg.active.get("role", "—")}</b>'
                      if cfg.active else '')
        info.setStyleSheet(f'color:{C["text2"]};font-size:12px;')
        vl.addWidget(info)

        vl.addStretch()

        btns = QHBoxLayout()
        cancel = QPushButton('Cancelar'); cancel.clicked.connect(self.reject)
        save   = QPushButton('Guardar'); save.setProperty('role', 'accent')
        save.clicked.connect(self._save)
        btns.addStretch()
        btns.addWidget(cancel)
        btns.addWidget(save)
        vl.addLayout(btns)

    def _save(self):
        url = self._url.text().strip()
        if url and url != (self._cfg.active.get('url', '') if self._cfg.active else ''):
            self.server_changed.emit(url)
        self.accept()


# ══════════════════════════════════════════════════════════════════════════════
# Main Window
# ══════════════════════════════════════════════════════════════════════════════

class MainWindow(QMainWindow):
    def __init__(self, api: APIClient, proxy: AuthProxy, cfg: Config):
        super().__init__()
        self._api    = api
        self._proxy  = proxy
        self._cfg    = cfg
        self._libs   = []
        self._sort   = 'name'
        self._workers: list[Worker] = []

        self.setWindowTitle(APP_NAME)
        self.resize(1280, 780)
        self.setMinimumSize(900, 600)

        # Central widget
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Top bar
        self._topbar = TopBar(cfg)
        self._topbar.search_submitted.connect(self._do_search)
        self._topbar.view_toggled.connect(self._on_view_toggle)
        self._topbar.sort_changed.connect(self._on_sort_change)
        root.addWidget(self._topbar)

        # Body (sidebar + content)
        body = QWidget()
        body_lay = QHBoxLayout(body)
        body_lay.setContentsMargins(0, 0, 0, 0)
        body_lay.setSpacing(0)

        # Body (sidebar + stacked: content / media viewer)
        body = QWidget()
        body_lay = QHBoxLayout(body)
        body_lay.setContentsMargins(0, 0, 0, 0)
        body_lay.setSpacing(0)

        self._sidebar = Sidebar()
        self._sidebar.nav_home.connect(self._go_home)
        self._sidebar.nav_library.connect(self._go_library)
        self._sidebar.nav_search.connect(lambda: self._topbar._search.setFocus())
        self._sidebar.nav_settings.connect(self._open_settings)
        self._sidebar.nav_logout.connect(self._logout)
        body_lay.addWidget(self._sidebar)

        # Stacked widget: page 0 = contenido normal, page 1 = visor multimedia
        self._body_stack = QStackedWidget()

        self._content = ContentView(api, cfg)
        self._content.media_clicked.connect(self._play)
        self._body_stack.addWidget(self._content)   # index 0

        # Player bar (necesita crearse antes que MediaViewer para pasarle el player)
        self._player = PlayerBar(proxy, cfg, api)
        self._player.play_media.connect(self._on_player_info_click)
        self._player.open_viewer.connect(self._show_viewer)

        # MediaViewer embebido (index 1 del stack)
        self._viewer = MediaViewer(self._player.player, proxy)
        self._viewer.closed.connect(self._hide_viewer)
        self._viewer.position_saved.connect(self._player._save_progress)
        self._body_stack.addWidget(self._viewer)    # index 1

        body_lay.addWidget(self._body_stack, 1)
        root.addWidget(body, 1)

        root.addWidget(self._player)

        # Load initial data
        self._load_libraries()
        self._go_home()

    # ── Navigation ─────────────────────────────────────────────────────────────

    def _go_home(self):
        self._topbar.set_title('⊞  Inicio')
        self._topbar.show_sort(False)
        self._sidebar.set_active('home')
        self._body_stack.setCurrentIndex(0)
        self._content.show_loading()
        self._run(self._fetch_home, self._show_home)

    def _go_library(self, lib: dict):
        lid  = lib['id']
        name = lib.get('name', '—')
        icon = {'movies': '🎬', 'shows': '📺', 'music': '🎵',
                'photos': '🖼️', 'other': '📁'}.get(lib.get('type', ''), '📁')
        self._topbar.set_title(f'{icon}  {name}')
        self._topbar.show_sort(True)
        self._sidebar.set_active(f'lib_{lid}')
        self._current_lib = lib
        self._body_stack.setCurrentIndex(0)
        self._load_library_media(lid)

    def _load_library_media(self, lid: int, sort: str = None):
        s = sort or self._sort
        self._content.show_loading()
        self._run(
            lambda: self._api.get(
                f'/api/libraries/{lid}/media?sort={s}&limit=200'
            ),
            lambda d: self._content.show_media(d.get('media', []))
        )

    def _do_search(self, q: str):
        if not q:
            return
        self._topbar.set_title(f'🔍  "{q}"')
        self._topbar.show_sort(False)
        self._sidebar.set_active('search')
        self._content.show_loading()
        self._run(
            lambda: self._api.get(f'/api/media?q={q}&limit=200'),
            lambda d: self._content.show_media(
                d.get('media', []),
                title=f'{len(d.get("media", []))} resultados'
            )
        )

    # ── Data fetchers ──────────────────────────────────────────────────────────

    def _fetch_home(self) -> dict:
        stats    = self._api.get('/api/stats')
        cont_raw = self._api.get('/api/continue?limit=12')
        return {'stats': stats, 'continue': cont_raw.get('media', [])}

    def _show_home(self, data: dict):
        sections = []
        cont = data.get('continue', [])
        if cont:
            sections.append({'title': '⏯  Continuar viendo / escuchando', 'items': cont})

        recently = data.get('stats', {}).get('recently_added', [])
        if recently:
            sections.append({'title': '🆕  Añadido recientemente', 'items': recently})

        most_played = data.get('stats', {}).get('most_played', [])
        if most_played:
            sections.append({'title': '🏆  Más reproducidos', 'items': most_played})

        if sections:
            self._content.show_sections(sections)
        else:
            self._content.show_empty('Sin contenido aún. Añade una biblioteca desde la interfaz web.')

    def _load_libraries(self):
        def _done(data: dict):
            self._libs = data.get('libraries', [])
            self._sidebar.set_libraries(self._libs)

        self._run(lambda: self._api.get('/api/libraries'), _done)

    # ── Playback ───────────────────────────────────────────────────────────────

    def _play(self, media: dict):
        """Inicia la reproducción o visualización según el tipo de medio."""
        mtype = media.get('type', '')

        # Imágenes → visor embebido directo (sin QMediaPlayer)
        if mtype == 'image':
            self._show_viewer('image', media)
            return

        # Audio / Vídeo → construir cola y cargar en el reproductor
        def _fetch():
            if media.get('library_id'):
                data = self._api.get(
                    f'/api/libraries/{media["library_id"]}/media'
                    f'?type={media["type"]}&limit=200&sort={self._sort}'
                )
                siblings = data.get('media', [])
            else:
                siblings = [media]
            return siblings

        def _start(siblings: list):
            try:
                idx = next(i for i, m in enumerate(siblings) if m['id'] == media['id'])
            except StopIteration:
                idx = 0
                siblings = [media]
            self._player.load(media, queue=siblings, queue_idx=idx)

        self._run(_fetch, _start)

    # ── Viewer helpers ─────────────────────────────────────────────────────────

    def _show_viewer(self, mode: str, media: dict):
        """Muestra el visor multimedia embebido (vídeo o imagen).
        El QStackedWidget debe cambiar a la página del visor ANTES de que
        show_video() llame a setVideoOutput, para que el widget sea visible
        en la jerarquía de ventanas cuando Qt asigna el pipeline de vídeo."""
        self._body_stack.setCurrentIndex(1)
        # Forzar que Qt procese el cambio de visibilidad antes de continuar
        QApplication.instance().processEvents()
        if mode == 'video':
            self._viewer.show_video(media)
        else:
            self._viewer.show_image(media)
        self._viewer.setFocus()

    def _hide_viewer(self):
        """Vuelve a la vista de contenido normal."""
        self._body_stack.setCurrentIndex(0)

    def _on_player_info_click(self, media: dict):
        """Jump back to the library of the currently playing item."""
        if media.get('library_id'):
            for lib in self._libs:
                if lib['id'] == media['library_id']:
                    self._go_library(lib)
                    return

    # ── Sort / view ────────────────────────────────────────────────────────────

    def _on_sort_change(self, sort: str):
        self._sort = sort
        if hasattr(self, '_current_lib'):
            self._load_library_media(self._current_lib['id'], sort)

    def _on_view_toggle(self, _mode: str):
        # Re-render current view
        if hasattr(self, '_current_lib'):
            self._load_library_media(self._current_lib['id'])
        else:
            self._go_home()

    # ── Settings / logout ──────────────────────────────────────────────────────

    def _open_settings(self):
        d = SettingsDialog(self._cfg, self)
        d.server_changed.connect(self._reconnect)
        d.exec()

    def _reconnect(self, new_url: str):
        d = LoginDialog(self._api, self._cfg, self)
        d._url.setText(new_url)

        def _on_login(url, token, user):
            self._proxy.update(url, token)
            self._api.connect(url, token)
            self._cfg.set_server(url, token, user)
            self._sidebar.set_user(user.get('display', '—'), user.get('avatar', '🎬'))
            self._load_libraries()
            self._go_home()

        d.logged_in.connect(_on_login)
        d.exec()

    def _logout(self):
        if QMessageBox.question(self, 'Cerrar sesión',
                '¿Cerrar sesión y volver a la pantalla de inicio?',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        ) == QMessageBox.StandardButton.Yes:
            try:
                self._api.post('/api/logout')
            except Exception:
                pass
            self._cfg.logout()
            self._player.stop_and_hide()
            self.close()
            _start_login(self._api, self._proxy, self._cfg, QApplication.instance())

    # ── Worker helper ──────────────────────────────────────────────────────────

    def _run(self, fn, on_result=None, on_error=None):
        w = Worker(fn)
        if on_result:
            w.result.connect(on_result)
        w.error.connect(lambda e: self._content.show_error(e))
        if on_error:
            w.error.connect(on_error)
        w.setParent(self)                # MainWindow posee el objeto C++
        w.finished.connect(w.deleteLater)
        w.start()
        self._workers.append(w)
        # Poda segura: isFinished() puede lanzar RuntimeError si deleteLater ya ejecutó
        active = []
        for x in self._workers:
            try:
                if not x.isFinished():
                    active.append(x)
            except RuntimeError:
                pass
        self._workers = active

    def closeEvent(self, e):
        self._player.stop_and_hide()
        for w in list(self._workers):
            try:
                if w.isRunning():
                    w.requestInterruption()
                    w.wait(2000)
            except RuntimeError:
                pass
        self._workers.clear()
        self._proxy.stop()
        super().closeEvent(e)


# ══════════════════════════════════════════════════════════════════════════════
# Bootstrap
# ══════════════════════════════════════════════════════════════════════════════

def _start_login(api: APIClient, proxy: AuthProxy, cfg: Config, app: QApplication):
    dlg = LoginDialog(api, cfg)
    app._dlg = dlg

    def _on_login(url: str, token: str, user: dict):
        cfg.set_server(url, token, user)
        api.connect(url, token)
        proxy.start(url, token)
        win = MainWindow(api, proxy, cfg)
        win.setWindowTitle(f'{APP_NAME} — {user.get("display", "")}')
        win._sidebar.set_user(user.get('display', '—'), user.get('avatar', '🎬'))
        win.show()
        app._win = win

    dlg.logged_in.connect(_on_login)
    dlg.rejected.connect(QApplication.quit)
    dlg.show()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setStyleSheet(STYLESHEET)

    # Dark title bar on Windows
    if sys.platform == 'win32':
        try:
            import ctypes
            ctypes.windll.dwmapi.DwmSetWindowAttribute
        except Exception:
            pass

    # High-DPI
    try:
        app.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps, True)
    except AttributeError:
    # El atributo no existe en Qt 6, se ignora silenciosamente
     pass

    cfg   = Config()
    api   = APIClient()
    proxy = AuthProxy()

    # Try auto-login with saved token
    srv = cfg.active
    if srv and srv.get('token'):
        api.connect(srv['url'], srv['token'])
        proxy.start(srv['url'], srv['token'])

        def _check():
            try:
                data = api.get('/api/me')
                if data.get('ok'):
                    return data
            except Exception:
                pass
            return None

        w = Worker(_check)
        app._w = w

        def _on_check(data):
            if data:
                user = data.get('user', {})
                win  = MainWindow(api, proxy, cfg)
                win.setWindowTitle(f'{APP_NAME} — {user.get("display", "")}')
                win._sidebar.set_user(user.get('display', '—'), user.get('avatar', '🎬'))
                win.show()
                app._win = win
            else:
                cfg.logout()
                _start_login(api, proxy, cfg, app)

        w.result.connect(_on_check)
        w.error.connect(lambda _: _start_login(api, proxy, cfg, app))
        w.start()
    else:
        _start_login(api, proxy, cfg, app)

    sys.exit(app.exec())


if __name__ == '__main__':
    main()