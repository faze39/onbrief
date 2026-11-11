"""End-to-end: the real Hermes agent loop with onbrief enabled.

Runs only with ONBRIEF_E2E=1 and `hermes` on PATH. It uses a throwaway
HERMES_HOME, the demo outbox tool and the scripted local model in demo/, so it
needs no API key and touches no real account.
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HERMES = os.environ.get("HERMES_BIN") or shutil.which("hermes")
pytestmark = pytest.mark.skipif(not (HERMES and os.environ.get("ONBRIEF_E2E") == "1"),
                                reason="set ONBRIEF_E2E=1 with hermes on PATH (or HERMES_BIN)")

DRAFT = "film the kids at every cosplay con, nobody needs to ask \u2014 #CosplayAndCameras"
FIXED = ("ask before you film a cosplayer. shoot at eye level, then send them the shot\n\n"
         "#CosplayAndCameras #FilmingEtiquette")


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _hermes(home, *args, timeout=300):
    env = {**os.environ, "HERMES_HOME": str(home)}
    return subprocess.run([HERMES, *args], env=env, capture_output=True, text=True, timeout=timeout,
                          cwd=home.parent)


def test_agent_loop_blocks_then_allows(tmp_path):
    home = tmp_path / "home"
    plugins = home / "plugins"
    plugins.mkdir(parents=True)
    shutil.copytree(ROOT, plugins / "onbrief", ignore=shutil.ignore_patterns(".git", "tests", "demo", "__pycache__"))
    shutil.copytree(ROOT / "demo" / "demo_outbox", plugins / "demo_outbox")
    for name in ("onbrief", "demo_outbox"):
        assert _hermes(home, "plugins", "enable", name).returncode == 0
    port = _free_port()
    for key, value in (("model.provider", "custom"), ("model.base_url", f"http://127.0.0.1:{port}/v1"),
                       ("model.default", "scripted"), ("model.api_key", "local-demo"),
                       ("tools.tool_search.enabled", "off")):
        _hermes(home, "config", "set", key, value)
    assert "wrote" in _hermes(home, "onbrief", "init", "cosplay-etiquette").stdout

    model = subprocess.Popen([sys.executable, str(ROOT / "demo" / "scripted_model.py"), str(port), DRAFT, FIXED])
    try:
        time.sleep(1)
        out = _hermes(home, "chat", "-q", "post our cosplay filming etiquette update", "--oneshot")
        assert out.returncode == 0, out.stderr
    finally:
        model.terminate()
        _hermes(home, "gateway", "stop")  # the chat may have started a gateway for this home

    sent = [json.loads(l)["text"] for l in
            (home / "plugin-data" / "demo_outbox" / "outbox.jsonl").read_text().splitlines()]
    assert sent == [FIXED]  # the draft never reached the tool
    log = _hermes(home, "onbrief", "log").stdout
    assert "blocked" in log and "allowed" in log
    assert 'banned word "kids"' in out.stdout
