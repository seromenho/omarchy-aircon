# omarchy-aircon

Control your air conditioner from the [Omarchy](https://omarchy.org/) status bar.
No cloud, no phone app, no hunting for the remote.

<p align="center">
  <img src="docs/screenshot.png" alt="The aircon widget in the Omarchy bar with its panel open" width="320">
</p>

## What it does

- Shows your air conditioner in the bar: mode and target temperature at a glance.
- One click opens a panel with temperature, mode, fan speed, airflow, sleep and a timer.
- Matches your Omarchy theme, and follows it when you switch themes.
- Talks to the air conditioner directly on your home network.
- Handles several air conditioners, one tab each.
- Adds quick actions to the Omarchy menu.

## Supported air conditioners

- **Haier** units with the SmartAir2 USB Wi-Fi module (the one paired with the
  HaierSmartAir2 app).

Yours isn't listed? See [Add your air conditioner](#add-your-air-conditioner).

## Install

```bash
git clone https://github.com/seromenho/omarchy-aircon.git
cd omarchy-aircon
./install.sh
```

Then click the new icon in the bar. The panel looks for air conditioners on your network;
pick yours, give it a name, and you're done. Use **+ Add** for more rooms.

> **Haier:** pair the unit's Wi-Fi module once with the HaierSmartAir2 app first. After that,
> neither the app nor the internet is needed.

## Add your air conditioner

aircon is built around pluggable backends, so another brand or protocol (Midea, Gree, Daikin,
Tuya-based units, an ESPHome board, …) is one module away. The panel, the timer, the menu and
everything else work with any backend.

The easiest way: open a coding agent (Claude Code, Codex, …) in this repository and paste the
prompt from [docs/prompts/add-a-backend.md](docs/prompts/add-a-backend.md), filling in what
you know about your unit. It explains what to build, where, and how to test it without
touching your air conditioner.

Got it working? A pull request is very welcome.

## More

- [Reference](docs/reference.md): command line, configuration file, socket API, how it works.
- [AGENTS.md](AGENTS.md): how the code is organised and the rules for contributing.

## Credits

The Haier protocol was worked out by the community, notably
[bstuff/haier-ac-remote](https://github.com/bstuff/haier-ac-remote) and the
[ESPHome Haier component](https://esphome.io/components/climate/haier/).

## License

[MIT](LICENSE)
