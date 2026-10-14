#!/usr/bin/env bash
# Install, update or remove aircon on this machine.
#
#   ./install.sh              install (from a clone, or from the plugin folder itself)
#   ./install.sh --update     refresh commands and service after the code changed
#   ./install.sh --uninstall  remove commands, service and menu entries (keeps your config)
#
#   --menu      add the Air Conditioner entries to the Omarchy menu
#   --no-menu   remove them
#   --midea     install optional Midea support in a private Python environment
#
# Without --menu or --no-menu, an interactive install asks before touching the Omarchy menu,
# and entries that already exist are kept up to date. The code runs straight from the Omarchy
# plugin folder with the system python3; nothing is installed system-wide. Safe to re-run.
set -euo pipefail

MODE="install"
MENU=""
MIDEA=""
usage_error() { echo "install.sh: $*" >&2; exit 2; }
for arg in "$@"; do
  case "$arg" in
    --update | --uninstall)
      [[ $MODE == "install" ]] || usage_error "use only one of --update and --uninstall"
      MODE="${arg#--}"
      ;;
    --menu | --no-menu)
      [[ -z $MENU ]] || usage_error "use only one of --menu and --no-menu"
      MENU="${arg#--}"
      ;;
    --midea) MIDEA="yes" ;;
    -h | --help)
      sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) usage_error "unknown option: $arg" ;;
  esac
done
[[ $MODE != "uninstall" || -z $MENU ]] || usage_error "--uninstall always removes the menu entries"
[[ $MODE != "uninstall" || -z $MIDEA ]] || usage_error "--midea cannot be used with --uninstall"

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
PLUGIN_ID="io.github.seromenho.aircon"
PLUGINS_DIR="$CONFIG_HOME/omarchy/plugins"
PLUGIN_DIR="$PLUGINS_DIR/$PLUGIN_ID"
SHELL_FILE="$CONFIG_HOME/omarchy/shell.json"
UNIT_DIR="$CONFIG_HOME/systemd/user"
BIN_DIR="$HOME/.local/bin"
APP_CONFIG="$CONFIG_HOME/aircon/config.toml"
MIDEA_VENV="${XDG_DATA_HOME:-$HOME/.local/share}/aircon/midea-venv"
# Hidden folders: the shell ignores them, so staging there does not reload any plugin.
STAGE_DIR="$PLUGINS_DIR/.aircon-stage.$$"
TRASH_DIR="$PLUGINS_DIR/.aircon-trash.$$"

step() { printf '\033[1m==> %s\033[0m\n' "$*"; }
fail() { echo "install.sh: $*" >&2; exit 1; }
same() { [[ "$(realpath -m -- "$1")" == "$(realpath -m -- "$2")" ]]; }
enabled() { grep -qF "\"$1\"" "$SHELL_FILE" 2>/dev/null; }
trash() { mkdir -p "$TRASH_DIR" && mv -T -- "$1" "$TRASH_DIR/${1##*/}"; }
# Old plugin folders go to the trash, except git checkouts, which may hold local work.
retire() {
  if [[ -e "$1/.git" ]]; then
    local backup
    backup="$PLUGINS_DIR/.${1##*/}.bak.$(date -u +%Y%m%d%H%M%S)"
    mv -T -- "$1" "$backup"
    echo "Kept the old git checkout as $backup"
  else
    trash "$1"
  fi
}

# -- uninstall ------------------------------------------------------------------------------

if [[ $MODE == "uninstall" ]]; then
  step "Stopping the aircond service"
  systemctl --user disable --now aircond.service >/dev/null 2>&1 || true
  rm -f "$UNIT_DIR/aircond.service"
  systemctl --user daemon-reload || true
  step "Removing the Omarchy menu entries"
  if [[ -x "$BIN_DIR/aircon" ]]; then
    "$BIN_DIR/aircon" menu remove || true
  fi
  rm -f "$BIN_DIR/aircon" "$BIN_DIR/aircond"
  step "Done. Your settings are kept in $CONFIG_HOME/aircon/."
  echo "To remove the bar widget too, run: omarchy plugin remove $PLUGIN_ID"
  exit 0
