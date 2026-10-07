# PyLex — revisión y correcciones

Todos los fixes marcados con ✔ se han **probado** (servidor + API en vivo con 29 comprobaciones; cliente Qt en modo offscreen con 20).

## Bugs que rompían cosas
| Dónde | Problema | Estado |
|---|---|---|
| `pylex_api.py` | Las cabeceras CORS se enviaban **antes** de `HTTP/1.0 200 OK` → respuesta malformada para cualquier cliente sin `Origin` (tu cliente de escritorio ya llevaba un parche `_LenientHTTPResponse` para esquivarlo). Ahora se emiten en `end_headers()` | ✔ |
| `pylex_api.py` | `import pylex` fallaba si el fichero se llama `pylexv2.py`, y importaba una 2ª copia del módulo si se ejecutaba como script. Ahora `_px()` reutiliza el módulo ya cargado | ✔ |
| `pylex_api.py` | Auth Bearer anunciada (el login devolvía `token`) pero `get_token()` solo leía la cookie | ✔ |
| Servidor | Contador de reproducciones y `activity_log` subían en **cada latido de progreso** (cada 8–10 s). Ahora 1 por usuario/archivo cada 30 min | ✔ |
| Servidor | Navegación por artista/álbum no filtraba nada (el parámetro se ignoraba) y el orden `track` no existía | ✔ |
| Servidor | Escaneo mantenía el bloqueo de escritura de SQLite durante toda la pasada → `database is locked` al guardar progreso. Commits por lotes | ✔ |
| Servidor | Escaneo no eliminaba ficheros borrados (y si el NAS está desmontado no vacía la biblioteca) | ✔ |
| Servidor | Miniaturas de vídeos <5 s nunca se generaban; sin caché negativa se relanzaba ffmpeg en cada petición; escritura no atómica | ✔ |
| Servidor | `Range: bytes=-N` devolvía el principio en vez del final; 416 sin `Content-Range` | ✔ |
| Servidor | `/api/play` con `progress` inválido → 500; con id inexistente → 200 + fila huérfana | ✔ |
| Servidor | `clean_title`: "2001 A Space Odyssey" quedaba vacío, `Don'T`, años con `_` no se detectaban; ahora usa tag `title` en audio | ✔ |
| Servidor | Tags con doble escape (`&amp;amp;`) en la ficha; retomaba desde el 99 % de un vídeo ya visto | ✔ |
| Servidor | Auto-scan leía el intervalo solo al arrancar; `timeout=300` no tenía efecto; `log_message` comparaba strings | ✔ |
| Escritorio | `QObject` sin importar (solo en anotación, no crasheaba), `import requests` innecesario (dependencia falsa) | ✔ |
| Escritorio | Auto-login **borraba el token** ante cualquier fallo de red | ✔ |
| Escritorio | Búsquedas sin URL-encode (`&`, `#`, ñ, emoji rompían o fallaban) | ✔ |
| Escritorio | Solo `http` y puerto 80 por defecto → no funcionaba tras HTTPS | ✔ (unit) |
| Escritorio | Proxy local monohilo (conexiones Range simultáneas de QMediaPlayer se bloqueaban) | ✔ |
| Escritorio | Audio nunca guardaba posición; ahora cada 15 s y al terminar | ⚠ no probado con reproducción real |
| Escritorio | Cerrar sesión cerraba la última ventana → posible salida de la app. Ahora se abre el login antes | ⚠ no probado en GUI real |
| Escritorio | 1 `QThread` por miniatura (cientos de hilos y peticiones a ffmpeg) + `QPixmap` creado fuera del hilo GUI. Ahora `QThreadPool` de 6 + `QImage` + caché | ✔ |

## Seguridad
| Problema | Estado |
|---|---|
| **XSS reflejado** en `/library/?sort=…` y `/search?type=/year=/lib=` | ✔ whitelist/validación |
| XSS almacenado vía `</script>` en título/artista/álbum (tags ID3 o nombre de fichero) dentro de `<script>` → `_js()` | ✔ |
| Token de sesión visible en el HTML de `/profile` (anulaba `HttpOnly`) → ahora id público; API igual | ✔ |
| Sin límite de intentos de login + enumeración de usuarios por tiempo → 5 fallos/5 min por IP+usuario, hash ficticio | ✔ |
| Rol arbitrario (`superroot`) aceptado en rutas nativas | ✔ |
| Cookie dura 1 día pero la sesión en BD 30 → ahora coinciden | ✔ |
| API creaba bibliotecas sin la lista de rutas prohibidas; blocklist ampliada y compartida | ✔ |
| Usuarios `viewer` veían rutas del disco del servidor en la API | ✔ |
| CORS: con `PYLEX_CORS_ORIGINS=*` reflejaba cualquier origen **con credenciales**. Ahora solo lista explícita | ✔ |
| Proxy local del escritorio reenviaba *cualquier* ruta (incluida `/api/users`) con tu sesión a cualquier proceso local → secreto + solo `stream|thumb/<id>` | ✔ |
| `config.json` con el token en claro → permisos 600 | ✔ |
| Cambiar contraseña ahora cierra las demás sesiones; cabeceras `nosniff`, `X-Frame-Options`; `Cache-Control: private` en miniaturas; sesiones caducadas se purgan | ✔ |

## Rendimiento
`needs_setup()` ya no consulta la BD en cada petición (incl. cada miniatura/Range); `get_db()` no repite `PRAGMA journal_mode`; el cliente ya no lanza cientos de peticiones simultáneas de miniaturas.

## NO arreglado (decisiones tuyas / cambios de diseño)
1. **Progreso y "continuar viendo" son globales, no por usuario** (columnas `progress/position` en `media`). Con varios usuarios se pisan. Requiere tabla `user_progress`.
2. Sin transcodificación: `.mkv/.avi/.wmv/.flv` no reproducen en el navegador (el escritorio sí vía Qt/FFmpeg).
3. Las fotos se sirven a tamaño original como miniatura (Pillow lo resolvería).
4. Primer arranque: quien llegue antes a la web crea el admin. Si lo expones, créalo tú primero.
5. HTTP plano en el servidor: usa un reverse proxy con TLS si sale de tu LAN (añadir `Secure` a la cookie entonces).
6. Duración/resolución de vídeo siguen a 0 (haría falta `ffprobe`).
7. `_is_usable_ip` descarta 172.16/12 (también redes LAN legítimas).
8. Python ≥ 3.10 (anotaciones `X | None` en el escritorio).

## Notas
- Con el servidor corregido, `_LenientHTTPResponse` del cliente ya no es necesario; lo dejé por compatibilidad con servidores antiguos.
- `GET /api/sessions` ahora devuelve `id` (+`current`) en vez de `token_preview`; `DELETE /api/sessions/<id>` usa ese id.
- `POST /api/libraries` (API) ahora lanza el escaneo en segundo plano y devuelve `scanning: true`.
