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
import shutil
import signal
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from src.exceptions import FFmpegNotFoundError

logger = logging.getLogger(__name__)


def _terminate_process_group(proc: subprocess.Popen) -> None:
    """Terminate process group gracefully with SIGTERM, then escalate to SIGKILL."""
    if proc.poll() is not None:
        return
    try:
        if os.name != "nt" and hasattr(os, "killpg"):
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        else:
            proc.terminate()
    except (OSError, ProcessLookupError):
        pass

    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        try:
            if os.name != "nt" and hasattr(os, "killpg"):
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            else:
                proc.kill()
        except (OSError, ProcessLookupError):
            pass


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
    is_cancelled: Any = None
    on_proc_started: Any = None


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

        output_path: str | None = None
        temp_download: str | None = None

        try:
            self._ensure_ffmpeg_available()

            source_path = request.source

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

            mode = request.mode
            proc_stderr = ""
            clip_duration = self._clip_duration_seconds(request)
            timeout = self._ffmpeg_timeout_seconds(clip_duration, mode)

            for attempt in range(2):
                if attempt == 1:
                    # Remove corrupt partial from failed fast (stream-copy) attempt
                    if os.path.exists(output_path):
                        try:
                            os.remove(output_path)
                        except OSError:
                            pass

                cmd = self._build_ffmpeg_cmd(
                    source_path, output_path, request, mode=mode
                )

                logger.info(
                    "Extracting clip (%s): %s to %s (timeout=%ss)",
                    mode,
                    request.start_time,
                    request.end_time,
                    timeout,
                )
                logger.debug("FFmpeg cmd: %s", cmd)

                start_new_session = (os.name != "nt")
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    start_new_session=start_new_session,
                )

                if callable(getattr(request, "on_proc_started", None)):
                    try:
                        request.on_proc_started(proc)
                    except Exception:
                        pass

                start_t = time.time()
                is_cancelled_fn = getattr(request, "is_cancelled", None)

                while proc.poll() is None:
                    if callable(is_cancelled_fn) and is_cancelled_fn():
                        _terminate_process_group(proc)
                        raise RuntimeError("Cancelled by user")
                    if time.time() - start_t > timeout:
                        _terminate_process_group(proc)
                        raise RuntimeError(
                            f"FFmpeg timed out after {timeout}s while encoding a "
                            f"{clip_duration:.0f}s clip. Try a shorter segment or fast mode."
                        )
                    time.sleep(0.2)

                proc_stdout, proc_stderr = proc.communicate()
                proc_stderr = proc_stderr or ""

                if proc.returncode != 0:
                    err = proc_stderr.strip() or f"exit code {proc.returncode}"
                    raise RuntimeError(
                        f"FFmpeg failed ({mode}): {err[-2000:]}"
                    )

                if not os.path.exists(output_path):
                    raise RuntimeError("Output file was not created")

                if mode == "fast" and not self._is_output_playable(output_path):
                    logger.warning(
                        "Fast stream-copy clip is empty or unplayable; "
                        "retrying with precise re-encode"
                    )
                    mode = "precise"
                    timeout = self._ffmpeg_timeout_seconds(clip_duration, mode)
                    continue
                break

            file_size = os.path.getsize(output_path)
            if not self._is_output_playable(output_path):
                raise RuntimeError(
                    "Clip output is not playable (missing moov atom or zero "
                    f"duration). FFmpeg stderr: {proc_stderr.strip()[-1000:]}"
                )
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

        except FFmpegNotFoundError as e:
            result.success = False
            result.error = str(e)
            logger.error("Clip extraction failed: %s", e)
        except Exception as e:
            result.success = False
            result.error = str(e)
            logger.error("Clip extraction failed: %s", e)
            if output_path and os.path.exists(output_path):
                try:
                    os.remove(output_path)
                except OSError:
                    pass
        finally:
            if not result.success and temp_download and os.path.exists(temp_download):
                logger.info("Keeping temp download for retry: %s", temp_download)

        return result

    def _ensure_ffmpeg_available(self) -> None:
        """Verify configured ffmpeg/ffprobe paths exist."""
        for label, path in (("ffmpeg", self._ffmpeg), ("ffprobe", self._ffprobe)):
            if not Path(path).is_file() and not shutil.which(path):
                raise FFmpegNotFoundError(
                    f"{label} not found at {path!r}. Install FFmpeg and set "
                    "GID_FFMPEG_LOCATION or add ffmpeg to PATH."
                )

    @staticmethod
    def _is_output_playable(file_path: str) -> bool:
        """Return True if ffprobe can read a non-zero duration (moov atom present)."""
        try:
            if not os.path.isfile(file_path):
                return False
            if os.path.getsize(file_path) < 512:
                return False
            from src.env_loader import resolve_ffmpeg_binaries

            _, ffprobe = resolve_ffmpeg_binaries()
            cmd = [
                ffprobe,
                "-protocol_whitelist",
                "file,crypto,data",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                file_path,
            ]
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=30
            )
            if result.returncode != 0:
                return False
            stderr = (result.stderr or "").lower()
            if "moov atom not found" in stderr or "invalid data" in stderr:
                return False
            return float(result.stdout.strip()) > 0
        except Exception:
            return False

    @staticmethod
    def _clip_duration_seconds(request: ClipRequest) -> float:
        start = ClipExtractor._timestamp_to_seconds(request.start_time)
        end = ClipExtractor._timestamp_to_seconds(request.end_time)
        return max(end - start, 0.0)

    @staticmethod
    def _ffmpeg_timeout_seconds(clip_duration: float, mode: str) -> int:
        """Scale subprocess timeout with clip length (re-encode can be slow)."""
        if mode == "fast":
            return max(180, int(clip_duration) + 120)
        # Precise re-encode: allow ~10x realtime + 5 min headroom (min 15 min)
        return max(900, int(clip_duration * 10) + 300)

    def _build_ffmpeg_cmd(
        self,
        source: str,
        output: str,
        request: ClipRequest,
        mode: str | None = None,
    ) -> list[str]:
        """Build FFmpeg command for clip extraction."""
        use_mode = mode or request.mode
        duration = self._calc_duration(request.start_time, request.end_time)
        cmd = [
            self._ffmpeg,
            "-y",
            "-nostdin",
            "-protocol_whitelist",
            "file,crypto,data",
        ]

        # -ss before -i = fast input seek (critical for long videos)
        cmd.extend(["-ss", request.start_time])
        cmd.extend(["-i", source])
        cmd.extend(["-t", duration])


        if use_mode == "fast":
            cmd.extend(["-c", "copy"])
            cmd.extend(["-avoid_negative_ts", "make_zero"])
            cmd.extend(["-reset_timestamps", "1"])
        else:
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
            if request.output_format == "mp4":
                cmd.extend(["-movflags", "+faststart"])

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

    @staticmethod
    def _resolve_ffmpeg_dir_for_ytdlp() -> str | None:
        """Same ffmpeg resolution as download (YouTube) — single source of truth."""
        from src.env_loader import get_ffmpeg_location
        return get_ffmpeg_location()

    def _download_source(self, url: str, clip_id: str) -> str:
        """Download video from URL for clipping.

        If ffmpeg is available (GID_FFMPEG_LOCATION or PATH), uses bestvideo+bestaudio
        and merges to mp4. Otherwise uses a single format that does not require merging.
        """
        import yt_dlp

        output_template = str(
            Path(self._output_dir) / f"_temp_{clip_id}.%(ext)s"
        )

        ffmpeg_dir = self._resolve_ffmpeg_dir_for_ytdlp()
        trusted_gateway = os.environ.get("MEDIAFORGE_TRUSTED_EGRESS_GATEWAY")
        ydl_opts = {
            "outtmpl": output_template,
            "quiet": True,
            "no_warnings": True,
            "proxy": trusted_gateway,
        }
        if ffmpeg_dir:
            ydl_opts["ffmpeg_location"] = ffmpeg_dir
            ydl_opts["format"] = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
            ydl_opts["merge_output_format"] = "mp4"
        else:
            # No ffmpeg: avoid merge so yt-dlp does not require ffmpeg
            ydl_opts["format"] = "best[ext=mp4]/best"

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
                "-protocol_whitelist",
                "file,crypto,data",
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

    @staticmethod
    def validate_timestamps(
        start: str,
        end: str,
        source_duration: float | None = None,
    ) -> tuple[bool, str]:
        """Validate start and end timestamps.

        Returns (is_valid, error_message).
        """
        try:
            start_s = ClipExtractor._timestamp_to_seconds(start)
            end_s = ClipExtractor._timestamp_to_seconds(end)
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
    """Return the global ClipExtractor instance, creating it if needed.

    Uses resolve_ffmpeg_binaries() so discovery matches download (YouTube).
    """
    global _extractor
    if _extractor is None:
        from src.env_loader import resolve_ffmpeg_binaries

        ffmpeg_path, ffprobe_path = resolve_ffmpeg_binaries()
        _extractor = ClipExtractor(
            ffmpeg_path=ffmpeg_path,
            ffprobe_path=ffprobe_path,
        )
    return _extractor


def reset_clip_extractor() -> None:
    """Reset the global extractor (for tests)."""
    global _extractor
    _extractor = None
