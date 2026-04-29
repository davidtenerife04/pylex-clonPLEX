#!/usr/bin/env python3
"""
pylex_api.py — Capa REST/JSON para PyLex Media Server
=======================================================
Extiende PyLex con endpoints que devuelven JSON puro,
listos para ser consumidos por cualquier cliente externo
(React SPA, app móvil, CLI, etc.).

Uso:
    Coloca este fichero junto a pylex.py y añade al final
    de pylex.py, justo antes de main():

        import pylex_api
        pylex_api.patch(PyLexHandler)

O arranca directamente con:

        python pylex_api.py

────────────────────────────────────────────────────────
Endpoints añadidos
────────────────────────────────────────────────────────

AUTH
  POST  /api/login               Iniciar sesión → cookie + token
  POST  /api/logout              Cerrar sesión actual
  POST  /api/setup               Primera configuración (sin admin)

USUARIO ACTUAL
  GET   /api/me                  Perfil + estadísticas del usuario
  POST  /api/me                  Actualizar display/avatar
  POST  /api/me/password         Cambiar contraseña

SESIONES
  GET   /api/sessions            Sesiones activas del usuario actual
  DELETE /api/sessions           Cerrar todas las sesiones
  DELETE /api/sessions/<token>   Cerrar una sesión concreta

BIBLIOTECAS
  GET   /api/libraries           Listar todas las bibliotecas
  POST  /api/libraries           Crear biblioteca           [admin]
  GET   /api/libraries/<id>      Detalle de biblioteca
  DELETE /api/libraries/<id>     Eliminar biblioteca        [admin]
  POST  /api/libraries/<id>/scan Escanear biblioteca        [admin]
  GET   /api/libraries/<id>/media Contenido de biblioteca
                                   ?sort=name|date|size|year|play_count
                                   ?type=video|audio|image
                                   ?page=1&limit=50

MEDIA
  GET   /api/media               Listar/filtrar media
                                   ?q=texto          búsqueda
                                   ?type=video|audio|image
                                   ?year=2023
                                   ?lib=<id>
                                   ?sort=name|date|size|year|play_count
                                   ?page=1&limit=50
  GET   /api/media/<id>          Detalle de un archivo
  GET   /api/media/<id>/related  Archivos relacionados (misma biblioteca)
  POST  /api/media/<id>/play     Registrar reproducción y/o progreso
                                   body: { progress, position }

UTILIDADES
  GET   /api/continue            Items con progreso > 0, ordenados
  GET   /api/stats               Estadísticas globales del servidor
  GET   /api/search              Alias de /api/media?q=...
  GET   /api/activity            Historial de actividad    [admin]

USUARIOS  (solo admin)
  GET   /api/users               Listar usuarios
  POST  /api/users               Crear usuario
  DELETE /api/users/<id>         Eliminar usuario
  POST  /api/users/<id>/role     Cambiar rol

CONFIGURACIÓN  (solo admin)
  GET   /api/settings            Obtener configuración
  POST  /api/settings            Guardar configuración

DIAGNÓSTICO  (solo admin)
  GET   /api/debug               Info interna del servidor

Rutas de streaming (ya existían en pylex.py, sin cambios):
  GET   /stream/<id>             Stream con soporte de rango de bytes
  GET   /thumb/<id>              Miniatura del archivo
"""

import re
import json
import os
import sys
from datetime import datetime
from urllib.parse import urlparse, parse_qs

# ──────────────────────────────────────────────────────────────────────────────
# Helpers internos
# ──────────────────────────────────────────────────────────────────────────────

def _row(r):
    """Convierte sqlite3.Row en dict serializable."""
    return dict(r) if r else None


def _rows(rs):
    return [dict(r) for r in rs]


def _page_params(qs: dict):
    """Extrae page y limit de query string con valores seguros."""
    try:
        page = max(1, int(qs.get('page', [1])[0]))
    except (ValueError, TypeError):
        page = 1
    try:
        limit = min(200, max(1, int(qs.get('limit', [50])[0])))
    except (ValueError, TypeError):
        limit = 50
    return page, limit, (page - 1) * limit


VALID_SORT = {
    'name':       'title COLLATE NOCASE',
    'date':       'added_at DESC',
    'size':       'size DESC',
    'year':       'year DESC',
    'play_count': 'play_count DESC',
    'last_played':'last_played DESC',
}


def _sort_clause(qs: dict, default='name') -> str:
    key = qs.get('sort', [default])[0]
    return VALID_SORT.get(key, VALID_SORT[default])


def _media_url(media_id: str) -> dict:
    """URLs de stream y thumb para un media_id."""
    return {
        'stream_url': f'/stream/{media_id}',
        'thumb_url':  f'/thumb/{media_id}',
    }


# ──────────────────────────────────────────────────────────────────────────────
# Handlers de endpoint
# ──────────────────────────────────────────────────────────────────────────────

# ── AUTH ──────────────────────────────────────────────────────────────────────

