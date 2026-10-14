"""install.sh end to end, against a throwaway HOME and stubbed systemctl/omarchy commands."""

import json
import os
import re
import select
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

import aircon
from aircon import menu

REPO = Path(__file__).resolve().parents[1]
PLUGIN_ID = json.loads((REPO / "manifest.json").read_text())["id"]

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")

# The omarchy stub keeps the enabled widget ids in a side file and mirrors them into
# shell.json, like `omarchy plugin enable/disable` do.
STUBS = {
    "systemctl": 'echo "systemctl $*" >> "$STUB_LOG"\n',
    "omarchy-shell": 'echo "omarchy-shell $*" >> "$STUB_LOG"\n',
    "omarchy": (
        'echo "omarchy $*" >> "$STUB_LOG"\n'
        'state="$HOME/.config/omarchy/enabled-ids"\n'
        'if [ "$1" = plugin ] && { [ "$2" = enable ] || [ "$2" = disable ]; }; then\n'
        '  mkdir -p "$HOME/.config/omarchy" && touch "$state"\n'
        '  grep -vxF "$3" "$state" > "$state.tmp"\n'
        '  if [ "$2" = enable ]; then echo "$3" >> "$state.tmp"; fi\n'
        '  mv "$state.tmp" "$state"\n'
        '  ids=$(sed \'s/.*/{"id":"&"}/\' "$state" | paste -sd, -)\n'
        '  printf \'{"bar":{"layout":{"right":[%s]}}}\' "$ids" '
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
                          text=True, timeout=120, check=False, stdin=subprocess.DEVNULL)


def run_in_terminal(script: Path, env: dict, answer: str, *args: str) -> tuple[int, str]:
    """Run with a pseudo terminal, answering the menu question when it shows up."""
    master, slave = os.openpty()
    proc = subprocess.Popen(["bash", str(script), *args], env=env,
                            stdin=slave, stdout=slave, stderr=slave)
    os.close(slave)
    output, answered = b"", False
    while True:
        if not select.select([master], [], [], 60)[0]:
            proc.kill()  # stuck, e.g. waiting for an answer to a question we did not expect
            break
        try:
            chunk = os.read(master, 4096)
        except OSError:  # the terminal closes when the script exits
            break
        if not chunk:
            break
        output += chunk
        if not answered and b"[Y/n]" in output:
            os.write(master, answer.encode() + b"\n")
            answered = True
    os.close(master)
    return proc.wait(timeout=120), output.decode(errors="replace")


def plugins(home: Path) -> Path:
    return home / ".config/omarchy/plugins"


def menu_file(home: Path) -> Path:
    return home / ".config/omarchy/extensions/omarchy-menu.jsonc"


def enabled_ids(home: Path) -> list[str]:
    shell = home / ".config/omarchy/shell.json"
    if not shell.exists():
        return []
    return [w["id"] for w in json.loads(shell.read_text())["bar"]["layout"]["right"]]


def set_enabled(home: Path, *ids: str) -> None:
    (home / ".config/omarchy").mkdir(parents=True, exist_ok=True)
    (home / ".config/omarchy/enabled-ids").write_text("".join(f"{i}\n" for i in ids))
    widgets = ",".join(json.dumps({"id": i}) for i in ids)
    (home / ".config/omarchy/shell.json").write_text(
        '{"bar":{"layout":{"right":[' + widgets + "]}}}")


def old_plugin(home: Path, folder: str, name: str = "Air Conditioner") -> Path:
    path = plugins(home) / folder
    path.mkdir(parents=True)
    (path / "manifest.json").write_text(json.dumps({"id": folder, "name": name}, indent=2))
    return path


def hidden_leftovers(home: Path) -> list[str]:
    return [p.name for p in plugins(home).iterdir() if p.name.startswith(".")]


def test_versions_match():
    manifest = json.loads((REPO / "manifest.json").read_text())
    pyproject = tomllib.loads((REPO / "pyproject.toml").read_text())
    service = (REPO / "plugin" / "Service.qml").read_text()
    qml_version = re.search(r'pluginVersion: "([^"]+)"', service).group(1)
    assert manifest["version"] == aircon.__version__ == qml_version
    assert pyproject["project"]["version"] == aircon.__version__


def test_plugin_id_is_the_same_everywhere():
    panel = (REPO / "plugin" / "Panel.qml").read_text()
    script = (REPO / "install.sh").read_text()
    assert re.search(r'moduleName: "([^"]+)"', panel).group(1) == PLUGIN_ID
    assert re.search(r'ipcTarget: "([^"]+)"', panel).group(1) == PLUGIN_ID
    assert re.search(r'^PLUGIN_ID="([^"]+)"', script, re.M).group(1) == PLUGIN_ID
    assert menu.OPEN_PANEL.split()[-1] == PLUGIN_ID


def test_install_update_and_uninstall(sandbox):
    home, env, log = sandbox
    plugin_dir = plugins(home) / PLUGIN_ID
    bin_dir = home / ".local/bin"
    unit = home / ".config/systemd/user/aircond.service"

    result = run(REPO / "install.sh", env, "--menu")
    assert result.returncode == 0, result.stderr
    assert "click the new icon" in result.stdout

    assert (plugin_dir / "manifest.json").is_file()
    assert (plugin_dir / "plugin" / "Panel.qml").is_file()
    for unwanted in (".git", ".venv", ".claude"):
        assert not (plugin_dir / unwanted).exists()
    assert not [p for p in plugin_dir.rglob("*") if p.is_symlink()]
    assert hidden_leftovers(home) == []

    help_run = subprocess.run([str(bin_dir / "aircon"), "--help"], env=env,
                              capture_output=True, text=True, timeout=30, check=False)
    assert help_run.returncode == 0, help_run.stderr
    assert "air conditioners" in help_run.stdout
    assert str(plugin_dir / "src") in (bin_dir / "aircond").read_text()
    assert "ExecStart=%h/.local/bin/aircond" in unit.read_text()
    assert menu.BEGIN in menu_file(home).read_text()
    calls = log.read_text()
    assert "systemctl --user restart aircond.service" in calls
    assert f"omarchy plugin enable {PLUGIN_ID} right" in calls
    assert enabled_ids(home) == [PLUGIN_ID]

    # The plugin's own copy can refresh itself (what the panel does after an update).
    result = run(plugin_dir / "install.sh", env, "--update")
    assert result.returncode == 0, result.stderr
    assert f"Updated to {aircon.__version__}" in result.stdout
    assert (plugin_dir / "manifest.json").is_file()

    result = run(plugin_dir / "install.sh", env, "--uninstall")
    assert result.returncode == 0, result.stderr
    assert not (bin_dir / "aircon").exists() and not (bin_dir / "aircond").exists()
    assert not unit.exists()
    assert menu.BEGIN not in menu_file(home).read_text()
    assert "systemctl --user disable --now aircond.service" in log.read_text()


def test_install_twice_keeps_the_plugin(sandbox):
    home, env, log = sandbox
    for _ in range(2):
        result = run(REPO / "install.sh", env)
        assert result.returncode == 0, result.stderr
    assert (plugins(home) / PLUGIN_ID / "manifest.json").is_file()
    assert enabled_ids(home) == [PLUGIN_ID]
    assert f"disable {PLUGIN_ID}" not in log.read_text()
    assert hidden_leftovers(home) == []


# -- the Omarchy menu is only touched with consent ------------------------------------------


def test_install_without_a_terminal_leaves_the_menu_alone(sandbox):
    home, env, _ = sandbox
    result = run(REPO / "install.sh", env)
    assert result.returncode == 0, result.stderr
    assert "aircon menu sync" in result.stdout
    assert not menu_file(home).exists()


@pytest.mark.parametrize(("answer", "added"), [("", True), ("y", True), ("n", False)])
def test_install_asks_before_adding_menu_entries(sandbox, answer, added):
    home, env, _ = sandbox
    code, output = run_in_terminal(REPO / "install.sh", env, answer)
    assert code == 0, output
    assert "Add Air Conditioner entries to the Omarchy menu?" in output
    assert menu_file(home).exists() is added


def test_update_in_a_terminal_does_not_ask(sandbox):
    home, env, _ = sandbox
    code, output = run_in_terminal(REPO / "install.sh", env, "y", "--update")
    assert code == 0, output
    assert "[Y/n]" not in output
    assert not menu_file(home).exists()


def test_update_refreshes_existing_menu_entries_only(sandbox):
    home, env, _ = sandbox
    path = menu_file(home)
    path.parent.mkdir(parents=True)
    path.write_text('{\n  "mine": {"label": "Mine"}\n}\n')
    result = run(REPO / "install.sh", env, "--update")
    assert result.returncode == 0, result.stderr
    assert path.read_text() == '{\n  "mine": {"label": "Mine"}\n}\n'
    assert not path.with_name(path.name + ".aircon.bak").exists()

    # Entries written by an older version still open the panel through the old plugin id.
    menu.sync(path, None)
    path.write_text(path.read_text().replace(PLUGIN_ID, "aircon"))
    result = run(REPO / "install.sh", env, "--update")
    assert result.returncode == 0, result.stderr
    assert menu.OPEN_PANEL in path.read_text()


def test_no_menu_removes_the_entries(sandbox):
    home, env, _ = sandbox
    menu.sync(menu_file(home), None)
    result = run(REPO / "install.sh", env, "--no-menu")
    assert result.returncode == 0, result.stderr
    assert menu.BEGIN not in menu_file(home).read_text()


# -- moving from the plugin ids used before 0.3 ---------------------------------------------


def test_replaces_a_copy_under_the_old_id(sandbox):
    home, env, log = sandbox
    old_plugin(home, "aircon")
    set_enabled(home, "aircon")
    result = run(REPO / "install.sh", env)
    assert result.returncode == 0, result.stderr
    assert not (plugins(home) / "aircon").exists()
    assert (plugins(home) / PLUGIN_ID / "manifest.json").is_file()
    assert enabled_ids(home) == [PLUGIN_ID]
    calls = log.read_text()
    # The old widget goes before the service restarts, so it cannot reinstall itself.
    assert calls.index("omarchy plugin disable aircon") < calls.index("systemctl --user restart")
    assert str(plugins(home) / PLUGIN_ID / "src") in (home / ".local/bin/aircon").read_text()
    assert hidden_leftovers(home) == []


def test_removes_only_our_old_folders(sandbox):
    home, env, log = sandbox
    old_plugin(home, "someone.aircon")
    foreign = old_plugin(home, "foo.aircon", name="Something Else")
    set_enabled(home, "someone.aircon", "foo.aircon")
    result = run(REPO / "install.sh", env)
    assert result.returncode == 0, result.stderr
    assert not (plugins(home) / "someone.aircon").exists()
    assert (foreign / "manifest.json").is_file()
    assert enabled_ids(home) == ["foo.aircon", PLUGIN_ID]
    assert "disable foo.aircon" not in log.read_text()


def test_moves_a_git_clone_under_the_old_id(sandbox):
    """`omarchy plugin update aircon` pulled 0.3 into plugins/aircon; it runs its own script."""
    home, env, _ = sandbox
    clone = plugins(home) / "aircon"
    shutil.copytree(REPO, clone, ignore=shutil.ignore_patterns(
        ".git", ".venv", ".claude", "__pycache__", ".pytest_cache", ".ruff_cache"))
    (clone / ".git").mkdir()
    (clone / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    set_enabled(home, "aircon")
    result = run(clone / "install.sh", env, "--update")
    assert result.returncode == 0, result.stderr
    assert not clone.exists()
    assert (plugins(home) / PLUGIN_ID / ".git" / "HEAD").is_file()
    assert enabled_ids(home) == [PLUGIN_ID]
    assert f"Updated to {aircon.__version__}" in result.stdout
    assert hidden_leftovers(home) == []


def test_keeps_an_old_git_checkout_as_a_backup(sandbox):
    home, env, _ = sandbox
    old = old_plugin(home, "aircon")
    (old / ".git").mkdir()
    result = run(REPO / "install.sh", env)
    assert result.returncode == 0, result.stderr
    assert not old.exists()
    assert "Kept the old git checkout" in result.stdout
    backups = [p for p in plugins(home).iterdir() if p.name.startswith(".aircon.bak.")]
    assert len(backups) == 1 and (backups[0] / ".git").is_dir()


@pytest.mark.parametrize("args", [[], ["--update"]])
def test_cleans_up_after_an_interrupted_run(sandbox, args):
    home, env, _ = sandbox
    for leftover in (".aircon-trash.1", ".aircon-stage.2"):
        (plugins(home) / leftover / "old").mkdir(parents=True)
    result = run(REPO / "install.sh", env, *args)
    assert result.returncode == 0, result.stderr
    assert hidden_leftovers(home) == []


@pytest.mark.parametrize("worktree", [False, True])
def test_hands_over_to_a_managed_clone(sandbox, worktree):
    home, env, _ = sandbox
    clone = plugins(home) / PLUGIN_ID
    clone.mkdir(parents=True)
    if worktree:  # in a git worktree, .git is a file
        (clone / ".git").write_text("gitdir: /elsewhere\n")
    else:
        (clone / ".git").mkdir()
    (clone / "install.sh").write_text('echo "clone ran $*" > "$HOME/handover"\n')
    result = run(REPO / "install.sh", env, "--update")
    assert result.returncode == 0, result.stderr
    assert "managed by omarchy plugin add" in result.stdout
    assert (home / "handover").read_text() == "clone ran --update\n"
    assert (clone / ".git").exists()


def test_enable_matches_the_exact_id(sandbox):
    home, env, log = sandbox
    set_enabled(home, PLUGIN_ID.replace(".", "-"))
    result = run(REPO / "install.sh", env)
    assert result.returncode == 0, result.stderr
    assert f"omarchy plugin enable {PLUGIN_ID} right" in log.read_text()


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


@pytest.mark.parametrize("args", [
    ["--bogus"], ["--update", "--uninstall"], ["--menu", "--no-menu"], ["--uninstall", "--menu"],
])
def test_bad_options(sandbox, args):
    _, env, _ = sandbox
    assert run(REPO / "install.sh", env, *args).returncode == 2


def test_midea_install_uses_external_venv_and_survives_update(sandbox):
    home, env, log = sandbox
    stubs = Path(env["PATH"].split(":")[0])
    uv = stubs / "uv"
    uv.write_text(
        '#!/bin/sh\necho "uv $*" >> "$STUB_LOG"\n'
        'if [ "$1" = venv ]; then\n'
        '  mkdir -p "$4/bin"\n'
        '  cat > "$4/bin/python" <<\'EOF\'\n'
        '#!/bin/sh\necho "midea-python $*" >> "$STUB_LOG"\n'
        'exec /usr/bin/python3 "$@"\nEOF\n'
        '  chmod +x "$4/bin/python"\n'
        'fi\n'
    )
    uv.chmod(0o755)
    result = run(REPO / "install.sh", env, "--midea")
    assert result.returncode == 0, result.stderr
    venv = home / ".local/share/aircon/midea-venv"
    assert (venv / "bin/python").exists()
    assert "uv pip install --python" in log.read_text()
    assert "msmart-ng>=2026.9.0,<2027" in log.read_text()
    plugin_dir = plugins(home) / PLUGIN_ID
    assert not [p for p in plugin_dir.rglob("*") if p.is_symlink()]
    for args in ([], ["--update"]):
        result = run(plugin_dir / "install.sh", env, *args)
        assert result.returncode == 0, result.stderr
        help_run = subprocess.run([str(home / ".local/bin/aircon"), "--help"],
                                  env=env, capture_output=True, text=True, check=False)
        assert help_run.returncode == 0, help_run.stderr
    assert "midea-python -B -m aircon.cli --help" in log.read_text()
    assert log.read_text().count("uv pip install") == 1


def test_cannot_install_midea_during_uninstall(sandbox):
    _, env, _ = sandbox
    assert run(REPO / "install.sh", env, "--uninstall", "--midea").returncode == 2


def test_installed_commands_never_create_cache_files_in_watched_plugin(sandbox):
    home, env, _ = sandbox
    env = {**env, "AIRCON_SOCKET": str(home / "runtime/aircon.sock")}
    result = run(REPO / "install.sh", env, "--update")
    assert result.returncode == 0, result.stderr
    plugin_dir = plugins(home) / PLUGIN_ID
    before = {str(path.relative_to(plugin_dir)) for path in plugin_dir.rglob("*")}
    commands = [
        [str(home / ".local/bin/aircon"), "--help"],
        [str(home / ".local/bin/aircond"), "--help"],
        # Import the first-use credentials module, then fail device validation before
        # reading any account credentials or contacting a real device/cloud service.
        [str(home / ".local/bin/aircon"), "setup", "apply", "--backend=midea_lan",
         "--host=192.168.1.50", "--smarthome", "--credentials-stdin", "--no-restart"],
    ]
    for index, command in enumerate(commands):
        result = subprocess.run(command, env=env, capture_output=True, text=True,
                                stdin=subprocess.DEVNULL, timeout=15, check=False)
        assert result.returncode == (1 if index == 2 else 0), result.stderr
        if index == 2:
            assert "device_id" in result.stderr
    after = {str(path.relative_to(plugin_dir)) for path in plugin_dir.rglob("*")}
    assert before == after
    assert not list(plugin_dir.rglob("__pycache__"))
