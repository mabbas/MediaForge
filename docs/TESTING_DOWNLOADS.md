# Testing Video Downloading (Web App & Desktop App)

This guide explains how to test the video downloading flow using the **CLI**, **desktop app**, and **web app**.

## Prerequisites

- **Python** env with project dependencies: `pip install -e ".[dev]"`
- **ffmpeg** and **yt-dlp** available (for YouTube and other providers)
- For **web app**: Node.js 18+; run `cd web && npm install`
- Optional: **Database** for API (desktop uses SQLite by default; standalone API can use same or env `GID_API_DATABASE_URL`)

---

## 1. Test with CLI (no API)

Fastest way to verify the download engine:

```bash
# Single video
python -m src.cli.cli download "https://www.youtube.com/watch?v=VIDEO_ID" -q 720p

# Batch from a text file (one URL per line)
echo "https://www.youtube.com/watch?v=VIDEO_ID" > urls.txt
python -m src.cli.cli batch urls.txt -m video -q 720p
```

Or with the `gid` entry point if installed:

```bash
gid download "https://www.youtube.com/watch?v=VIDEO_ID" -q 720p
gid batch urls.txt
```

Output files go to the configured download directory (see config or `GID_DOWNLOAD_OUTPUT_DIR`).

---

## 2. Test with Desktop App

The desktop app runs an **embedded API server** and a built-in dashboard that talks to it.

1. **Start the desktop app**
   ```bash
   python -m desktop
   ```
   Or: `make desktop` (if Makefile target exists).

2. The app will:
   - Start the API on **http://127.0.0.1:8765** (default)
   - Open a window with the built-in download UI

3. **In the desktop window**
   - Paste a video URL (e.g. a YouTube link)
   - Choose mode (video/audio) and quality
   - Click the download button to submit a job
   - Use Pause/Resume/Cancel as needed
   - Progress updates via WebSocket

4. **Optional: use the same API from the web app**
   - Start the desktop app first (so the API is running on port 8765).
   - Run the web app and point it at the desktop API:
     ```bash
     cd web && set NEXT_PUBLIC_API_URL=http://127.0.0.1:8765/api/v1 && npm run dev
     ```
   - Open http://localhost:3000 and use the web UI; requests go to the desktop’s backend.

---

## 3. Test with Web App (standalone API)

To use the **web app** with a **standalone API** (e.g. uvicorn):

1. **Start the API**
   ```bash
   make api-dev
   ```
   Or:
   ```bash
   uvicorn api.main:app --reload --host 0.0.0.0 --port 8000
   ```
   API base: **http://localhost:8000/api/v1**

2. **Start the web app and point to the API**
   ```bash
   cd web
   set NEXT_PUBLIC_API_URL=http://localhost:8000/api/v1
   npm run dev
   ```
   (On macOS/Linux use `export NEXT_PUBLIC_API_URL=http://localhost:8000/api/v1`.)

3. **In the browser**
   - Open http://localhost:3000
   - Go to **Downloads**
   - Paste a URL and click **Download** (single), or use **Batch** / **Playlist** tabs
   - Jobs appear in the list; click a job to open the detail drawer (retry/cancel/open URL)

4. **CORS**
   - The API allows CORS; if you use a different host/port for the web app, ensure `GID_API_CORS_ORIGINS` includes it.

---

## 4. Test with Web App + Desktop API

- Start **only the desktop app** (`python -m desktop`).
- Set `NEXT_PUBLIC_API_URL=http://127.0.0.1:8765/api/v1` and run `npm run dev` in `web/`.
- Use the web UI while the desktop’s embedded server handles downloads and storage.

---

## Quick checklist

| Method        | API running      | How to test                          |
|---------------|------------------|--------------------------------------|
| **CLI**       | No               | `gid download URL` / `gid batch file` |
| **Desktop**   | Embedded (8765)  | Run `python -m desktop`, use in-window UI |
| **Web (standalone)** | Yes (e.g. 8000) | Run API + `NEXT_PUBLIC_API_URL=... npm run dev` |
| **Web (desktop API)** | Desktop on 8765 | Run desktop, then web with `NEXT_PUBLIC_API_URL=http://127.0.0.1:8765/api/v1` |

---

## API endpoints used

- **POST /api/v1/downloads** — single download (body: `url`, `mode`, `quality`, `priority`)
- **POST /api/v1/downloads/batch** — batch (body: `urls`, `mode`, `quality`)
- **POST /api/v1/downloads/playlist** — playlist (body: `url`, `mode`, `quality`, `concurrency`)
- **POST /api/v1/downloads/{id}/cancel** — cancel job
- **POST /api/v1/downloads/{id}/resume** — resume failed/paused job
- **GET /api/v1/downloads** — list jobs
- **GET /api/v1/downloads/{id}/file** — download completed file
- **POST /api/v1/queue/pause** — pause engine
- **POST /api/v1/queue/resume** — resume engine

WebSocket progress: **ws://host:port/api/v1/ws/progress** (optional; desktop dashboard uses it).
