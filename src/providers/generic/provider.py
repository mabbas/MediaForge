"""GrabItDown Generic provider — catches any URL that yt-dlp can handle."""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any, Dict, List

import yt_dlp

from src.core.base_provider import BaseMediaProvider
from src.core.interfaces import ProgressCallback, ProviderCapabilities
from src.exceptions import DownloadCancelledError, DownloadError, ProviderError
from src.models.download import DownloadRequest, DownloadResult, DownloadProgress
from src.models.enums import DownloadStatus, MediaType, ProviderType, Quality
from src.models.media import MediaFormat, MediaInfo, Thumbnail

logger = logging.getLogger(__name__)


class GenericProvider(BaseMediaProvider):
    """Fallback provider for any yt-dlp supported site."""

    def __init__(self) -> None:
        """Initialize the generic provider."""

    @property
    def name(self) -> str:
        """Return the human-readable provider name."""
        return "Generic"

    @property
    def provider_type(self) -> ProviderType:
        """Return the provider type enum."""
        return ProviderType.GENERIC

    @property
    def capabilities(self) -> ProviderCapabilities:
        """Return the provider capabilities."""
        return ProviderCapabilities(
            supports_video=True,
            supports_audio=True,
            supports_playlists=False,
            supports_subtitles=False,
            supports_live_streams=False,
            supports_formats_selection=True,
            supports_resume=True,
            max_quality=Quality.BEST,
            supported_domains=[],
        )

    def can_handle(self, url: str) -> bool:
        """Accept any valid HTTP/HTTPS URL."""
        return self.validate_url(url)

    def extract_info(self, url: str) -> MediaInfo:
        """Extract info using yt-dlp's generic extractor."""
        ydl_opts: Dict[str, Any] = {
            "quiet": True,
            "no_color": True,
            "no_warnings": True,
            "extract_flat": False,
            "skip_download": True,
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                if info is None:
                    raise ProviderError("Failed to extract info", provider=self.name)
                return self._map_info(info)
        except yt_dlp.utils.DownloadError as exc:
            raise ProviderError(f"Extraction failed: {str(exc)}", provider=self.name) from exc
        except ProviderError:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            raise ProviderError(f"Unexpected error: {str(exc)}", provider=self.name) from exc

    def get_formats(self, url: str) -> list[MediaFormat]:
        """Return available formats for the URL."""
        info = self.extract_info(url)
        return info.formats

    def download(
        self,
        request: DownloadRequest,
        output_dir: str | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> DownloadResult:
        """Download using yt-dlp's generic extractor."""
        started_at = datetime.now()
        out_dir = output_dir or request.output_directory or "./downloads"
        os.makedirs(out_dir, exist_ok=True)

        ydl_opts: Dict[str, Any] = {
            "quiet": True,
            "no_color": True,
            "outtmpl": os.path.join(out_dir, "%(title)s.%(ext)s"),
            "overwrites": True,
        }

        if request.media_type == MediaType.AUDIO:
            ydl_opts["format"] = "bestaudio/best"
            ydl_opts["postprocessors"] = [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": request.audio_format.value,
                    "preferredquality": request.audio_bitrate.replace("k", ""),
                }
            ]
        else:
            # No fallback to single-stream so we always get merged video+audio (with sound)
            ydl_opts["format"] = "bestvideo+bestaudio"
            ydl_opts["merge_output_format"] = request.video_format.value

        if progress_callback:
            ydl_opts["progress_hooks"] = [lambda d: self._generic_progress_hook(d, progress_callback)]

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(request.url, download=True)
                if info is None:
                    raise DownloadError("Download returned no info", url=request.url)

                filepath = ydl.prepare_filename(info)
                if not os.path.exists(filepath):
                    base = os.path.splitext(filepath)[0]
                    for ext in [".mp4", ".mkv", ".webm", ".mp3", ".m4a", ".opus"]:
                        candidate = base + ext
                        if os.path.exists(candidate):
                            filepath = candidate
                            break

                file_size = os.path.getsize(filepath) if os.path.exists(filepath) else None

                return DownloadResult(
                    job_id="generic-download",
                    url=request.url,
                    provider=self.provider_type,
                    status=DownloadStatus.COMPLETED,
                    title=info.get("title", "Unknown"),
                    file_path=filepath,
                    file_size_bytes=file_size,
                    media_type=request.media_type,
                    duration_seconds=info.get("duration"),
                    started_at=started_at,
                    completed_at=datetime.now(),
                )
        except DownloadError:
            raise
        except yt_dlp.utils.DownloadError as exc:
            raise DownloadError(f"Download failed: {str(exc)}", url=request.url) from exc
        except Exception as exc:  # pragma: no cover - defensive
            raise DownloadError(f"Unexpected error: {str(exc)}", url=request.url) from exc

    def _map_info(self, info_dict: Dict[str, Any]) -> MediaInfo:
        """Map generic yt-dlp info to MediaInfo."""
        formats: List[MediaFormat] = []
        for fmt in info_dict.get("formats", []) or []:
            vcodec = fmt.get("vcodec", "none")
            acodec = fmt.get("acodec", "none")
            has_video = vcodec not in ("none", None)
            has_audio = acodec not in ("none", None)
            resolution = None
            if fmt.get("width") and fmt.get("height"):
                resolution = f"{fmt['width']}x{fmt['height']}"
            formats.append(
                MediaFormat(
                    format_id=str(fmt.get("format_id", "unknown")),
                    extension=fmt.get("ext", "unknown"),
                    quality=fmt.get("format_note"),
                    resolution=resolution,
                    fps=fmt.get("fps"),
                    vcodec=vcodec if has_video else None,
                    acodec=acodec if has_audio else None,
                    filesize_bytes=fmt.get("filesize"),
                    filesize_approx_bytes=fmt.get("filesize_approx"),
                    bitrate=fmt.get("tbr"),
                    has_video=has_video,
                    has_audio=has_audio,
                )
            )

        thumbnails: List[Thumbnail] = [
            Thumbnail(url=t.get("url", ""), width=t.get("width"), height=t.get("height"))
            for t in info_dict.get("thumbnails", []) or []
        ]

        return MediaInfo(
            url=info_dict.get("webpage_url", info_dict.get("url", "")),
            provider=self.provider_type,
            media_id=info_dict.get("id", "unknown"),
            title=info_dict.get("title", "Unknown"),
            description=info_dict.get("description"),
            duration_seconds=info_dict.get("duration"),
            channel_name=info_dict.get("channel") or info_dict.get("uploader"),
            thumbnails=thumbnails,
            formats=formats,
        )

    def _generic_progress_hook(self, d: Dict[str, Any], callback: ProgressCallback) -> None:
        """Convert yt-dlp progress to DownloadProgress."""
        status_map: Dict[str, DownloadStatus] = {
            "downloading": DownloadStatus.DOWNLOADING,
            "finished": DownloadStatus.PROCESSING,
            "error": DownloadStatus.FAILED,
        }
        total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
        downloaded = d.get("downloaded_bytes", 0)
        percent = (downloaded / total * 100) if total > 0 else 0.0

        progress = DownloadProgress(
            job_id="generic",
            status=status_map.get(d.get("status"), DownloadStatus.DOWNLOADING),
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

