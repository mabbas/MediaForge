"""YouTube caption / transcript extraction via yt-dlp.

Mechanism: yt-dlp ``--write-subs`` / ``--write-auto-subs`` (not youtube-transcript-api).
Whisper fallback is configured elsewhere but not used here.

Datacenter / cloud hosting note
--------------------------------
YouTube increasingly returns HTTP 403 / "Sign in to confirm you're not a bot" for
subtitle and metadata requests from datacenter IPs (Render, AWS, GCP, etc.).
When that happens locally the code raises ``TranscriptBotDetectionError``.
Mitigations (document only — wire via env/config):
  - ``GID_YTDLP_COOKIES_FILE`` or ``providers.youtube.cookies_file`` in config
  - PO-token / proxy support as described in yt-dlp docs
"""

from __future__ import annotations

import logging
import re
import tempfile
import time
from pathlib import Path

from src.exceptions import (
    TranscriptBotDetectionError,
    TranscriptError,
    TranscriptNotAvailableError,
    TranscriptRateLimitError,
)
from src.transcript.romanize import is_indic_roman_language, romanize_subtitle_content

logger = logging.getLogger(__name__)

_BOT_MARKERS = (
    "sign in to confirm",
    "not a bot",
    "http error 403",
    "403 forbidden",
    "unable to download video subtitles",
    "blocked",
)

_RATE_LIMIT_MARKERS = ("429", "too many requests")

# Avoid hammering YouTube after a 429 (video_id -> unix timestamp).
_rate_limit_until: dict[str, float] = {}
_RATE_LIMIT_COOLDOWN_SECONDS = 180

# Cache successful extractions (cache_key -> (expires_at, content, fmt)).
_transcript_cache: dict[str, tuple[float, str, str]] = {}
_CACHE_TTL_SECONDS = 1800


def is_rate_limit_error(message: str) -> bool:
    """Return True if *message* indicates YouTube HTTP 429 rate limiting."""
    lower = message.lower()
    return any(marker in lower for marker in _RATE_LIMIT_MARKERS)


def _cache_key(
    video_id: str,
    language: str,
    prefer_script: str | None,
    output_format: str,
) -> str:
    script = (prefer_script or "default").strip().lower()
    return f"{video_id}:{language}:{script}:{output_format.lower()}"


def _get_cached_transcript(key: str) -> tuple[str, str] | None:
    entry = _transcript_cache.get(key)
    if not entry:
        return None
    expires_at, content, fmt = entry
    if time.time() >= expires_at:
        _transcript_cache.pop(key, None)
        return None
    return content, fmt


