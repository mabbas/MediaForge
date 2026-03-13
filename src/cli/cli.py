"""GrabItDown CLI — main command group and commands.

Entry point for both `gid` and `grabitdown` commands.
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path
from typing import List, Union

import click
from rich import box
from rich.panel import Panel
from rich.table import Table

from src import __version__
from src.cli.console import (
    console,
    create_download_progress,
    format_duration,
    format_size,
    print_banner,
    print_error,
    print_info,
    print_playlist_table,
    print_providers_table,
    print_stats_table,
    print_success,
    print_video_info_table,
    print_warning,
    print_formats_table,
    _bool_icon,
)


# ── Main Group ───────────────────────────────────────────────────────────────

@click.group(invoke_without_command=True)
@click.version_option(
    version=__version__,
    prog_name="GrabItDown",
    message="%(prog)s v%(version)s",
)
@click.option(
    "--debug",
    is_flag=True,
    default=False,
    help="Enable debug logging",
)
@click.pass_context
def main(ctx: click.Context, debug: bool) -> None:
    """GrabItDown — Production-grade media downloader.

    Download videos, audio, and playlists from YouTube
    and other sites. Type 'gid --help' for all commands.
    """
    log_level = logging.DEBUG if debug else logging.WARNING
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    ctx.ensure_object(dict)
    ctx.obj["debug"] = debug

    if ctx.invoked_subcommand is None:
        print_banner()
        console.print("\nRun [bold]gid --help[/bold] for all commands.\n")


# ── Download Command ─────────────────────────────────────────────────────────

@main.command()
@click.argument("url")
@click.option(
    "-m",
    "--mode",
    type=click.Choice(["video", "audio"], case_sensitive=False),
    default="video",
    help="Download mode",
)
@click.option(
    "-q",
    "--quality",
    type=click.Choice(
        [
            "2160p",
            "1440p",
            "1080p",
            "720p",
            "480p",
            "360p",
            "best",
            "worst",
        ],
        case_sensitive=False,
    ),
    default="1080p",
    help="Video quality",
)
@click.option(
    "-f",
    "--format",
    "output_format",
    type=str,
    default=None,
    help="Output format (mp4/mkv/webm for video, mp3/m4a/opus/flac for audio)",
)
@click.option(
    "-o",
    "--output",
    type=click.Path(),
    default=None,
    help="Output directory",
)
@click.option(
    "--audio-bitrate",
    type=str,
    default="192k",
    help="Audio bitrate (e.g., 320k, 192k)",
)
@click.option(
    "--embed-subs",
    is_flag=True,
    default=False,
    help="Embed subtitles in video",
)
@click.option(
    "--sub-langs",
    type=str,
    default=None,
    help="Subtitle languages (comma-separated, e.g., en,ur)",
)
@click.option(
    "--priority",
    type=click.Choice(["high", "normal", "low"]),
    default="normal",
    help="Download priority",
)
@click.option(
    "--template",
    type=str,
    default=None,
    help="Filename template (e.g., '{title} [{quality}].{ext}')",
)
@click.pass_context
def download(
    ctx: click.Context,
    url: str,
    mode: str,
    quality: str,
    output_format: str | None,
    output: str | None,
    audio_bitrate: str,
    embed_subs: bool,
    sub_langs: str | None,
    priority: str,
    template: str | None,
) -> None:
    """Download a video or audio from a URL.

    Examples:

      gid download https://youtube.com/watch?v=xxx

      gid download URL -m audio -f mp3

      gid download URL -q 720p -o ./videos

      gid download URL --embed-subs --sub-langs en,ur
    """
    from src.exceptions import (
        FeatureDisabledError,
        GrabItDownError,
        LimitExceededError,
    )
    from src.grabitdown import GrabItDown

    app = None
    try:
        app = GrabItDown()
        app.start()

        print_info("Resolving URL...")

        # Optional quick info (title, duration) before submit
        try:
            quick_info = app.get_info(url)
            title = quick_info.get("title", "Unknown")
            duration = format_duration(quick_info.get("duration_seconds"))
            print_info(f"Title: [bold]{title}[/bold]")
            print_info(f"Duration: {duration}")
        except Exception:
            pass

        job = app.download(
            url=url,
            mode=mode,
            quality=quality,
            output_dir=output,
            priority=priority,
        )

        print_success(f"Download queued: {job.job_id[:8]}")

        _wait_for_job(app, job.job_id, mode)

    except FeatureDisabledError as e:
        print_error(str(e))
        sys.exit(1)
    except LimitExceededError as e:
        print_error(str(e))
        sys.exit(1)
    except GrabItDownError as e:
        print_error(f"Download failed: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print_warning("\nDownload cancelled by user")
        sys.exit(130)
    except Exception as e:
        if ctx.obj.get("debug"):
            console.print_exception()
        else:
            print_error(f"Unexpected error: {e}")
        sys.exit(1)
    finally:
        if app:
            app.shutdown(wait=False)


# ── Playlist Command ─────────────────────────────────────────────────────────

@main.command()
@click.argument("url")
@click.option(
    "-m",
    "--mode",
    type=click.Choice(["video", "audio"], case_sensitive=False),
    default="video",
    help="Download mode",
)
@click.option(
    "-q",
    "--quality",
    type=click.Choice(
        [
            "2160p",
            "1440p",
            "1080p",
            "720p",
            "480p",
            "360p",
            "best",
            "worst",
        ],
        case_sensitive=False,
    ),
    default="1080p",
    help="Video quality",
)
@click.option(
    "-o",
    "--output",
    type=click.Path(),
    default=None,
    help="Output directory",
)
@click.option(
    "--items",
    type=str,
    default="all",
    help="Items to download (all, or comma-separated: 1,3,5-8)",
)
@click.option(
    "--concurrency",
    type=int,
    default=3,
    help="Concurrent downloads (1-5)",
)
@click.option(
    "--info-only",
    is_flag=True,
    default=False,
    help="Show playlist info without downloading",
)
@click.pass_context
def playlist(
    ctx: click.Context,
    url: str,
    mode: str,
    quality: str,
    output: str | None,
    items: str,
    concurrency: int,
    info_only: bool,
) -> None:
    """Download a playlist.

    Examples:

      gid playlist https://youtube.com/playlist?list=xxx

      gid playlist URL -m audio --items 1,3,5-8

      gid playlist URL --info-only

      gid playlist URL --concurrency 5
    """
    from src.exceptions import GrabItDownError
    from src.grabitdown import GrabItDown
    from src.models.enums import ProviderType

    app = None
    try:
        app = GrabItDown()
        app.start()

        if info_only:
            print_info("Fetching playlist info...")
            provider = app._registry.detect_provider(url)

            if provider.provider_type == ProviderType.YOUTUBE:
                from src.providers.youtube.playlist import YouTubePlaylistHandler

                handler = YouTubePlaylistHandler(provider)
                pl_info = handler.get_playlist_info(url)
                # Build dict for table: title, item_count, channel_name, items
                pl_dict = pl_info.model_dump()
                pl_dict["item_count"] = len(pl_info.items)
                print_playlist_table(pl_dict)
            else:
                print_error(f"{provider.name} doesn't support playlists")
            return

        parsed_items = _parse_items(items)

        if not (1 <= concurrency <= 5):
            print_error("Concurrency must be between 1 and 5")
            sys.exit(1)

        print_info(
            f"Fetching playlist and queueing downloads (concurrency: {concurrency})..."
        )

        _, jobs = app.download_playlist(
            url=url,
            mode=mode,
            quality=quality,
            output_dir=output,
            items=parsed_items,
            concurrency=concurrency,
        )

        print_success(f"Playlist queued: {len(jobs)} videos")

        _wait_for_playlist(app, jobs)

    except GrabItDownError as e:
        print_error(f"Playlist download failed: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print_warning("\nPlaylist download cancelled")
        sys.exit(130)
    except Exception as e:
        if ctx.obj.get("debug"):
            console.print_exception()
        else:
            print_error(f"Unexpected error: {e}")
        sys.exit(1)
    finally:
        if app:
            app.shutdown(wait=False)


# ── Batch Command ───────────────────────────────────────────────────────────

@main.command()
@click.argument("file", type=click.Path(exists=True))
@click.option(
    "-m",
    "--mode",
    type=click.Choice(["video", "audio"], case_sensitive=False),
    default="video",
    help="Download mode",
)
@click.option(
    "-q",
    "--quality",
    type=click.Choice(
        [
            "2160p",
            "1440p",
            "1080p",
            "720p",
            "480p",
            "360p",
            "best",
            "worst",
        ],
        case_sensitive=False,
    ),
    default="1080p",
    help="Video quality",
)
@click.option(
    "-o",
    "--output",
    type=click.Path(),
    default=None,
    help="Output directory",
)
@click.pass_context
def batch(
    ctx: click.Context,
    file: str,
    mode: str,
    quality: str,
    output: str | None,
) -> None:
    """Download multiple URLs from a text file.

    The file should contain one URL per line.
    Empty lines and lines starting with # are skipped.

    Examples:

      gid batch urls.txt

      gid batch urls.txt -m audio -q best
    """
    from src.exceptions import GrabItDownError
    from src.grabitdown import GrabItDown

    app = None
    try:
        urls = _read_url_file(file)
        if not urls:
            print_error("No valid URLs found in file")
            sys.exit(1)

        print_info(f"Found {len(urls)} URLs in {file}")

        app = GrabItDown()
        app.start()

        jobs = app.download_batch(
            urls=urls,
            mode=mode,
            quality=quality,
            output_dir=output,
        )

        print_success(f"Batch queued: {len(jobs)} downloads")

        _wait_for_playlist(app, jobs)

    except GrabItDownError as e:
        print_error(f"Batch download failed: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print_warning("\nBatch download cancelled")
        sys.exit(130)
    except Exception as e:
        if ctx.obj.get("debug"):
            console.print_exception()
        else:
            print_error(f"Unexpected error: {e}")
        sys.exit(1)
    finally:
        if app:
            app.shutdown(wait=False)


# ── Info Command ─────────────────────────────────────────────────────────────


@main.command()
@click.argument("url")
@click.option(
    "--formats",
    "show_formats",
    is_flag=True,
    default=False,
    help="Show available formats",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help="Output as JSON",
)
@click.pass_context
def info(ctx: click.Context, url: str, show_formats: bool, as_json: bool) -> None:
    """Get media information without downloading."""
    import json as json_module

    from src.grabitdown import GrabItDown

    app = None
    try:
        app = GrabItDown()

        print_info("Fetching media info...")
        info_data = app.get_info(url)

        if as_json:
            console.print_json(json_module.dumps(info_data, indent=2, default=str))
        else:
            print_video_info_table(info_data)

            if show_formats:
                formats_data = info_data.get("formats", [])
                if formats_data:
                    print_formats_table(formats_data)
                else:
                    print_warning("No formats found")

    except Exception as exc:  # pragma: no cover - CLI error path
        if ctx.obj.get("debug"):
            console.print_exception()
        else:
            print_error(f"Failed to get info: {exc}")
        sys.exit(1)
    finally:
        if app:
            app.shutdown(wait=False)


# ── Formats Command (shortcut) ───────────────────────────────────────────────


@main.command()
@click.argument("url")
@click.option(
    "--video-only",
    is_flag=True,
    default=False,
    help="Show video formats only",
)
@click.option(
    "--audio-only",
    is_flag=True,
    default=False,
    help="Show audio formats only",
)
@click.pass_context
def formats(ctx: click.Context, url: str, video_only: bool, audio_only: bool) -> None:
    """List available download formats for a URL."""
    from src.grabitdown import GrabItDown

    app = None
    try:
        app = GrabItDown()

        print_info("Fetching formats...")
        formats_data = app.get_formats(url)

        if video_only:
            formats_data = [f for f in formats_data if f.get("has_video")]
        elif audio_only:
            formats_data = [
                f for f in formats_data if f.get("has_audio") and not f.get("has_video")
            ]

        if formats_data:
            print_formats_table(formats_data)
            print_info(f"Total: {len(formats_data)} formats")
        else:
            print_warning("No formats found")

    except Exception as exc:  # pragma: no cover - CLI error path
        if ctx.obj.get("debug"):
            console.print_exception()
        else:
            print_error(f"Failed to get formats: {exc}")
        sys.exit(1)
    finally:
        if app:
            app.shutdown(wait=False)


# ── Transcript Command ───────────────────────────────────────────────────────


@main.command()
@click.argument("url")
@click.option(
    "-l",
    "--lang",
    type=str,
    default="en",
    help="Language code (e.g., en, ur)",
)
@click.option(
    "-f",
    "--format",
    "output_format",
    type=click.Choice(["srt", "vtt", "txt", "json"], case_sensitive=False),
    default="srt",
    help="Output format",
)
@click.option(
    "-o",
    "--output",
    type=click.Path(),
    default=None,
    help="Output file path",
)
@click.option(
    "--whisper",
    is_flag=True,
    default=False,
    help="Use Whisper for transcription (not yet implemented)",
)
@click.option(
    "--list-langs",
    is_flag=True,
    default=False,
    help="List available subtitle languages",
)
@click.pass_context
def transcript(
    ctx: click.Context,
    url: str,
    lang: str,
    output_format: str,
    output: str | None,
    whisper: bool,
    list_langs: bool,
) -> None:
    """Extract transcript/subtitles from a video."""
    import yt_dlp

    from src.grabitdown import GrabItDown

    app = None
    try:
        app = GrabItDown()

        if list_langs:
            print_info("Fetching available languages...")
            info_data = app.get_info(url)
            subs = info_data.get("subtitles_available", {})
            if subs:
                table = Table(
                    title="Available Subtitle Languages",
                    box=box.ROUNDED,
                )
                table.add_column("Language Code")
                table.add_column("Formats")
                for code, fmts in sorted(subs.items()):
                    table.add_row(code, ", ".join(fmts))
                console.print(table)
                print_info(f"Total: {len(subs)} languages")
            else:
                print_warning(
                    "No subtitles available. Try --whisper for AI transcription."
                )
            return

        if whisper:
            print_warning(
                "Whisper transcription is not yet implemented. Coming in a future release."
            )
            print_info("For now, use YouTube's subtitles: gid transcript URL -l en")
            return

        print_info(f"Extracting {lang} subtitles ({output_format})...")

        ydl_opts: dict = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": [lang],
            "subtitlesformat": output_format,
        }

        if output:
            out_path = output
        else:
            out_path = f"%(title)s.{lang}.{output_format}"

        ydl_opts["outtmpl"] = {"default": out_path}

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

            all_subs: dict = {}
            all_subs.update(info.get("subtitles", {}))
            all_subs.update(info.get("automatic_captions", {}))

            if lang not in all_subs:
                available = list(all_subs.keys())[:10]
                print_error(f"Language '{lang}' not available.")
                if available:
                    print_info(f"Available: {', '.join(available)}")
                print_info("Try --whisper for AI transcription")
                sys.exit(1)

            ydl.download([url])
            print_success(f"Transcript saved ({lang}, {output_format})")

    except Exception as exc:  # pragma: no cover - CLI error path
        if ctx.obj.get("debug"):
            console.print_exception()
        else:
            print_error(f"Transcript extraction failed: {exc}")
        sys.exit(1)
    finally:
        if app:
            app.shutdown(wait=False)


# ── Providers Command ────────────────────────────────────────────────────────


@main.command()
@click.pass_context
def providers(ctx: click.Context) -> None:
    """List registered download providers."""
    from src.grabitdown import GrabItDown

    app = None
    try:
        app = GrabItDown()
        providers_data = app.list_providers()
        print_providers_table(providers_data)
    except Exception as exc:  # pragma: no cover - CLI error path
        print_error(f"Failed to list providers: {exc}")
        sys.exit(1)
    finally:
        if app:
            app.shutdown(wait=False)


# ── Status Command ───────────────────────────────────────────────────────────


@main.command()
@click.pass_context
def status(ctx: click.Context) -> None:
    """Show download engine status."""
    from src.grabitdown import GrabItDown

    app = None
    try:
        app = GrabItDown()

        stats = app.get_stats()
        print_stats_table(stats)

        disk = app.get_disk_stats()
        if "error" not in disk:
            console.print()
            table = Table(
                title="Disk Space",
                box=box.ROUNDED,
                show_header=False,
                title_style="bold cyan",
            )
            table.add_column("Metric", style="bold")
            table.add_column("Value")
            table.add_row("Free", f"{disk['free_gb']} GB")
            table.add_row("Used", f"{disk['used_gb']} GB ({disk['usage_percent']}%)")
            table.add_row("Total", f"{disk['total_gb']} GB")
            table.add_row("Download Dir", disk.get("download_dir", "N/A"))
            console.print(table)

        bw = app.get_bandwidth_limit()
        if bw > 0:
            print_info(f"Bandwidth limit: {format_size(bw)}/s")
        else:
            print_info("Bandwidth: Unlimited")

    except Exception as exc:  # pragma: no cover - CLI error path
        print_error(f"Failed to get status: {exc}")
        sys.exit(1)
    finally:
        if app:
            app.shutdown(wait=False)


# ── Config Command ───────────────────────────────────────────────────────────


@main.command()
@click.option(
    "--show",
    "show_config",
    is_flag=True,
    default=True,
    help="Show current configuration",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help="Output as JSON",
)
@click.pass_context
def config(ctx: click.Context, show_config: bool, as_json: bool) -> None:
    """Show current configuration."""
    import json as json_module

    from src.config import get_settings

    try:
        settings = get_settings()

        if as_json:
            settings_dict = {
                "app": {
                    "name": settings.app.name,
                    "version": settings.app.version,
                    "environment": settings.app.environment,
                    "debug": settings.app.debug,
                    "log_level": settings.app.log_level,
                },
                "download": {
                    "output_directory": settings.download.output_directory,
                    "max_concurrent_downloads": settings.download.max_concurrent_downloads,
                    "absolute_max_concurrent": settings.download.absolute_max_concurrent,
                    "queue_max_size": settings.download.queue_max_size,
                    "retry_max_attempts": settings.download.retry_max_attempts,
                    "max_file_size_mb": settings.download.max_file_size_mb,
                    "min_disk_space_mb": settings.download.min_disk_space_mb,
                },
                "video": {
                    "default_quality": settings.video.default_quality,
                    "preferred_format": settings.video.preferred_format,
                    "embed_subtitles": settings.video.embed_subtitles,
                },
                "audio": {
                    "default_format": settings.audio.default_format,
                    "default_bitrate": settings.audio.default_bitrate,
                },
                "transcript": {
                    "default_languages": settings.transcript.default_languages,
                    "whisper_model": settings.transcript.whisper_model,
                },
                "resume": {
                    "enabled": settings.resume.enabled,
                    "max_auto_retries": settings.resume.max_auto_retries,
                    "scan_incomplete_on_start": settings.resume.scan_incomplete_on_start,
                },
            }
            console.print_json(json_module.dumps(settings_dict, indent=2))
        else:
            table = Table(
                title="GrabItDown Configuration",
                box=box.ROUNDED,
                title_style="bold cyan",
            )
            table.add_column("Setting", style="bold")
            table.add_column("Value")

            table.add_row("[bold]App[/bold]", "")
            table.add_row("  Name", settings.app.name)
            table.add_row("  Version", settings.app.version)
            table.add_row("  Environment", settings.app.environment)

            table.add_row("[bold]Download[/bold]", "")
            table.add_row("  Output Directory", settings.download.output_directory)
            table.add_row(
                "  Max Concurrent", str(settings.download.max_concurrent_downloads)
            )
            table.add_row("  Queue Max Size", str(settings.download.queue_max_size))
            table.add_row("  Max Retries", str(settings.download.retry_max_attempts))
            table.add_row(
                "  Max File Size", f"{settings.download.max_file_size_mb} MB"
            )
            table.add_row(
                "  Min Disk Space", f"{settings.download.min_disk_space_mb} MB"
            )

            table.add_row("[bold]Video[/bold]", "")
            table.add_row("  Default Quality", settings.video.default_quality)
            table.add_row("  Preferred Format", settings.video.preferred_format)
            table.add_row("  Embed Subtitles", str(settings.video.embed_subtitles))

            table.add_row("[bold]Audio[/bold]", "")
            table.add_row("  Default Format", settings.audio.default_format)
            table.add_row("  Default Bitrate", settings.audio.default_bitrate)

            table.add_row("[bold]Transcript[/bold]", "")
            table.add_row(
                "  Languages", ", ".join(settings.transcript.default_languages)
            )
            table.add_row("  Whisper Model", settings.transcript.whisper_model)

            table.add_row("[bold]Resume[/bold]", "")
            table.add_row("  Enabled", str(settings.resume.enabled))
            table.add_row(
                "  Max Auto Retries", str(settings.resume.max_auto_retries)
            )

            table.add_row("[bold]Providers[/bold]", "")
            table.add_row("  YouTube", str(settings.providers.youtube.enabled))
            table.add_row("  Facebook", str(settings.providers.facebook.enabled))
            table.add_row("  Mega", str(settings.providers.mega.enabled))
            table.add_row("  Dropbox", str(settings.providers.dropbox.enabled))
            table.add_row("  Generic", str(settings.providers.generic.enabled))

            console.print(table)

    except Exception as exc:  # pragma: no cover - CLI error path
        print_error(f"Failed to load config: {exc}")
        sys.exit(1)


# ── Recovery Command ─────────────────────────────────────────────────────────


@main.command()
@click.option(
    "--cleanup",
    is_flag=True,
    default=False,
    help="Clean up stale partial downloads",
)
@click.option(
    "--cleanup-all",
    is_flag=True,
    default=False,
    help="Remove ALL partial downloads",
)
@click.pass_context
def recovery(ctx: click.Context, cleanup: bool, cleanup_all: bool) -> None:
    """Manage incomplete/partial downloads."""
    from src.grabitdown import GrabItDown

    app = None
    try:
        app = GrabItDown()

        if cleanup_all:
            recoverable = app.get_recoverable_downloads()
            if not recoverable:
                print_info("No partial downloads found")
                return

            if not click.confirm(
                f"Remove all {len(recoverable)} partial downloads?", default=False
            ):
                print_info("Cancelled")
                return

            for dl in recoverable:
                app._recovery_manager.cleanup_download(dl.job_id)
            print_success(f"Removed {len(recoverable)} partial downloads")
            return

        if cleanup:
            count = app.cleanup_stale()
            print_success(f"Cleaned up {count} stale downloads")
            return

        recoverable = app.get_recoverable_downloads()
        if not recoverable:
            print_info("No incomplete downloads found")
            return

        table = Table(
            title="Incomplete Downloads",
            box=box.ROUNDED,
            title_style="bold cyan",
        )
        table.add_column("Job ID", style="dim")
        table.add_column("URL")
        table.add_column("Provider")
        table.add_column("Progress", justify="right")
        table.add_column("Size", justify="right")
        table.add_column("Status")

        for dl in recoverable:
            status = (
                "[green]Resumable[/green]" if dl.can_resume else "[red]Damaged[/red]"
            )

            short_url = (
                dl.metadata.url[:50] + "..." if len(dl.metadata.url) > 50 else dl.metadata.url
            )

            table.add_row(
                dl.job_id[:8] + "...",
                short_url,
                dl.metadata.provider,
                f"{dl.percent_complete:.1f}%",
                format_size(dl.part_file_size),
                status,
            )

        console.print(table)
        print_info(f"Found {len(recoverable)} incomplete downloads")

        resumable = [d for d in recoverable if d.can_resume]
        if resumable:
            print_info(
                f"{len(resumable)} can be resumed. Resume support coming in a future release."
            )

    except Exception as exc:  # pragma: no cover - CLI error path
        if ctx.obj.get("debug"):
            console.print_exception()
        else:
            print_error(f"Recovery failed: {exc}")
        sys.exit(1)
    finally:
        if app:
            app.shutdown(wait=False)


# ── Features Command ─────────────────────────────────────────────────────────


@main.command()
@click.option(
    "--tier",
    type=click.Choice(["basic", "pro", "platinum"]),
    default=None,
    help="Show features for specific tier",
)
@click.pass_context
def features(ctx: click.Context, tier: str | None) -> None:
    """Show available features and tier limits."""
    from src.features.feature_flags import load_feature_flags
    from src.features.feature_gate import FeatureGate

    try:
        flags = load_feature_flags()
        gate = FeatureGate(flags)

        print_info(f"Mode: {flags.mode} (tier: {flags.personal_mode_tier})")
        console.print()

        if tier:
            tiers_to_show = [tier]
        else:
            tiers_to_show = list(flags.tiers.keys())

        for t in tiers_to_show:
            tier_config = flags.tiers[t]
            tf = tier_config.features

            table = Table(
                title=f"{tier_config.display_name} Tier (${tier_config.price_monthly}/mo)",
                box=box.ROUNDED,
                title_style="bold cyan",
            )
            table.add_column("Feature", style="bold")
            table.add_column("Enabled")
            table.add_column("Limit")

            table.add_row(
                "Video Download",
                _bool_icon(tf.video_download.enabled),
                (
                    f"Max {tf.video_download.max_quality}, {tf.video_download.daily_limit}/day"
                    if tf.video_download.daily_limit != -1
                    else f"Max {tf.video_download.max_quality}, Unlimited"
                ),
            )
            table.add_row(
                "Audio Download",
                _bool_icon(tf.audio_download.enabled),
                ", ".join(tf.audio_download.formats),
            )
            table.add_row(
                "Playlist Download",
                _bool_icon(tf.playlist_download.enabled),
                (
                    f"Max {tf.playlist_download.max_playlist_size} items"
                    if tf.playlist_download.max_playlist_size != -1
                    else "Unlimited"
                ),
            )
            table.add_row(
                "Batch Download",
                _bool_icon(tf.batch_download.enabled),
                (
                    f"Max {tf.batch_download.max_urls} URLs"
                    if tf.batch_download.max_urls != -1
                    else "Unlimited"
                ),
            )
            table.add_row(
                "Concurrent Downloads",
                _bool_icon(tf.concurrent_downloads.enabled),
                f"Max {tf.concurrent_downloads.max_value}",
            )
            table.add_row(
                "Multi-Connection",
                _bool_icon(tf.multi_connection.enabled),
                f"Max {tf.multi_connection.max_connections} connections",
            )
            table.add_row(
                "YouTube Subtitles",
                _bool_icon(tf.transcript_youtube.enabled),
                f"{tf.transcript_youtube.languages}",
            )
            table.add_row(
                "Whisper Transcription",
                _bool_icon(tf.transcript_whisper.enabled),
                (
                    f"Model: {tf.transcript_whisper.model}"
                    if tf.transcript_whisper.enabled
                    else "N/A"
                ),
            )
            table.add_row(
                "API Access",
                _bool_icon(tf.api_access.enabled),
                (
                    f"{tf.api_access.rate_limit_per_hour}/hr"
                    if tf.api_access.enabled
                    else "N/A"
                ),
            )
            table.add_row(
                "Resume Download",
                _bool_icon(tf.resume_download.enabled),
                "",
            )
            table.add_row(
                "Download History",
                _bool_icon(tf.download_history.enabled),
                (
                    f"{tf.download_history.retention_days} days"
                    if tf.download_history.retention_days != -1
                    else "Unlimited"
                ),
            )
            table.add_row(
                "Priority Queue",
                _bool_icon(tf.priority_queue.enabled),
                "",
            )

            table.add_row("[bold]Providers[/bold]", "", "")
            table.add_row("  YouTube", _bool_icon(tf.providers.youtube), "")
            table.add_row("  Facebook", _bool_icon(tf.providers.facebook), "")
            table.add_row("  Mega", _bool_icon(tf.providers.mega), "")
            table.add_row("  Dropbox", _bool_icon(tf.providers.dropbox), "")
            table.add_row("  Generic (1000+ sites)", _bool_icon(tf.providers.generic), "")

            console.print(table)
            console.print()

    except Exception as exc:  # pragma: no cover - CLI error path
        print_error(f"Failed to load features: {exc}")
        sys.exit(1)


# ── Check Command ────────────────────────────────────────────────────────────


@main.command()
@click.pass_context
def check(ctx: click.Context) -> None:
    """Run system health checks."""
    import shutil
    import subprocess

    from src.config import get_settings
    from src.core.provider_factory import create_provider_registry
    from src.download.disk_monitor import DiskMonitor
    from src.features.feature_flags import load_feature_flags
    from src.features.feature_gate import FeatureGate

    print_banner()
    console.print()

    errors: list[str] = []

    console.print("[bold]Configuration...[/bold]")
    try:
        settings = get_settings()
        print_success(f"{settings.app.name} v{settings.app.version}")
    except Exception as exc:
        print_error(f"Config: {exc}")
        errors.append("config")

    console.print("[bold]Feature Flags...[/bold]")
    try:
        flags = load_feature_flags()
        gate = FeatureGate(flags)
        tier_count = len(flags.tiers)
        features = gate.list_all_features()
        enabled = sum(1 for v in features.values() if v)
        print_success(f"Mode: {flags.mode}, {tier_count} tiers, {enabled}/{len(features)} enabled")
    except Exception as exc:
        print_error(f"Feature flags: {exc}")
        errors.append("features")

    console.print("[bold]Providers...[/bold]")
    try:
        registry = create_provider_registry()
        providers = registry.list_providers()
        names = [p["name"] for p in providers]
        print_success(f"{len(providers)} providers: {', '.join(names)}")
    except Exception as exc:
        print_error(f"Providers: {exc}")
        errors.append("providers")

    console.print("[bold]ffmpeg...[/bold]")
    import os
    try:
        from src.env_loader import load_project_dotenv
        load_project_dotenv()
    except Exception:
        pass
    ffmpeg_path = shutil.which("ffmpeg")
    if not ffmpeg_path:
        ffdir = os.environ.get("GID_FFMPEG_LOCATION", "").strip() or None
        if ffdir:
            p = Path(ffdir.replace("\\", "/").expanduser()).resolve()
            if p.is_dir():
                exe = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
                candidate = p / exe
                if candidate.is_file():
                    ffmpeg_path = str(candidate)
    if ffmpeg_path:
        try:
            result = subprocess.run(
                [ffmpeg_path, "-version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            version_line = result.stdout.split("\n")[0]
            print_success(f"Found: {version_line[:60]}")
        except Exception:
            print_success(f"Found at: {ffmpeg_path}")
    else:
        print_warning(
            "ffmpeg not found. Set GID_FFMPEG_LOCATION in .env or add to PATH. "
            "Video merging and audio extraction may fail."
        )

    console.print("[bold]yt-dlp...[/bold]")
    try:
        import yt_dlp

        print_success(f"Version: {yt_dlp.version.__version__}")
    except Exception as exc:
        print_error(f"yt-dlp: {exc}")
        errors.append("yt-dlp")

    console.print("[bold]Disk Space...[/bold]")
    try:
        monitor = DiskMonitor(min_space_mb=settings.download.min_disk_space_mb)
        can_proceed, reason = monitor.check_before_download()
        if can_proceed:
            stats = monitor.get_stats()
            print_success(f"{stats['free_gb']} GB free ({stats['usage_percent']}% used)")
        else:
            print_warning(reason)
    except Exception as exc:
        print_error(f"Disk check: {exc}")

    console.print()
    if not errors:
        print_success("[bold]All checks passed![/bold] GrabItDown is ready.")
    else:
        print_error(
            f"[bold]{len(errors)} check(s) failed:[/bold] {', '.join(errors)}"
        )
    if errors:
        sys.exit(1)


# ── Resolve Command ────────────────────────────────────────────────────────


@main.command()
@click.argument("url")
@click.pass_context
def resolve(ctx: click.Context, url: str) -> None:
    """Detect provider and show URL info."""
    from src.exceptions import ProviderError
    from src.grabitdown import GrabItDown

    app = None
    try:
        app = GrabItDown()

        provider = app._registry.detect_provider(url)

        console.print(
            Panel(
                f"[bold]URL:[/bold] {url}\n"
                f"[bold]Provider:[/bold] [cyan]{provider.name}[/cyan]\n"
                f"[bold]Type:[/bold] {provider.provider_type.value}\n"
                f"[bold]Video:[/bold] {_bool_icon(provider.capabilities.supports_video)} "
                f"[bold]Audio:[/bold] {_bool_icon(provider.capabilities.supports_audio)} "
                f"[bold]Playlists:[/bold] {_bool_icon(provider.capabilities.supports_playlists)} "
                f"[bold]Subtitles:[/bold] {_bool_icon(provider.capabilities.supports_subtitles)}",
                title="URL Resolution",
                box=box.ROUNDED,
                style="cyan",
            )
        )

        try:
            print_info("Fetching media info...")
            info_data = app.get_info(url)
            title = info_data.get("title", "N/A")
            duration = format_duration(info_data.get("duration_seconds"))
            fmt_count = len(info_data.get("formats", []))
            console.print(
                f"  Title: [bold]{title}[/bold]\n"
                f"  Duration: {duration}\n"
                f"  Formats: {fmt_count}"
            )
        except Exception:
            print_warning(
                "Could not fetch detailed info. The URL may require authentication or may not be a media page."
            )

    except ProviderError as exc:
        print_error(str(exc))
        sys.exit(1)
    except Exception as exc:
        if ctx.obj.get("debug"):
            console.print_exception()
        else:
            print_error(f"Resolution failed: {exc}")
        sys.exit(1)
    finally:
        if app:
            app.shutdown(wait=False)


# ── History Command ────────────────────────────────────────────────────────


@main.command()
@click.option(
    "-n",
    "--limit",
    type=int,
    default=20,
    help="Number of entries to show",
)
@click.option(
    "--status",
    "status_filter",
    type=click.Choice(["completed", "failed", "cancelled", "all"], case_sensitive=False),
    default="all",
    help="Filter by status",
)
@click.option(
    "--clear",
    is_flag=True,
    default=False,
    help="Clear download history",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help="Output as JSON",
)
@click.pass_context
def history(
    ctx: click.Context,
    limit: int,
    status_filter: str,
    clear: bool,
    as_json: bool,
) -> None:
    """Show download history."""
    import json as json_module

    from src.grabitdown import GrabItDown
    from src.models.enums import DownloadStatus

    app = None
    try:
        app = GrabItDown()

        if clear:
            print_info(
                "Session history cleared. Note: persistent history requires database support (coming in API phase)."
            )
            return

        jobs = app.get_all_jobs()

        if not jobs:
            print_info("No download history in this session.")
            print_info("Start a download with: gid download <URL>")
            return

        terminal_statuses = {
            "completed": {DownloadStatus.COMPLETED},
            "failed": {DownloadStatus.FAILED},
            "cancelled": {DownloadStatus.CANCELLED},
            "all": set(DownloadStatus),
        }
        allowed = terminal_statuses.get(status_filter, set(DownloadStatus))
        filtered = [j for j in jobs if j.progress.status in allowed]
        filtered = filtered[:limit]

        if as_json:
            entries = []
            for job in filtered:
                entry = {
                    "job_id": job.job_id,
                    "url": job.request.url,
                    "status": job.progress.status.value,
                    "media_type": job.request.media_type.value,
                    "quality": job.request.quality.value,
                    "user_id": job.user_id,
                    "created_at": job.created_at.isoformat(),
                }
                if job.result:
                    entry["title"] = job.result.title
                    entry["file_path"] = job.result.file_path
                    entry["file_size_bytes"] = job.result.file_size_bytes
                    entry["error"] = job.result.error_message
                entries.append(entry)
            console.print_json(json_module.dumps(entries, indent=2, default=str))
            return

        table = Table(
            title=f"Download History ({len(filtered)} entries)",
            box=box.ROUNDED,
            title_style="bold cyan",
        )
        table.add_column("ID", style="dim", width=10)
        table.add_column("Title", max_width=40)
        table.add_column("Status")
        table.add_column("Type")
        table.add_column("Quality")
        table.add_column("Size", justify="right")
        table.add_column("Time", style="dim")

        for job in filtered:
            s = job.progress.status
            if s == DownloadStatus.COMPLETED:
                status_str = "[green]Completed[/green]"
            elif s == DownloadStatus.FAILED:
                status_str = "[red]Failed[/red]"
            elif s == DownloadStatus.CANCELLED:
                status_str = "[yellow]Cancelled[/yellow]"
            elif s == DownloadStatus.DOWNLOADING:
                status_str = "[blue]Downloading[/blue]"
            elif s == DownloadStatus.QUEUED:
                status_str = "[dim]Queued[/dim]"
            else:
                status_str = s.value

            title = "Unknown"
            size = ""
            if job.result:
                title = job.result.title or "Unknown"
                if job.result.file_size_bytes:
                    size = format_size(job.result.file_size_bytes)

            if len(title) > 40:
                title = title[:37] + "..."

            table.add_row(
                job.job_id[:8] + "..",
                title,
                status_str,
                job.request.media_type.value,
                job.request.quality.value,
                size,
                job.created_at.strftime("%H:%M:%S"),
            )

        console.print(table)

        completed = sum(1 for j in jobs if j.progress.status == DownloadStatus.COMPLETED)
        failed = sum(1 for j in jobs if j.progress.status == DownloadStatus.FAILED)
        print_info(f"Total: {len(jobs)} jobs ({completed} completed, {failed} failed)")

    except Exception as exc:
        if ctx.obj.get("debug"):
            console.print_exception()
        else:
            print_error(f"Failed to get history: {exc}")
        sys.exit(1)
    finally:
        if app:
            app.shutdown(wait=False)


# ── Helper Functions ────────────────────────────────────────────────────────

def _wait_for_job(app, job_id: str, mode: str) -> None:
    """Wait for a single download with progress bar."""
    from src.models.enums import DownloadStatus

    with create_download_progress() as progress:
        task = progress.add_task(f"Downloading ({mode})", total=100)

        while True:
            job = app.get_job(job_id)
            if job is None:
                break

            status = job.progress.status

            if status == DownloadStatus.COMPLETED:
                progress.update(task, completed=100)
                break
            elif status in (DownloadStatus.FAILED, DownloadStatus.CANCELLED):
                break

            percent = job.progress.percent
            progress.update(
                task,
                completed=percent,
                description=f"Downloading ({mode}) • {job.progress.speed_human}",
            )

            time.sleep(0.3)

    job = app.get_job(job_id)
    if job and job.result:
        if job.result.is_successful:
            print_success(f"Downloaded: {job.result.title}")
            if job.result.file_path:
                print_info(f"File: {job.result.file_path}")
            if job.result.file_size_bytes:
                print_info(f"Size: {job.result.file_size_human}")
        else:
            print_error(f"Failed: {job.result.error_message or 'Unknown error'}")


def _wait_for_playlist(app, jobs: list) -> None:
    """Wait for multiple downloads with progress."""
    from src.models.enums import DownloadStatus

    terminal_states = {
        DownloadStatus.COMPLETED,
        DownloadStatus.FAILED,
        DownloadStatus.CANCELLED,
    }

    total = len(jobs)

    with create_download_progress() as progress:
        task = progress.add_task(f"Playlist (0/{total})", total=total)

        while True:
            completed = 0
            failed = 0

            for job in jobs:
                current = app.get_job(job.job_id)
                if current and current.progress.status in terminal_states:
                    if current.progress.status == DownloadStatus.COMPLETED:
                        completed += 1
                    else:
                        failed += 1

            done = completed + failed
            progress.update(
                task,
                completed=done,
                description=f"Playlist ({done}/{total}) • {completed} ok, {failed} failed",
            )

            if done >= total:
                break

            time.sleep(0.5)

    print_success(
        f"Playlist complete: {completed} downloaded, {failed} failed out of {total}"
    )

    # Show failed downloads
    failed_jobs = [
        j
        for j in jobs
        if app.get_job(j.job_id)
        and app.get_job(j.job_id).progress.status == DownloadStatus.FAILED
    ]
    if failed_jobs:
        console.print()
        print_warning("Failed downloads:")
        for fj in failed_jobs:
            current = app.get_job(fj.job_id)
            error = ""
            if current and current.result:
                error = current.result.error_message or ""
            console.print(
                f"  [red]✗[/red] {fj.request.url[:60]} — {error[:40]}"
            )


def _parse_items(items_str: str) -> Union[str, List[int]]:
    """Parse playlist items string.

    'all' → 'all'
    '1,3,5-8' → [1, 3, 5, 6, 7, 8]
    """
    if items_str.lower() == "all":
        return "all"

    result: List[int] = []
    for part in items_str.split(","):
        part = part.strip()
        if "-" in part:
            start, end = part.split("-", 1)
            result.extend(range(int(start.strip()), int(end.strip()) + 1))
        else:
            result.append(int(part))
    return result


def _read_url_file(filepath: str) -> List[str]:
    """Read URLs from a text file.

    Skips empty lines and comments (#).
    """
    urls: List[str] = []
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                urls.append(line)
    return urls
