# 🎬 PyLex Media Server

> Un clon de Plex Media Server escrito íntegramente en Python, sin dependencias de frameworks externos.

![Python](https://img.shields.io/badge/Python-3.8%2B-blue?logo=python&logoColor=white)
![SQLite](https://img.shields.io/badge/Database-SQLite-lightblue?logo=sqlite)
![Version](https://img.shields.io/badge/version-1.2.0-orange)
![License](https://img.shields.io/badge/license-MIT-green)

---

## ✨ Características

- 🎥 **Soporte multimedia completo** — vídeo, audio e imágenes
- 📚 **Bibliotecas** — organiza tu colección por tipo y directorio
- 🔍 **Búsqueda** — filtra por título, género, artista, álbum y más
- 👥 **Multiusuario** — roles `admin` y `viewer` con sesiones seguras
- 📡 **Streaming con rango de bytes** — reproducción fluida y seek compatible
- 🖼️ **Miniaturas automáticas** — extrae carátulas de audio y fotogramas de vídeo
- 🏷️ **Metadatos** — lee etiquetas ID3/Vorbis/MP4 con `mutagen`
- 🔄 **Escaneo automático** — refresca las bibliotecas cada N horas
- 🌐 **Acceso en red local** — detección automática de IP para compartir en LAN
- 📊 **Historial y progreso** — registro de reproducciones y posición de vuelta
- 🎨 **Interfaz web oscura** — diseño responsivo con tema oscuro sin dependencias frontend

---

## 📋 Requisitos

| Componente | Versión mínima | Notas |
|---|---|---|
| Python | 3.8+ | Solo stdlib para el core |
| `mutagen` | cualquiera | Opcional — metadatos y carátulas de audio |
| `ffmpeg` | cualquiera | Opcional — miniaturas de vídeo |

> **PyLex funciona sin instalar nada extra.** `mutagen` y `ffmpeg` amplían las funciones de metadatos y thumbnails, pero no son obligatorios.

---

## 🚀 Instalación y uso

### 1. Clona el repositorio

```bash
git clone https://github.com/tu-usuario/pylex.git
cd pylex
```

### 2. (Opcional) Instala dependencias adicionales

```bash
pip install mutagen          # Metadatos y carátulas de audio
# ffmpeg debe instalarse desde https://ffmpeg.org o con tu gestor de paquetes
```

### 3. Ejecuta el servidor

```bash
python pylex.py
```

Al arrancar, PyLex:
1. Inicializa la base de datos `pylex.db`
2. Detecta automáticamente tu IP de red local
3. Te pide confirmación de la IP (o permite introducirla manualmente)
4. Lanza el servidor en el puerto **7777**

### 4. Abre el navegador

```
http://localhost:7777
```

En la **primera ejecución** se te pedirá crear la cuenta de administrador.

---

## ⚙️ Configuración

Las opciones principales se encuentran al inicio de `pylex.py`:

| Variable | Valor por defecto | Descripción |
|---|---|---|
| `PORT` | `7777` | Puerto del servidor HTTP |
| `DB_PATH` | `pylex.db` | Ruta de la base de datos SQLite |
| `THUMB_DIR` | `thumbs/` | Directorio de miniaturas generadas |
| `CHUNK_SIZE` | `1 MB` | Tamaño de chunk para streaming |
| `SESSION_DAYS` | `30` | Duración de las sesiones de usuario |
| `AUTO_SCAN_HOURS` | `4` | Intervalo de escaneo automático (0 = desactivado) |

---

## 🗂️ Formatos soportados

| Tipo | Extensiones |
|---|---|
| **Vídeo** | `.mp4` `.mkv` `.avi` `.mov` `.wmv` `.flv` `.webm` `.m4v` `.mpeg` `.mpg` `.ts` `.3gp` `.ogv` |
| **Audio** | `.mp3` `.flac` `.ogg` `.wav` `.aac` `.m4a` `.wma` `.opus` `.aiff` `.ape` `.wv` |
| **Imagen** | `.jpg` `.jpeg` `.png` `.gif` `.webp` `.bmp` `.tiff` `.svg` |

---

## 🗄️ Estructura de la base de datos

PyLex usa **SQLite** con las siguientes tablas:

```
users          — Cuentas de usuario y roles
sessions       — Tokens de sesión activos
libraries      — Bibliotecas multimedia definidas por el admin
media          — Archivos indexados con metadatos
settings       — Configuración del servidor
activity_log   — Historial de reproducciones
```

Las migraciones se aplican automáticamente al arrancar.

---

## 👤 Roles de usuario

| Rol | Permisos |
|---|---|
| `admin` | Gestión de bibliotecas, usuarios, configuración y escaneo |
| `viewer` | Reproducción, búsqueda y consulta de bibliotecas |

---

## 🌐 Acceso en red local

Al iniciar, PyLex detecta tu IP de red local automáticamente. Una vez confirmada, cualquier dispositivo de tu LAN puede acceder al servidor en:

```
http://<tu-ip>:7777
```

Si la detección automática falla, puedes introducir la IP manualmente. Consúltala con:

- **Windows:** `ipconfig`
- **Linux / macOS:** `ip a` o `ifconfig`

---

## 🔐 Seguridad

- Contraseñas hasheadas con **PBKDF2-HMAC-SHA256** (200.000 iteraciones)
- Comparación segura con `hmac.compare_digest`
- Tokens de sesión generados con `secrets.token_hex`
- Cookies con flags `HttpOnly` y `SameSite=Lax`
- Sesiones con expiración configurable

---

## 🛠️ Endpoints de diagnóstico

```
GET /api/debug    → Información del servidor (solo admin)
```

---

## 📁 Estructura del proyecto

```
pylex/
├── pylex.py        # Servidor principal (todo en un solo fichero)
├── pylex.db        # Base de datos SQLite (generada al arrancar)
└── thumbs/         # Miniaturas generadas automáticamente
```

---

## 🤝 Contribuir

Las contribuciones son bienvenidas. Para cambios importantes, abre primero un _issue_ para discutir qué te gustaría modificar.

1. Haz un fork del repositorio
2. Crea una rama: `git checkout -b feature/nueva-funcionalidad`
3. Haz commit de tus cambios: `git commit -m 'feat: añade nueva funcionalidad'`
4. Haz push a la rama: `git push origin feature/nueva-funcionalidad`
5. Abre un Pull Request

---

## 📄 Licencia

Distribuido bajo la licencia **MIT**. Consulta el fichero `LICENSE` para más información.

---

> Hecho con ❤️ y Python puro.
