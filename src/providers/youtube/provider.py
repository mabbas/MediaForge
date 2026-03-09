"""GrabItDown YouTube provider — downloads videos and audio from YouTube using yt-dlp."""

from __future__ import annotations

import logging
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

# Load .env from project root so GID_FFMPEG_LOCATION is available (CLI, API, desktop)
try:
    from src.env_loader import load_project_dotenv
    load_project_dotenv()
except Exception:  # noqa: BLE001 - best-effort; avoid import cycles
    pass

import yt_dlp

from src.core.base_provider import BaseMediaProvider
from src.core.interfaces import ProgressCallback, ProviderCapabilities
from src.exceptions import DownloadCancelledError, DownloadError, ProviderError
from src.models.download import DownloadRequest, DownloadResult, DownloadProgress
from src.models.enums import AudioFormat, MediaType, ProviderType, Quality, DownloadStatus, VideoFormat
from src.models.media import MediaFormat, MediaInfo, Thumbnail

logger = logging.getLogger(__name__)


class YouTubeProvider(BaseMediaProvider):
    """YouTube media provider using yt-dlp."""

    SUPPORTED_DOMAINS = [
        "youtube.com",
        "youtu.be",
        "www.youtube.com",
        "m.youtube.com",
        "music.youtube.com",
    ]

    def __init__(self, cookies_file: str | None = None, geo_bypass: bool = True) -> None:
        """Initialize YouTube provider."""
        self._cookies_file = cookies_file
        self._geo_bypass = geo_bypass

    @property
    def name(self) -> str:
        """Return the human-readable provider name."""
        return "YouTube"

    @property
    def provider_type(self) -> ProviderType:
        """Return the provider type enum."""
        return ProviderType.YOUTUBE

    @property
    def capabilities(self) -> ProviderCapabilities:
        """Return the provider capabilities."""
        return ProviderCapabilities(
            supports_video=True,
            supports_audio=True,
            supports_playlists=True,
            supports_subtitles=True,
            supports_live_streams=True,
            supports_formats_selection=True,
            supports_resume=True,
            max_quality=Quality.Q_2160P,
            supported_domains=self.SUPPORTED_DOMAINS,
        )

    def can_handle(self, url: str) -> bool:
        """Check if URL is a YouTube URL."""
        return self.validate_url(url)

    def extract_info(self, url: str) -> MediaInfo:
        """Extract video metadata from YouTube."""
        ydl_opts: Dict[str, Any] = self._build_base_opts()
        ydl_opts.update(
            {
                "extract_flat": False,
                "skip_download": True,
                "no_warnings": True,
                "ignoreerrors": False,
            }
        )

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                if info is None:
                    raise ProviderError("Failed to extract video info", provider=self.name)
                return self._map_info_to_media(info)
        except yt_dlp.utils.DownloadError as exc:
            raise ProviderError(f"Failed to extract info: {str(exc)}", provider=self.name) from exc
        except ProviderError:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            raise ProviderError(f"Unexpected error: {str(exc)}", provider=self.name) from exc

    def get_formats(self, url: str) -> list[MediaFormat]:
        """Get available download formats for a YouTube video."""
        info = self.extract_info(url)
        return info.formats

    def download(
        self,
        request: DownloadRequest,
        output_dir: str | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> DownloadResult:
        """Download a YouTube video or audio."""
        started_at = datetime.now()
        job_id = request.filename or "download"

        out_dir = output_dir or request.output_directory or "./downloads"
        os.makedirs(out_dir, exist_ok=True)

        ydl_opts = self._build_download_opts(request, out_dir, progress_callback)

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(request.url, download=True)
                if info is None:
                    raise DownloadError("Download returned no info", url=request.url)

                file_path = self._find_downloaded_file(info, ydl, out_dir)
                file_size = os.path.getsize(file_path) if file_path and os.path.exists(file_path) else None

                return DownloadResult(
                    job_id=job_id,
                    url=request.url,
                    provider=self.provider_type,
                    status=DownloadStatus.COMPLETED,
                    title=info.get("title", "Unknown"),
                    file_path=str(file_path) if file_path else None,
                    file_size_bytes=file_size,
                    media_type=request.media_type,
                    quality=request.quality.value if request.media_type == MediaType.VIDEO else None,
                    format=(
                        request.audio_format.value
                        if request.media_type == MediaType.AUDIO
                        else request.video_format.value
                    ),
                    duration_seconds=info.get("duration"),
                    started_at=started_at,
                    completed_at=datetime.now(),
                )
        except DownloadError:
            raise
        except yt_dlp.utils.DownloadError as exc:
            raise DownloadError(f"Download failed: {str(exc)}", url=request.url) from exc
        except Exception as exc:  # pragma: no cover - defensive
            raise DownloadError(f"Unexpected download error: {str(exc)}", url=request.url) from exc

    # ── Private helpers ──────────────────────────────────────────────────────

    def _get_ffmpeg_location(self) -> str | None:
        """Return directory containing ffmpeg/ffprobe, or None if not found.

        Reads GID_FFMPEG_LOCATION from env (loaded from project .env).         On Windows use forward slashes in .env to avoid backslash issues, e.g.:
          GID_FFMPEG_LOCATION=C:/ffmpeg/bin
        """
        raw = os.environ.get("GID_FFMPEG_LOCATION", "").strip() or None
        if not raw:
            ffmpeg_path = shutil.which("ffmpeg")
            if ffmpeg_path:
                return str(Path(ffmpeg_path).resolve().parent)
            return None
        # Normalize path: .env on Windows may have backslashes interpreted (\f, \b)
        ffmpeg_dir = Path(raw).expanduser().resolve()
        if ffmpeg_dir.is_dir():
            return str(ffmpeg_dir)
        # Try as literal path in case expanduser/resolve changed it
        if os.path.isdir(raw):
            return os.path.normpath(raw)
        return None

    def _build_base_opts(self) -> Dict[str, Any]:
        """Build base yt-dlp options shared across operations."""
        opts: Dict[str, Any] = {
            "quiet": True,
            "no_color": True,
            "geo_bypass": self._geo_bypass,
        }
        if self._cookies_file:
            opts["cookiefile"] = self._cookies_file
        ffmpeg_dir = self._get_ffmpeg_location()
        if ffmpeg_dir:
            opts["ffmpeg_location"] = ffmpeg_dir
        return opts

    def _build_download_opts(
        self,
        request: DownloadRequest,
        output_dir: str,
        progress_callback: ProgressCallback | None,
    ) -> Dict[str, Any]:
        """Build yt-dlp options for downloading."""
        opts = self._build_base_opts()

        safe_title = "%(title)s.%(ext)s"
        opts["outtmpl"] = os.path.join(output_dir, safe_title)
        opts["overwrites"] = True
        opts["noplaylist"] = True
        # Give downloads more time on slow networks (e.g. full test suite / CI)
        opts["socket_timeout"] = 120.0

        if request.media_type == MediaType.AUDIO:
            opts["format"] = "bestaudio/best"
            opts["postprocessors"] = [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": request.audio_format.value,
                    "preferredquality": request.audio_bitrate.replace("k", ""),
                }
            ]
            if request.embed_thumbnail:
                opts["postprocessors"].append({"key": "EmbedThumbnail"})
                opts["writethumbnail"] = True
        else:
            format_str = self._build_format_string(request.quality)
            opts["format"] = format_str
            opts["merge_output_format"] = request.video_format.value
            if not opts.get("ffmpeg_location"):
                logger.warning(
                    "ffmpeg_location not set; video+audio merge may fail. "
                    "Set GID_FFMPEG_LOCATION to the directory containing ffmpeg."
                )

            if request.embed_subtitles and request.subtitle_languages:
                opts["writesubtitles"] = True
                opts["subtitleslangs"] = request.subtitle_languages
                opts["postprocessors"] = opts.get("postprocessors", [])
                opts["postprocessors"].append({"key": "FFmpegEmbedSubtitle"})

            if request.embed_thumbnail:
                opts["postprocessors"] = opts.get("postprocessors", [])
                opts["postprocessors"].append({"key": "EmbedThumbnail"})
                opts["writethumbnail"] = True

        if progress_callback:
            opts["progress_hooks"] = [lambda d: self._progress_hook(d, progress_callback)]

        return opts

    def _build_format_string(self, quality: Quality) -> str:
        """Build yt-dlp format string for video quality.

        Use only merged video+audio (no single-stream fallback) so we never
        get video-only downloads without sound. Merging requires ffmpeg to be
        installed and on PATH (or set GID_FFMPEG_LOCATION).
        """
        quality_map: Dict[Quality, str] = {
            Quality.Q_2160P: "bestvideo[height<=2160]+bestaudio/bestvideo+bestaudio",
            Quality.Q_1440P: "bestvideo[height<=1440]+bestaudio/bestvideo+bestaudio",
            Quality.Q_1080P: "bestvideo[height<=1080]+bestaudio/bestvideo+bestaudio",
            Quality.Q_720P: "bestvideo[height<=720]+bestaudio/bestvideo+bestaudio",
            Quality.Q_480P: "bestvideo[height<=480]+bestaudio/bestvideo+bestaudio",
            Quality.Q_360P: "bestvideo[height<=360]+bestaudio/bestvideo+bestaudio",
            Quality.BEST: "bestvideo+bestaudio",
            Quality.WORST: "worstvideo+worstaudio",
        }
        return quality_map.get(quality, "bestvideo+bestaudio")

    def _map_info_to_media(self, info_dict: Dict[str, Any]) -> MediaInfo:
        """Map yt-dlp info_dict to our MediaInfo model."""
        thumbnails: List[Thumbnail] = []
        for thumb in info_dict.get("thumbnails", []) or []:
            thumbnails.append(
                Thumbnail(
                    url=thumb.get("url", ""),
                    width=thumb.get("width"),
                    height=thumb.get("height"),
                    quality=thumb.get("id"),
                )
            )

        formats: List[MediaFormat] = []
        for fmt in info_dict.get("formats", []) or []:
            formats.append(self._map_format(fmt))

        subtitles: Dict[str, List[str]] = {}
        for lang, subs in (info_dict.get("subtitles") or {}).items():
            subtitles[lang] = [s.get("ext", "unknown") for s in subs]

        for lang, subs in (info_dict.get("automatic_captions") or {}).items():
            if lang not in subtitles:
                subtitles[lang] = [s.get("ext", "unknown") for s in subs]

        return MediaInfo(
            url=info_dict.get("webpage_url", info_dict.get("url", "")),
            provider=self.provider_type,
            media_id=info_dict.get("id", "unknown"),
            title=info_dict.get("title", "Unknown"),
            description=info_dict.get("description"),
            duration_seconds=info_dict.get("duration"),
            channel_name=info_dict.get("channel") or info_dict.get("uploader"),
            channel_url=info_dict.get("channel_url") or info_dict.get("uploader_url"),
            upload_date=info_dict.get("upload_date"),
            view_count=info_dict.get("view_count"),
            like_count=info_dict.get("like_count"),
            thumbnails=thumbnails,
            formats=formats,
            subtitles_available=subtitles,
            is_live=info_dict.get("is_live", False),
            is_playlist="entries" in info_dict,
        )

    def _map_format(self, fmt_dict: Dict[str, Any]) -> MediaFormat:
        """Map a single yt-dlp format dict to MediaFormat."""
        vcodec = fmt_dict.get("vcodec", "none")
        acodec = fmt_dict.get("acodec", "none")
        has_video = vcodec not in ("none", None)
        has_audio = acodec not in ("none", None)

        resolution = None
        if fmt_dict.get("width") and fmt_dict.get("height"):
            resolution = f"{fmt_dict['width']}x{fmt_dict['height']}"

        return MediaFormat(
            format_id=str(fmt_dict.get("format_id", "unknown")),
            extension=fmt_dict.get("ext", "unknown"),
            quality=fmt_dict.get("format_note"),
            resolution=resolution,
            fps=fmt_dict.get("fps"),
            vcodec=vcodec if has_video else None,
            acodec=acodec if has_audio else None,
            filesize_bytes=fmt_dict.get("filesize"),
            filesize_approx_bytes=fmt_dict.get("filesize_approx"),
            bitrate=fmt_dict.get("tbr"),
            has_video=has_video,
            has_audio=has_audio,
            note=fmt_dict.get("format"),
        )

    def _progress_hook(self, d: Dict[str, Any], callback: ProgressCallback) -> None:
        """Bridge yt-dlp progress dict to our DownloadProgress model."""
        status_map: Dict[str, DownloadStatus] = {
            "downloading": DownloadStatus.DOWNLOADING,
            "finished": DownloadStatus.PROCESSING,
            "error": DownloadStatus.FAILED,
        }
        status = status_map.get(d.get("status"), DownloadStatus.DOWNLOADING)

        total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
        downloaded = d.get("downloaded_bytes", 0)
        percent = (downloaded / total * 100) if total > 0 else 0.0

        progress = DownloadProgress(
            job_id="active",
            status=status,
            bytes_downloaded=downloaded,
            total_bytes=total if total > 0 else None,
            percent=round(percent, 1),
            speed_bytes_per_second=d.get("speed") or 0.0,
            eta_seconds=d.get("eta"),
            current_file=d.get("filename"),
        )

        try:
            callback(progress)
        except DownloadCancelledError:
            raise  # Let cancellation propagate so yt-dlp aborts the download
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Progress callback error: %s", exc)

    def _find_downloaded_file(self, info_dict: Dict[str, Any], ydl: yt_dlp.YoutubeDL, output_dir: str) -> str | None:
        """Find the actual downloaded file path."""
        try:
            filepath = ydl.prepare_filename(info_dict)
            if os.path.exists(filepath):
                return filepath
            base = os.path.splitext(filepath)[0]
            for ext in [".mp4", ".mkv", ".webm", ".mp3", ".m4a", ".opus", ".flac", ".wav"]:
                candidate = base + ext
                if os.path.exists(candidate):
                    return candidate
        except Exception:
            pass

        try:
            files = list(Path(output_dir).glob("*"))
            if files:
                return str(max(files, key=os.path.getmtime))
        except Exception:
            pass

        return None

