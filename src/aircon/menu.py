"""Omarchy menu entries for aircon, kept in a marked block of omarchy-menu.jsonc."""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

from aircon.config import Config

BEGIN = "  // >>> aircon: managed by `aircon menu sync`; changes inside this block are overwritten"
END = "  // <<< aircon"
LEGACY_HEADER = "// Air conditioner (added by aircon/install.sh)"

CLI = "$HOME/.local/bin/aircon"
OPEN_PANEL = "omarchy-shell shell toggle aircon"

SNOWFLAKE = ""
SUN = ""
POWER = ""
CLOCK = ""
MOON = ""
SLIDERS = ""
GEAR = ""


def default_menu_path() -> Path:
    override = os.environ.get("AIRCON_MENU_FILE")
    if override:
        return Path(override)
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "omarchy" / "extensions" / "omarchy-menu.jsonc"


def _device_actions(prefix: str, cli: str) -> list[tuple[str, dict]]:
    return [
        (f"{prefix}.toggle", {"icon": POWER, "label": "Power on/off",
                              "action": f"{cli} toggle", "checked": f"{cli} is-on"}),
        (f"{prefix}.cool", {"icon": SNOWFLAKE, "label": "Cool 24°",
                            "action": f"{cli} set --mode cool --temp 24"}),
        (f"{prefix}.heat", {"icon": SUN, "label": "Heat 22°",
                            "action": f"{cli} set --mode heat --temp 22"}),
        (f"{prefix}.off-later", {"icon": CLOCK, "label": "Power off in 1 hour",
                                 "action": f"{cli} timer off --in 1h"}),
        (f"{prefix}.sleep", {"icon": MOON, "label": "Start sleep curve",
                             "action": f"{cli} sleep-curve start"}),
    ]


def entries(config: Config | None) -> list[tuple[str, dict]]:
    devices = config.devices if config else []
    label = "Air Conditioners" if len(devices) > 1 else "Air Conditioner"
    rows: list[tuple[str, dict]] = [("aircon", {"icon": SNOWFLAKE, "label": label})]
    if not devices:
        rows.append(("aircon.setup", {"icon": GEAR, "label": "Set up air conditioner",
                                      "action": OPEN_PANEL}))
        return rows
    if len(devices) == 1:
        rows += _device_actions("aircon", CLI)
    else:
        for device in devices:
            rows.append((f"aircon.{device.id}", {"icon": SNOWFLAKE, "label": device.name}))
            rows += _device_actions(f"aircon.{device.id}", f"{CLI} -d {device.id}")
        rows.append(("aircon.all-off", {"icon": POWER, "label": "Turn all off",
                                        "action": f"{CLI} all off"}))
    rows.append(("aircon.panel", {"icon": SLIDERS, "label": "Open panel",
                                  "action": OPEN_PANEL}))
    return rows


def render_block(config: Config | None) -> str:
    lines = [BEGIN]
    lines += [f"  {json.dumps(key)}: {json.dumps(value, ensure_ascii=False)},"
              for key, value in entries(config)]
    lines.append(END)
    return "\n".join(lines)


_MANAGED = re.compile(r"[ \t]*// >>> aircon:.*?// <<< aircon[^\n]*\n?", re.S)


def _strip_ours(text: str) -> str:
    text = _MANAGED.sub("", text)
    kept: list[str] = []
    in_legacy = False
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith(LEGACY_HEADER):
            in_legacy = True
            continue
        if in_legacy and stripped.startswith('"aircon'):
            continue
        in_legacy = False
        kept.append(line)
    return "\n".join(kept)


def sync(path: Path, config: Config | None) -> bool:
    """Rewrite the aircon block; returns True if the file changed."""
    original = path.read_text() if path.exists() else "{\n}\n"
    base = _strip_ours(original)
    end = base.rstrip().rfind("}")
    if end < 0:
        raise ValueError(f"{path}: no closing brace found")
    updated = base[:end].rstrip() + "\n\n" + render_block(config) + "\n}\n"
    if updated == original:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = path.with_name(path.name + ".aircon.bak")
    if path.exists() and not backup.exists():
        shutil.copy2(path, backup)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(updated)
    os.replace(tmp, path)
    return True