fi

# -- install / update -----------------------------------------------------------------------

command -v python3 >/dev/null || fail "python3 is required"
python3 -c 'import sys; sys.exit(sys.version_info < (3, 12))' \
  || fail "python3 3.12 or newer is required (found $(python3 --version 2>&1))"

# One run at a time: the widget starts `--update` on its own after an update.
LOCK_DIR="${XDG_RUNTIME_DIR:-$CONFIG_HOME/aircon}"
mkdir -p "$LOCK_DIR"
exec 9>>"$LOCK_DIR/aircon-install.lock"
flock 9

# `omarchy plugin add` keeps a git clone that `omarchy plugin update` pulls; never overwrite it.
# A run that waited for the lock may also find its own folder already moved there.
if { [[ -e "$PLUGIN_DIR/.git" ]] || [[ ! -d $SOURCE_DIR ]]; } && ! same "$SOURCE_DIR" "$PLUGIN_DIR"; then
  echo "$PLUGIN_DIR is managed by omarchy plugin add; running its own install.sh"
  exec bash "$PLUGIN_DIR/install.sh" "$@"
fi

# Earlier versions used the plugin ids "aircon" and "<user>.aircon". Only folders whose
# manifest names this plugin are touched.
LEGACY_IDS=(aircon)
LEGACY_DIRS=()
for dir in "$PLUGINS_DIR/aircon" "$PLUGINS_DIR"/*.aircon; do
  if [[ -d $dir ]] && ! same "$dir" "$PLUGIN_DIR" \
    && grep -q '"name": *"Air Conditioner"' "$dir/manifest.json" 2>/dev/null; then
    LEGACY_IDS+=("${dir##*/}")
    same "$dir" "$SOURCE_DIR" || LEGACY_DIRS+=("$dir")
  fi
done

if same "$SOURCE_DIR" "$PLUGIN_DIR"; then
  :  # running from the plugin folder itself
elif same "$(dirname "$SOURCE_DIR")" "$PLUGINS_DIR"; then
  # An older plugin folder (e.g. a clone under the old id): move it, so a git clone stays one.
  step "Moving $SOURCE_DIR to $PLUGIN_DIR"
  if [[ -e $PLUGIN_DIR || -L $PLUGIN_DIR ]]; then trash "$PLUGIN_DIR"; fi
  mv -T -- "$SOURCE_DIR" "$PLUGIN_DIR"
else
  step "Copying aircon into $PLUGIN_DIR"
  mkdir -p "$STAGE_DIR"
  tar -C "$SOURCE_DIR" \
    --exclude=./.git --exclude=./.venv --exclude=./.claude \
    --exclude=__pycache__ --exclude=.pytest_cache --exclude=.ruff_cache \
    -cf - . | tar -C "$STAGE_DIR" -xf -
  if [[ -e $PLUGIN_DIR || -L $PLUGIN_DIR ]]; then trash "$PLUGIN_DIR"; fi
  mv -T -- "$STAGE_DIR" "$PLUGIN_DIR"
fi

# Turn the old widget off while the shell still knows it, then move its folder away; an old
# panel left running could otherwise reinstall itself when the service restarts below.
for id in "${LEGACY_IDS[@]}"; do
  if enabled "$id"; then
    omarchy plugin disable "$id" >/dev/null 2>&1 || true
  fi
done
for dir in "${LEGACY_DIRS[@]}"; do
  step "Removing the old plugin folder $dir"
  retire "$dir"
done

# Versions before 0.2 were installed with `uv tool`; drop them so the commands below win.
if command -v uv >/dev/null && uv tool list 2>/dev/null | grep -q '^aircon '; then
  uv tool uninstall aircon >/dev/null 2>&1 || true
fi

if [[ -n $MIDEA ]]; then
  step "Installing optional Midea support into $MIDEA_VENV"
  if command -v uv >/dev/null; then
    [[ -x "$MIDEA_VENV/bin/python" ]] || uv venv --python python3 "$MIDEA_VENV"
    uv pip install --python "$MIDEA_VENV/bin/python" 'msmart-ng>=2026.9.0,<2027'
  else
    [[ -x "$MIDEA_VENV/bin/python" ]] || python3 -m venv "$MIDEA_VENV"
    "$MIDEA_VENV/bin/python" -m pip install 'msmart-ng>=2026.9.0,<2027'
  fi
