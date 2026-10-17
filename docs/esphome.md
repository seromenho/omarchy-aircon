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

## 1. Prepare the firmware (once)

Make a folder, copy [`esphome/haier-smartair2.yaml`](esphome/haier-smartair2.yaml) into it
and add a `secrets.yaml` next to it. Keep this file private and keep a copy: without the OTA
password you can only update a board over USB.

```yaml
wifi_ssid: "your network"         # 2.4 GHz: the ESP32-C3 cannot see 5 GHz-only networks
wifi_password: "..."
ap_password: "..."                # fallback hotspot, used only if the Wi-Fi fails
ota_password: "..."               # protects over-the-air updates
```

Make one copy of the YAML per air conditioner (for example `ac-bedroom.yaml`,
`ac-office.yaml`) and change `name` and `friendly_name` at the top of each. Check it with
`esphome config ac-bedroom.yaml`.

## 2. Flash each board (at the desk)

1. Plug the board into the computer with the USB-C cable. It shows up as `/dev/ttyACM0`.
   If it doesn't, or flashing fails with "Failed to connect", unplug it, hold the **BOOT**
   button, plug it in, release the button and try again. A charge-only cable never works.
2. Run `esphome run ac-bedroom.yaml` and pick the serial port. The first build takes a few
   minutes.
3. When it finishes, the log shows `WiFi Connected` and an `IP Address`. Write it down,
   reserve that address for the board in your router, then stop the log with Ctrl+C.
   Messages such as `Answer timeout` are normal here: the board isn't connected to an air
   conditioner yet.
4. Label the board (a piece of tape with its name) and repeat for the next one.

After this firmware the board no longer appears as `/dev/ttyACM0`, because its USB pins now
belong to the air conditioner. That's expected.

## 3. Install it in the air conditioner

1. Optional but recommended for a first unit: with the air conditioner powered and nothing
   plugged in, measure D+ and D− of its USB socket against GND (a USB-A breakout with
   screw terminals makes this easy). About 3.3 V is fine; about 5 V needs a level shifter.
2. Switch the air conditioner off at the breaker, not just with the remote.
3. Plug the USB-A end of the cable into the air conditioner's Wi-Fi socket (it only goes
   in one way) and the USB-C end into the board.
4. Switch the breaker back on. The board's **red** LED means it has power.
5. Fix the board and the cable with double-sided tape or a cable tie so nothing hangs.
6. Within a minute, `http://<board IP>` shows the *Air conditioner* card with the room
   temperature. Change the target temperature there and check that the unit follows. If
   the card never gets a temperature, swap `tx_pin` and `rx_pin` in the YAML and update the
   board over Wi-Fi (step 5).

## 4. Add it to aircon

The panel's **+ Add** finds ESPHome boards on the network next to Haier modules. From a
terminal, `aircon setup` does the same, or add it directly:

```bash
aircon setup apply --backend esphome --host <board IP> --mac <board MAC> --name Bedroom
```

## 5. Update a board later

Edit its YAML and run `esphome run ac-bedroom.yaml` again; choose the board's IP address
(over the air) instead of a serial port. It updates and restarts in under a minute, without
opening the air conditioner.

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
