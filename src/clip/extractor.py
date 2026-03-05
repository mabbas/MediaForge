"""Clip extractor — trims video/audio files using FFmpeg.

Two cutting modes:
- Fast (keyframe): Uses -ss before -i, copy codecs.
  Nearly instant but may have a few frames of
  inaccuracy at the start.
- Precise (re-encode): Uses -ss after -i, re-encodes.
  Frame-accurate but slower.
"""

from __future__ import annotations

import logging
import os
import subprocess
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class ClipRequest:
    """Request to extract a clip."""

    source: str  # file path or URL
    start_time: str  # "HH:MM:SS" or "HH:MM:SS.mmm" or seconds as string
    end_time: str  # "HH:MM:SS" or "HH:MM:SS.mmm" or seconds as string
    output_path: str | None = None
    output_format: str = "mp4"
    mode: str = "precise"  # "fast" or "precise"
    video_codec: str = "libx264"
    audio_codec: str = "aac"
    video_bitrate: str | None = None
    audio_bitrate: str = "192k"
    resolution: str | None = None  # e.g. "1280x720"
    clip_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    is_url: bool = False


@dataclass
class ClipResult:
    """Result of clip extraction."""

    clip_id: str = ""
    source: str = ""
    output_path: str = ""
    start_time: str = ""
    end_time: str = ""
    duration_seconds: float = 0.0
    file_size_bytes: int = 0
    file_size_human: str = ""
    created_at: str = ""
    success: bool = False
    error: str | None = None


