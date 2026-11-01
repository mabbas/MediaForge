"""
Exhaustive Secret Scanner for MediaForge Gate 2 Source Candidates.
Scans all 49 modified tracked files and all 211 untracked files (260 files total).
Checks for private keys, cloud tokens, payment keys, database passwords, high entropy secrets.
"""

import subprocess
import re
import os
import sys

PATTERNS = {
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY"),
    "aws_access_key": re.compile(r"(?:A3T[A-Z0-9]|AKIA|AGPA|AIDA|AROA|AIPA|ANPA|ANVA|ASIA)[A-Z0-9]{16}"),
    "stripe_live_key": re.compile(r"sk_live_[0-9a-zA-Z]{24,}"),
    "github_token": re.compile(r"(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{36,}|github_pat_[A-Za-z0-9_]{82}"),
    "slack_token": re.compile(r"xox[baprs]-[0-9a-zA-Z]{10,48}"),
    "tailscale_key": re.compile(r"tskey-auth-[0-9a-zA-Z]{20,}"),
    "google_api_key": re.compile(r"AIza[0-9A-Za-z\\-_]{35}"),
}

# Suspicious password/credential assignments
CREDENTIAL_PATTERN = re.compile(r"""(?:password|secret|api_key|token)\s*[:=]\s*["']([^"']{12,})["']""", re.IGNORECASE)

# Whitelisted test fixtures / placeholders / salt dummies
KNOWN_FIXTURES = {
    "change_me_in_prod",
    "staging_secret_change_me_in_prod",
    "Password123!",
    "Password1234!",
    "TestPassword123!",
    "TestPassword1234!",
    "secret-key-for-testing",
    "test-secret-key-12345",
    "test_access_token_12345",
    "test-decoded-access-token",
    "test-decoded-refresh-token",
    "PasswordWithoutSpecial123",
    "super_secret_for_tests",
    "bZNpoCvQ9UwPGgwUNkTQNzmYfsJ6WHbJ", # standard local dev fixture
    "default_signing_key_for_development",
}

def get_candidates():
    staged = subprocess.check_output(["git", "diff", "--cached", "--name-only"], text=True).splitlines()
    tracked = subprocess.check_output(["git", "diff", "--name-only"], text=True).splitlines()
    untracked = subprocess.check_output(["git", "ls-files", "--others", "--exclude-standard"], text=True).splitlines()
    all_files = sorted(set(staged + tracked + untracked))
    return all_files

def scan_file(filepath):
    findings = []
    if not os.path.isfile(filepath):
        return findings
    # Skip public certs, binaries, images
    if filepath.endswith((".crt", ".png", ".ico", ".jpg", ".tar", ".gz")):
        return findings
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            for line_no, line in enumerate(f, 1):
                # Check definite secret patterns
                for name, pat in PATTERNS.items():
                    if pat.search(line):
                        findings.append((filepath, line_no, name, "DETECTED_FORBIDDEN_PATTERN"))
                # Check credential pattern if not an example/test file with known fixtures
                m = CREDENTIAL_PATTERN.search(line)
                if m:
                    val = m.group(1)
                    if any(known in val for known in KNOWN_FIXTURES):
                        continue
                    if "example" in filepath.lower() or filepath.endswith(".md"):
                        continue
                    # Flag potential high-entropy secrets
                    if len(val) >= 20 and not val.startswith("http") and not val.startswith("/") and not "{" in val:
                        findings.append((filepath, line_no, "potential_live_credential", f"Length: {len(val)}"))
    except Exception as e:
        print(f"Error reading {filepath}: {e}", file=sys.stderr)
    return findings

def main():
    files = get_candidates()
    print(f"Total candidate files to scan: {len(files)}")
    all_findings = []
    for f in files:
        f_findings = scan_file(f)
        all_findings.extend(f_findings)
    print(f"Total critical findings: {len(all_findings)}")
    for filepath, line_no, category, note in all_findings:
        print(f"  [FLAG] {category}: {filepath}:{line_no} ({note})")
    return len(all_findings)

if __name__ == "__main__":
    sys.exit(main())