def api_login(handler):
    """POST /api/login  { username, password, remember? }"""
    import pylex as px
    body = handler.read_json()
    un   = body.get('username', '').strip().lower()
    pw   = body.get('password', '')
    if not un or not pw:
        handler.send_json({'ok': False, 'error': 'Faltan credenciales'}, 400)
        return
    db = px.get_db()
    u  = db.execute("SELECT * FROM users WHERE username=?", (un,)).fetchone()
    db.close()
    if not u or not px.verify_password(pw, u['pw_hash'], u['pw_salt']):
        handler.send_json({'ok': False, 'error': 'Credenciales incorrectas'}, 401)
        return
    days  = px.SESSION_DAYS if body.get('remember') else 1
    token = px.create_session(u['id'], handler.get_ip(),
                              handler.headers.get('User-Agent', ''))
    payload = json.dumps({
        'ok': True,
        'token': token,
        'user': {
            'id':       u['id'],
            'username': u['username'],
            'display':  u['display'],
            'role':     u['role'],
            'avatar':   u['avatar'],
        }
    }).encode()
    handler.send_response(200)
    handler.send_header('Content-Type', 'application/json')
    handler.send_header('Set-Cookie', px.make_session_cookie(token, days * 86400))
    handler.send_header('Content-Length', str(len(payload)))
    handler.end_headers()
    handler.wfile.write(payload)


def api_logout(handler):
    """POST /api/logout"""
    import pylex as px
    token = handler.get_token()
    if token:
        px.revoke_session(token)
    payload = json.dumps({'ok': True}).encode()
    handler.send_response(200)
    handler.send_header('Content-Type', 'application/json')
    handler.send_header('Set-Cookie', px.clear_session_cookie())
    handler.send_header('Content-Length', str(len(payload)))
    handler.end_headers()
    handler.wfile.write(payload)


def api_setup(handler):
    """POST /api/setup  { username, display, password }"""
    import pylex as px
    import sqlite3
    if not px.needs_setup():
        handler.send_json({'ok': False, 'error': 'Ya configurado'}, 400)
        return
    body = handler.read_json()
    un   = body.get('username', '').strip().lower()
    dis  = body.get('display',  '').strip()
    pw   = body.get('password', '')
    if not un or not dis or len(pw) < 8:
        handler.send_json({'ok': False,
                           'error': 'username, display y password (≥8 chars) son obligatorios'}, 400)
        return
    h, s = px.hash_password(pw)
    db   = px.get_db()
    try:
        db.execute(
            "INSERT INTO users(username,display,pw_hash,pw_salt,role,avatar) VALUES(?,?,?,?,?,?)",
            (un, dis, h, s, 'admin', '🎬'))
        db.commit()
        uid   = db.execute("SELECT id FROM users WHERE username=?", (un,)).fetchone()['id']
        db.close()
        token = px.create_session(uid, handler.get_ip(), handler.headers.get('User-Agent', ''))
        payload = json.dumps({'ok': True, 'token': token}).encode()
        handler.send_response(200)
        handler.send_header('Content-Type', 'application/json')
        handler.send_header('Set-Cookie', px.make_session_cookie(token))
        handler.send_header('Content-Length', str(len(payload)))
        handler.end_headers()
        handler.wfile.write(payload)
    except sqlite3.IntegrityError:
        db.close()
        handler.send_json({'ok': False, 'error': 'El usuario ya existe'}, 409)


# ── USUARIO ACTUAL ─────────────────────────────────────────────────────────────

def api_me_get(handler, user: dict):
    """GET /api/me"""
    import pylex as px
    db = px.get_db()
    play_count = db.execute(
        "SELECT COUNT(*) FROM activity_log WHERE user_id=?", (user['id'],)
    ).fetchone()[0]
    last_plays = _rows(db.execute("""
        SELECT m.id, m.title, m.type, al.created_at
        FROM activity_log al JOIN media m ON al.media_id=m.id
        WHERE al.user_id=?
        ORDER BY al.created_at DESC LIMIT 5
    """, (user['id'],)).fetchall())
    db.close()
    for p in last_plays:
        p.update(_media_url(p['id']))
    handler.send_json({
        'ok': True,
        'user': {
            'id':         user['id'],
            'username':   user['username'],
            'display':    user['display'],
            'role':       user['role'],
            'avatar':     user['avatar'],
            'last_login': user.get('last_login'),
        },
        'stats': {
            'total_plays': play_count,
        },
        'recent_activity': last_plays,
    })


def api_me_post(handler, user: dict):
    """POST /api/me  { display?, avatar? }"""
    import pylex as px
    body = handler.read_json()
    dis  = body.get('display', '').strip()
    av   = body.get('avatar',  '').strip()
    if not dis:
        handler.send_json({'ok': False, 'error': 'display no puede estar vacío'}, 400)
        return
    db = px.get_db()
    db.execute("UPDATE users SET display=?, avatar=? WHERE id=?",
               (dis, av or user['avatar'], user['id']))
    db.commit()
    db.close()
    handler.send_json({'ok': True})


