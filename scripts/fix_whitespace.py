"""
Utility script to fix trailing whitespaces and ensure clean single EOF newline
for files reported by git diff --cached --check.
"""

import subprocess
import os

def get_check_files():
    out = subprocess.run(["git", "diff", "--cached", "--check"], capture_output=True, text=True).stdout
    files = set()
    for line in out.splitlines():
        if ":" in line:
            parts = line.split(":")
            if os.path.isfile(parts[0]):
                files.add(parts[0])
    return sorted(files)

def clean_file(path):
    # Skip certs or binary files
    if path.endswith((".crt", ".png", ".ico", ".jpg", ".tar", ".gz")):
        return
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        lines = [line.rstrip() for line in content.splitlines()]
        # Strip empty lines from the end
        while lines and lines[-1] == "":
            lines.pop()
        new_content = "\n".join(lines) + "\n" if lines else ""
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(new_content)
    except Exception as e:
        print(f"Error cleaning {path}: {e}")

def main():
    files = get_check_files()
    print(f"Files needing whitespace cleanup: {len(files)}")
    for f in files:
        clean_file(f)
    # Restage
    if files:
        subprocess.run(["git", "add"] + files, check=True)
    # Recheck
    recheck = subprocess.run(["git", "diff", "--cached", "--check"], capture_output=True, text=True)
    if recheck.returncode == 0:
        print("SUCCESS: git diff --cached --check exited with 0 errors!")
    else:
        print("STILL REPORTING ERRORS:")
        print(recheck.stdout[:500])

if __name__ == "__main__":
    main()
