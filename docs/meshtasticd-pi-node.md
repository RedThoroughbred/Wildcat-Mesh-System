# meshtasticd node on a Raspberry Pi 3B + Waveshare SX1262 HAT

Runbook for a dedicated 24/7 Meshtastic node (`meshtasticd`, the native Linux
daemon) using a **Waveshare SX1262 LoRaWAN/GNSS HAT** on a **Raspberry Pi 3B**.
Written before the hardware arrived, from the Meshtastic docs and the community
script at `github.com/FixedBit/meshtastic_scripts`. Nothing here has been run on
the actual Pi yet, so the **Troubleshooting** section matters.

Companion script: `docs/install.sh` (same folder). Everything below can be done
by hand; the script just does it idempotently.

---

## 0. Recommendation (read this first)

**Use a fresh SD card (Path A).** A dedicated node should be boring: Raspberry Pi
OS **Lite**, nothing else installed. Path B (clone PiFlip) works, but PiFlip's
CC1101 / PN532 / RTL-SDR hardware is wired to the same 40-pin header (SPI0) the
HAT needs, so PiFlip and the HAT can't share one Pi anyway. Clone only if you
want a *card* that can be swapped between the two setups.

Suggested hostname: `wildcat-mesh1`.

---

## 1. SD card prep

### Path A: Fresh (recommended)

1. On the Mac: install **Raspberry Pi Imager** (`brew install --cask raspberry-pi-imager`).
2. Choose device **Raspberry Pi 3**, OS **Raspberry Pi OS (other) -> Raspberry Pi OS Lite**,
   storage = the 64 GB card.
   - Either 32-bit or 64-bit Lite works. On a 1 GB Pi 3B, **32-bit Lite** is the lighter default and
     matches Meshtastic's `Raspbian_*` repo. 64-bit uses the `Debian_*` repo. `install.sh`
     detects which.
3. Click **Next -> Edit Settings** and set:
   - Hostname: `wildcat-mesh1`
   - Username + password (not the default `pi`/`raspberry`)
   - Wi-Fi: SSID, password, **country = US**
   - Locale/timezone
   - Services: **enable SSH**, ideally *allow public-key only* and paste your Mac's `~/.ssh/id_ed25519.pub`
