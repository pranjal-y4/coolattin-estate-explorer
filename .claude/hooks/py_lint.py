#!/usr/bin/env python3
"""
Post-edit hook: check Python syntax immediately after any .py file is written.
Receives tool input/output as JSON on stdin. Exits 1 (blocking) on syntax error.
"""
import json
import subprocess
import sys


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return  # no stdin or malformed — don't block

    file_path = data.get("file_path", "")
    if not file_path.endswith(".py"):
        return

    result = subprocess.run(
        ["python3", "-m", "py_compile", file_path],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"\nSYNTAX ERROR in {file_path}:", file=sys.stderr)
        print(result.stderr.strip(), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
