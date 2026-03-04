"""GrabItDown Rich console utilities — shared formatters and display helpers."""

from __future__ import annotations

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from rich.table import Table
from rich.text import Text

console = Console()
error_console = Console(stderr=True)


def print_banner() -> None:
    """Print GrabItDown banner."""
    from src import __version__

    banner = Text()
    banner.append("GrabItDown", style="bold cyan")
    banner.append(" v", style="dim")
    banner.append(__version__, style="dim")
    console.print(
        Panel(
            banner,
            subtitle="Production-grade media downloader",
            box=box.ROUNDED,
            style="cyan",
        )
    )


def print_success(message: str) -> None:
    """Print success message."""
    console.print(f"[green]✓[/green] {message}")


def print_error(message: str) -> None:
    """Print error message to stderr."""
    error_console.print(f"[red]✗[/red] {message}", style="red")


def print_warning(message: str) -> None:
    """Print warning message."""
    console.print(f"[yellow]![/yellow] {message}")


def print_info(message: str) -> None:
    """Print info message."""
    console.print(f"[blue]ℹ[/blue] {message}")


def format_size(bytes_val: int | None) -> str:
    """Format bytes to human readable."""
    if bytes_val is None or bytes_val == 0:
        return "Unknown"
    if bytes_val < 1024:
        return f"{bytes_val} B"
    if bytes_val < 1048576:
        return f"{bytes_val / 1024:.1f} KB"
    if bytes_val < 1073741824:
        return f"{bytes_val / 1048576:.2f} MB"
    return f"{bytes_val / 1073741824:.2f} GB"


def format_duration(seconds: int | None) -> str:
    """Format seconds to human readable."""
    if seconds is None:
        return "Unknown"
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60}s"
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h}h {m}m {s}s"


def create_download_progress() -> Progress:
    """Create a Rich Progress instance for download tracking."""
    return Progress(
        TextColumn("[bold blue]{task.description}"),
        BarColumn(bar_width=40),
        "[progress.percentage]{task.percentage:>3.1f}%",
        "•",
        DownloadColumn(),
        "•",
        TransferSpeedColumn(),
        "•",
        TimeRemainingColumn(),
        console=console,
        transient=False,
    )


def print_video_info_table(info: dict) -> None:
    """Display video info as a Rich table."""
    table = Table(
        title="Video Information",
        box=box.ROUNDED,
        show_header=False,
        title_style="bold cyan",
    )

    table.add_column("Field", style="bold")
    table.add_column("Value")

    table.add_row("Title", info.get("title", "N/A"))
    table.add_row("Provider", info.get("provider", "N/A"))
    table.add_row("ID", info.get("media_id", "N/A"))
    table.add_row("Duration", format_duration(info.get("duration_seconds")))
    table.add_row("Channel", info.get("channel_name", "N/A"))

    if info.get("view_count") is not None:
        table.add_row("Views", f"{info['view_count']:,}")

    if info.get("upload_date"):
        date_str = info["upload_date"]
        if len(date_str) == 8:
            date_str = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
        table.add_row("Upload Date", date_str)

    formats = info.get("formats", [])
    video_fmts = [f for f in formats if f.get("has_video")]
    audio_fmts = [
        f for f in formats if f.get("has_audio") and not f.get("has_video")
    ]
    table.add_row("Video Formats", str(len(video_fmts)))
    table.add_row("Audio Formats", str(len(audio_fmts)))

    subs = info.get("subtitles_available", {})
    if subs:
        langs = ", ".join(list(subs.keys())[:10])
        if len(subs) > 10:
            langs += f" (+{len(subs) - 10} more)"
        table.add_row("Subtitles", langs)

    console.print(table)


