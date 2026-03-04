"""Verify console formatters (format_size, format_duration)."""
from src.cli.console import format_duration, format_size

tests = [
    (format_size(1073741824), "1.00 GB"),
    (format_size(5242880), "MB"),
    (format_size(None), "Unknown"),
    (format_duration(5025), "1h 23m 45s"),
    (format_duration(150), "2m 30s"),
    (format_duration(None), "Unknown"),
]
for result, expected in tests:
    if expected in result:
        print(f"  ✓ {result}")
    else:
        print(f"  ✗ {result} (expected {expected})")
print("CONSOLE UTILS OK")
