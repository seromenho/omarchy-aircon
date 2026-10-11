#!/usr/bin/env bash
# Install aircon on this machine: the aircon/aircond commands, the systemd user
# service, the Omarchy shell plugin and the Omarchy menu entries. Safe to re-run.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
PLUGIN_ID="aircon"
PLUGIN_DIR="$CONFIG_HOME/omarchy/plugins/$PLUGIN_ID"
SHELL_FILE="$CONFIG_HOME/omarchy/shell.json"
UNIT_DIR="$CONFIG_HOME/systemd/user"
APP_CONFIG="$CONFIG_HOME/aircon/config.toml"

step() { printf '\033[1m==> %s\033[0m\n' "$*"; }

step "Installing the aircon and aircond commands"
uv tool install --force --reinstall --quiet "$REPO_DIR"

step "Installing and starting the aircond user service"
mkdir -p "$UNIT_DIR"
install -m 644 "$REPO_DIR/systemd/aircond.service" "$UNIT_DIR/aircond.service"
systemctl --user daemon-reload
systemctl --user enable --quiet aircond.service
systemctl --user restart aircond.service

step "Installing the Omarchy shell plugin ($PLUGIN_ID)"
# Earlier versions installed the plugin under a "<user>.aircon" id; replace it.
for legacy_dir in "$CONFIG_HOME"/omarchy/plugins/*.aircon; do
  [[ -d "$legacy_dir" ]] || continue
  legacy_id="$(basename "$legacy_dir")"
  omarchy plugin disable "$legacy_id" >/dev/null 2>&1 || true
  rm -rf "$legacy_dir"
done
mkdir -p "$PLUGIN_DIR"
find "$PLUGIN_DIR" -mindepth 1 -delete
cp -r "$REPO_DIR/plugin/$PLUGIN_ID/." "$PLUGIN_DIR/"
omarchy plugin validate "$PLUGIN_DIR"
# The shell has to discover the new folder before it can be enabled.
omarchy-shell shell rescanPlugins >/dev/null 2>&1 || true
if ! grep -q "\"$PLUGIN_ID\"" "$SHELL_FILE" 2>/dev/null; then
  for _ in 1 2 3 4 5; do
    omarchy plugin enable "$PLUGIN_ID" right 2>/dev/null && break
    sleep 1
  done
  grep -q "\"$PLUGIN_ID\"" "$SHELL_FILE" || { echo "could not enable $PLUGIN_ID" >&2; exit 1; }
fi

step "Updating the Omarchy menu entries"
"$HOME/.local/bin/aircon" menu sync

if [[ ! -f "$APP_CONFIG" ]]; then
  step "Almost there: click the new icon in the bar to find and add your air conditioner"
fi

step "Done"
"$HOME/.local/bin/aircon" status || true
