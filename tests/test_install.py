"""install.sh end to end, against a throwaway HOME and stubbed systemctl/omarchy commands."""

import json
import os
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

import aircon
from aircon import menu

REPO = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")

STUBS = {
    "systemctl": 'echo "systemctl $*" >> "$STUB_LOG"\n',
    "omarchy-shell": 'echo "omarchy-shell $*" >> "$STUB_LOG"\n',
    "omarchy": (
        'echo "omarchy $*" >> "$STUB_LOG"\n'
        'if [ "$1 $2" = "plugin enable" ]; then\n'
        '  mkdir -p "$HOME/.config/omarchy"\n'
        '  printf \'{"bar":{"layout":{"right":[{"id":"%s"}]}}}\' "$3" '
        '> "$HOME/.config/omarchy/shell.json"\n'
        "fi\n"
    ),
}


@pytest.fixture
def sandbox(tmp_path):
    home = tmp_path / "home"
    stubs = tmp_path / "stubs"
    home.mkdir()
    stubs.mkdir()
    for name, body in STUBS.items():
        script = stubs / name
        script.write_text("#!/bin/sh\n" + body + "exit 0\n")
        script.chmod(0o755)
    env = {
        "HOME": str(home),
        "PATH": f"{stubs}:/usr/bin:/bin",
        "STUB_LOG": str(tmp_path / "calls.log"),
        "LANG": "C.UTF-8",
    }
    return home, env, tmp_path / "calls.log"


def run(script: Path, env: dict, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(script), *args], env=env, capture_output=True,
                          text=True, timeout=120, check=False)


def test_versions_match():
    manifest = json.loads((REPO / "manifest.json").read_text())
    pyproject = tomllib.loads((REPO / "pyproject.toml").read_text())
    service = (REPO / "plugin" / "Service.qml").read_text()
    qml_version = re.search(r'pluginVersion: "([^"]+)"', service).group(1)
    assert manifest["version"] == aircon.__version__ == qml_version
    assert pyproject["project"]["version"] == aircon.__version__


def test_install_update_and_uninstall(sandbox):
    home, env, log = sandbox
    plugin_dir = home / ".config/omarchy/plugins/aircon"
    bin_dir = home / ".local/bin"
    unit = home / ".config/systemd/user/aircond.service"
    menu_file = home / ".config/omarchy/extensions/omarchy-menu.jsonc"

    result = run(REPO / "install.sh", env)
    assert result.returncode == 0, result.stderr
    assert "click the new icon" in result.stdout

    assert (plugin_dir / "manifest.json").is_file()
    assert (plugin_dir / "plugin" / "Panel.qml").is_file()
    for unwanted in (".git", ".venv", ".claude"):
        assert not (plugin_dir / unwanted).exists()
    assert not [p for p in plugin_dir.rglob("*") if p.is_symlink()]

    help_run = subprocess.run([str(bin_dir / "aircon"), "--help"], env=env,
                              capture_output=True, text=True, timeout=30, check=False)
    assert help_run.returncode == 0, help_run.stderr
    assert "air conditioners" in help_run.stdout
    assert str(plugin_dir / "src") in (bin_dir / "aircond").read_text()
    assert "ExecStart=%h/.local/bin/aircond" in unit.read_text()
    assert menu.BEGIN in menu_file.read_text()
    calls = log.read_text()
    assert "systemctl --user restart aircond.service" in calls
    assert "omarchy plugin enable aircon right" in calls

    # The plugin's own copy can refresh itself (what the panel does after an update).
    result = run(plugin_dir / "install.sh", env, "--update")
    assert result.returncode == 0, result.stderr
    assert f"Updated to {aircon.__version__}" in result.stdout
    assert (plugin_dir / "manifest.json").is_file()

    result = run(plugin_dir / "install.sh", env, "--uninstall")
    assert result.returncode == 0, result.stderr
    assert not (bin_dir / "aircon").exists() and not (bin_dir / "aircond").exists()
    assert not unit.exists()
    assert menu.BEGIN not in menu_file.read_text()
    assert "systemctl --user disable --now aircond.service" in log.read_text()


def test_install_replaces_symlinked_commands(sandbox):
    """Older versions left ~/.local/bin/aircon as a symlink; writing through it would be wrong."""
    home, env, _ = sandbox
    bin_dir = home / ".local/bin"
    bin_dir.mkdir(parents=True)
    target = home / "elsewhere"
    target.write_text("keep me")
    os.symlink(target, bin_dir / "aircon")
    result = run(REPO / "install.sh", env)
    assert result.returncode == 0, result.stderr
    assert not (bin_dir / "aircon").is_symlink()
    assert target.read_text() == "keep me"


def test_unknown_option(sandbox):
    _, env, _ = sandbox
    result = run(REPO / "install.sh", env, "--bogus")
    assert result.returncode == 2
