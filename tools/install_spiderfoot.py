"""Install the inspected upstream SpiderFoot revision into a separate venv.

This does not enable automatic scans. Shark consumes approved CSV exports only.
"""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
REVISION = "0f815a203afebf05c98b605dba5cf0475a0ee5fd"


def run(*args):
    subprocess.run([str(arg) for arg in args], cwd=ROOT, check=True)


def main():
    tools = ROOT / ".tools"
    tools.mkdir(exist_ok=True)
    source, environment = tools / "spiderfoot", tools / "spiderfoot-venv"
    if not source.exists():
        run("git", "clone", "--no-checkout", "https://github.com/smicallef/spiderfoot.git", source)
        run("git", "-C", source, "checkout", "--detach", REVISION)
    actual = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    if actual != REVISION:
        raise SystemExit("Existing SpiderFoot checkout differs from the inspected revision; use a separate directory.")
    run(sys.executable, "-m", "venv", environment)
    python = environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    run(python, "-m", "pip", "install", "-r", source / "requirements.txt")
    run(python, source / "sf.py", "--help")
    print("SpiderFoot installed separately. Automatic scans remain disabled; see README.md for CSV import.")


if __name__ == "__main__":
    main()
