# Haier air conditioners without a Wi-Fi module: an ESPHome board

Many Haier wall units have a USB-A socket for the SmartAir2 Wi-Fi module. If the module is
missing, broken or locked to the cloud, a tiny ESP32 board running
[ESPHome](https://esphome.io/) can take its place, and aircon talks to that board on your
network with the `esphome` backend. No soldering, no cloud.

The firmware is in [`esphome/haier-smartair2.yaml`](esphome/haier-smartair2.yaml).

## Tested on

| | |
|---|---|
| Air conditioner | Haier AS20S2SF2FA-1 (wall unit, SmartAir2 USB-A Wi-Fi port) |
| Board | ESP32-C3 SuperMini (4 MB flash), headers not needed |
| Cable | plain USB-A to USB-C data cable |
| Firmware | ESPHome 2026.9, `haier` climate with `protocol: smartair2` |
| Verified | room temperature, power, mode and target temperature from the board's page and from aircon |

Other Haier units with the same USB Wi-Fi port should work the same way; please report
yours.

## Why a USB cable works

The socket in the air conditioner looks like USB but is a **5 V serial port**: VBUS is 5 V,
GND is ground, and D+/D− carry the serial lines at 9600 baud. The ESP32-C3 has its USB pins
on GPIO18 (D−) and GPIO19 (D+), and they can be used as an ordinary UART. So a normal
USB-A to USB-C cable powers the board from the air conditioner and wires the serial lines
to GPIO18/19, without touching the board's pin headers.

The trade-off: once flashed, the board no longer shows up as a USB serial device. Updates go
over Wi-Fi; to flash over USB again, hold **BOOT** while plugging it into the computer.

The serial lines of the port were not measured on the tested unit. The ESP32-C3 expects
3.3 V on its pins; if your unit's data lines idle at 5 V (measure D+ and D− against GND
with the unit powered and nothing plugged in), use a level shifter instead of the direct
cable.

## What you need

- ESP32-C3 SuperMini (a few euros; any ESP32-C3 board with a USB-C port wired to
  GPIO18/19 should do);
- USB-A to USB-C **data** cable, short if possible;
- a computer with [ESPHome](https://esphome.io/guides/installing_esphome) (for example
  `uv tool install esphome`) and access to serial ports (on Arch: `sudo usermod -aG uucp
  $USER`, then log in again).

## Steps

1. Put `haier-smartair2.yaml` in a folder with a `secrets.yaml`:

   ```yaml
   wifi_ssid: "your 2.4 GHz network"
   wifi_password: "..."
   ap_password: "a password for the fallback hotspot"
   ota_password: "a password for over-the-air updates"
   ```

   Change `name` and `friendly_name` at the top of the YAML (one copy per air conditioner).
2. Plug the board into the computer and flash it: `esphome run haier-smartair2.yaml`. Note
   the IP address it reports, and reserve it in your router.
3. With the air conditioner switched off at the breaker, plug the cable into its USB
   socket and into the board. Switch it back on. The board's **red** LED shows it has
   power.
4. Open `http://<board IP>` in a browser: the *Air conditioner* card should show the room
   temperature within a minute. If it never does, swap `tx_pin` and `rx_pin` in the YAML
   and update the board over Wi-Fi with `esphome run`.
5. Add it to aircon: the panel's **+ Add** finds ESPHome boards on the network, or run
   `aircon setup`, or `aircon setup apply --backend esphome --host <board IP> --mac <board MAC>`.

## Troubleshooting

| What you see | What to do |
|---|---|
| Only a blue LED on the board | No 5 V reaches it: check both plugs and that the cable carries data. |
| The page opens but shows no room temperature | Swap `tx_pin` and `rx_pin` and update over Wi-Fi. |
| The page doesn't open | Look for the `<name>-setup` hotspot on your phone and pick your Wi-Fi again. |
| The board keeps rebooting | The port can't supply enough current; try another cable or board. |

## How aircon uses the board

The `esphome` backend uses ESPHome's `web_server` API on port 80: it follows the `/events`
stream for live state, reads `GET /climate/<name>` and sends changes with
`POST /climate/<name>/set`. Modes, fan speeds, swing positions and the temperature range
come from what the board reports. The web server has no password, like the original Wi-Fi
module: keep the board on a network you trust.
