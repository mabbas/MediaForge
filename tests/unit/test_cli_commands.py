"""Tests for GrabItDown CLI commands using Click's CliRunner."""

from click.testing import CliRunner

from src.cli.cli import main


def test_main_no_args():
    """Running with no args shows banner."""
    runner = CliRunner()
    result = runner.invoke(main)
    assert result.exit_code == 0
    assert "GrabItDown" in result.output


def test_main_help():
    """--help shows usage info."""
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "download" in result.output
    assert "playlist" in result.output
    assert "batch" in result.output


def test_download_help():
    """download --help shows options."""
    runner = CliRunner()
    result = runner.invoke(main, ["download", "--help"])
    assert result.exit_code == 0
    assert "--mode" in result.output
    assert "--quality" in result.output
    assert "--output" in result.output


def test_playlist_help():
    """playlist --help shows options."""
    runner = CliRunner()
    result = runner.invoke(main, ["playlist", "--help"])
    assert result.exit_code == 0
    assert "--items" in result.output
    assert "--concurrency" in result.output
    assert "--info-only" in result.output


def test_batch_help():
    """batch --help shows options."""
    runner = CliRunner()
    result = runner.invoke(main, ["batch", "--help"])
    assert result.exit_code == 0
    assert "FILE" in result.output or "file" in result.output.lower()


def test_version():
    """--version shows version."""
    runner = CliRunner()
    result = runner.invoke(main, ["--version"])
    assert result.exit_code == 0
    assert "GrabItDown" in result.output


def test_info_help():
    """info --help shows options."""
    runner = CliRunner()
    result = runner.invoke(main, ["info", "--help"])
    assert result.exit_code == 0
    assert "--formats" in result.output
    assert "--json" in result.output


def test_formats_help():
    """formats --help shows options."""
    runner = CliRunner()
    result = runner.invoke(main, ["formats", "--help"])
    assert result.exit_code == 0
    assert "--video-only" in result.output
    assert "--audio-only" in result.output


def test_transcript_help():
    """transcript --help shows options."""
    runner = CliRunner()
    result = runner.invoke(main, ["transcript", "--help"])
    assert result.exit_code == 0
    assert "--lang" in result.output
    assert "--whisper" in result.output
    assert "--list-langs" in result.output


def test_providers_help():
    """providers --help shows description."""
    runner = CliRunner()
    result = runner.invoke(main, ["providers", "--help"])
    assert result.exit_code == 0


def test_status_help():
    """status --help shows description."""
    runner = CliRunner()
    result = runner.invoke(main, ["status", "--help"])
    assert result.exit_code == 0


def test_config_help():
    """config --help shows options."""
    runner = CliRunner()
    result = runner.invoke(main, ["config", "--help"])
    assert result.exit_code == 0
    assert "--json" in result.output


def test_recovery_help():
    """recovery --help shows options."""
    runner = CliRunner()
    result = runner.invoke(main, ["recovery", "--help"])
    assert result.exit_code == 0
    assert "--cleanup" in result.output


def test_features_help():
    """features --help shows options."""
    runner = CliRunner()
    result = runner.invoke(main, ["features", "--help"])
    assert result.exit_code == 0
    assert "--tier" in result.output


def test_check_help():
    """check --help shows description."""
    runner = CliRunner()
    result = runner.invoke(main, ["check", "--help"])
    assert result.exit_code == 0


def test_config_runs():
    """config command runs without error."""
    runner = CliRunner()
    result = runner.invoke(main, ["config"])
    assert result.exit_code == 0
    assert "GrabItDown" in result.output


def test_features_runs():
    """features command runs without error."""
    runner = CliRunner()
    result = runner.invoke(main, ["features"])
    assert result.exit_code == 0


def test_features_tier_basic():
    """features --tier basic shows basic tier."""
    runner = CliRunner()
    result = runner.invoke(main, ["features", "--tier", "basic"])
    assert result.exit_code == 0
    assert "Basic" in result.output


def test_resolve_help():
    """resolve --help shows description."""
    runner = CliRunner()
    result = runner.invoke(main, ["resolve", "--help"])
    assert result.exit_code == 0
    assert "URL" in result.output


def test_history_help():
    """history --help shows options."""
    runner = CliRunner()
    result = runner.invoke(main, ["history", "--help"])
    assert result.exit_code == 0
    assert "--limit" in result.output
    assert "--status" in result.output
    assert "--clear" in result.output
    assert "--json" in result.output


def test_all_commands_complete():
    """ALL 14 commands appear in help."""
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    commands = [
        "download",
        "playlist",
        "batch",
        "info",
        "formats",
        "transcript",
        "providers",
        "status",
        "config",
        "recovery",
        "features",
        "check",
        "resolve",
        "history",
    ]
    for cmd in commands:
        assert cmd in result.output, f"Missing command: {cmd}"


def test_download_has_template_option():
    """download --help shows --template."""
    runner = CliRunner()
    result = runner.invoke(main, ["download", "--help"])
    assert "--template" in result.output


def test_all_commands_in_help():
    """All commands appear in main help."""
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    commands = [
        "download",
        "playlist",
        "batch",
        "info",
        "formats",
        "transcript",
        "providers",
        "status",
        "config",
        "recovery",
        "features",
        "check",
        "resolve",
        "history",
    ]
    for cmd in commands:
        assert cmd in result.output, f"Command '{cmd}' not in help output"
