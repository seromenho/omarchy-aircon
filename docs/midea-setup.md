# Set up a Midea air conditioner

The plugin controls your AC over your home network. Newer V3 Wi-Fi modules need a local
token and key. Sign in to SmartHome once in the panel to obtain and save them automatically.
Normal control does not require the internet or a cloud account login.

## 1. Install SmartHome on your phone

Install **SmartHome (formerly MSmartHome)** by Midea:

- [Android: Google Play](https://play.google.com/store/apps/details?id=com.midea.ai.overseas)
- [iPhone: App Store](https://apps.apple.com/app/id1254346490)

Create a **fresh SmartHome account** and choose your country in the app's service-region
selector. This is separate from `msmart-ng`'s built-in account choices: its `US`, `DE` and
`KR` flags are not a list of supported countries.

Set an account **password** in SmartHome if you registered with an email verification code.
The plugin needs email/password login. Apple/Google sign-in or an email code alone is not
enough for this flow.

## 2. Add the AC in SmartHome

Connect your phone to the home Wi-Fi, then use **Add device** or **+** in SmartHome and
follow the instructions for your AC's Wi-Fi module. Many modules require a 2.4 GHz network;
follow the instructions for your model rather than using a generic remote-button sequence.

If you previously used NetHome Plus or Midea Air, add the device directly to the fresh
SmartHome account. Avoid the app's account-migration feature for token retrieval.
The [Midea AC LAN maintainers](https://github.com/wuwentao/midea_ac_lan#important-notice)
currently report that NetHome Plus cannot retrieve tokens and that migrated SmartHome
accounts may fail. Existing saved local tokens can still be used.

Confirm you can see and control the AC in SmartHome before continuing. The computer and AC
must be on the same home network. Reserving the AC's address in your router helps it stay
reachable after a restart.

## 3. Enable Midea support on the computer

For a first install or an older Midea dependency, open **SmartHome setup guide** in the panel
and click **Install Midea support**. It opens the installer in a terminal; no command typing
is needed. When it finishes, reopen the widget.

Alternatively, run this once in a terminal (also works in fish):

```sh
~/.config/omarchy/plugins/io.github.seromenho.aircon/install.sh --midea
```

This installs `msmart-ng` privately for the plugin. No root password or system-wide Python
packages are needed. It is separate from installing SmartHome on your phone.

## 4. Sign in and add the AC in the panel

1. Open the air conditioner widget and select **+ Add** if another AC is already configured.
2. Select **Midea / SmartHome**, then pick your AC from discovery and give it a name.
3. For a V3 device, enter your **SmartHome email** and **password**.
4. Click **Sign in and add**. Allow up to two minutes on a slow connection.

The plugin retrieves the token/key from SmartHome and verifies them by authenticating with
the AC. It then saves them in your private `~/.config/aircon/config.toml` (permissions `600`)
and starts local control immediately. You do not need to copy tokens or run a separate script.
The sign-in check does not change temperature, mode or power.

The email and password are used only for this setup attempt. They are not saved in the
config, passed as process arguments, or included in logs. The password field clears after
submission and when you leave setup. A failed attempt leaves the saved configuration intact.

V2 devices only need the discovered device ID; they do not need a cloud sign-in.

## Use credentials you already have

Enable **Use a saved token/key file**, enter your private TOML file path, then click **Add**.
For example:

```toml
device_id = "123456789"
protocol_version = 3
token = "YOUR_HEXADECIMAL_TOKEN"
key = "YOUR_64_HEXADECIMAL_CHARACTER_KEY"
```

Keep this file outside the repository and protect it with `chmod 600`. The plugin imports
its values into the private AC config. Saved credentials work without another cloud request.

## Terminal setup

Run `aircon setup --backend midea_lan` and select **Sign in to SmartHome** when asked. Or,
if you already know the device's address and ID:

```sh
aircon setup apply --backend midea_lan --host 192.168.1.50 \
  --device-id 123456789 --protocol-version 3 --smarthome --name "Living room"
```

Email and password are requested interactively; the password is hidden. Never put your
password in a command or paste it into a chat or bug report.

## Troubleshooting

- **AC not found:** check power, Wi-Fi and that the computer is on the same network.
  Use **Enter address** if discovery misses it. See the [reference](reference.md#midea-lan-notes)
  for saved discovery addresses. Saved addresses are hints; the panel also searches elsewhere
  on the network. ACs are marked already added by device ID, even if their IP address changes.
- **Sign-in failed:** use the fresh SmartHome account, not NetHome Plus credentials. Set
  a password if you normally use email codes. Check that the app's service region is correct.
- **Token/key could not be verified:** confirm the AC is added directly to this account
  and is online in SmartHome. A migrated account may fail. A cloud error code alone does not
  prove the password is wrong.
- **Missing or old Midea support:** rerun the `install.sh --midea` command above.
- **Popup closes during sign-in:** update to version 0.4.2 or later. Earlier versions could
  write a Python cache file inside the watched plugin folder and trigger a reload during
  the first sign-in. Installed commands now disable those writes. Tab also stays within
  setup inputs while you are editing.
- **Saved credentials already work:** keep using them; no need to sign in again. Back up
  your private AC config. Re-pairing the Wi-Fi module may require new credentials.
