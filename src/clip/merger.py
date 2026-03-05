"""Clip merger — combines multiple video/audio clips into a single file using FFmpeg.

Two merging strategies:
- Concat demuxer: Fast, no re-encode. Works when all clips have the same codec/resolution/fps.
- Filter complex: Re-encodes. Works with any mix of codecs/resolutions. Normalizes everything.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class MergeRequest:
    """Request to merge clips."""

    clips: list[str]  # List of file paths
    output_path: str | None = None
    output_format: str = "mp4"
    mode: str = "auto"  # "auto", "concat", "reencode"
    video_codec: str = "libx264"
    audio_codec: str = "aac"
    video_bitrate: str | None = None
    audio_bitrate: str = "192k"
    resolution: str | None = None
    transition: str | None = None  # Future: fade, etc.
    merge_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])


@dataclass
class MergeResult:
    """Result of clip merging."""

    merge_id: str = ""
    clip_count: int = 0
    output_path: str = ""
    total_duration_seconds: float = 0.0
    file_size_bytes: int = 0
    file_size_human: str = ""
    created_at: str = ""
    success: bool = False
    error: str | None = None
    clips_used: list[str] = field(default_factory=list)


class ClipMerger:
    """Merges multiple video/audio clips."""

    def __init__(
        self,
        ffmpeg_path: str = "ffmpeg",
        ffprobe_path: str = "ffprobe",
        output_dir: str | None = None,
    ):
        self._ffmpeg = ffmpeg_path
        self._ffprobe = ffprobe_path
        self._output_dir = output_dir or str(
            Path.home() / "Downloads" / "GrabItDown" / "merged"
        )
        Path(self._output_dir).mkdir(parents=True, exist_ok=True)

    def merge(self, request: MergeRequest) -> MergeResult:
        """Merge clips into single file."""
        result = MergeResult(
            merge_id=request.merge_id,
            clip_count=len(request.clips),
            clips_used=list(request.clips),
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        try:
            self._validate_clips(request.clips)

            if request.output_path:
                output_path = request.output_path
            else:
                filename = (
                    f"merged_{request.merge_id}_"
                    f"{len(request.clips)}clips."
                    f"{request.output_format}"
                )
                output_path = str(Path(self._output_dir) / filename)

            if request.mode == "auto":
                strategy = self._detect_strategy(request.clips)
            elif request.mode == "concat":
                strategy = "concat"
            else:
                strategy = "reencode"

            logger.info(
                "Merging %s clips using %s strategy",
                len(request.clips),
                strategy,
            )

            if strategy == "concat":
                self._merge_concat(request.clips, output_path, request)
            else:
                self._merge_reencode(request.clips, output_path, request)

            if not os.path.exists(output_path):
                raise RuntimeError("Output file was not created")

            file_size = os.path.getsize(output_path)
            duration = self._get_duration(output_path)

            result.output_path = output_path
            result.file_size_bytes = file_size
            result.file_size_human = self._format_bytes(file_size)
            result.total_duration_seconds = duration
            result.success = True

            logger.info(
                "Merge complete: %s (%s, %.1fs)",
                output_path,
                result.file_size_human,
                duration,
            )

        except Exception as e:
            result.success = False
            result.error = str(e)
            logger.error("Merge failed: %s", e)

        return result

    def _validate_clips(self, clips: list[str]) -> None:
        """Validate clip files exist and are valid."""
        if len(clips) < 2:
            raise ValueError("At least 2 clips required for merging")
        if len(clips) > 20:
            raise ValueError("Maximum 20 clips can be merged at once")

        for i, clip in enumerate(clips):
            if not os.path.exists(clip):
                raise FileNotFoundError(f"Clip {i + 1} not found: {clip}")
            if os.path.getsize(clip) == 0:
                raise ValueError(f"Clip {i + 1} is empty: {clip}")

    def _detect_strategy(self, clips: list[str]) -> str:
        """Detect whether concat (fast) or reencode is needed.

        Uses concat if all clips have the same codec, resolution, and fps.
        Otherwise re-encodes.
        """
        try:
            codec_info = []
            for clip in clips:
                info = self._get_stream_info(clip)
                codec_info.append(info)

            if len(codec_info) < 2:
                return "concat"

            first = codec_info[0]
            for info in codec_info[1:]:
                if (
                    info.get("codec") != first.get("codec")
                    or info.get("width") != first.get("width")
                    or info.get("height") != first.get("height")
                ):
                    logger.info(
                        "Mixed codecs/resolutions detected, using re-encode strategy"
                    )
                    return "reencode"

            return "concat"
        except Exception:
            return "reencode"

    def _get_stream_info(self, file_path: str) -> dict:
        """Get video stream info using ffprobe."""
        cmd = [
            self._ffprobe,
            "-v",
            "quiet",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name,width,height,r_frame_rate",
            "-of",
            "json",
            file_path,
        ]
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0 or not result.stdout.strip():
            return {}
        try:
            data = json.loads(result.stdout)
            streams = data.get("streams", [])
            if streams:
                s = streams[0]
                return {
                    "codec": s.get("codec_name"),
                    "width": s.get("width"),
                    "height": s.get("height"),
                    "fps": s.get("r_frame_rate"),
                }
        except (json.JSONDecodeError, TypeError):
            pass
        return {}

    def _merge_concat(
        self,
        clips: list[str],
        output: str,
        request: MergeRequest,
    ) -> None:
        """Merge using concat demuxer (fast, no re-encode)."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False
        ) as f:
            for clip in clips:
                safe_path = clip.replace("'", "'\\''")
                f.write(f"file '{safe_path}'\n")
            concat_file = f.name

        try:
            cmd = [
                self._ffmpeg,
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                concat_file,
                "-c",
                "copy",
                output,
            ]

            logger.debug("Concat cmd: %s", " ".join(cmd))
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=600
            )

            if proc.returncode != 0:
                err_tail = (proc.stderr or "")[-500:]
                raise RuntimeError(f"FFmpeg concat failed: {err_tail}")
        finally:
            try:
                os.unlink(concat_file)
            except OSError:
                pass

    def _merge_reencode(
        self,
        clips: list[str],
        output: str,
        request: MergeRequest,
    ) -> None:
        """Merge using filter_complex (re-encode)."""
        cmd = [self._ffmpeg, "-y"]

        for clip in clips:
            cmd.extend(["-i", clip])

        n = len(clips)
        if request.resolution:
            w, h = request.resolution.split("x")
        else:
            w, h = "1920", "1080"

        filter_parts = []
        for i in range(n):
            filter_parts.append(
                f"[{i}:v]scale={w}:{h}:"
                "force_original_aspect_ratio=decrease,"
                f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,"
                f"setsar=1[v{i}]"
            )
            filter_parts.append(
                f"[{i}:a]aformat=sample_rates=44100:"
                f"channel_layouts=stereo[a{i}]"
            )

        v_inputs = "".join(f"[v{i}]" for i in range(n))
        a_inputs = "".join(f"[a{i}]" for i in range(n))
        filter_parts.append(
            f"{v_inputs}{a_inputs}concat=n={n}:v=1:a=1[outv][outa]"
        )

        filter_str = ";".join(filter_parts)

        cmd.extend(
            [
                "-filter_complex",
                filter_str,
                "-map",
                "[outv]",
                "-map",
                "[outa]",
                "-c:v",
                request.video_codec,
                "-c:a",
                request.audio_codec,
                "-b:a",
                request.audio_bitrate,
            ]
        )

        if request.video_bitrate:
            cmd.extend(["-b:v", request.video_bitrate])

        cmd.append(output)

        logger.debug("Reencode cmd: %s", " ".join(cmd))
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=1800
        )

        if proc.returncode != 0:
            err_tail = (proc.stderr or "")[-500:]
            raise RuntimeError(f"FFmpeg reencode failed: {err_tail}")

    def _get_duration(self, file_path: str) -> float:
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


_merger: ClipMerger | None = None


def get_clip_merger() -> ClipMerger:
    """Return the global ClipMerger instance."""
    global _merger
    if _merger is None:
        try:
            from src.env_loader import load_project_dotenv
            load_project_dotenv()
        except Exception:  # noqa: S110
            pass
        ffdir = os.environ.get("GID_FFMPEG_LOCATION", "").strip() or None
        if ffdir:
            p = Path(ffdir)
            _merger = ClipMerger(
                ffmpeg_path=str(p / "ffmpeg"),
                ffprobe_path=str(p / "ffprobe"),
            )
        else:
            _merger = ClipMerger()
    return _merger


def reset_clip_merger() -> None:
    """Reset the global merger (for tests)."""
    global _merger
    _merger = None