def api_me_password(handler, user: dict):
    """POST /api/me/password  { old_password, new_password }"""
    import pylex as px
    body = handler.read_json()
    old  = body.get('old_password', '')
    new  = body.get('new_password', '')
    db   = px.get_db()
    u    = db.execute("SELECT * FROM users WHERE id=?", (user['id'],)).fetchone()
    if not px.verify_password(old, u['pw_hash'], u['pw_salt']):
        db.close()
        handler.send_json({'ok': False, 'error': 'Contraseña actual incorrecta'}, 400)
        return
    if len(new) < 8:
        db.close()
        handler.send_json({'ok': False, 'error': 'Mínimo 8 caracteres'}, 400)
        return
    h, s = px.hash_password(new)
    db.execute("UPDATE users SET pw_hash=?, pw_salt=? WHERE id=?", (h, s, user['id']))
    db.commit()
    db.close()
    handler.send_json({'ok': True})


# ── SESIONES ───────────────────────────────────────────────────────────────────

def api_sessions_get(handler, user: dict):
    """GET /api/sessions"""
    import pylex as px
    db   = px.get_db()
    rows = _rows(db.execute(
        "SELECT token, created_at, expires_at, ip, ua FROM sessions WHERE user_id=? ORDER BY created_at DESC",
        (user['id'],)
    ).fetchall())
    db.close()
    # Ocultar token completo por seguridad; exponer sólo un preview
    for r in rows:
        r['token_preview'] = r['token'][:8] + '…'
        del r['token']
    handler.send_json({'ok': True, 'sessions': rows})


def api_sessions_delete_all(handler, user: dict):
    """DELETE /api/sessions"""
    import pylex as px
    db = px.get_db()
    db.execute("DELETE FROM sessions WHERE user_id=?", (user['id'],))
    db.commit()
    db.close()
    handler.send_json({'ok': True})


def api_session_delete(handler, user: dict, token: str):
    """DELETE /api/sessions/<token>"""
    import pylex as px
    db = px.get_db()
    s  = db.execute("SELECT user_id FROM sessions WHERE token=?", (token,)).fetchone()
    db.close()
    if s and s['user_id'] == user['id']:
        px.revoke_session(token)
        handler.send_json({'ok': True})
    else:
        handler.send_json({'ok': False, 'error': 'No autorizado'}, 403)


# ── BIBLIOTECAS ────────────────────────────────────────────────────────────────

def api_libraries_get(handler, user: dict):
    """GET /api/libraries"""
    import pylex as px
    db   = px.get_db()
    libs = _rows(db.execute("""
        SELECT l.id, l.name, l.path, l.type, l.last_scan, l.created_at,
               COUNT(m.id) AS media_count,
               SUM(m.size)  AS total_size
        FROM libraries l
        LEFT JOIN media m ON m.library_id = l.id
        GROUP BY l.id
        ORDER BY l.name COLLATE NOCASE
    """).fetchall())
    db.close()
    handler.send_json({'ok': True, 'libraries': libs})


def api_library_get(handler, user: dict, lib_id: int):
    """GET /api/libraries/<id>"""
    import pylex as px
    db  = px.get_db()
    lib = _row(db.execute("""
        SELECT l.id, l.name, l.path, l.type, l.last_scan, l.created_at,
               COUNT(m.id) AS media_count,
               SUM(m.size) AS total_size
        FROM libraries l
        LEFT JOIN media m ON m.library_id = l.id
        WHERE l.id=?
        GROUP BY l.id
    """, (lib_id,)).fetchone())
    db.close()
    if not lib:
        handler.send_json({'ok': False, 'error': 'Biblioteca no encontrada'}, 404)
        return
    handler.send_json({'ok': True, 'library': lib})


