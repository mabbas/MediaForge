import subprocess
import re
import os

# Patterns to scan
patterns = {
    "private_key": re.compile(r"-----BEGIN (RSA|EC|DSA|OPENSSH|PGP|PRIVATE) KEY"),
    "aws_access_key": re.compile(r"(A3T[A-Z0-9]|AKIA|AGPA|AIDA|AROA|AIPA|ANPA|ANVA|ASIA)[A-Z0-9]{16}"),
    "stripe_live_key": re.compile(r"sk_live_[0-9a-zA-Z]{24}"),
    "generic_secret": re.compile(r"(password|secret|token)\s*=\s*['\"][0-9a-zA-Z!@#$%^&*()_+]{20,}['\"]", re.IGNORECASE),
}

# Get untracked files
files = subprocess.check_output(["git", "ls-files", "--others", "--exclude-standard"], text=True).splitlines()

findings = []
for fpath in files:
    if not os.path.isfile(fpath):
        continue
    # Skip certs (.crt) as they are public certificates
    if fpath.endswith((".crt", ".png", ".ico", ".jpg", ".tar", ".gz")):
        continue
    try:
        with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
            for line_no, line in enumerate(f, 1):
                # Skip example env files, markdown docs, or tests containing dummy fixture values
                for name, pat in patterns.items():
                    if pat.search(line):
                        # Filter out known false positives (e.g. comments, placeholders, example files)
                        if "example" in fpath.lower() or "test" in fpath.lower() or fpath.endswith(".md"):
                            continue
                        findings.append((fpath, line_no, name, line.strip()[:80]))
    except Exception as e:
        print(f"Error reading {fpath}: {e}")

print(f"Total untracked files scanned: {len(files)}")
print(f"Total secret findings in non-test/non-doc untracked source files: {len(findings)}")
for f, l, name, text in findings:
    print(f"  [{name}] {f}:{l} -> {text}")
