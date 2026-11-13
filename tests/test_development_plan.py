"""Keep the bundled development-plan integrity checks in the offline suite."""
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".agents/skills/gemini-web-mcp-development/scripts"


def test_development_plan_integrity() -> None:
    subprocess.run([sys.executable, str(SCRIPTS / "plan.py"), "check"], check=True, timeout=30)


def test_development_plan_receipt_regressions() -> None:
    subprocess.run([sys.executable, str(SCRIPTS / "test_plan.py")], check=True, timeout=60)