fi

step "Installing the aircon and aircond commands into $BIN_DIR"
mkdir -p "$BIN_DIR"
write_wrapper() {
  local name="$1" module="$2" target="$BIN_DIR/$1"
  rm -f -- "$target"
  cat >"$target" <<EOF
#!/bin/sh
# Generated by aircon's install.sh; runs aircon from the Omarchy plugin folder.
AIRCON_PYTHON=python3
if [ -x "$MIDEA_VENV/bin/python" ]; then AIRCON_PYTHON="$MIDEA_VENV/bin/python"; fi
# The shell watches this folder: Python cache writes would reload the widget mid-setup.
PYTHONPATH="$PLUGIN_DIR/src\${PYTHONPATH:+:\$PYTHONPATH}" exec "\$AIRCON_PYTHON" -B -m $module "\$@"
EOF
  chmod 755 "$target"
}
write_wrapper aircon aircon.cli
write_wrapper aircond aircon.daemon

step "Installing and starting the aircond user service"
mkdir -p "$UNIT_DIR"
install -m 644 "$PLUGIN_DIR/systemd/aircond.service" "$UNIT_DIR/aircond.service"
systemctl --user daemon-reload
systemctl --user enable --quiet aircond.service
systemctl --user restart aircond.service

step "Enabling the bar widget"
omarchy plugin validate "$PLUGIN_DIR"
# The shell has to discover a new folder before it can be enabled.
omarchy-shell shell rescanPlugins >/dev/null 2>&1 || true
if ! enabled "$PLUGIN_ID"; then
  for _ in 1 2 3 4 5; do
    omarchy plugin enable "$PLUGIN_ID" right >/dev/null 2>&1 && break
    sleep 1
  done
  enabled "$PLUGIN_ID" || fail "could not enable the $PLUGIN_ID widget"
fi

# The Omarchy menu is the user's file: only touch it when asked to, or to refresh our entries.
menu_cmd() {
  "$BIN_DIR/aircon" menu "$1" >/dev/null || echo "install.sh: could not update the Omarchy menu" >&2
}
MENU_HINT="To add Air Conditioner entries to the Omarchy menu later, run: aircon menu sync"
if [[ $MENU == "menu" ]]; then
  step "Adding the Omarchy menu entries"
  menu_cmd sync
elif [[ $MENU == "no-menu" ]]; then
  step "Removing the Omarchy menu entries"
  menu_cmd remove
elif "$BIN_DIR/aircon" menu status >/dev/null 2>&1; then
  step "Updating the Omarchy menu entries"
  menu_cmd sync
elif [[ $MODE == "install" && -t 0 && -t 1 ]]; then
  read -r -p "Add Air Conditioner entries to the Omarchy menu? [Y/n] " answer || answer="n"
  if [[ $answer =~ ^[Nn] ]]; then
    echo "$MENU_HINT"
  else
    menu_cmd sync
  fi
elif [[ $MODE == "install" ]]; then
  echo "$MENU_HINT"
fi

# Also clears what an interrupted earlier run left behind.
rm -rf -- "$PLUGINS_DIR"/.aircon-trash.* "$PLUGINS_DIR"/.aircon-stage.*

if [[ $MODE == "update" ]]; then
  step "Updated to $(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' "$PLUGIN_DIR/manifest.json")"
  exit 0
fi

if [[ ! -f "$APP_CONFIG" ]]; then
  step "Almost there: click the new icon in the bar to find and add your air conditioner"
else
  chmod 600 "$APP_CONFIG"  # it may hold keys; older versions wrote it world-readable
  step "Done"
  # Give the service a moment to reach the units before showing them.
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    "$BIN_DIR/aircon" status 2>/dev/null | grep -q "unreachable" || break
    sleep 0.5
  done
  "$BIN_DIR/aircon" status || true
fi