def print_formats_table(formats: list[dict]) -> None:
    """Display available formats as a Rich table."""
    table = Table(
        title="Available Formats",
        box=box.SIMPLE_HEAD,
        title_style="bold cyan",
    )

    table.add_column("ID", style="dim")
    table.add_column("Ext")
    table.add_column("Quality")
    table.add_column("Resolution")
    table.add_column("FPS", justify="right")
    table.add_column("Size", justify="right")
    table.add_column("Type")
    table.add_column("Codecs", style="dim")

    for fmt in formats:
        has_v = fmt.get("has_video", False)
        has_a = fmt.get("has_audio", False)

        if has_v and has_a:
            ftype = "[green]V+A[/green]"
        elif has_v:
            ftype = "[blue]Video[/blue]"
        elif has_a:
            ftype = "[yellow]Audio[/yellow]"
        else:
            ftype = "?"

        size = format_size(
            fmt.get("filesize_bytes") or fmt.get("filesize_approx_bytes")
        )

        codecs_parts = []
        if fmt.get("vcodec"):
            codecs_parts.append(str(fmt["vcodec"])[:12])
        if fmt.get("acodec"):
            codecs_parts.append(str(fmt["acodec"])[:12])
        codecs = " / ".join(codecs_parts)

        table.add_row(
            fmt.get("format_id", "?"),
            fmt.get("extension", "?"),
            fmt.get("quality", ""),
            fmt.get("resolution", ""),
            str(fmt.get("fps", "")) if fmt.get("fps") else "",
            size,
            ftype,
            codecs,
        )

    console.print(table)


def print_playlist_table(playlist: dict) -> None:
    """Display playlist info as a Rich table."""
    console.print(
        Panel(
            f"[bold]{playlist.get('title', 'N/A')}[/bold]\n"
            f"Items: {playlist.get('item_count', 0)} • "
            f"Channel: {playlist.get('channel_name', 'N/A')}",
            title="Playlist",
            box=box.ROUNDED,
            style="cyan",
        )
    )

    items = playlist.get("items", [])
    if items:
        table = Table(box=box.SIMPLE, show_header=True)
        table.add_column("#", style="dim", justify="right")
        table.add_column("Title")
        table.add_column("Duration", justify="right")
        table.add_column("Available")

        for item in items[:50]:
            avail = (
                "[green]✓[/green]"
                if item.get("is_available", True)
                else "[red]✗[/red]"
            )
            table.add_row(
                str(item.get("index", "")),
                (item.get("title", "N/A") or "")[:60],
                format_duration(item.get("duration_seconds")),
                avail,
            )

        if len(items) > 50:
            table.add_row(
                "",
                f"... and {len(items) - 50} more",
                "",
                "",
            )

        console.print(table)


def print_providers_table(providers: list[dict]) -> None:
    """Display registered providers."""
    table = Table(
        title="Registered Providers",
        box=box.ROUNDED,
        title_style="bold cyan",
    )

    table.add_column("Name")
    table.add_column("Type")
    table.add_column("Kind")
    table.add_column("Video")
    table.add_column("Audio")
    table.add_column("Playlists")
    table.add_column("Subtitles")

    for p in providers:
        caps = p.get("capabilities", {})
        table.add_row(
            p.get("name", "?"),
            p.get("type", "?"),
            p.get("kind", "media"),
            _bool_icon(caps.get("supports_video")),
            _bool_icon(caps.get("supports_audio")),
            _bool_icon(caps.get("supports_playlists")),
            _bool_icon(caps.get("supports_subtitles")),
        )

    console.print(table)


def print_stats_table(stats: dict) -> None:
    """Display engine statistics."""
    table = Table(
        title="GrabItDown Status",
        box=box.ROUNDED,
        show_header=False,
        title_style="bold cyan",
    )

    table.add_column("Metric", style="bold")
    table.add_column("Value")

    table.add_row("Active Downloads", str(stats.get("active", 0)))
    table.add_row("Max Concurrent", str(stats.get("max_concurrent", 0)))

    q = stats.get("queue", {})
    table.add_row(
        "Queue (High/Normal/Low)",
        f"{q.get('high', 0)} / {q.get('normal', 0)} / {q.get('low', 0)}",
    )
    table.add_row("Queue Total", str(q.get("total", 0)))

    table.add_row("Paused", str(stats.get("is_paused", False)))
    table.add_row("Total Jobs", str(stats.get("total_jobs", 0)))

    jobs_by_status = stats.get("jobs_by_status", {})
    if jobs_by_status:
        for status, count in jobs_by_status.items():
            table.add_row(f"  {status}", str(count))

    console.print(table)


def _bool_icon(val: bool | None) -> str:
    """Return Rich icon for boolean."""
    if val is True:
        return "[green]✓[/green]"
    if val is False:
        return "[red]✗[/red]"
    return "?"
