"""Verify CLI commands (banner, help, download help)."""
from click.testing import CliRunner

from src.cli.cli import main

runner = CliRunner()

# Test main
result = runner.invoke(main)
print(result.output)
assert result.exit_code == 0

# Test help
result = runner.invoke(main, ["--help"])
print(result.output[:400])
assert "download" in result.output

# Test download help
result = runner.invoke(main, ["download", "--help"])
print(result.output[:400])
assert "--mode" in result.output
assert "--quality" in result.output

print("CLI COMMANDS OK")
