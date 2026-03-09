"""GrabItDown YouTube playlist handler."""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List

import yt_dlp

from src.core.interfaces import ProgressCallback
from src.exceptions import DownloadError, ProviderError
from src.models.download import DownloadRequest, DownloadResult
from src.models.enums import DownloadStatus, MediaType, ProviderType
from src.models.playlist import PlaylistDownloadRequest, PlaylistInfo, PlaylistItem
from src.log_safe import safe_str
from src.providers.youtube.provider import YouTubeProvider

logger = logging.getLogger(__name__)


class YouTubePlaylistHandler:
    """Handles YouTube playlist extraction and sequential download."""

    def __init__(self, provider: YouTubeProvider) -> None:
        """Initialize with a YouTube provider instance."""
        self._provider = provider

    @staticmethod
    def _normalize_playlist_url(url: str) -> str:
        """Use canonical playlist URL so yt-dlp returns all entries, not just one video.

        For watch URLs like ...?v=VIDEO&list=PLxxx, yt-dlp may use the video extractor
        and return a single entry. Using .../playlist?list=PLxxx forces the playlist
        extractor and returns all items.
        """
        match = re.search(r"[?&]list=([^&]+)", url, re.IGNORECASE)
        if match:
            playlist_id = match.group(1).strip()
            return f"https://www.youtube.com/playlist?list={playlist_id}"
        return url

    def get_playlist_info(self, url: str) -> PlaylistInfo:
        """Extract playlist metadata (all items)."""
        url = self._normalize_playlist_url(url)
        ydl_opts: Dict[str, Any] = self._provider._build_base_opts()  # type: ignore[attr-defined]
        ydl_opts.update(
            {
                "extract_flat": True,
                "skip_download": True,
                "quiet": True,
                "no_warnings": True,
                "noplaylist": False,
            }
        )

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                # process=False so we get raw entries (generator); we consume them ourselves
                info = ydl.extract_info(url, download=False, process=False)
                if info is None:
                    raise ProviderError("Failed to extract playlist info", provider="YouTube")
                raw_entries = info.get("entries") or []
                # Force full consumption so we get all playlist pages (entries may be a generator)
                info["entries"] = list(raw_entries)
                result = self._map_playlist_info(info)
                logger.info(
                    "Playlist '%s': %s items (available: %s)",
                    safe_str(result.title),
                    result.item_count,
                    len(result.available_items),
                )
                return result
        except yt_dlp.utils.DownloadError as exc:
            raise ProviderError(f"Playlist extraction failed: {str(exc)}", provider="YouTube") from exc
        except ProviderError:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            raise ProviderError(f"Unexpected playlist error: {str(exc)}", provider="YouTube") from exc

    def download_playlist(
        self,
        request: PlaylistDownloadRequest,
        progress_callback: ProgressCallback | None = None,
    ) -> list[DownloadResult]:
        """Download playlist items sequentially."""
        playlist = self.get_playlist_info(request.url)

        if request.items == "all":
            items = playlist.available_items
        else:
            items = [item for item in playlist.available_items if item.index in request.items]

        results: List[DownloadResult] = []
        total = len(items)

        for i, item in enumerate(items, 1):
            logger.info("Downloading %s/%s: %s", i, total, safe_str(item.title))

            dl_request = DownloadRequest(
                url=item.url,
                media_type=request.media_type,
                quality=request.quality,
                video_format=request.video_format,
                audio_format=request.audio_format,
                audio_bitrate=request.audio_bitrate,
                output_directory=request.output_directory,
                embed_subtitles=request.embed_subtitles,
                subtitle_languages=request.subtitle_languages,
            )

            try:
                result = self._provider.download(
                    request=dl_request,
                    output_dir=request.output_directory,
                    progress_callback=progress_callback,
                )
                results.append(result)
            except DownloadError as exc:
                logger.error("Failed to download '%s': %s", safe_str(item.title), exc)
                results.append(
                    DownloadResult(
                        job_id=f"playlist-{item.index}",
                        url=item.url,
                        provider=ProviderType.YOUTUBE,
                        status=DownloadStatus.FAILED,
                        title=item.title,
                        media_type=request.media_type,
                        error_message=str(exc),
                    )
                )

        return results

    def _map_playlist_info(self, info_dict: Dict[str, Any]) -> PlaylistInfo:
        """Map yt-dlp playlist info_dict to PlaylistInfo model."""
        items: List[PlaylistItem] = []
        entries = info_dict.get("entries", []) or []

        for i, entry in enumerate(entries, 1):
            if entry is None:
                continue

            video_id = entry.get("id", "")
            video_url = entry.get("url") or f"https://www.youtube.com/watch?v={video_id}"

            thumbnails = entry.get("thumbnails") or []
            thumbnail_url = thumbnails[-1].get("url") if thumbnails else None

            title = entry.get("title") or f"Video {i}"
            is_available = title not in ("[Private video]", "[Deleted video]")

            items.append(
                PlaylistItem(
                    index=i,
                    url=video_url,
                    title=title,
                    media_id=video_id,
                    duration_seconds=entry.get("duration"),
                    channel_name=entry.get("channel") or entry.get("uploader"),
                    thumbnail_url=thumbnail_url,
                    is_available=is_available,
                )
            )

        playlist_thumbs = info_dict.get("thumbnails") or []
        playlist_thumb_url = playlist_thumbs[-1].get("url") if playlist_thumbs else None

        return PlaylistInfo(
            url=info_dict.get("webpage_url", info_dict.get("url", "")),
            provider=ProviderType.YOUTUBE,
            playlist_id=info_dict.get("id", "unknown"),
            title=info_dict.get("title", "Unknown Playlist"),
            description=info_dict.get("description"),
            channel_name=info_dict.get("channel") or info_dict.get("uploader"),
            item_count=len(items),
            items=items,
            thumbnail_url=playlist_thumb_url,
        )

