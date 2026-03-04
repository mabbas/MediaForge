# MediaForge — Build, Run & Usage Guide

This guide covers how to build and run **MediaForge** (GrabItDown) and how to use the CLI, API, Web UI, and Desktop app.

---

## Table of contents

1. [Prerequisites](#prerequisites)
2. [Clone and setup](#clone-and-setup)
3. [Backend API](#backend-api)
4. [Web UI](#web-ui)
5. [Desktop app](#desktop-app)
6. [Environment variables](#environment-variables)
7. [Usage — CLI](#usage--cli)
8. [Usage — API](#usage--api)
9. [Usage — Web UI & Desktop](#usage--web-ui--desktop)
10. [Transcripts & rate limits](#transcripts--rate-limits)

---

## Prerequisites

- **Python 3.11+** (3.11 or 3.12 recommended; 3.14 may have issues with optional desktop native window)
- **Node.js 18+** and **npm** (for the Web UI)
- **ffmpeg** and **ffprobe** on PATH, or set `GID_FFMPEG_LOCATION` (see [Environment variables](#environment-variables))
- **PostgreSQL** (optional): required only if you run the full API with database; desktop and CLI can run without it
- **WebView2** (Windows): required only for the optional native desktop window (pywebview)

---

## Clone and setup

```bash
git clone https://github.com/your-org/GrabItDown.git
cd GrabItDown
```

Create a virtual environment and install the package:

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

python -m pip install -e .
```

Optional extras:

- **Desktop native window:** `python -m pip install -e ".[desktop]"`
- **Development (tests, lint):** `python -m pip install -e ".[dev]"`

Or use the project Makefile (Unix):

```bash
make install   # installs with [dev]
```

---

## Backend API

The API serves downloads, queue, transcripts, and history. It uses **PostgreSQL** by default (or SQLite for testing).

### Database (PostgreSQL)

Start PostgreSQL (e.g. via Docker):

```bash
make db-up
# or: docker compose -f docker/docker-compose.dev.yml up -d
```

Run migrations:

```bash
make db-migrate
# or: alembic upgrade head
```

### Run the API

From the project root:

```bash
uvicorn api.main:app --reload --host 0.0.0.0 --port 8000
```

Or:

```bash
make api-dev
```

API base URL: **http://localhost:8000**. Docs: **http://localhost:8000/docs**.

To use SQLite for local testing (no PostgreSQL):

```bash
set GID_API_DATABASE_URL=sqlite+aiosqlite:///./data/api.db
uvicorn api.main:app --reload --host 0.0.0.0 --port 8000
```

---

## Web UI

The Web UI is a Next.js app that talks to the API. Run it only after the API is running (or configure `NEXT_PUBLIC_API_URL` to point to your API).

```bash
cd web
npm install
npm run dev
```

Open **http://localhost:3000**. The app uses `NEXT_PUBLIC_API_URL` or defaults to `/api/v1` (same origin). For a separate API server set e.g. `NEXT_PUBLIC_API_URL=http://localhost:8000/api/v1` in `web/.env.local`.

Build for production:

```bash
npm run build
npm run start
```

---

## Desktop app

The desktop app runs a local server and opens a window (or uses the system browser). It uses **SQLite** locally and does not require PostgreSQL.

```bash
python -m desktop
```

Or:

```bash
make desktop
```

- **With native window:** install `.[desktop]` (pywebview). On Windows, WebView2 is required; Python 3.11/3.12 are recommended.
- **Debug:** `GID_DEBUG=true python -m desktop` or `make desktop-debug`.

The desktop server runs on **http://127.0.0.1:8765** by default. You can change host/port with `GID_DESKTOP_SERVER_HOST` and `GID_DESKTOP_SERVER_PORT`.

---

## Environment variables

| Variable | Where | Description |
|----------|--------|-------------|
| `GID_FFMPEG_LOCATION` | CLI, API, Desktop | Directory containing `ffmpeg` and `ffprobe`. Use forward slashes on Windows (e.g. `C:/ffmpeg/bin`). |
| `GID_API_DATABASE_URL` | API | PostgreSQL: `postgresql+asyncpg://user:pass@host:5432/dbname`. SQLite: `sqlite+aiosqlite:///./data/api.db`. |
| `GID_API_HOST` / `GID_API_PORT` | API | Bind host and port (default `0.0.0.0`, `8000`). |
| `GID_DESKTOP_SERVER_HOST` / `GID_DESKTOP_SERVER_PORT` | Desktop | Local server (default `127.0.0.1`, `8765`). |
| `NEXT_PUBLIC_API_URL` | Web | API base URL (e.g. `http://localhost:8000/api/v1`). |

Optional: create a `.env` file at the project root; `GID_*` and API/Desktop prefixed vars are loaded from there.

---

## Usage — CLI

MediaForge installs the **`gid`** (and **`grabitdown`**) command. Use it for one-off downloads, playlists, batch files, and transcripts.

### Commands overview

```bash
gid --help
gid download --help
gid playlist --help
gid batch --help
gid transcript --help
gid formats --help
gid check
gid config
```

### Download a single URL

```bash
# Video (default 1080p)
gid download "https://www.youtube.com/watch?v=VIDEO_ID"

# Audio (default 192k)
gid download "https://www.youtube.com/watch?v=VIDEO_ID" -m audio

# Quality and format
gid download "URL" -m video -q 720p -f mp4
gid download "URL" -m audio -f mp3 --audio-bitrate 320k

# Output directory and subtitles
gid download "URL" -o ./downloads --embed-subs --sub-langs en,ur
```

### Playlist

```bash
# Download entire playlist (video, 1080p, 3 concurrent)
gid playlist "https://www.youtube.com/playlist?list=PLAYLIST_ID"

# Audio, specific items, 5 concurrent
gid playlist "URL" -m audio --items 1,3,5-8 --concurrency 5

# Only show playlist info
gid playlist "URL" --info-only
```

### Batch from file

```bash
# One URL per line in urls.txt
gid batch urls.txt -m video -q 720p
gid batch urls.txt -m audio --concurrency 4
```

### Transcripts (CLI)

```bash
# Default: English, SRT
gid transcript "https://www.youtube.com/watch?v=VIDEO_ID"

# Language and format
gid transcript "URL" -l ur -f vtt -o transcript.vtt

# List available subtitle languages
gid transcript "URL" --list-langs
```

### Other

- **Formats:** `gid formats "URL"` (optionally `--video-only` or `--audio-only`)
- **Check:** `gid check` — environment and config
- **Config:** `gid config` — show current settings

Use `--debug` for verbose logs, e.g. `gid --debug download "URL"`.

---

## Usage — API

Base URL: **http://localhost:8000** (or your deployment). Prefix for app routes: **/api/v1**.

### Downloads

- **POST /api/v1/downloads/batch** — Submit multiple URLs (body: `urls`, `mode`, `quality`).
- **POST /api/v1/downloads/playlist** — Submit a playlist (body: `url`, `mode`, `quality`, `concurrency`).
- **GET /api/v1/downloads** — List jobs.
- **POST /api/v1/downloads/{job_id}/cancel** — Cancel a job.
- **POST /api/v1/downloads/{job_id}/resume** — Resume a failed/paused job.
- **GET /api/v1/downloads/{job_id}/file** — Stream or get the downloaded file when completed.

### Queue

- **GET /api/v1/queue** — Queue status.
- **POST /api/v1/queue/pause** — Pause queue.
- **POST /api/v1/queue/resume** — Resume queue.

### Transcripts

- **GET /api/v1/transcripts/languages?url=...** — List available subtitle languages for a URL.
- **POST /api/v1/transcripts** — Request a transcript (body: URL, language, format, script preference, etc.).
- **GET /api/v1/transcripts/{id}** — Get transcript status or result.

### History & config

- **GET /api/v1/history** — Download history (with pagination).
- **GET /api/v1/config** — Public config (e.g. quality options).

Interactive API docs: **http://localhost:8000/docs**.

---

## Usage — Web UI & Desktop

Both the **Web UI** and the **Desktop app** offer the same flows, with the desktop adding tray and system integration.

### Common flows

1. **Download (single or batch)**  
   Paste one or more URLs, choose **Video** or **Audio**, select quality, then start. Jobs appear in the download list with progress; you can **Cancel** or **Resume** per job.

2. **Playlist**  
   Submit a playlist URL with mode and quality; concurrency controls how many items download at once. Jobs are queued and shown in the same list.

3. **Queue**  
   Use **Pause** / **Resume** to pause or resume the whole queue. The “simultaneous downloads” (concurrency) setting applies to how many jobs run at once.

4. **Transcripts**  
   Open the transcript page, enter a URL. Use “Detect languages” (or the API’s language list) to see available languages. Choose language, script (e.g. Roman vs native), and format. If the service returns **429 (rate limit)**, the app may wait or show a cooldown (e.g. ~3 minutes) before retrying.

5. **History**  
   View and, where supported, export or open past downloads.

### Desktop-only

- **Tray:** Minimize to system tray; optional “start on login”.
- **Clipboard:** Optional paste-from-clipboard for URLs.
- **Notifications:** Optional completion notifications.

---

## Transcripts & rate limits

- **Script:** You can choose **Roman** (romanized) or **native** script for supported languages (e.g. Urdu).
- **429 handling:** When the upstream service rate-limits (429), the app may wait and retry after a cooldown (e.g. 3 minutes); avoid submitting many transcript requests in a short time.
- **Languages:** Use “List languages” (CLI: `gid transcript "URL" --list-langs`) or the API/UI “Detect languages” to see what’s available for a given URL.

---

## Quick reference

| Goal | Command / Action |
|------|------------------|
| Install | `python -m pip install -e .` (optional: `.[desktop]`, `.[dev]`) |
| Run API | `uvicorn api.main:app --reload --host 0.0.0.0 --port 8000` or `make api-dev` |
| Run Web | `cd web && npm install && npm run dev` |
| Run Desktop | `python -m desktop` or `make desktop` |
| CLI download | `gid download "URL"` or `gid download "URL" -m audio` |
| CLI playlist | `gid playlist "PLAYLIST_URL"` |
| CLI transcript | `gid transcript "URL"` or `gid transcript "URL" -l ur -f vtt` |
| API docs | http://localhost:8000/docs |
| Web app | http://localhost:3000 (with API on 8000 or proxy) |

This guide uses the product name **MediaForge**; repository and package names (e.g. GrabItDown, `grabitdown`) remain as in the codebase.