class ClipExtractor:
    """Extracts clips from video/audio files."""

    def __init__(
        self,
        ffmpeg_path: str = "ffmpeg",
        ffprobe_path: str = "ffprobe",
        output_dir: str | None = None,
    ):
        self._ffmpeg = ffmpeg_path
        self._ffprobe = ffprobe_path
        self._output_dir = output_dir or str(
            Path.home() / "Downloads" / "GrabItDown" / "clips"
        )
        Path(self._output_dir).mkdir(parents=True, exist_ok=True)

    def extract(self, request: ClipRequest) -> ClipResult:
        """Extract a clip from source.

        If source is a URL (request.is_url=True),
        downloads first then clips. If source is a
        local file, clips directly.
        """
        result = ClipResult(
            clip_id=request.clip_id,
            source=request.source,
            start_time=request.start_time,
            end_time=request.end_time,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        try:
            source_path = request.source
            temp_download = None

            if request.is_url:
                source_path = self._download_source(
                    request.source, request.clip_id
                )
                temp_download = source_path

            if not os.path.exists(source_path):
                raise FileNotFoundError(
                    f"Source file not found: {source_path}"
                )

            if request.output_path:
                output_path = request.output_path
            else:
                src_name = Path(source_path).stem
                safe_start = request.start_time.replace(":", "-").replace(
                    ".", "-"
                )
                safe_end = request.end_time.replace(":", "-").replace(
                    ".", "-"
                )
                filename = (
                    f"{src_name}_clip_{request.clip_id}_{safe_start}_to_"
                    f"{safe_end}.{request.output_format}"
                )
                output_path = str(Path(self._output_dir) / filename)

            cmd = self._build_ffmpeg_cmd(
                source_path, output_path, request
            )

            logger.info(
                "Extracting clip: %s to %s",
                request.start_time,
                request.end_time,
            )
            logger.debug("FFmpeg cmd: %s", " ".join(cmd))

            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=600,
            )

            if proc.returncode != 0:
                err_tail = (proc.stderr or "")[-500:]
                raise RuntimeError(f"FFmpeg failed: {err_tail}")

            if not os.path.exists(output_path):
                raise RuntimeError("Output file was not created")

            file_size = os.path.getsize(output_path)
            duration = self._get_duration(output_path)

            result.output_path = output_path
            result.file_size_bytes = file_size
            result.file_size_human = self._format_bytes(file_size)
            result.duration_seconds = duration
            result.success = True

            logger.info(
                f"Clip extracted: {output_path} "
                f"({result.file_size_human}, {duration:.1f}s)"
            )

            if temp_download and os.path.exists(temp_download):
                try:
                    os.remove(temp_download)
                except OSError:
                    pass

        except Exception as e:
            result.success = False
            result.error = str(e)
            logger.error("Clip extraction failed: %s", e)

        return result

    def _build_ffmpeg_cmd(
        self,
        source: str,
        output: str,
        request: ClipRequest,
    ) -> list[str]:
        """Build FFmpeg command for clip extraction."""
        cmd = [self._ffmpeg, "-y"]

        if request.mode == "fast":
            cmd.extend(["-ss", request.start_time])
            cmd.extend(["-i", source])
            cmd.extend(
                [
                    "-to",
                    self._calc_duration(
                        request.start_time, request.end_time
                    ),
                ]
            )
            cmd.extend(["-c", "copy"])
            cmd.extend(["-avoid_negative_ts", "make_zero"])
        else:
            cmd.extend(["-i", source])
            cmd.extend(["-ss", request.start_time])
            cmd.extend(["-to", request.end_time])
            cmd.extend(
                [
                    "-c:v",
                    request.video_codec,
                    "-c:a",
                    request.audio_codec,
                ]
            )
            if request.audio_bitrate:
                cmd.extend(["-b:a", request.audio_bitrate])
            if request.video_bitrate:
                cmd.extend(["-b:v", request.video_bitrate])
            if request.resolution:
                cmd.extend(["-s", request.resolution])

        cmd.extend(["-map", "0"])
        cmd.append(output)

        return cmd

    def _calc_duration(self, start: str, end: str) -> str:
        """Calculate duration between start and end timestamps for fast mode."""
        start_secs = self._timestamp_to_seconds(start)
        end_secs = self._timestamp_to_seconds(end)
        duration = end_secs - start_secs
        if duration <= 0:
            raise ValueError(
                f"End time ({end}) must be after start time ({start})"
            )
        return str(duration)

    @staticmethod
    def _timestamp_to_seconds(ts: str) -> float:
        """Convert timestamp string to seconds.

        Supports: "SS", "MM:SS", "HH:MM:SS",
                  "HH:MM:SS.mmm", or plain seconds.
        """
        ts = ts.strip()
        try:
            return float(ts)
        except ValueError:
            pass

        parts = ts.split(":")
        if len(parts) == 3:
            h, m, s = parts
            return int(h) * 3600 + int(m) * 60 + float(s)
        if len(parts) == 2:
            m, s = parts
            return int(m) * 60 + float(s)
        if len(parts) == 1:
            return float(parts[0])
        raise ValueError(f"Invalid timestamp format: {ts}")

    def _download_source(self, url: str, clip_id: str) -> str:
        """Download video from URL for clipping."""
        import yt_dlp

        output_template = str(
            Path(self._output_dir) / f"_temp_{clip_id}.%(ext)s"
        )

        ydl_opts = {
            "outtmpl": output_template,
            "quiet": True,
            "no_warnings": True,
            "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "merge_output_format": "mp4",
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            if os.path.exists(filename):
                return filename
            mp4_path = str(Path(filename).with_suffix(".mp4"))
            if os.path.exists(mp4_path):
                return mp4_path
            raise FileNotFoundError(
                f"Downloaded file not found: {filename}"
            )

    def _get_duration(self, file_path: str) -> float:
        """Get duration of a media file using ffprobe."""
        try:
            cmd = [
                self._ffprobe,
                "-v",
                "quiet",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                file_path,
            ]
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=30
            )
            return float(result.stdout.strip())
        except Exception:
            return 0.0

    @staticmethod
    def _format_bytes(b: int) -> str:
        if b < 1024:
            return f"{b} B"
        if b < 1024**2:
            return f"{b / 1024:.1f} KB"
        if b < 1024**3:
            return f"{b / 1024**2:.2f} MB"
        return f"{b / 1024**3:.2f} GB"

    def get_video_duration(self, file_path: str) -> float:
        """Public method to get video duration."""
        return self._get_duration(file_path)

    def validate_timestamps(
        self,
        start: str,
        end: str,
        source_duration: float | None = None,
    ) -> tuple[bool, str]:
        """Validate start and end timestamps.

        Returns (is_valid, error_message).
        """
        try:
            start_s = self._timestamp_to_seconds(start)
            end_s = self._timestamp_to_seconds(end)
        except ValueError as e:
            return False, str(e)

        if start_s < 0:
            return False, "Start time cannot be negative"
        if end_s <= start_s:
            return False, "End time must be after start time"
        if source_duration is not None and end_s > source_duration:
            return False, (
                f"End time ({end_s}s) exceeds video "
                f"duration ({source_duration}s)"
            )
        if end_s - start_s < 0.5:
            return False, "Clip must be at least 0.5 seconds"
        if end_s - start_s > 7200:
            return False, "Clip cannot exceed 2 hours"

        return True, ""


_extractor: ClipExtractor | None = None


def get_clip_extractor() -> ClipExtractor:
    """Return the global ClipExtractor instance, creating it if needed."""
    global _extractor
    if _extractor is None:
        try:
            from src.env_loader import load_project_dotenv
            load_project_dotenv()
        except Exception:  # noqa: S110
            pass
        ffdir = os.environ.get("GID_FFMPEG_LOCATION", "").strip() or None
        if ffdir:
            p = Path(ffdir)
            _extractor = ClipExtractor(
                ffmpeg_path=str(p / "ffmpeg"),
                ffprobe_path=str(p / "ffprobe"),
            )
        else:
            _extractor = ClipExtractor()
    return _extractor


def reset_clip_extractor() -> None:
    """Reset the global extractor (for tests)."""
    global _extractor
    _extractor = None