class _YtdlpCaptureLogger:
    """yt-dlp logger hook — captures warning/error lines (including 429)."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def debug(self, msg: object) -> None:
        pass

    def info(self, msg: object) -> None:
        pass

    def warning(self, msg: object) -> None:
        self.messages.append(str(msg))

    def error(self, msg: object) -> None:
        self.messages.append(str(msg))


def _raise_if_rate_limited(*messages: str) -> None:
    combined = " ".join(messages)
    if is_rate_limit_error(combined):
        raise TranscriptRateLimitError(
            "YouTube rate limit (429). Wait 3 minutes before trying again."
        )


def _download_subtitles(
    ydl_opts: dict,
    url: str,
) -> None:
    """Run yt-dlp subtitle download; raise on rate limit."""
    import yt_dlp

    capture_logger = _YtdlpCaptureLogger()
    opts = dict(ydl_opts)
    opts["logger"] = capture_logger
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
    except yt_dlp.utils.DownloadError as exc:
        _raise_if_rate_limited(str(exc), *capture_logger.messages)
        raise

    _raise_if_rate_limited(*capture_logger.messages)


def _store_cached_transcript(key: str, content: str, fmt: str) -> None:
    _transcript_cache[key] = (
        time.time() + _CACHE_TTL_SECONDS,
        content,
        fmt,
    )


def _check_rate_limit_cooldown(video_id: str) -> None:
    until = _rate_limit_until.get(video_id, 0)
    if time.time() < until:
        wait = int(until - time.time())
        raise TranscriptRateLimitError(
            f"YouTube rate limit active for this video. Try again in {wait // 60 + 1} minute(s)."
        )


def _mark_rate_limited(video_id: str) -> None:
    _rate_limit_until[video_id] = time.time() + _RATE_LIMIT_COOLDOWN_SECONDS


_SUBTITLE_EXTS = frozenset({"srt", "vtt", "ass", "ttml", "json3", "srv1", "srv2", "srv3"})


def _base_youtube_ydl_opts(cookies_file: str | None = None) -> dict:
    """Shared yt-dlp options aligned with the YouTube download provider."""
    opts: dict = {
        "quiet": True,
        "no_warnings": True,
        "geo_bypass": True,
        "extractor_args": {
            "youtube": {
                "player_client": ["android", "web", "tv_embedded"],
            }
        },
    }
    if cookies_file:
        opts["cookiefile"] = cookies_file
    cookie_env = __import__("os").environ.get("GID_YTDLP_COOKIES_FILE", "").strip()
    if cookie_env and not cookies_file:
        opts["cookiefile"] = cookie_env
    return opts


def _language_matches(code: str, lang: str) -> bool:
    if code == lang:
        return True
    if code.endswith(f"-{lang}"):
        return True
    if code.startswith(f"{lang}-"):
        return True
    if f"-{lang}-" in f"-{code}-":
        return True
    return False


def is_bot_detection_error(message: str) -> bool:
    """Return True if *message* looks like YouTube bot / IP blocking."""
    lower = message.lower()
    return any(marker in lower for marker in _BOT_MARKERS)


def resolve_caption_language(
    info: dict,
    language: str,
    prefer_script: str | None = None,
) -> tuple[str, bool]:
    """Pick the yt-dlp subtitle language key and whether it is auto-generated.

    prefer_script:
      - ``None`` / ``default``: manual track first, then auto
      - ``native``: manual / uploaded captions only
      - ``roman``: auto-generated captions only (often Latin script for Indic langs)

    Returns (yt_dlp_lang_code, is_automatic).
    Raises TranscriptNotAvailableError when no matching track exists.
    """
    manual: dict = dict(info.get("subtitles") or {})
    auto: dict = dict(info.get("automatic_captions") or {})
    script = (prefer_script or "default").strip().lower()

    # Exact key from language detection UI — use as-is when unambiguous.
    if script == "default":
        if language in manual and language not in auto:
            return language, False
        if language in auto and language not in manual:
            return language, True
        if language in manual:
            return language, False
        if language in auto:
            return language, True

    def _matches(code: str, lang: str) -> bool:
        return _language_matches(code, lang)

    def _best_in(pool: dict, lang: str) -> str | None:
        if lang in pool:
            return lang
        matches = [k for k in pool if _matches(k, lang)]
        if not matches:
            return None
        # Prefer exact code, then shortest variant (e.g. "en" over "ab-en")
        matches.sort(key=lambda k: (k != lang, len(k), k))
        return matches[0]

    if script == "native":
        key = _best_in(manual, language)
        if key:
            return key, False
        raise TranscriptNotAvailableError(
            f"No manually uploaded captions for language '{language}'. "
            f"Available manual: {', '.join(sorted(manual.keys())[:12]) or 'none'}"
        )

    if script == "roman":
        latn_keys = [
            k for k in auto
            if _language_matches(k, language) and "latn" in k.lower()
        ]
        if latn_keys:
            latn_keys.sort(key=lambda k: (k != f"{language}-Latn", len(k), k))
            return latn_keys[0], True
        key = _best_in(auto, language)
        if key:
            return key, True
        key = _best_in(manual, language)
        if key:
            return key, False
        raise TranscriptNotAvailableError(
            f"No auto-generated captions for language '{language}'. "
            f"Try script=native or another language."
        )

    # default — manual first, then auto
    key = _best_in(manual, language)
    if key:
        return key, False
    key = _best_in(auto, language)
    if key:
        return key, True

    sample = sorted(set(list(manual.keys()) + list(auto.keys())))[:12]
    raise TranscriptNotAvailableError(
        f"No transcript available for language '{language}'. "
        f"Available: {', '.join(sample) or 'none'}"
    )


def parse_srt_to_text(content: str) -> str:
    """Strip SRT timing/index lines, returning plain text."""
    lines: list[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.isdigit():
            continue
        if "-->" in stripped:
            continue
        lines.append(stripped)
    return "\n".join(lines)


def parse_vtt_to_text(content: str) -> str:
    """Strip WebVTT headers/timing, returning plain text."""
    lines: list[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("WEBVTT"):
            continue
        if "-->" in stripped:
            continue
        if stripped.startswith("NOTE"):
            continue
        lines.append(stripped)
    return "\n".join(lines)


def normalize_subtitle_content(content: str, source_ext: str, output_format: str) -> tuple[str, str]:
    """Convert downloaded subtitle content to the requested output format."""
    fmt = output_format.lower()
    ext = source_ext.lower().lstrip(".")

    if fmt in ("srt", "vtt"):
        return content, fmt

    if fmt == "txt":
        if ext == "vtt":
            return parse_vtt_to_text(content), "txt"
        if ext in ("srt", "ass"):
            return parse_srt_to_text(content), "txt"
        # best effort: drop timing-like lines
        cleaned = re.sub(r"^\d+\s*$", "", content, flags=re.MULTILINE)
        cleaned = re.sub(r"^\d{2}:\d{2}.*-->.*$", "", cleaned, flags=re.MULTILINE)
        return cleaned.strip(), "txt"

    if fmt == "json":
        return content, ext if ext.startswith("json") else "json3"

    return content, ext or fmt


def _ydl_subtitle_format(output_format: str) -> str:
    """Map API format to a yt-dlp subtitlesformat value."""
    fmt = output_format.lower()
    if fmt == "txt":
        return "vtt/best"
    if fmt == "json":
        return "json3/best"
    return f"{fmt}/vtt/best"


def _subtitle_language_candidates(
    info: dict,
    language: str,
    prefer_script: str | None,
) -> list[str]:
    """Build ordered yt-dlp subtitle language keys to attempt."""
    manual: dict = dict(info.get("subtitles") or {})
    auto: dict = dict(info.get("automatic_captions") or {})
    seen: set[str] = set()
    candidates: list[str] = []

    def add(code: str | None) -> None:
        if code and code not in seen:
            seen.add(code)
            candidates.append(code)

    add(language)
    try:
        resolved, _ = resolve_caption_language(info, language, prefer_script)
        add(resolved)
    except TranscriptNotAvailableError:
        pass

    for pool in (manual, auto):
        for key in pool:
            if _language_matches(key, language):
                add(key)

    if prefer_script == "roman":
        for key in auto:
            if "latn" in key.lower() and _language_matches(key, language):
                add(key)

    return candidates


def _build_ydl_opts(
    tmpdir: str,
    lang_code: str,
    output_format: str,
    cookies_file: str | None,
) -> dict:
    out_tmpl = str(Path(tmpdir) / "%(id)s")
    opts = _base_youtube_ydl_opts(cookies_file)
    opts.update(
        {
            "skip_download": True,
            # Request both manual and auto — avoids wrong manual/auto flag.
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": [lang_code],
            "subtitlesformat": _ydl_subtitle_format(output_format),
            "outtmpl": {"default": out_tmpl},
            "sleep_interval_requests": 5,
            "sleep_interval_subtitles": 12,
        }
    )
    return opts


def _find_subtitle_file(tmpdir: Path, video_id: str | None) -> Path | None:
    """Locate the subtitle file yt-dlp wrote in *tmpdir*."""
    if not tmpdir.is_dir():
        return None

    candidates: list[Path] = []
    for path in tmpdir.rglob("*"):
        if not path.is_file():
            continue
        ext = path.suffix.lstrip(".").lower()
        if ext in _SUBTITLE_EXTS:
            candidates.append(path)
        elif video_id and path.name.startswith(video_id):
            candidates.append(path)

    if not candidates:
        return None

    candidates.sort(
        key=lambda p: p.stat().st_mtime if p.is_file() else 0,
        reverse=True,
    )
    return candidates[0]


def extract_youtube_transcript(
    url: str,
    language: str = "en",
    output_format: str = "srt",
    prefer_script: str | None = None,
    cookies_file: str | None = None,
) -> tuple[str, str]:
    """Download YouTube captions and return (content, format).

    Raises TranscriptNotAvailableError, TranscriptBotDetectionError, or TranscriptError.
    """
    import yt_dlp

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            probe_opts = _base_youtube_ydl_opts(cookies_file)
            probe_opts["skip_download"] = True
            with yt_dlp.YoutubeDL(probe_opts) as ydl:
                info = ydl.extract_info(url, download=False)

            if not info:
                raise TranscriptError("Could not extract video info")

            video_id = info.get("id") or url
            _check_rate_limit_cooldown(str(video_id))

            cache_key = _cache_key(
                str(video_id), language, prefer_script, output_format
            )
            cached = _get_cached_transcript(cache_key)
            if cached:
                logger.info("Transcript cache hit: %s lang=%s", str(video_id)[:20], language)
                return cached

            lang_candidates = _subtitle_language_candidates(info, language, prefer_script)
            if not lang_candidates:
                raise TranscriptNotAvailableError(
                    f"No transcript available for language '{language}'."
                )

            sub_path = None
            last_lang: str | None = None
            saw_rate_limit = False
            for attempt, lang_code in enumerate(lang_candidates):
                attempt_dir = str(Path(tmpdir) / f"attempt_{attempt}")
                ydl_opts = _build_ydl_opts(
                    attempt_dir, lang_code, output_format, cookies_file
                )
                try:
                    _download_subtitles(ydl_opts, url)
                except TranscriptRateLimitError:
                    saw_rate_limit = True
                    _mark_rate_limited(str(video_id))
                    raise
                except Exception as exc:
                    if is_rate_limit_error(str(exc)):
                        saw_rate_limit = True
                        _mark_rate_limited(str(video_id))
                        raise TranscriptRateLimitError(
                            "YouTube rate limit (429). Wait 3 minutes before trying again."
                        ) from exc
                    logger.debug(
                        "Subtitle download failed for %s (%s): %s",
                        lang_code,
                        url[:60],
                        exc,
                    )
                    continue
                last_lang = lang_code
                sub_path = _find_subtitle_file(Path(attempt_dir), video_id)
                if sub_path and sub_path.is_file():
                    break

            if not sub_path or not sub_path.is_file():
                if saw_rate_limit:
                    raise TranscriptRateLimitError(
                        "YouTube rate limit (429). Wait 3 minutes before trying again."
                    )
                raise TranscriptNotAvailableError(
                    f"Subtitle file was not written for language '{language}' "
                    f"(tried: {', '.join(lang_candidates[:6])}). "
                    "This video may have no captions on YouTube."
                )

            logger.info(
                "Transcript extracted: %s lang=%s file=%s",
                (video_id or url)[:20],
                last_lang,
                sub_path.name,
            )

            raw = sub_path.read_text(encoding="utf-8", errors="replace")
            content, fmt = normalize_subtitle_content(
                raw, sub_path.suffix.lstrip("."), output_format
            )
            if prefer_script == "roman" and is_indic_roman_language(language):
                content = romanize_subtitle_content(content, language, fmt)
            _store_cached_transcript(cache_key, content, fmt)
            return content, fmt
    except (
        TranscriptNotAvailableError,
        TranscriptBotDetectionError,
        TranscriptRateLimitError,
        TranscriptError,
    ):
        raise
    except Exception as exc:
        msg = str(exc)
        if is_rate_limit_error(msg):
            vid = url
            _mark_rate_limited(vid)
            raise TranscriptRateLimitError(
                "YouTube rate limit (429). Wait 3 minutes before trying again."
            ) from exc
        if is_bot_detection_error(msg):
            raise TranscriptBotDetectionError(
                "YouTube blocked subtitle access (likely datacenter IP or bot check). "
                "Try from a residential network or configure cookies via "
                "GID_YTDLP_COOKIES_FILE / providers.youtube.cookies_file. "
                f"Original: {msg[:200]}"
            ) from exc
        raise TranscriptError(f"Transcript extraction failed: {msg}") from exc
