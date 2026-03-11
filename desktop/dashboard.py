"""Built-in HTML dashboard for the desktop app.

This is a single-page app that uses the embedded
API server and WebSocket for real-time updates.
It's self-contained (no build step needed).
"""

from __future__ import annotations


def get_dashboard_html(api_base: str) -> str:
    """Generate the dashboard HTML.

    Returns complete HTML with inline CSS and JS
    that connects to the GrabItDown API.
    """

    ws_base = api_base.replace("http://", "ws://")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport"
          content="width=device-width, initial-scale=1.0">
    <title>GrabItDown</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont,
                'Segoe UI', system-ui, sans-serif;
            background: #0f0f10;
            color: #e0e0e0;
            min-height: 100vh;
        }}
        .header {{
            background: linear-gradient(135deg, #1a1a2e, #16213e);
            padding: 16px 24px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            border-bottom: 1px solid #2a2a3e;
        }}
        .header h1 {{
            font-size: 20px;
            background: linear-gradient(90deg, #00d4ff, #7b2ff7);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }}
        .status-badge {{
            padding: 4px 12px;
            border-radius: 12px;
            font-size: 12px;
            font-weight: 600;
        }}
        .status-online {{ background: #1a3a2a; color: #4ade80; }}
        .status-offline {{ background: #3a1a1a; color: #f87171; }}
        .container {{ max-width: 1000px; margin: 0 auto; padding: 24px; }}
        .input-group {{
            display: flex;
            gap: 8px;
            margin-bottom: 24px;
        }}
        .input-group input {{
            flex: 1;
            padding: 12px 16px;
            border: 1px solid #2a2a3e;
            border-radius: 8px;
            background: #1a1a2e;
            color: #e0e0e0;
            font-size: 14px;
            outline: none;
        }}
        .input-group input:focus {{ border-color: #00d4ff; }}
        .input-group input::placeholder {{ color: #666; }}
        .btn {{
            padding: 12px 24px;
            border: none;
            border-radius: 8px;
            cursor: pointer;
            font-size: 14px;
            font-weight: 600;
            transition: all 0.2s;
        }}
        .btn-primary {{
            background: linear-gradient(135deg, #00d4ff, #7b2ff7);
            color: white;
        }}
        .btn-primary:hover {{ opacity: 0.9; transform: translateY(-1px); }}
        .btn-sm {{
            padding: 6px 14px;
            font-size: 12px;
            border-radius: 6px;
        }}
        .btn-icon {{
            padding: 4px 10px;
            min-width: 32px;
        }}
        .move-btns {{
            display: inline-flex;
            gap: 2px;
        }}
        .btn-danger {{ background: #dc2626; color: white; }}
        .btn-outline {{
            background: transparent;
            border: 1px solid #2a2a3e;
            color: #e0e0e0;
        }}
        .options-row {{
            display: flex;
            gap: 12px;
            margin-bottom: 24px;
            flex-wrap: wrap;
        }}
        .options-row select {{
            padding: 8px 12px;
            border: 1px solid #2a2a3e;
            border-radius: 6px;
            background: #1a1a2e;
            color: #e0e0e0;
            font-size: 13px;
        }}
        .input-style {{
            padding: 12px 16px;
            border: 1px solid #2a2a3e;
            border-radius: 8px;
            background: #1a1a2e;
            color: #e0e0e0;
            font-size: 14px;
            outline: none;
        }}
        .input-style:focus {{ border-color: #00d4ff; }}
        .select-style {{
            padding: 8px 12px;
            border: 1px solid #2a2a3e;
            border-radius: 6px;
            background: #1a1a2e;
            color: #e0e0e0;
            font-size: 13px;
        }}
        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
            gap: 12px;
            margin-bottom: 24px;
        }}
        .stat-card {{
            background: #1a1a2e;
            border: 1px solid #2a2a3e;
            border-radius: 10px;
            padding: 16px;
            text-align: center;
        }}
        .stat-card .value {{
            font-size: 28px;
            font-weight: 700;
            color: #00d4ff;
        }}
        .stat-card .label {{
            font-size: 11px;
            color: #888;
            margin-top: 4px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}
        .section {{ margin-bottom: 24px; }}
        .section-title {{
            font-size: 16px;
            font-weight: 600;
            margin-bottom: 12px;
            color: #ccc;
        }}
        .job-list {{ display: flex; flex-direction: column; gap: 8px; }}
        .job-card {{
            background: #1a1a2e;
            border: 1px solid #2a2a3e;
            border-radius: 10px;
            padding: 14px 18px;
            display: flex;
            align-items: center;
            gap: 14px;
        }}
        .job-info {{ flex: 1; min-width: 0; }}
        .job-title {{
            font-size: 14px;
            font-weight: 500;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }}
        .job-meta {{
            font-size: 12px;
            color: #888;
            margin-top: 3px;
        }}
        .job-progress {{
            width: 200px;
            display: flex;
            flex-direction: column;
            align-items: flex-end;
            gap: 4px;
        }}
        .progress-bar {{
            width: 100%;
            height: 6px;
            background: #2a2a3e;
            border-radius: 3px;
            overflow: hidden;
        }}
        .progress-fill {{
            height: 100%;
            border-radius: 3px;
            transition: width 0.3s;
            background: linear-gradient(90deg, #00d4ff, #7b2ff7);
        }}
        .progress-text {{ font-size: 12px; color: #aaa; }}
        .job-status {{
            padding: 3px 10px;
            border-radius: 10px;
            font-size: 11px;
            font-weight: 600;
        }}
        .status-queued {{ background: #2a2a3e; color: #aaa; }}
        .status-deferred {{ background: #2a2a2e; color: #a78bfa; }}
        .status-downloading {{ background: #1a2a3e; color: #60a5fa; }}
        .status-paused {{ background: #2a2a2e; color: #fbbf24; }}
        .status-completed {{ background: #1a3a2a; color: #4ade80; }}
        .status-failed {{ background: #3a1a1a; color: #f87171; }}
        .status-cancelled {{ background: #3a3a1a; color: #fbbf24; }}
        .empty-state {{
            text-align: center;
            padding: 48px;
            color: #666;
        }}
        .empty-state .icon {{ font-size: 48px; margin-bottom: 12px; }}
        .toast-container {{
            position: fixed;
            bottom: 20px;
            right: 20px;
            z-index: 9999;
            display: flex;
            flex-direction: column;
            gap: 8px;
        }}
        .toast {{
            padding: 12px 20px;
            border-radius: 8px;
            font-size: 13px;
            animation: slideIn 0.3s ease;
            max-width: 350px;
        }}
        .toast-success {{ background: #1a3a2a; border: 1px solid #4ade80; color: #4ade80; }}
        .toast-error {{ background: #3a1a1a; border: 1px solid #f87171; color: #f87171; }}
        .toast-info {{ background: #1a2a3e; border: 1px solid #60a5fa; color: #60a5fa; }}
        .clip-job-card {{
            display: none;
            margin-bottom: 12px;
        }}
        .clip-job-card.visible {{
            display: flex;
        }}
        .clip-job-card .job-progress {{ width: 100%; max-width: 240px; }}
        .clip-job-card.visible .clip-progress-fill {{
            width: 45% !important;
            animation: clipIndeterminate 1.5s ease-in-out infinite;
        }}
        @keyframes clipIndeterminate {{
            0% {{ margin-left: 0; }}
            50% {{ margin-left: 55%; }}
            100% {{ margin-left: 0; }}
        }}
        @keyframes slideIn {{
            from {{ transform: translateX(100%); opacity: 0; }}
            to {{ transform: translateX(0); opacity: 1; }}
        }}
        .tab-bar {{
            display: flex;
            gap: 4px;
            margin-bottom: 20px;
            border-bottom: 1px solid #2a2a3e;
        }}
        .tab-btn {{
            padding: 12px 20px;
            background: transparent;
            border: none;
            border-bottom: 3px solid transparent;
            color: #888;
            font-size: 14px;
            font-weight: 600;
            cursor: pointer;
            margin-bottom: -1px;
        }}
        .tab-btn:hover {{ color: #e0e0e0; }}
        .tab-btn.active {{
            color: #00d4ff;
            border-bottom-color: #00d4ff;
        }}
        .tab-panel {{
            display: none;
        }}
        .tab-panel.active {{
            display: block;
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1>GrabItDown</h1>
        <div>
            <span id="status-badge"
                  class="status-badge status-online">
                ● Online
            </span>
        </div>
    </div>

    <div class="container">
        <div class="tab-bar">
            <button type="button" class="tab-btn active" id="tab-btn-download"
                onclick="switchTab('download')">📥 Download</button>
            <button type="button" class="tab-btn" id="tab-btn-extract"
                onclick="switchTab('extract')">✂️ Extract Clip</button>
            <button type="button" class="tab-btn" id="tab-btn-merge"
                onclick="switchTab('merge')">🔗 Merge Clips</button>
        </div>

        <div id="panel-download" class="tab-panel active">
        <div class="input-group">
            <input type="text" id="url-input"
                   placeholder="Paste video URL here... (YouTube, Vimeo, etc.)"
                   autocomplete="off">
            <button class="btn btn-primary"
                    onclick="submitDownload()">
                Add to queue
            </button>
        </div>

        <div class="options-row">
            <select id="mode-select">
                <option value="video">Video</option>
                <option value="audio">Audio</option>
                <option value="transcript">Transcript</option>
            </select>
            <select id="quality-select">
                <option value="best">Best</option>
                <option value="1080p" selected>1080p</option>
                <option value="720p">720p</option>
                <option value="480p">480p</option>
                <option value="360p">360p</option>
            </select>
            <select id="priority-select">
                <option value="high">High Priority</option>
                <option value="normal" selected>Normal</option>
                <option value="low">Low Priority</option>
            </select>
            <label for="input-max-concurrent" style="font-size:12px;color:#888;margin-left:8px;">Simultaneous:</label>
            <input type="number" id="input-max-concurrent" min="1" max="10" value="3"
                   style="width:42px;padding:6px 8px;font-size:13px;border:1px solid #2a2a3e;border-radius:6px;background:#1a1a2e;color:#e0e0e0;margin-left:4px;">
            <button class="btn btn-outline btn-sm" id="btn-pause-queue"
                    onclick="pauseQueue()">Pause</button>
            <button class="btn btn-outline btn-sm" id="btn-resume-queue"
                    onclick="resumeQueue()">Resume</button>
        </div>

        <div class="stats-grid" id="stats-grid">
            <div class="stat-card">
                <div class="value" id="stat-active">0</div>
                <div class="label">Active</div>
            </div>
            <div class="stat-card">
                <div class="value" id="stat-queued">0</div>
                <div class="label">Queued</div>
            </div>
            <div class="stat-card">
                <div class="value" id="stat-completed">0</div>
                <div class="label">Completed</div>
            </div>
            <div class="stat-card">
                <div class="value" id="stat-failed">0</div>
                <div class="label">Failed</div>
            </div>
            <div class="stat-card">
                <div class="value" id="stat-disk">--</div>
                <div class="label">Free Space (GB)</div>
            </div>
        </div>

        <div class="section">
            <div class="section-title">Downloads</div>
            <div class="queue-actions" style="display:flex;align-items:center;gap:8px;margin-bottom:12px;">
                <button type="button" class="btn btn-primary btn-sm" id="btn-start-all"
                    onclick="startAllDownloads()">Start all download</button>
                <button type="button" class="btn btn-outline btn-sm" id="btn-pause-all"
                    onclick="pauseQueue()">Pause all</button>
            </div>
            <div id="clip-job-card" class="clip-job-card job-card">
                <div class="job-info">
                    <div class="job-title">✂️ Extracting clip…</div>
                    <div class="job-meta" id="clip-job-meta">—</div>
                </div>
                <div class="job-progress">
                    <div class="progress-bar"><div class="progress-fill clip-progress-fill" style="width:0%"></div></div>
                    <div class="progress-text" id="clip-progress-text">0:00</div>
                </div>
                <span class="job-status status-downloading">extracting</span>
            </div>
            <div class="job-list" id="job-list">
                <div class="empty-state">
                    <div class="icon">📥</div>
                    <div>No downloads yet. Paste a URL and click Add to queue, then Start all or start each job.</div>
                </div>
            </div>
        </div>
        </div>

        <div id="panel-extract" class="tab-panel">
        <div class="section">
            <div class="section-title">✂️ Clip Extraction</div>
            <div id="clip-extract-progress-card" class="clip-job-card job-card">
                <div class="job-info">
                    <div class="job-title">✂️ Extracting clip…</div>
                    <div class="job-meta" id="clip-extract-job-meta">—</div>
                </div>
                <div class="job-progress">
                    <div class="progress-bar"><div class="progress-fill clip-progress-fill" style="width:0%"></div></div>
                    <div class="progress-text" id="clip-extract-progress-text">0:00</div>
                </div>
                <span class="job-status status-downloading">extracting</span>
            </div>
            <p style="font-size:12px;color:#888;margin-bottom:8px;">
                From a local video or paste a URL to download and clip.
            </p>
            <div style="display:flex;gap:8px;margin-bottom:8px;align-items:center;">
                <input type="text" id="clip-source"
                    placeholder="Video file path or URL"
                    style="flex:2;" class="input-style">
                <button type="button" class="btn btn-outline btn-sm"
                    onclick="browseClipSource()" title="Select video on your computer">
                    📁 Browse
                </button>
            </div>
            <div style="display:flex;gap:8px;margin-bottom:8px;
                align-items:center;">
                <input type="text" id="clip-start"
                    value="00:10:00" placeholder="Start (00:01:30)"
                    style="width:140px;" class="input-style">
                <span style="color:#888;">→</span>
                <input type="text" id="clip-end"
                    value="00:30:00" placeholder="End (00:03:45)"
                    style="width:140px;" class="input-style">
                <select id="clip-mode" class="select-style">
                    <option value="precise">Precise</option>
                    <option value="fast">Fast (keyframe)</option>
                </select>
                <select id="clip-format" class="select-style">
                    <option value="mp4">MP4</option>
                    <option value="mkv">MKV</option>
                    <option value="webm">WebM</option>
                </select>
                <button class="btn btn-primary" id="btn-extract-clip"
                    onclick="extractClip()">
                    ✂️ Extract Clip
                </button>
            </div>
            <div id="clip-result"></div>
        </div>
        </div>

        <div id="panel-merge" class="tab-panel">
        <div class="section">
            <div class="section-title">🔗 Merge Clips</div>
            <p style="font-size:12px;color:#888;margin-bottom:8px;">
                Add 2–10 clips as <strong>local file paths</strong> (not URLs). Download videos in the Download tab first, then use Browse or paste paths (e.g. path/to/video.mp4).
            </p>
            <div id="merge-clips-list" style="margin-bottom:8px;">
                <div class="merge-clip-row" style="display:flex;gap:8px;margin-bottom:4px;align-items:center;">
                    <input type="text" class="merge-clip-input input-style"
                        placeholder="Clip 1 file path" style="flex:1;">
                    <button type="button" class="btn btn-outline btn-sm" onclick="browseMergeClip(this)" title="Select video">📁 Browse</button>
                </div>
                <div class="merge-clip-row" style="display:flex;gap:8px;margin-bottom:4px;align-items:center;">
                    <input type="text" class="merge-clip-input input-style"
                        placeholder="Clip 2 file path" style="flex:1;">
                    <button type="button" class="btn btn-outline btn-sm" onclick="browseMergeClip(this)" title="Select video">📁 Browse</button>
                </div>
            </div>
            <div style="display:flex;gap:8px;align-items:center;
                margin-bottom:8px;">
                <button class="btn btn-outline btn-sm"
                    onclick="addMergeClipInput()">
                    + Add Clip
                </button>
                <select id="merge-mode" class="select-style">
                    <option value="auto">Auto Detect</option>
                    <option value="concat">Fast (same codec)</option>
                    <option value="reencode">Re-encode (any mix)</option>
                </select>
                <select id="merge-format" class="select-style">
                    <option value="mp4">MP4</option>
                    <option value="mkv">MKV</option>
                </select>
                <div style="flex:1;"></div>
                <button class="btn btn-primary"
                    onclick="mergeClips()">
                    🔗 Merge Clips
                </button>
            </div>
            <div id="merge-result"></div>
        </div>
        </div>
    </div>

    <div class="toast-container" id="toasts"></div>

    <script>
    const API = '{api_base}/api/v1';
    const WS_URL = '{ws_base}/api/v1/ws/progress';
    let ws = null;
    let jobs = {{}};

    function switchTab(tabId) {{
        ['download', 'extract', 'merge'].forEach(function(id) {{
            var panel = document.getElementById('panel-' + id);
            var btn = document.getElementById('tab-btn-' + id);
            if (panel) panel.classList.toggle('active', id === tabId);
            if (btn) btn.classList.toggle('active', id === tabId);
        }});
    }}

    async function api(method, path, body) {{
        const opts = {{
            method,
            headers: {{'Content-Type': 'application/json'}},
        }};
        if (body) opts.body = JSON.stringify(body);
        const r = await fetch(API + path, opts);
        return r.json();
    }}

    function isPlaylistUrl(url) {{
        return /[?&]list=/.test(url);
    }}

    async function submitDownload() {{
        const url = document.getElementById('url-input').value.trim();
        if (!url) return;

        const mode = document.getElementById('mode-select').value;
        const quality = document.getElementById('quality-select').value;
        const priority = document.getElementById('priority-select').value;

        if (mode === 'transcript') {{
            window.open(window.location.origin + '/transcripts?url=' + encodeURIComponent(url), '_blank');
            document.getElementById('url-input').value = '';
            toast('Transcript page opened', 'info');
            return;
        }}

        try {{
            if (isPlaylistUrl(url)) {{
                const data = await api('POST', '/downloads/playlist', {{
                    url, mode, quality, concurrency: 3, start: false
                }});
                const jobList = data.jobs || [];
                const n = jobList.length;
                if (n > 0) {{
                    jobList.forEach(j => {{
                        jobs[j.job_id] = j;
                        subscribeJob(j.job_id);
                    }});
                    renderJobs();
                    document.getElementById('url-input').value = '';
                    toast('Added to queue: ' + n + ' videos', 'success');
                }} else {{
                    toast(data.error || 'No videos in playlist', 'error');
                }}
                }} else {{
                    const data = await api('POST', '/downloads', {{
                    url, mode, quality, priority, start: false
                }});
                if (data.success && data.job) {{
                    jobs[data.job.job_id] = data.job;
                    subscribeJob(data.job.job_id);
                    renderJobs();
                    document.getElementById('url-input').value = '';
                    toast('Added to queue: ' + (data.job.title || url.slice(0, 50)), 'success');
                }} else {{
                    toast(data.error || 'Failed to queue', 'error');
                }}
            }}
        }} catch(e) {{
            toast('Error: ' + e.message, 'error');
        }}
    }}

    async function startJob(jobId) {{
        try {{
            await api('POST', '/downloads/' + jobId + '/start');
            toast('Download started', 'success');
            refreshJobs();
            refreshStats();
        }} catch (e) {{
            toast(e.message || 'Failed to start', 'error');
        }}
    }}

    async function startAllDownloads() {{
        try {{
            const data = await api('POST', '/queue/start-all');
            const n = data.started ?? 0;
            if (n > 0) toast('Started ' + n + ' download(s)', 'success');
            else toast('No deferred downloads to start', 'info');
            refreshJobs();
            refreshStats();
        }} catch (e) {{
            toast(e.message || 'Failed to start all', 'error');
        }}
    }}

    async function pauseJob(jobId) {{
        try {{
            await api('POST', '/downloads/' + jobId + '/pause');
            toast('Download paused', 'info');
            refreshJobs();
            refreshStats();
        }} catch (e) {{
            toast(e.message || 'Failed to pause', 'error');
        }}
    }}

    async function cancelJob(jobId) {{
        const job = jobs[jobId];
        if (job && job.status === 'cancelled') return;
        if (job) {{ job.status = 'cancelled'; renderJobs(); }}
        try {{
            await api('POST', '/downloads/' + jobId + '/cancel');
            toast('Cancelled', 'info');
            refreshJobs();
        }} catch (e) {{
            toast(e.message || 'Failed to cancel', 'error');
            refreshJobs();
        }}
    }}

    async function resumeJob(jobId) {{
        await api('POST', '/downloads/' + jobId + '/resume');
        toast('Download resumed', 'success');
        refreshJobs();
        refreshStats();
    }}

    async function moveJobUp(jobId) {{
        try {{
            await api('POST', '/downloads/' + jobId + '/move-up');
            toast('Moved up', 'success');
            refreshJobs();
        }} catch (e) {{
            toast(e.message || 'Cannot move up', 'error');
        }}
    }}

    async function moveJobDown(jobId) {{
        try {{
            await api('POST', '/downloads/' + jobId + '/move-down');
            toast('Moved down', 'success');
            refreshJobs();
        }} catch (e) {{
            toast(e.message || 'Cannot move down', 'error');
        }}
    }}

    async function pauseQueue() {{
        await api('POST', '/queue/pause');
        toast('Queue paused', 'info');
        updatePauseResumeButtons(true);
    }}

    async function resumeQueue() {{
        await api('POST', '/queue/resume');
        toast('Queue resumed', 'success');
        updatePauseResumeButtons(false);
    }}

    window.addEventListener('pywebviewready', function() {{ window._pywebviewReady = true; }});

    async function browseClipSource() {{
        function getApi() {{
            try {{
                if (typeof pywebview !== 'undefined' && pywebview.api) {{
                    if (typeof pywebview.api.select_video_file === 'function')
                        return pywebview.api.select_video_file;
                    if (typeof pywebview.api.selectVideoFile === 'function')
                        return pywebview.api.selectVideoFile;
                }}
            }} catch(e) {{}}
            return null;
        }}
        let fn = getApi();
        if (!fn && typeof pywebview !== 'undefined') {{
            await new Promise(function(resolve) {{
                if (window._pywebviewReady) return resolve();
                window.addEventListener('pywebviewready', resolve, {{ once: true }});
                setTimeout(resolve, 800);
            }});
            fn = getApi();
        }}
        if (fn) {{
            try {{
                const path = await (typeof fn === 'function' ? fn() : fn);
                if (path) {{
                    document.getElementById('clip-source').value = path;
                    toast('Video selected', 'success');
                }}
            }} catch(e) {{
                toast('Could not open file dialog: ' + (e.message || 'unknown'), 'error');
            }}
        }} else {{
            toast('Paste the full path to your video, or use the desktop app to browse.', 'info');
        }}
    }}

    let clipTimerId = null;
    let clipStartTime = 0;
    function setClipProgress(visible, label) {{
        const card = document.getElementById('clip-job-card');
        const cardExtract = document.getElementById('clip-extract-progress-card');
        const meta = document.getElementById('clip-job-meta');
        const metaExtract = document.getElementById('clip-extract-job-meta');
        const textEl = document.getElementById('clip-progress-text');
        const textElExtract = document.getElementById('clip-extract-progress-text');
        const btn = document.getElementById('btn-extract-clip');
        if (card) card.classList.toggle('visible', !!visible);
        if (cardExtract) cardExtract.classList.toggle('visible', !!visible);
        if (btn) btn.disabled = !!visible;
        if (visible) {{
            if (meta) meta.textContent = label || 'Preparing…';
            if (metaExtract) metaExtract.textContent = label || 'Preparing…';
            clipStartTime = Date.now();
            function tick() {{
                const sec = Math.floor((Date.now() - clipStartTime) / 1000);
                const m = Math.floor(sec / 60);
                const s = sec % 60;
                const t = m + ':' + (s < 10 ? '0' : '') + s;
                if (textEl) textEl.textContent = t;
                if (textElExtract) textElExtract.textContent = t;
            }}
            tick();
            clipTimerId = setInterval(tick, 1000);
        }} else {{
            if (clipTimerId) {{ clearInterval(clipTimerId); clipTimerId = null; }}
            if (textEl) textEl.textContent = '0:00';
            if (textElExtract) textElExtract.textContent = '0:00';
        }}
    }}

    async function extractClip() {{
        const source = document.getElementById('clip-source').value.trim();
        const start = document.getElementById('clip-start').value.trim();
        const end = document.getElementById('clip-end').value.trim();
        const mode = document.getElementById('clip-mode').value;
        const format = document.getElementById('clip-format').value;

        if (!source || !start || !end) {{
            toast('Please fill all clip fields (source, start time, end time)', 'error');
            return;
        }}

        const label = source.length > 50 ? source.slice(0, 47) + '…' : source;
        setClipProgress(true, label);
        toast('Extracting clip...', 'info');
        document.getElementById('clip-result').innerHTML = '';

        try {{
            const data = await api('POST', '/clips/extract', {{
                source,
                start_time: start,
                end_time: end,
                mode,
                output_format: format
            }});
            if (data.success) {{
                toast('Clip extracted: ' + data.file_size_human + ', ' +
                    data.duration_seconds.toFixed(1) + 's', 'success');
                document.getElementById('clip-result').innerHTML =
                    '<div style="margin-top:8px;padding:12px;' +
                    'background:#1a3a2a;border-radius:8px;' +
                    'border:1px solid #4ade80;">' +
                    '<div>✅ Clip saved: ' + data.output_path + '</div>' +
                    '<div>Duration: ' + data.duration_seconds.toFixed(1) + 's' +
                    ' | Size: ' + data.file_size_human + '</div></div>';
            }} else {{
                toast(data.error || data.detail || 'Extraction failed', 'error');
            }}
        }} catch(e) {{
            toast('Error: ' + e.message, 'error');
        }} finally {{
            setClipProgress(false);
        }}
    }}

    async function browseMergeClip(btn) {{
        var row = btn.closest('.merge-clip-row');
        var input = row ? row.querySelector('.merge-clip-input') : null;
        if (!input) return;
        function getApi() {{
            try {{
                if (typeof pywebview !== 'undefined' && pywebview.api) {{
                    if (typeof pywebview.api.select_video_file === 'function')
                        return pywebview.api.select_video_file;
                    if (typeof pywebview.api.selectVideoFile === 'function')
                        return pywebview.api.selectVideoFile;
                }}
            }} catch(e) {{}}
            return null;
        }}
        var fn = getApi();
        if (!fn && typeof pywebview !== 'undefined') {{
            await new Promise(function(resolve) {{
                if (window._pywebviewReady) return resolve();
                window.addEventListener('pywebviewready', resolve, {{ once: true }});
                setTimeout(resolve, 800);
            }});
            fn = getApi();
        }}
        if (fn) {{
            try {{
                var path = await (typeof fn === 'function' ? fn() : fn);
                if (path) {{ input.value = path; toast('Clip selected', 'success'); }}
            }} catch(e) {{
                toast('Could not open file dialog: ' + (e.message || 'unknown'), 'error');
            }}
        }} else {{
            toast('Paste the full path, or use the desktop app to browse.', 'info');
        }}
    }}

    function addMergeClipInput() {{
        const list = document.getElementById('merge-clips-list');
        const count = list.querySelectorAll('.merge-clip-input').length;
        if (count >= 10) {{
            toast('Maximum 10 clips', 'error');
            return;
        }}
        const div = document.createElement('div');
        div.className = 'merge-clip-row';
        div.style.cssText = 'display:flex;gap:8px;margin-bottom:4px;align-items:center;';
        div.innerHTML = '<input type="text" class="merge-clip-input input-style" ' +
            'placeholder="Clip ' + (count + 1) + ' file path" style="flex:1;">' +
            '<button type="button" class="btn btn-outline btn-sm" onclick="browseMergeClip(this)" title="Select video">📁 Browse</button>' +
            '<button class="btn btn-outline btn-sm" onclick="this.parentElement.remove()" style="padding:6px 10px;">✕</button>';
        list.appendChild(div);
    }}

    async function mergeClips() {{
        const inputs = document.querySelectorAll('.merge-clip-input');
        const clips = Array.from(inputs)
            .map(i => i.value.trim())
            .filter(v => v.length > 0);

        if (clips.length < 2) {{
            toast('At least 2 clips required', 'error');
            document.getElementById('merge-result').innerHTML =
                '<div style="margin-top:8px;padding:12px;background:#3a1a1a;border-radius:8px;border:1px solid #f87171;color:#f87171;">' +
                'At least 2 clips required. Add file paths or use Browse to select videos.</div>';
            return;
        }}

        var isUrl = clips.some(function(c) {{ return c.indexOf('http://') === 0 || c.indexOf('https://') === 0; }});
        if (isUrl) {{
            toast('Use local file paths, not URLs', 'error');
            document.getElementById('merge-result').innerHTML =
                '<div style="margin-top:8px;padding:12px;background:#3a1a1a;border-radius:8px;border:1px solid #f87171;color:#f87171;">' +
                '<strong>Merge needs local files, not URLs.</strong><br>Download the videos in the Download tab first, then use the Browse button here to select the saved files.</div>';
            return;
        }}

        const mode = document.getElementById('merge-mode').value;
        const format = document.getElementById('merge-format').value;
        document.getElementById('merge-result').innerHTML = '';

        toast('Merging ' + clips.length + ' clips...', 'info');

        try {{
            const r = await fetch(API + '/clips/merge', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{ clips, mode, output_format: format }}),
            }});
            const data = await r.json();
            if (r.ok && data.success) {{
                toast('Merged ' + data.clip_count + ' clips: ' +
                    data.file_size_human + ', ' +
                    data.total_duration_seconds.toFixed(1) + 's', 'success');
                document.getElementById('merge-result').innerHTML =
                    '<div style="margin-top:8px;padding:12px;' +
                    'background:#1a3a2a;border-radius:8px;' +
                    'border:1px solid #4ade80;">' +
                    '<div>✅ Merged: ' + data.output_path + '</div>' +
                    '<div>' + data.clip_count + ' clips | ' +
                    'Duration: ' + data.total_duration_seconds.toFixed(1) + 's | ' +
                    'Size: ' + data.file_size_human + '</div></div>';
            }} else {{
                var msg = data.detail || data.error || 'Merge failed';
                if (Array.isArray(data.detail)) msg = (data.detail[0] && data.detail[0].msg) || msg;
                toast(msg, 'error');
                document.getElementById('merge-result').innerHTML =
                    '<div style="margin-top:8px;padding:12px;background:#3a1a1a;border-radius:8px;border:1px solid #f87171;color:#f87171;">' +
                    '<strong>Could not merge</strong><br>' + (typeof msg === 'string' ? msg : JSON.stringify(msg)) + '</div>';
            }}
        }} catch(e) {{
            toast('Error: ' + e.message, 'error');
            document.getElementById('merge-result').innerHTML =
                '<div style="margin-top:8px;padding:12px;background:#3a1a1a;border-radius:8px;border:1px solid #f87171;color:#f87171;">' +
                'Network error: ' + e.message + '</div>';
        }}
    }}

    function updatePauseResumeButtons(isPaused) {{
        const pauseBtn = document.getElementById('btn-pause-queue');
        const resumeBtn = document.getElementById('btn-resume-queue');
        if (pauseBtn) pauseBtn.disabled = !!isPaused;
        if (resumeBtn) resumeBtn.disabled = !isPaused;
    }}

    function connectWS() {{
        try {{
            ws = new WebSocket(WS_URL);
            ws.onopen = () => {{
                console.log('WS connected');
                ws.send(JSON.stringify({{type: 'subscribe_all'}}));
                const badge = document.getElementById('status-badge');
                badge.className = 'status-badge status-online';
                badge.textContent = '● Online';
            }};
            ws.onmessage = (e) => {{
                const msg = JSON.parse(e.data);
                if (msg.type === 'progress' || msg.type === 'status_change') {{
                    updateJobProgress(msg);
                }}
            }};
            ws.onclose = () => {{
                const badge = document.getElementById('status-badge');
                badge.className = 'status-badge status-offline';
                badge.textContent = '● Offline';
                setTimeout(connectWS, 3000);
            }};
            ws.onerror = () => ws.close();
        }} catch(e) {{
            setTimeout(connectWS, 3000);
        }}
    }}

    function subscribeJob(jobId) {{
        if (ws && ws.readyState === 1) {{
            ws.send(JSON.stringify({{
                type: 'subscribe', job_ids: [jobId]
            }}));
        }}
    }}

    function updateJobProgress(msg) {{
        const job = jobs[msg.job_id] || {{}};
        job.job_id = msg.job_id;
        if (msg.data) {{
            job.status = msg.data.status || msg.status || job.status;
            job.progress_percent = msg.data.percent || 0;
            job.speed_human = msg.data.speed_human;
            job.eta_human = msg.data.eta_human;
        }}
        if (msg.status) job.status = msg.status;
        jobs[msg.job_id] = job;
        renderJobs();
    }}

    function renderJobs() {{
        const list = document.getElementById('job-list');
        const entries = Object.values(jobs);

        if (entries.length === 0) {{
            list.innerHTML = '<div class="empty-state">' +
                '<div class="icon">📥</div>' +
                '<div>No downloads yet.</div></div>';
            return;
        }}

        list.innerHTML = entries.map(j => {{
            const pct = (j.progress_percent || 0).toFixed(1);
            const statusClass = 'status-' + (j.status || 'queued');
            const title = j.title || j.url || 'Unknown';
            const speed = j.speed_human || '';
            const eta = j.eta_human || '';
            const meta = [j.media_type, j.quality, speed, eta]
                .filter(Boolean).join(' • ');

            let actions = '';
            if (j.status === 'cancelled' || j.status === 'completed') {{
                actions = '';
            }} else if (j.status === 'deferred') {{
                actions = '<button class="btn btn-primary btn-sm"' +
                    ' onclick="startJob(\\''+j.job_id+'\\')">Download</button> ' +
                    '<button class="btn btn-outline btn-sm"' +
                    ' onclick="pauseJob(\\''+j.job_id+'\\')">Pause</button> ' +
                    '<button class="btn btn-danger btn-sm"' +
                    ' onclick="cancelJob(\\''+j.job_id+'\\')">Cancel</button>';
            }} else if (['queued','downloading'].includes(j.status)) {{
                const moveBtns = j.status === 'queued' ?
                    '<span class="move-btns">' +
                    '<button class="btn btn-outline btn-sm btn-icon" title="Move up" onclick="moveJobUp(\\''+j.job_id+'\\')">↑</button>' +
                    '<button class="btn btn-outline btn-sm btn-icon" title="Move down" onclick="moveJobDown(\\''+j.job_id+'\\')">↓</button>' +
                    '</span> ' : '';
                actions = moveBtns +
                    '<button class="btn btn-outline btn-sm"' +
                    ' onclick="pauseJob(\\''+j.job_id+'\\')">Pause</button> ' +
                    '<button class="btn btn-danger btn-sm"' +
                    ' onclick="cancelJob(\\''+j.job_id+'\\')">Cancel</button>';
            }} else if (['paused','interrupted','failed'].includes(j.status)) {{
                actions = '<button class="btn btn-outline btn-sm"' +
                    ' onclick="resumeJob(\\''+j.job_id+'\\')">Resume</button> ' +
                    '<button class="btn btn-danger btn-sm"' +
                    ' onclick="cancelJob(\\''+j.job_id+'\\')">Cancel</button>';
            }}

            return '<div class="job-card">' +
                '<div class="job-info">' +
                    '<div class="job-title">' + title + '</div>' +
                    '<div class="job-meta">' + meta + '</div>' +
                '</div>' +
                '<div class="job-progress">' +
                    '<div class="progress-bar"><div class="progress-fill" ' +
                        'style="width:'+pct+'%"></div></div>' +
                    '<div class="progress-text">' + pct + '%</div>' +
                '</div>' +
                '<span class="job-status '+statusClass+'">' +
                    (j.status||'queued') + '</span>' +
                actions +
                '</div>';
        }}).join('');
    }}

    async function refreshStats() {{
        try {{
            const stats = await api('GET', '/queue/stats');
            document.getElementById('stat-active').textContent = stats.active || 0;
            const q = stats.queue || {{}};
            document.getElementById('stat-queued').textContent = stats.queue_total ?? q.total ?? 0;

            const byStatus = stats.jobs_by_status || {{}};
            document.getElementById('stat-completed').textContent =
                byStatus.completed || 0;
            document.getElementById('stat-failed').textContent =
                byStatus.failed || 0;

            updatePauseResumeButtons(stats.is_paused);

            const mcInput = document.getElementById('input-max-concurrent');
            if (mcInput && stats.max_concurrent !== undefined && document.activeElement !== mcInput) {{
                const v = Number(stats.max_concurrent);
                if (v >= 1 && v <= 10) mcInput.value = String(v);
            }}
        }} catch(e) {{}}

        try {{
            const disk = await api('GET', '/health/disk');
            document.getElementById('stat-disk').textContent =
                disk.free_gb ? disk.free_gb.toFixed(1) : '--';
        }} catch(e) {{}}
    }}

    async function refreshJobs() {{
        try {{
            const data = await api('GET', '/downloads?page_size=50');
            if (data.jobs && Array.isArray(data.jobs)) {{
                data.jobs.forEach(j => {{ jobs[j.job_id] = j; }});
                renderJobs();
            }}
        }} catch(e) {{}}
    }}

    function toast(msg, type) {{
        const container = document.getElementById('toasts');
        const el = document.createElement('div');
        el.className = 'toast toast-' + (type || 'info');
        el.textContent = msg;
        container.appendChild(el);
        setTimeout(() => el.remove(), 4000);
    }}

    document.getElementById('url-input')
        .addEventListener('keydown', (e) => {{
            if (e.key === 'Enter') submitDownload();
        }});

    async function applyMaxConcurrent() {{
        const el = document.getElementById('input-max-concurrent');
        if (!el) return;
        let v = parseInt(el.value, 10);
        if (isNaN(v) || v < 1) v = 1;
        if (v > 10) v = 10;
        el.value = String(v);
        try {{
            await api('PUT', '/config', {{ max_concurrent_downloads: v }});
            toast('Simultaneous downloads set to ' + v, 'success');
        }} catch (e) {{
            toast('Failed to update: ' + (e.message || 'error'), 'error');
        }}
    }}
    document.getElementById('input-max-concurrent')?.addEventListener('change', applyMaxConcurrent);

    connectWS();
    refreshStats();
    refreshJobs();
    setInterval(refreshStats, 10000);
    setInterval(refreshJobs, 15000);
    </script>
</body>
</html>"""


def get_transcripts_html(api_base: str) -> str:
    """Generate the transcripts page HTML (URL + language + download).

    Served at GET /transcripts?url=... so the dashboard Transcript option
    can open this page with the URL pre-filled.
    """
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Transcripts – GrabItDown</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
            background: #0f0f10;
            color: #e0e0e0;
            min-height: 100vh;
        }}
        .header {{
            background: linear-gradient(135deg, #1a1a2e, #16213e);
            padding: 16px 24px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            border-bottom: 1px solid #2a2a3e;
        }}
        .header h1 {{
            font-size: 20px;
            background: linear-gradient(90deg, #00d4ff, #7b2ff7);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }}
        .container {{ max-width: 640px; margin: 0 auto; padding: 24px; }}
        .back-link {{
            display: inline-block;
            margin-bottom: 24px;
            color: #00d4ff;
            text-decoration: none;
            font-size: 14px;
        }}
        .back-link:hover {{ text-decoration: underline; }}
        .section {{
            background: #1a1a2e;
            border: 1px solid #2a2a3e;
            border-radius: 12px;
            padding: 24px;
            margin-bottom: 16px;
        }}
        .section-title {{ font-size: 14px; font-weight: 600; margin-bottom: 12px; color: #a0a0a0; }}
        input, select {{
            width: 100%;
            padding: 12px 16px;
            border: 1px solid #2a2a3e;
            border-radius: 8px;
            background: #0f0f10;
            color: #e0e0e0;
            font-size: 14px;
            margin-bottom: 12px;
        }}
        input:focus, select:focus {{ outline: none; border-color: #00d4ff; }}
        .btn {{
            padding: 12px 24px;
            border: none;
            border-radius: 8px;
            cursor: pointer;
            font-size: 14px;
            font-weight: 600;
            margin-right: 8px;
            margin-top: 8px;
        }}
        .btn-primary {{
            background: linear-gradient(135deg, #00d4ff, #7b2ff7);
            color: white;
        }}
        .btn-primary:hover {{ opacity: 0.9; }}
        .btn-outline {{
            background: transparent;
            border: 1px solid #2a2a3e;
            color: #e0e0e0;
        }}
        .toast-container {{ position: fixed; bottom: 24px; right: 24px; z-index: 1000; }}
        .toast {{ padding: 12px 20px; border-radius: 8px; margin-top: 8px; font-size: 14px; }}
        .toast-info {{ background: #1a2a3e; color: #00d4ff; }}
        .toast-success {{ background: #1a3a2a; color: #4ade80; }}
        .toast-error {{ background: #3a1a1a; color: #f87171; }}
        .extract-progress {{ margin-top: 16px; display: none; }}
        .extract-progress.visible {{ display: block; }}
        .extract-progress .progress-bar {{
            height: 8px; border-radius: 4px; background: #2a2a3e;
            overflow: hidden; margin-bottom: 8px;
        }}
        .extract-progress .progress-fill {{
            height: 100%; background: linear-gradient(90deg, #00d4ff, #7b2ff7);
            animation: extract-indeterminate 1.5s ease-in-out infinite;
        }}
        @keyframes extract-indeterminate {{
            0% {{ transform: translateX(-100%); width: 40%; }}
            50% {{ transform: translateX(150%); width: 40%; }}
            100% {{ transform: translateX(-100%); width: 40%; }}
        }}
        .extract-progress .progress-text {{ font-size: 13px; color: #a0a0a0; }}
    </style>
</head>
<body>
    <div class="header">
        <h1>GrabItDown – Transcripts</h1>
    </div>
    <div class="container">
        <a href="{api_base.rstrip('/')}/dashboard" class="back-link">← Back to Dashboard</a>
        <div class="section">
            <div class="section-title">Video URL</div>
            <input type="text" id="url-input" placeholder="https://www.youtube.com/watch?v=...">
        </div>
        <div class="section">
            <div class="section-title">Language</div>
            <select id="language-select">
                <option value="en">English</option>
                <option value="hi">Hindi</option>
                <option value="ur">Urdu</option>
                <option value="es">Spanish</option>
                <option value="fr">French</option>
                <option value="de">German</option>
            </select>
            <p style="font-size:12px;color:#888;margin-top:6px;">Click “Detect languages”, then pick Urdu/Hindi or English.</p>
            <button type="button" class="btn btn-outline" id="detect-btn">Detect languages</button>
        </div>
        <div class="section" id="script-section" style="display:none;">
            <div class="section-title">Hindi/Urdu script</div>
            <select id="script-select">
                <option value="default">Default (try both)</option>
                <option value="roman">Roman / Latin (e.g. Roman Hindi, Roman Urdu)</option>
                <option value="native">Native script (Devanagari for Hindi, Urdu script)</option>
            </select>
        </div>
        <div class="section">
            <button type="button" class="btn btn-primary" id="download-btn">Download transcript</button>
            <div class="extract-progress" id="extract-progress">
                <div class="progress-bar"><div class="progress-fill"></div></div>
                <div class="progress-text" id="extract-progress-text">Extracting transcript…</div>
            </div>
            <div id="rate-limit-countdown" style="display:none; margin-top:12px; padding:12px; background:#2a1a1a; border:1px solid #4a2a2a; border-radius:8px; font-size:14px; color:#f87171;">
                <strong>Rate limited.</strong> Try again in <span id="rate-limit-timer">3:00</span>.
            </div>
        </div>
        <div class="section" id="result-section" style="display:none;">
            <div class="section-title">Transcript</div>
            <textarea id="result-content" readonly rows="12" style="width:100%;resize:vertical;margin-bottom:8px;"></textarea>
            <button type="button" class="btn btn-outline" id="copy-btn">Copy</button>
            <button type="button" class="btn btn-outline" id="save-file-btn">Save as file</button>
        </div>
    </div>
    <div class="toast-container" id="toasts"></div>
    <script>
    const API = '{api_base}/api/v1';
    const urlInput = document.getElementById('url-input');
    const langSelect = document.getElementById('language-select');
    const detectBtn = document.getElementById('detect-btn');
    const downloadBtn = document.getElementById('download-btn');
    const resultSection = document.getElementById('result-section');
    const resultContent = document.getElementById('result-content');
    const copyBtn = document.getElementById('copy-btn');
    const saveFileBtn = document.getElementById('save-file-btn');
    const extractProgress = document.getElementById('extract-progress');
    const extractProgressText = document.getElementById('extract-progress-text');
    const scriptSection = document.getElementById('script-section');
    const scriptSelect = document.getElementById('script-select');
    const rateLimitCountdownEl = document.getElementById('rate-limit-countdown');
    const rateLimitTimerEl = document.getElementById('rate-limit-timer');
    var rateLimitCooldownActive = false;
    var rateLimitIntervalId = null;
    var extractingInProgress = false;

    function startRateLimitCountdown(seconds) {{
        rateLimitCooldownActive = true;
        downloadBtn.disabled = true;
        if (rateLimitIntervalId) clearInterval(rateLimitIntervalId);
        rateLimitCountdownEl.style.display = 'block';
        var remaining = seconds;
        function fmt(t) {{ var m = Math.floor(t / 60); var s = t % 60; return m + ':' + (s < 10 ? '0' : '') + s; }}
        rateLimitTimerEl.textContent = fmt(remaining);
        rateLimitIntervalId = setInterval(function() {{
            remaining--;
            rateLimitTimerEl.textContent = fmt(remaining);
            if (remaining <= 0) {{
                clearInterval(rateLimitIntervalId);
                rateLimitIntervalId = null;
                rateLimitCooldownActive = false;
                extractingInProgress = false;
                downloadBtn.disabled = false;
                rateLimitCountdownEl.style.display = 'none';
            }}
        }}, 1000);
    }}

    function updateScriptVisibility() {{
        var lang = langSelect.value;
        scriptSection.style.display = (lang === 'hi' || lang === 'ur' || lang === 'hi-IN' || lang === 'ur-PK') ? 'block' : 'none';
    }}
    langSelect.addEventListener('change', updateScriptVisibility);

    (function initFromQuery() {{
        const params = new URLSearchParams(window.location.search);
        const u = params.get('url');
        if (u) urlInput.value = decodeURIComponent(u);
        updateScriptVisibility();
    }})();

    function toast(msg, type) {{
        const container = document.getElementById('toasts');
        const el = document.createElement('div');
        el.className = 'toast toast-' + (type || 'info');
        el.textContent = msg;
        container.appendChild(el);
        setTimeout(function() {{ el.remove(); }}, 4000);
    }}

    detectBtn.addEventListener('click', async function() {{
        const url = urlInput.value.trim();
        if (!url) {{ toast('Enter a video URL first', 'error'); return; }}
        const origText = detectBtn.textContent;
        detectBtn.disabled = true;
        detectBtn.textContent = 'Detecting…';
        try {{
            const r = await fetch(API + '/transcripts/languages?url=' + encodeURIComponent(url));
            let data;
            try {{ data = await r.json(); }} catch (_) {{ data = {{}}; }}
            if (!r.ok) {{
                toast(data.detail || 'Request failed (' + r.status + ')', 'error');
                return;
            }}
            var langs = data.languages && typeof data.languages === 'object' ? data.languages : null;
            if (langs && Object.keys(langs).length > 0) {{
                var preferred = ['hi','ur','en','hi-IN','ur-PK','en-US','en-GB','ar','es','pt','fr','de'];
                var codes = Object.keys(langs);
                var ordered = preferred.filter(function(c) {{ return codes.indexOf(c) >= 0; }});
                codes.forEach(function(c) {{ if (ordered.indexOf(c) < 0) ordered.push(c); }});
                ordered.sort(function(a, b) {{
                    var aPrefer = preferred.indexOf(a); var bPrefer = preferred.indexOf(b);
                    if (aPrefer >= 0 && bPrefer >= 0) return aPrefer - bPrefer;
                    if (aPrefer >= 0) return -1; if (bPrefer >= 0) return 1;
                    return a.localeCompare(b);
                }});
                var names = {{ 'en':'English','hi':'Hindi','ur':'Urdu','ar':'Arabic','es':'Spanish','pt':'Portuguese','fr':'French','de':'German','hi-IN':'Hindi (India)','ur-PK':'Urdu (Pakistan)' }};
                langSelect.innerHTML = '';
                ordered.forEach(function(code) {{
                    const opt = document.createElement('option');
                    opt.value = code;
                    var label = (names[code] || code);
                    var formats = Array.isArray(langs[code]) ? langs[code] : [];
                    opt.textContent = label + (formats.indexOf('vtt') >= 0 ? ' (vtt)' : formats[0] ? ' (' + formats[0] + ')' : '');
                    langSelect.appendChild(opt);
                }});
                toast('Languages detected: ' + ordered.length, 'success');
                updateScriptVisibility();
            }} else {{
                toast('No subtitle languages found for this URL', 'error');
            }}
        }} catch (e) {{
            toast('Could not detect languages: ' + (e.message || 'Network error'), 'error');
        }} finally {{
            detectBtn.disabled = false;
            detectBtn.textContent = origText;
        }}
    }});

    var lastExtract = {{ content: '', format: 'srt' }};
    downloadBtn.addEventListener('click', async function() {{
        if (extractingInProgress) return;
        const url = urlInput.value.trim();
        if (!url) {{ toast('Enter a video URL first', 'error'); return; }}
        extractingInProgress = true;
        downloadBtn.disabled = true;
        const lang = langSelect.value;
        var scriptVal = 'default';
        if (scriptSection.style.display !== 'none' && scriptSelect) scriptVal = scriptSelect.value || 'default';
        resultSection.style.display = 'none';
        extractProgressText.textContent = 'Extracting transcript…';
        extractProgress.classList.add('visible');
        try {{
            const r = await fetch(API + '/transcripts/extract', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{ url: url, language: lang, format: 'srt', script: scriptVal }})
            }});
            const data = await r.json();
            if (!r.ok) {{
                var msg = data.detail || 'Extract failed';
                if (r.status === 429) {{
                    toast(msg, 'error');
                    startRateLimitCountdown(180);
                    return;
                }}
                toast(msg, 'error');
                return;
            }}
            lastExtract = {{ content: data.content || '', format: data.format || 'srt' }};
            resultContent.value = lastExtract.content;
            resultSection.style.display = 'block';
            toast('Transcript ready', 'success');
        }} catch (e) {{
            toast('Could not download transcript: ' + e.message, 'error');
        }} finally {{
            extractProgress.classList.remove('visible');
            if (!rateLimitCooldownActive) {{
                extractingInProgress = false;
                downloadBtn.disabled = false;
            }}
        }}
    }});

    copyBtn.addEventListener('click', function() {{
        resultContent.select();
        document.execCommand('copy');
        toast('Copied to clipboard', 'success');
    }});
    saveFileBtn.addEventListener('click', function() {{
        if (!lastExtract.content) return;
        const a = document.createElement('a');
        a.href = 'data:text/plain;charset=utf-8,' + encodeURIComponent(lastExtract.content);
        a.download = 'transcript.' + lastExtract.format;
        a.click();
        toast('Saved', 'success');
    }});
    </script>
</body>
</html>"""

