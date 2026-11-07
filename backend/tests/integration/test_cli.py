import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]


def _run(*args):
    return subprocess.run([sys.executable, "-m", "scripts.inspect_instagram_export", *args],
                          cwd=REPO, capture_output=True, text=True, timeout=60)


def test_cli_text(synthetic_zip):
    r = _run(str(synthetic_zip))
    assert r.returncode == 1  # the fixture deliberately contains a malformed file
    assert "Saved posts identified: 3" in r.stdout
    assert "Unique content items: 5" in r.stdout


def test_cli_json(synthetic_zip):
    data = json.loads(_run(str(synthetic_zip), "--json").stdout)
    assert data["unique_content_items"] == 5


def test_cli_bad_path(tmp_path):
    r = _run(str(tmp_path / "missing.zip"))
    assert r.returncode == 2 and "error" in r.stderr