4. Write, boot the Pi (HAT can be attached now, but see the HAT jumper notes in Section 3),
   wait ~2 minutes, then from the Mac:
   ```bash
   ssh <user>@wildcat-mesh1.local
   ```
   (If `.local` doesn't resolve, find the IP in the router or with `arp -a | grep -i b8:27:eb`.)

PiBakery is an alternative for pre-baking settings, but Imager's built-in
customization now covers the same ground. Skip it.

### Path B: Clone the PiFlip card (only if you want one card that does both)

1. Clone 32 GB -> 64 GB:
   - **Easiest:** on a Pi, Raspberry Pi OS **SD Card Copier** (Accessories). Source = PiFlip card, target = 64 GB card
     in a USB reader. **Tick "New Partition UUIDs"**. It auto-scales the last partition to fill the card.
   - **From the Mac:** balenaEtcher "Clone drive", or `dd` (double-check disk numbers with
     `diskutil list`, wrong target = data loss):
     ```bash
     diskutil unmountDisk /dev/diskSRC && diskutil unmountDisk /dev/diskDST
     sudo dd if=/dev/rdiskSRC of=/dev/rdiskDST bs=4m status=progress
     ```
     `dd` does NOT expand the filesystem; do step 3 below.
2. **MUST-DO on first boot of the clone** (before putting it on the LAN next to PiFlip, if you can; otherwise do it fast):
   ```bash
   # a) New hostname (avoid a second piflip.local)
   sudo hostnamectl set-hostname wildcat-mesh1
   sudo sed -i 's/piflip/wildcat-mesh1/g' /etc/hosts     # adjust if the old name differs
   # b) Fresh SSH host keys
   sudo rm -f /etc/ssh/ssh_host_*
   sudo dpkg-reconfigure openssh-server && sudo systemctl restart ssh
   # c) New machine-id (systemd + dbus + DHCP client id)
   sudo rm -f /etc/machine-id /var/lib/dbus/machine-id
   sudo systemd-machine-id-setup
   sudo ln -sf /etc/machine-id /var/lib/dbus/machine-id
   # d) Any VPN/agent identity copied from PiFlip (only if installed there):
   #    Tailscale: sudo systemctl stop tailscaled; sudo rm /var/lib/tailscale/tailscaled.state; then re-auth
   sudo reboot
   ```
3. Expand the rootfs to fill 64 GB if the tool didn't:
   ```bash
   sudo raspi-config nonint do_expand_rootfs && sudo reboot
   df -h /        # after reboot: should be ~58 GB
   ```
4. Optional: disable PiFlip services this node doesn't need. Look before you disable, service names weren't
   available when this was written:
   ```bash
   systemctl list-unit-files --state=enabled --type=service
   sudo systemctl disable --now <piflip-service-name>
   ```
5. Install meshtasticd **after** the clone/identity steps. If meshtasticd was already run on the source
   card, `sudo systemctl stop meshtasticd && sudo rm -rf /var/lib/meshtasticd` on the clone so it gets its own node ID and keys.

---

## 2. Install meshtasticd + HAT config (the fast way)

On the Pi (copy `install.sh` over first: `scp install.sh <user>@wildcat-mesh1.local:`):

```bash
sudo apt update && sudo apt full-upgrade -y     # optional but a good first step
chmod +x install.sh
DRY_RUN=1 sudo -E ./install.sh                  # optional: preview every change
sudo ./install.sh                               # phase 1
sudo reboot
sudo ./install.sh configure                     # phase 2: region US + name
sudo ./install.sh verify                        # phase 3: checks
```

Override defaults with env vars, e.g.
`sudo CHANNEL=beta NODE_LONG="Wildcat Mesh 1" NODE_SHORT=WM1 ./install.sh configure`.

### What phase 1 does (so you can do it by hand)

1. **Enable SPI, serial hardware (for the HAT's GNSS), disable serial login console; release CE0:**
   ```bash
   sudo raspi-config nonint do_spi 0
   sudo raspi-config nonint do_serial_cons 1      # no login shell on the serial port
   sudo raspi-config nonint do_serial_hw 0        # hardware UART on
   # append to /boot/firmware/config.txt  (older images: /boot/config.txt)
   echo 'dtoverlay=spi0-0cs' | sudo tee -a /boot/firmware/config.txt
   ```
   I2C isn't needed: the HAT's L76K GNSS is UART, and the SX1262 is SPI.
2. **Add the Meshtastic apt repo and install** (channel `beta` per current docs; `alpha`/`daily` also exist):
   - 32-bit Raspberry Pi OS, Debian 13 "trixie":
     ```bash
     echo 'deb http://download.opensuse.org/repositories/network:/Meshtastic:/beta/Raspbian_13/ /' | sudo tee /etc/apt/sources.list.d/network:Meshtastic:beta.list
     curl -fsSL https://download.opensuse.org/repositories/network:Meshtastic:beta/Raspbian_13/Release.key | gpg --dearmor | sudo tee /etc/apt/trusted.gpg.d/network_Meshtastic_beta.gpg > /dev/null
     sudo apt update && sudo apt install -y meshtasticd
     ```
   - 32-bit + bookworm: replace `Raspbian_13` with `Raspbian_12`.
   - 64-bit Pi OS: replace `Raspbian_13` with `Debian_13` (or `Debian_12`).
   - Check yours: `dpkg --print-architecture` (armhf = 32-bit, arm64 = 64-bit) and `. /etc/os-release; echo $VERSION_CODENAME`.
3. **HAT config**, create `/etc/meshtasticd/config.d/lora-waveshare-sx1262.yaml`:
   ```yaml
   Lora:
     Module: sx1262
     DIO2_AS_RF_SWITCH: true
     CS: 21
     IRQ: 16
     Busy: 20
     Reset: 18
     # SX126X_ANT_SW: 6      # uncomment if TX is dead/weak (TXEN is on BCM6)

   GPS:
     SerialPath: /dev/ttyS0
   ```
   Pins are BCM GPIO numbers. Source: community config for exactly this HAT
   (github.com/FixedBit/meshtastic_scripts `example_config/config.yaml`);
   Waveshare's wiki confirms TXEN=BCM6, RXEN tied to DIO2, DIO1 = interrupt.
   The main `/etc/meshtasticd/config.yaml` from the package already loads `config.d/`.
4. `sudo systemctl enable meshtasticd`, then **reboot** (the SPI/UART overlays only take effect after a reboot).

### Set region and name (phase 2, by hand)

```bash
meshtastic --host localhost --set lora.region US
meshtastic --host localhost --set-owner "Wildcat Mesh 1" --set-owner-short WM1
```
Region **US** = US915. Default modem preset is LONG_FAST. If your mesh uses
other channels (cincymesh, etc.), add those with `meshtastic --host localhost --ch-add ...`
or copy the URL from another node: `meshtastic --host localhost --seturl "<url>"`.
The CLI itself installs with `sudo apt install pipx && sudo PIPX_HOME=/opt/pipx PIPX_BIN_DIR=/usr/local/bin pipx install "meshtastic[cli]"`.

### Variants: what to change if your HAT differs

| Your board | Change |
|---|---|
| **SX1262 LoRaWAN/GNSS HAT** (this runbook) | nothing, use the config above |
| Same HAT **without** GNSS | delete the `GPS:` block |
| Pi **5** or Pi 4 using `/dev/ttyAMA0` for GPS | set `SerialPath: /dev/ttyAMA0` (community script does this for Pi 5) |
| Waveshare **"SX1262 LoRa HAT" UART type** (E22-style module with a serial interface, not SPI) | this runbook does **not** apply, meshtasticd needs an SPI radio. |
| SX1268 / 433-470 MHz HAT | wrong band for US915. Get the 868/915 MHz version. |
| Different pin numbers on your silkscreen/wiki | edit `CS/IRQ/Busy/Reset` (and `SX126X_ANT_SW`) to match the wiki's BCM numbers |

HAT hardware to check with the board in hand: I could not confirm from the
Waveshare wiki whether the GNSS UART is jumper-selectable, so look over the
board for jumper caps/solder pads and check the wiki's GNSS section. The GNSS
should connect to the Pi's UART pins (BCM14/15). Attach the antenna to the
**LoRa** SMA **before applying power**; transmitting into no antenna can damage the SX1262.

---

## 3. Power trimming for battery/solar (optional)

WiFi and Bluetooth stay **on** (you need WiFi). Run `sudo ./install.sh powersave`
or apply by hand to `/boot/firmware/config.txt` (a backup is saved once as
`config.txt.wildcat-orig`):

| Trim | How | Rough saving (Pi 3B, 5 V) |
|---|---|---|
| **HDMI off** | on headless Lite, comment out `dtoverlay=vc4-kms-v3d` (and `max_framebuffers=`). (`tvservice -o` doesn't work with the KMS driver on Bookworm+) | ~20-25 mA, **~0.1-0.13 W** (biggest single item) |
| **LEDs off** | `dtparam=act_led_trigger=none`, `dtparam=act_led_activelow=off`, `dtparam=pwr_led_trigger=none`, `dtparam=pwr_led_activelow=off` | ~5 mA each, **~0.03-0.05 W** total |
| **CPU cap** | `arm_freq=600` + `arm_freq_min=600` (meshtasticd is light) | ~0 W at idle (ondemand already idles ~600 MHz); **~0.1-0.3 W** under load |
| **WiFi power-save** (WiFi stays on) | `nmcli connection modify "<wifi>" 802-11-wireless.powersave 3` | ~0.1-0.2 W; costs a bit of inbound-connection latency |
| **Audio off** | `dtparam=audio=off` | negligible |
| **Idle services** | `sudo systemctl disable --now triggerhappy ModemManager apt-daily.timer apt-daily-upgrade.timer` | small CPU wakeups, fractions of a watt |
| **log2ram** | `sudo ./install.sh log2ram`, then reboot | **~0 W**, but greatly reduces SD-card writes on a 24/7 node. Uses 128 MB of the 1 GB RAM (edit `SIZE=` in `/etc/log2ram.conf`). |

Ballpark totals: a headless Pi 3B with WiFi idles around **1.3-1.7 W**; the HAT
adds roughly **0.1-0.2 W** listening (more while transmitting). The trims above
together get you maybe **0.3-0.5 W** back, real but modest. These are rough
community figures, not measurements of this board; measure with a USB power meter
before sizing a solar panel/battery. **Not recommended:** cutting power to the Pi's
USB/Ethernet hub, or disabling Bluetooth (you said keep them).

To undo HDMI/LED/CPU edits: `sudo cp /boot/firmware/config.txt.wildcat-orig /boot/firmware/config.txt && sudo reboot`
(this also drops the `spi0-0cs` line, so re-run `sudo ./install.sh` afterwards).

---

## 4. Verification

```bash
sudo ./install.sh verify      # runs all of the below
```

By hand:

1. **Service running:** `systemctl status meshtasticd` -> `active (running)`; logs: `sudo journalctl -u meshtasticd -f`
2. **SPI present:** `ls /dev/spidev*` -> `/dev/spidev0.0`
3. **Radio detected:** in the journal look for the SX1262 init/"Radio" lines and **no** repeated `Reset`/`Busy`/`Failed to init radio` errors.
4. **Node info:** `meshtastic --host localhost --info` -> shows your owner name, region `US`, firmware version.
5. **On the mesh:** wait a few minutes (LongFast beacons are infrequent) and run
   `meshtastic --host localhost --nodes`. You should see nearby nodes (e.g. your Den node `6b18`, GO,
   STAY). From another node/app, look for `Wildcat Mesh 1`. To force it, send a message:
   `meshtastic --host localhost --sendtext "wildcat-mesh1 online"`.
6. **Connect the app / CLI:**
   - CLI from the Mac: `pip install meshtastic` then `meshtastic --host wildcat-mesh1.local --info`
   - Meshtastic app (iOS/Android): Connections -> **+ / Network** -> host `wildcat-mesh1.local` (or the Pi's IP), TCP port **4403** (default).
   - Web client: `https://wildcat-mesh1.local/` (port 443, self-signed certificate; accept the warning).

---

## 5. Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `meshtasticd` exits immediately or logs no radio | Reboot pending after `spi0-0cs`; `ls /dev/spidev*`; check `grep -E 'spi|uart' /boot/firmware/config.txt`; confirm HAT is fully seated on the 40-pin header |
| `Failed to init`/Busy timeout | wrong pins, recheck against Waveshare's wiki table for **your** HAT revision and edit the yaml |
| Node shows up but can't be heard / very short range | antenna not on the **LoRa** connector; try uncommenting `SX126X_ANT_SW: 6`; confirm region US and preset LONG_FAST |
| GPS never gets a fix | `GPS:` path wrong for your OS (try `/dev/ttyAMA0`), UART jumpers on the HAT, serial console still enabled (`sudo raspi-config nonint do_serial_cons 1`), no sky view |
| `E: Unable to locate package meshtasticd` | repo line/OS mismatch, re-check `dpkg --print-architecture` + `VERSION_CODENAME` against Section 2 |
| Two Pis fighting over IP/hostname | clone path: redo Section 1B step 2 (hostname, ssh keys, machine-id) |
| Config edits don't stick | `sudo systemctl restart meshtasticd` after editing `config.d/*.yaml` |

## Sources
- Meshtastic meshtasticd docs: https://meshtastic.org/docs/meshtasticd/ (Debian/Raspbian install pages)
- Community Waveshare HAT scripts + example config: https://github.com/FixedBit/meshtastic_scripts
- Waveshare SX1262 LoRaWAN/GNSS HAT wiki (pin table): https://www.waveshare.com/wiki/SX1262_XXXM_LoRaWAN/GNSS_HAT
- log2ram: https://github.com/azlux/log2ram