def api_library_media(handler, user: dict, lib_id: int, qs: dict):
    """GET /api/libraries/<id>/media"""
    import pylex as px
    page, limit, offset = _page_params(qs)
    sort   = _sort_clause(qs)
    ftype  = qs.get('type', [''])[0]

    db = px.get_db()
    lib = _row(db.execute("SELECT id, name FROM libraries WHERE id=?", (lib_id,)).fetchone())
    if not lib:
        db.close()
        handler.send_json({'ok': False, 'error': 'Biblioteca no encontrada'}, 404)
        return

    where  = "library_id=?"
    params = [lib_id]
    if ftype in ('video', 'audio', 'image'):
        where  += " AND type=?"
        params.append(ftype)

    total = db.execute(f"SELECT COUNT(*) FROM media WHERE {where}", params).fetchone()[0]
    items = _rows(db.execute(
        f"SELECT * FROM media WHERE {where} ORDER BY {sort} LIMIT ? OFFSET ?",
        params + [limit, offset]
    ).fetchall())
    db.close()

    for item in items:
        item.update(_media_url(item['id']))

    handler.send_json({
        'ok': True,
        'library': lib,
        'pagination': {'page': page, 'limit': limit, 'total': total,
                       'pages': max(1, (total + limit - 1) // limit)},
        'media': items,
    })


def api_library_create(handler, user: dict):
    """POST /api/libraries  { name, path, type }"""
    import pylex as px
    body  = handler.read_json()
    name  = body.get('name', '').strip()
    fpath = body.get('path', '').strip()
    ltype = body.get('type', 'other')
    if not name or not fpath:
        handler.send_json({'ok': False, 'error': 'name y path son obligatorios'}, 400)
        return
    if not os.path.isdir(fpath):
        handler.send_json({'ok': False, 'error': f'El directorio no existe: {fpath}'}, 400)
        return
    db = px.get_db()
    cur = db.execute(
        "INSERT INTO libraries(name,path,type,created_by) VALUES(?,?,?,?)",
        (name, fpath, ltype, user['id'])
    )
    lib_id = cur.lastrowid
    db.commit()
    db.close()
    handler.send_json({'ok': True, 'library_id': lib_id})


def api_library_delete(handler, user: dict, lib_id: int):
    """DELETE /api/libraries/<id>"""
    import pylex as px
    db = px.get_db()
    lib = db.execute("SELECT id FROM libraries WHERE id=?", (lib_id,)).fetchone()
    if not lib:
        db.close()
        handler.send_json({'ok': False, 'error': 'Biblioteca no encontrada'}, 404)
        return
    db.execute("DELETE FROM media WHERE library_id=?", (lib_id,))
    db.execute("DELETE FROM libraries WHERE id=?", (lib_id,))
    db.commit()
    db.close()
    handler.send_json({'ok': True})


def api_library_scan(handler, user: dict, lib_id: int):
    """POST /api/libraries/<id>/scan"""
    import pylex as px
    import threading
    db  = px.get_db()
    lib = _row(db.execute("SELECT * FROM libraries WHERE id=?", (lib_id,)).fetchone())
    db.close()
    if not lib:
        handler.send_json({'ok': False, 'error': 'Biblioteca no encontrada'}, 404)
        return
    threading.Thread(
        target=px.scan_library,
        args=(lib_id, lib['path'], lib['type']),
        daemon=True
    ).start()
    handler.send_json({'ok': True, 'message': f'Escaneo iniciado para "{lib["name"]}"'})


# ── MEDIA ─────────────────────────────────────────────────────────────────────

def _build_media_query(qs: dict):
    """Construye WHERE + params a partir de query string."""
    q      = qs.get('q',    [''])[0].strip()
    ftype  = qs.get('type', [''])[0]
    year   = qs.get('year', [''])[0]
    lib_id = qs.get('lib',  [''])[0]

    conditions, params = [], []

    if q:
        conditions.append(
            "(title LIKE ? OR artist LIKE ? OR album LIKE ? OR genre LIKE ?)"
        )
        pattern = f'%{q}%'
        params += [pattern, pattern, pattern, pattern]
    if ftype in ('video', 'audio', 'image'):
        conditions.append("type=?")
        params.append(ftype)
    if year.isdigit():
        conditions.append("year=?")
        params.append(int(year))
    if lib_id.isdigit():
        conditions.append("library_id=?")
        params.append(int(lib_id))

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    return where, params


def api_media_list(handler, user: dict, qs: dict):
    """GET /api/media  (también usado por /api/search)"""
    import pylex as px
    page, limit, offset = _page_params(qs)
    sort  = _sort_clause(qs)
    where, params = _build_media_query(qs)

    db    = px.get_db()
    total = db.execute(f"SELECT COUNT(*) FROM media {where}", params).fetchone()[0]
    items = _rows(db.execute(
        f"SELECT * FROM media {where} ORDER BY {sort} LIMIT ? OFFSET ?",
        params + [limit, offset]
    ).fetchall())
    db.close()

    for item in items:
        item.update(_media_url(item['id']))

    handler.send_json({
        'ok': True,
        'pagination': {'page': page, 'limit': limit, 'total': total,
                       'pages': max(1, (total + limit - 1) // limit)},
        'media': items,
    })


def api_media_detail(handler, user: dict, media_id: str):
    """GET /api/media/<id>"""
    import pylex as px
    db   = px.get_db()
    item = _row(db.execute("SELECT * FROM media WHERE id=?", (media_id,)).fetchone())
    db.close()
    if not item:
        handler.send_json({'ok': False, 'error': 'No encontrado'}, 404)
        return
    item.update(_media_url(media_id))
    handler.send_json({'ok': True, 'media': item})


def api_media_related(handler, user: dict, media_id: str):
    """GET /api/media/<id>/related"""
    import pylex as px
    db   = px.get_db()
    item = _row(db.execute("SELECT library_id, type FROM media WHERE id=?", (media_id,)).fetchone())
    if not item:
        db.close()
        handler.send_json({'ok': False, 'error': 'No encontrado'}, 404)
        return
    if item['library_id']:
        related = _rows(db.execute(
            "SELECT id, title, type, artist, year FROM media WHERE library_id=? AND id!=? ORDER BY title COLLATE NOCASE LIMIT 30",
            (item['library_id'], media_id)
        ).fetchall())
    else:
        related = _rows(db.execute(
            "SELECT id, title, type, artist, year FROM media WHERE type=? AND id!=? ORDER BY title COLLATE NOCASE LIMIT 30",
            (item['type'], media_id)
        ).fetchall())
    db.close()
    for r in related:
        r.update(_media_url(r['id']))
    handler.send_json({'ok': True, 'related': related})


def api_media_play(handler, user: dict, media_id: str):
    """POST /api/media/<id>/play  { progress?, position? }"""
    import pylex as px
    body     = handler.read_json()
    progress = body.get('progress')
    position = body.get('position')
    db       = px.get_db()
    item     = db.execute("SELECT id FROM media WHERE id=?", (media_id,)).fetchone()
    if not item:
        db.close()
        handler.send_json({'ok': False, 'error': 'No encontrado'}, 404)
        return
    if progress is not None:
        db.execute(
            "UPDATE media SET play_count=play_count+1, last_played=?, progress=?, position=? WHERE id=?",
            (datetime.now().isoformat(), float(progress), float(position or 0), media_id)
        )
    else:
        db.execute(
            "UPDATE media SET play_count=play_count+1, last_played=? WHERE id=?",
            (datetime.now().isoformat(), media_id)
        )
    db.execute(
        "INSERT INTO activity_log(user_id, media_id, action) VALUES(?,?,?)",
        (user['id'], media_id, 'play')
    )
    db.commit()
    db.close()
    handler.send_json({'ok': True})


# ── UTILIDADES ─────────────────────────────────────────────────────────────────

def api_continue(handler, user: dict, qs: dict):
    """GET /api/continue  — Items con progreso parcial del usuario actual"""
    import pylex as px
    page, limit, offset = _page_params(qs)
    db    = px.get_db()
    total = db.execute(
        "SELECT COUNT(*) FROM media WHERE progress > 0 AND progress < 0.99"
    ).fetchone()[0]
    items = _rows(db.execute(
        """SELECT * FROM media
           WHERE progress > 0 AND progress < 0.99
           ORDER BY last_played DESC
           LIMIT ? OFFSET ?""",
        (limit, offset)
    ).fetchall())
    db.close()
    for item in items:
        item.update(_media_url(item['id']))
    handler.send_json({
        'ok': True,
        'pagination': {'page': page, 'limit': limit, 'total': total,
                       'pages': max(1, (total + limit - 1) // limit)},
        'media': items,
    })


def api_stats(handler, user: dict):
    """GET /api/stats"""
    import pylex as px
    db = px.get_db()

    counts = _row(db.execute("""
        SELECT
            COUNT(*)                                  AS total,
            SUM(CASE WHEN type='video' THEN 1 ELSE 0 END) AS videos,
            SUM(CASE WHEN type='audio' THEN 1 ELSE 0 END) AS audio,
            SUM(CASE WHEN type='image' THEN 1 ELSE 0 END) AS images,
            SUM(size)                                 AS total_size,
            SUM(play_count)                           AS total_plays
        FROM media
    """).fetchone())

    libs_count = db.execute("SELECT COUNT(*) FROM libraries").fetchone()[0]
    users_count = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    recently_added = _rows(db.execute(
        "SELECT id, title, type, added_at FROM media ORDER BY added_at DESC LIMIT 10"
    ).fetchall())
    for item in recently_added:
        item.update(_media_url(item['id']))

    most_played = _rows(db.execute(
        "SELECT id, title, type, play_count FROM media WHERE play_count > 0 ORDER BY play_count DESC LIMIT 10"
    ).fetchall())
    for item in most_played:
        item.update(_media_url(item['id']))

    svname = db.execute("SELECT value FROM settings WHERE key='server_name'").fetchone()
    db.close()

    handler.send_json({
        'ok': True,
        'server_name': svname['value'] if svname else 'PyLex Media Server',
        'media': counts,
        'libraries': libs_count,
        'users': users_count,
        'recently_added': recently_added,
        'most_played': most_played,
    })


def api_activity_get(handler, user: dict, qs: dict):
    """GET /api/activity  [admin]"""
    import pylex as px
    page, limit, offset = _page_params(qs)
    db    = px.get_db()
    total = db.execute("SELECT COUNT(*) FROM activity_log").fetchone()[0]
    rows  = _rows(db.execute("""
        SELECT al.id, al.action, al.created_at,
               u.id AS user_id, u.display AS user_display, u.avatar AS user_avatar,
               m.id AS media_id, m.title AS media_title, m.type AS media_type
        FROM activity_log al
        JOIN users u ON al.user_id = u.id
        JOIN media m ON al.media_id = m.id
        ORDER BY al.created_at DESC
        LIMIT ? OFFSET ?
    """, (limit, offset)).fetchall())
    db.close()
    for r in rows:
        r['stream_url'] = f'/stream/{r["media_id"]}'
        r['thumb_url']  = f'/thumb/{r["media_id"]}'
    handler.send_json({
        'ok': True,
        'pagination': {'page': page, 'limit': limit, 'total': total,
                       'pages': max(1, (total + limit - 1) // limit)},
        'activity': rows,
    })


# ── USUARIOS (admin) ───────────────────────────────────────────────────────────

def api_users_get(handler, user: dict):
    """GET /api/users  [admin]"""
    import pylex as px
    db   = px.get_db()
    rows = _rows(db.execute(
        "SELECT id, username, display, role, avatar, created_at, last_login FROM users ORDER BY created_at"
    ).fetchall())
    db.close()
    handler.send_json({'ok': True, 'users': rows})


def api_users_create(handler, user: dict):
    """POST /api/users  { username, display, password, role?, avatar? }  [admin]"""
    import pylex as px
    import sqlite3
    body = handler.read_json()
    un   = body.get('username', '').strip().lower()
    dis  = body.get('display',  '').strip()
    pw   = body.get('password', '')
    role = body.get('role', 'viewer')
    av   = body.get('avatar', '👤').strip() or '👤'
    if not un or not dis or len(pw) < 8:
        handler.send_json({'ok': False, 'error': 'username, display y password (≥8) son obligatorios'}, 400)
        return
    if role not in ('admin', 'viewer'):
        handler.send_json({'ok': False, 'error': 'role debe ser admin o viewer'}, 400)
        return
    h, s = px.hash_password(pw)
    db   = px.get_db()
    try:
        cur = db.execute(
            "INSERT INTO users(username,display,pw_hash,pw_salt,role,avatar) VALUES(?,?,?,?,?,?)",
            (un, dis, h, s, role, av)
        )
        new_id = cur.lastrowid
        db.commit()
        db.close()
        handler.send_json({'ok': True, 'user_id': new_id})
    except sqlite3.IntegrityError:
        db.close()
        handler.send_json({'ok': False, 'error': 'El usuario ya existe'}, 409)


def api_user_delete(handler, user: dict, uid: int):
    """DELETE /api/users/<id>  [admin]"""
    import pylex as px
    if uid == user['id']:
        handler.send_json({'ok': False, 'error': 'No puedes eliminarte a ti mismo'}, 400)
        return
    db = px.get_db()
    u  = db.execute("SELECT id FROM users WHERE id=?", (uid,)).fetchone()
    if not u:
        db.close()
        handler.send_json({'ok': False, 'error': 'Usuario no encontrado'}, 404)
        return
    db.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
    db.execute("DELETE FROM users WHERE id=?", (uid,))
    db.commit()
    db.close()
    handler.send_json({'ok': True})


def api_user_role(handler, user: dict, uid: int):
    """POST /api/users/<id>/role  { role }  [admin]"""
    import pylex as px
    if uid == user['id']:
        handler.send_json({'ok': False, 'error': 'No puedes cambiar tu propio rol'}, 400)
        return
    body = handler.read_json()
    role = body.get('role', '')
    if role not in ('admin', 'viewer'):
        handler.send_json({'ok': False, 'error': 'role debe ser admin o viewer'}, 400)
        return
    db = px.get_db()
    db.execute("UPDATE users SET role=? WHERE id=?", (role, uid))
    db.commit()
    db.close()
    handler.send_json({'ok': True})


# ── CONFIGURACIÓN (admin) ──────────────────────────────────────────────────────

def api_settings_get(handler, user: dict):
    """GET /api/settings  [admin]"""
    import pylex as px
    db   = px.get_db()
    rows = db.execute("SELECT key, value FROM settings").fetchall()
    db.close()
    settings = {r['key']: r['value'] for r in rows}
    handler.send_json({'ok': True, 'settings': settings})


def api_settings_post(handler, user: dict):
    """POST /api/settings  { server_name?, auto_scan_hours? }  [admin]"""
    import pylex as px
    body = handler.read_json()
    db   = px.get_db()
    allowed = ('server_name', 'auto_scan_hours')
    updated = {}
    for key in allowed:
        val = body.get(key, '').strip()
        if val:
            db.execute("INSERT OR REPLACE INTO settings VALUES(?,?)", (key, val))
            updated[key] = val
    db.commit()
    db.close()
    handler.send_json({'ok': True, 'updated': updated})


# ── DEBUG (admin) ──────────────────────────────────────────────────────────────

def api_debug(handler, user: dict):
    """GET /api/debug  [admin]"""
    import pylex as px
    db        = px.get_db()
    lib_cols  = [r[1] for r in db.execute("PRAGMA table_info(libraries)").fetchall()]
    med_cols  = [r[1] for r in db.execute("PRAGMA table_info(media)").fetchall()]
    lib_count = db.execute("SELECT COUNT(*) FROM libraries").fetchone()[0]
    med_count = db.execute("SELECT COUNT(*) FROM media").fetchone()[0]
    libs      = _rows(db.execute("SELECT id,name,path,type FROM libraries").fetchall())
    db.close()
    handler.send_json({
        'ok': True,
        'server': 'PyLex Media Server v1.2.0',
        'user': user['username'], 'role': user['role'],
        'libraries_count': lib_count, 'media_count': med_count,
        'libraries': libs,
        'schema': {'libraries': lib_cols, 'media': med_cols},
        'db_path': px.DB_PATH,
        'db_exists': os.path.isfile(px.DB_PATH),
        'db_size_bytes': os.path.getsize(px.DB_PATH) if os.path.isfile(px.DB_PATH) else 0,
        'python_version': sys.version,
        'cwd': os.getcwd(),
    })


# ──────────────────────────────────────────────────────────────────────────────
# Router principal
# ──────────────────────────────────────────────────────────────────────────────

# Rutas GET sin autenticación
_PUBLIC_GET = {
    '/api/setup': None,  # No se usa en GET; pero las dejamos mapeadas
}

# Patrones de ruta para GET autenticadas
_GET_ROUTES = [
    (r'^/api/me$',                         'me_get'),
    (r'^/api/libraries$',                  'libraries_get'),
    (r'^/api/libraries/(\d+)$',            'library_get'),
    (r'^/api/libraries/(\d+)/media$',      'library_media'),
    (r'^/api/media$',                      'media_list'),
    (r'^/api/search$',                     'media_list'),       # alias
    (r'^/api/media/([0-9a-f]{32})$',       'media_detail'),
    (r'^/api/media/([0-9a-f]{32})/related$', 'media_related'),
    (r'^/api/continue$',                   'continue'),
    (r'^/api/stats$',                      'stats'),
    (r'^/api/activity$',                   'activity'),
    (r'^/api/users$',                      'users_get'),
    (r'^/api/sessions$',                   'sessions_get'),
    (r'^/api/settings$',                   'settings_get'),
    (r'^/api/debug$',                      'debug'),
]

_POST_ROUTES = [
    (r'^/api/login$',                      'login'),
    (r'^/api/logout$',                     'logout'),
    (r'^/api/setup$',                      'setup'),
    (r'^/api/me$',                         'me_post'),
    (r'^/api/me/password$',                'me_password'),
    (r'^/api/libraries$',                  'lib_create'),
    (r'^/api/libraries/(\d+)/scan$',       'lib_scan'),
    (r'^/api/media/([0-9a-f]{32})/play$',  'media_play'),
    (r'^/api/users$',                      'users_create'),
    (r'^/api/users/(\d+)/role$',           'user_role'),
    (r'^/api/settings$',                   'settings_post'),
]

_DELETE_ROUTES = [
    (r'^/api/libraries/(\d+)$',            'lib_delete'),
    (r'^/api/users/(\d+)$',               'user_delete'),
    (r'^/api/sessions$',                   'sessions_delete_all'),
    (r'^/api/sessions/(.+)$',             'session_delete'),
]


def _dispatch_get(handler, path: str, qs: dict) -> bool:
    """Intenta despachar un GET de API. Devuelve True si fue manejado."""
    import pylex as px

    for pattern, action in _GET_ROUTES:
        m = re.match(pattern, path)
        if not m:
            continue

        # Rutas públicas (ninguna GET por ahora, pero extensible)
        user = handler.current_user()
        if not user:
            handler.send_json({'ok': False, 'error': 'No autenticado'}, 401)
            return True

        groups = m.groups()

        if action == 'me_get':
            api_me_get(handler, user)
        elif action == 'libraries_get':
            api_libraries_get(handler, user)
        elif action == 'library_get':
            api_library_get(handler, user, int(groups[0]))
        elif action == 'library_media':
            api_library_media(handler, user, int(groups[0]), qs)
        elif action == 'media_list':
            api_media_list(handler, user, qs)
        elif action == 'media_detail':
            api_media_detail(handler, user, groups[0])
        elif action == 'media_related':
            api_media_related(handler, user, groups[0])
        elif action == 'continue':
            api_continue(handler, user, qs)
        elif action == 'stats':
            api_stats(handler, user)
        elif action == 'activity':
            if user['role'] != 'admin':
                handler.send_json({'ok': False, 'error': 'Solo admin'}, 403)
            else:
                api_activity_get(handler, user, qs)
        elif action == 'users_get':
            if user['role'] != 'admin':
                handler.send_json({'ok': False, 'error': 'Solo admin'}, 403)
            else:
                api_users_get(handler, user)
        elif action == 'sessions_get':
            api_sessions_get(handler, user)
        elif action == 'settings_get':
            if user['role'] != 'admin':
                handler.send_json({'ok': False, 'error': 'Solo admin'}, 403)
            else:
                api_settings_get(handler, user)
        elif action == 'debug':
            if user['role'] != 'admin':
                handler.send_json({'ok': False, 'error': 'Solo admin'}, 403)
            else:
                api_debug(handler, user)
        return True

    return False


def _dispatch_post(handler, path: str) -> bool:
    """Intenta despachar un POST de API. Devuelve True si fue manejado."""
    import pylex as px

    for pattern, action in _POST_ROUTES:
        m = re.match(pattern, path)
        if not m:
            continue
        groups = m.groups()

        # Endpoints públicos (no requieren sesión)
        if action == 'login':
            api_login(handler)
            return True
        if action == 'logout':
            api_logout(handler)
            return True
        if action == 'setup':
            api_setup(handler)
            return True

        # Resto requieren autenticación
        user = handler.current_user()
        if not user:
            handler.send_json({'ok': False, 'error': 'No autenticado'}, 401)
            return True

        if action == 'me_post':
            api_me_post(handler, user)
        elif action == 'me_password':
            api_me_password(handler, user)
        elif action == 'lib_create':
            if user['role'] != 'admin':
                handler.send_json({'ok': False, 'error': 'Solo admin'}, 403)
            else:
                api_library_create(handler, user)
        elif action == 'lib_scan':
            if user['role'] != 'admin':
                handler.send_json({'ok': False, 'error': 'Solo admin'}, 403)
            else:
                api_library_scan(handler, user, int(groups[0]))
        elif action == 'media_play':
            api_media_play(handler, user, groups[0])
        elif action == 'users_create':
            if user['role'] != 'admin':
                handler.send_json({'ok': False, 'error': 'Solo admin'}, 403)
            else:
                api_users_create(handler, user)
        elif action == 'user_role':
            if user['role'] != 'admin':
                handler.send_json({'ok': False, 'error': 'Solo admin'}, 403)
            else:
                api_user_role(handler, user, int(groups[0]))
        elif action == 'settings_post':
            if user['role'] != 'admin':
                handler.send_json({'ok': False, 'error': 'Solo admin'}, 403)
            else:
                api_settings_post(handler, user)
        return True

    return False


def _dispatch_delete(handler, path: str) -> bool:
    """Intenta despachar un DELETE de API. Devuelve True si fue manejado."""
    import pylex as px

    for pattern, action in _DELETE_ROUTES:
        m = re.match(pattern, path)
        if not m:
            continue
        groups = m.groups()

        user = handler.current_user()
        if not user:
            handler.send_json({'ok': False, 'error': 'No autenticado'}, 401)
            return True

        if action == 'lib_delete':
            if user['role'] != 'admin':
                handler.send_json({'ok': False, 'error': 'Solo admin'}, 403)
            else:
                api_library_delete(handler, user, int(groups[0]))
        elif action == 'user_delete':
            if user['role'] != 'admin':
                handler.send_json({'ok': False, 'error': 'Solo admin'}, 403)
            else:
                api_user_delete(handler, user, int(groups[0]))
        elif action == 'sessions_delete_all':
            api_sessions_delete_all(handler, user)
        elif action == 'session_delete':
            api_session_delete(handler, user, groups[0])
        return True

    return False


# ──────────────────────────────────────────────────────────────────────────────
# CORS helper  (útil para SPA en desarrollo en otro puerto)
# ──────────────────────────────────────────────────────────────────────────────

CORS_ORIGINS = os.environ.get('PYLEX_CORS_ORIGINS', '').split(',')


def _add_cors_headers(handler):
    origin = handler.headers.get('Origin', '')
    if not CORS_ORIGINS or origin in CORS_ORIGINS or '*' in CORS_ORIGINS:
        handler.send_header('Access-Control-Allow-Origin', origin or '*')
        handler.send_header('Access-Control-Allow-Credentials', 'true')
        handler.send_header('Access-Control-Allow-Methods', 'GET, POST, DELETE, OPTIONS')
        handler.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization')


def _handle_options(handler, path: str):
    """Responde a preflight CORS."""
    handler.send_response(204)
    _add_cors_headers(handler)
    handler.end_headers()


# ──────────────────────────────────────────────────────────────────────────────
# patch(handler_class)  —  punto de entrada principal
# ──────────────────────────────────────────────────────────────────────────────

def patch(HandlerClass):
    """
    Monkey-patcha PyLexHandler para interceptar rutas /api/* antes
    de que las maneje el handler original.

    Uso en pylex.py, justo antes de main():

        import pylex_api
        pylex_api.patch(PyLexHandler)
    """
    _orig_get    = HandlerClass.do_GET
    _orig_post   = HandlerClass.do_POST
    _orig_delete = HandlerClass.do_DELETE

    def do_GET(self):
        parsed = urlparse(self.path)
        path   = parsed.path.rstrip('/') or '/'
        qs     = parse_qs(parsed.query)
        if path.startswith('/api/'):
            _add_cors_headers(self)
            try:
                if _dispatch_get(self, path, qs):
                    return
            except Exception as e:
                import logging
                logging.getLogger('pylex_api').error("GET %s: %s", path, e, exc_info=True)
                self.send_json({'ok': False, 'error': str(e)}, 500)
                return
        _orig_get(self)

    def do_POST(self):
        parsed = urlparse(self.path)
        path   = parsed.path.rstrip('/')
        _add_cors_headers(self)
        try:
            if path.startswith('/api/') and _dispatch_post(self, path):
                return
        except Exception as e:
            import logging
            logging.getLogger('pylex_api').error("POST %s: %s", path, e, exc_info=True)
            self.send_json({'ok': False, 'error': str(e)}, 500)
            return
        _orig_post(self)

    def do_DELETE(self):
        parsed = urlparse(self.path)
        path   = parsed.path.rstrip('/')
        _add_cors_headers(self)
        try:
            if path.startswith('/api/') and _dispatch_delete(self, path):
                return
        except Exception as e:
            import logging
            logging.getLogger('pylex_api').error("DELETE %s: %s", path, e, exc_info=True)
            self.send_json({'ok': False, 'error': str(e)}, 500)
            return
        _orig_delete(self)

    def do_OPTIONS(self):
        parsed = urlparse(self.path)
        _handle_options(self, parsed.path)

    HandlerClass.do_GET    = do_GET
    HandlerClass.do_POST   = do_POST
    HandlerClass.do_DELETE = do_DELETE
    HandlerClass.do_OPTIONS = do_OPTIONS

    import logging
    logging.getLogger('pylex_api').info("✅ pylex_api parcheado sobre %s", HandlerClass.__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Arranque directo: python pylex_api.py
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    # Importamos pylex y lo parcheamos antes de que arranque su main()
    import pylex
    patch(pylex.PyLexHandler)
    pylex.main()
