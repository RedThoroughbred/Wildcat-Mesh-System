# Reticulum Test-Drive Runbook (Raspberry Pi 3 base station)

Run Reticulum (RNS) **alongside** `meshtasticd` on the Pi 3. No conflict: `meshtasticd` owns the Waveshare SX1262 HAT over SPI; Reticulum's radio (Tier 1) is a *separate* board on USB.

Verified against the official Reticulum manual (RNS 1.5.x, `reticulum.network/manual`) and the installed `rnodeconf --help` / NomadNet 1.4.x on 2026-09-20. Anything I could not confirm is marked **VERIFY**.

> **Important correction to the original plan:** the old "Reticulum Testnet" (Amsterdam / Dublin / Frankfurt hubs) is **decommissioned** per the Reticulum wiki. The manual now says to bootstrap from the **volunteer-run global backbone** using community directories (`directory.rns.recipes`, `rmap.world`) and Reticulum's built-in **interface discovery**. Tier 0 below does exactly that. Do not use `amsterdam.connect.reticulum.network`; it is only a placeholder in old examples.

---

## Constraints (read once)

- **An RNode board can't also run Meshtastic.** RNode firmware and Meshtastic firmware are either/or on a given board. Flashing the spare RAK4631 as an RNode takes it out of Meshtastic use until you reflash it.
- **A real over-the-air LoRa test needs TWO RNodes.** With one board you can prove the board works and that it transmits, but not a two-node RF link. Lean on the internet backbone for peers now; add a second board later.
- **Reticulum and Meshtastic don't interoperate** over the air (different protocols). Two independent stacks that happen to share the 902–928 MHz band, so they contribute noise to each other; keep the Reticulum frequency off Meshtastic's default slot (see Tier 1).
- **On the Pi they coexist fine:** RNode on **USB** (`/dev/ttyACM0`), meshtasticd on the HAT via **SPI**. Different radios, different device nodes, separate processes.
- Public backbone nodes are **third-party, volunteer-run**. Traffic is end-to-end encrypted, but you are connecting your Pi to strangers' servers; that is normal for Reticulum. Keep it to a test Pi, not your LAN's trust boundary.

---

## Tier 0: no hardware, about 10 minutes

### 0.1 Install (Pi OS Bookworm is "externally managed", PEP 668)

Plain `pip install rns` will fail on Bookworm with `externally-managed-environment`. The Reticulum manual gives two official options; use **pipx**:

```bash
sudo apt update
sudo apt install -y pipx
pipx ensurepath
# open a NEW shell (or: source ~/.bashrc) so ~/.local/bin is on PATH

pipx install rns
rns --version 2>/dev/null; rnsd --version
```

Alternatives (both documented by the project):
```bash
# one-shot override (does not actually break system packages, per the manual)
pip install rns --break-system-packages

# or a single venv holding everything (my preference if you also want nomadnet in the same env)
python3 -m venv ~/rns-venv && ~/rns-venv/bin/pip install rns nomadnet
# then use ~/rns-venv/bin/rnsd, ~/rns-venv/bin/rnstatus, ...  (or add ~/rns-venv/bin to PATH)
```

### 0.2 Generate the default config

```bash
rnsd --exampleconfig > /dev/null   # prints an example; harmless
rnstatus                            # first run creates ~/.reticulum/config and starts a shared instance
```
The config lives at **`~/.reticulum/config`**. It ships with a local `AutoInterface` (LAN peer discovery) already enabled.

### 0.3 Add public backbone interfaces

Edit `nano ~/.reticulum/config`.

**(a) In the `[reticulum]` section, turn on interface discovery** (lets you find local/relevant nodes later; it needs the `LXMF` module, which NomadNet pulls in; if `rnstatus -d` complains, run `pipx inject rns lxmf` (or `pip install lxmf` in your venv)):

```ini
[reticulum]
  # ...leave existing lines as they are...
  discover_interfaces = yes
```

**(b) Under `[interfaces]`, add two or three backbone entries.** These came from the live directory (`directory.rns.recipes`, "discovered" feed) on 2026-09-20 and were showing **online**; the Sideband hub entry is the one printed in the official manual. Use `BackboneInterface` (recommended on Linux; it's wire-compatible with the older `TCPClientInterface`):

```ini
  [[Drizzlebit US-West Gateway]]
    type = BackboneInterface
    enabled = yes
    remote = rns.drizzlebit.net
    target_port = 4242
    transport_identity = 4102c6cc250bdc6dd7f0d197eae8feb4

  [[Panic Public PDX]]
    type = BackboneInterface
    enabled = yes
    remote = 38.103.165.39
    target_port = 4242
    transport_identity = 593efebe017cb8b40dc141de2b0678d4

  [[Sideband Hub]]
    type = BackboneInterface
    enabled = yes
    remote = 45.59.114.96
    target_port = 7822
    transport_identity = 521c87a83afb8f29e4455e77930b973b
```

Notes:
- The manual's own example lists the Sideband hub as `sideband.connect.reticulum.network:7822` with transport identity `521c87a8...`; the directory currently shows it as `45.59.114.96:7822` ("Bern IPv4"). Either should work; if DNS is fine you can use the hostname.
- Prefer **redundant** entries (the manual says not to rely on a single connection).
- Volunteer nodes come and go. If one is down, get fresh entries: open **https://directory.rns.recipes** (or **https://rmap.world**), copy a block into `[interfaces]`, restart. Or use discovery (0.5).
- If `BackboneInterface` is rejected on your build, the equivalent fallback is:
  ```ini
  [[Panic Public PDX TCP]]
    type = TCPClientInterface
    enabled = yes
    target_host = 38.103.165.39
    target_port = 4242
  ```

### 0.4 Start and confirm connectivity

```bash
# if a shared instance is already running from the first rnstatus, restart it:
pkill -f rnsd 2>/dev/null; sleep 1
rnsd -v        # foreground with verbose logging; Ctrl+C when satisfied (or use section "Run as a service")
```
In a second terminal:
```bash
rnstatus
```
Expect one block per interface with **Status: Up** and rising `Traffic` / announce counters (`Rate` and `Announces` lines fill in within a minute or two as the network announces arrive). Lines to look for: `BackboneInterface[Drizzlebit US-West Gateway] ... Status : Up`. If an entry shows Down, check the log from `rnsd -v` (DNS/firewall/port blocked) and try another entry.

Handy tools:
```bash
rnstatus -a          # show all interfaces incl. hidden
rnpath <hexhash>     # is there a path to a destination, how many hops, via which interface
rnprobe <aspect> <hexhash>   # ping-like; only works if the target answers probes
```

### 0.5 Optional: discover nodes instead of hand-copying

With `discover_interfaces = yes` set, after a few minutes:
```bash
rnstatus -d          # list discovered interfaces
rnstatus -D <name>   # details + a paste-ready [[...]] config block
```
To let Reticulum manage it automatically (uses your entries only to bootstrap):
```ini
[reticulum]
  discover_interfaces = yes
  autoconnect_discovered_interfaces = 3
```
and add `bootstrap_only = yes` to a backbone interface block if you want it dropped once enough discovered links are up. Start manual; add this later.

### 0.6 NomadNet (text-UI messenger and page browser)

```bash
pipx install nomadnet          # per the project README; if you use the venv: pip install nomadnet
nomadnet                       # launches the text UI; it reuses the running rnsd shared instance
```
Other modes: `nomadnet --daemon`, `nomadnet --daemon --console`, `nomadnet --help`.

Basics (the built-in **Guide** on first launch is the authoritative walkthrough; read it):
- **Conversations** tab: LXMF messaging. Your **LXMF address** (a 32-hex-char hash) is shown in the UI; give it to others so they can message you. Messages queue and deliver when both sides are reachable or via a propagation node.
- **Network** tab: shows **announced nodes** heard on the network. Select a node and press Enter to browse its pages (micron markup, text "websites"). Nodes appear as announces arrive; give it several minutes.
- To open a node you know but haven't heard: in the Network view press **Ctrl+U**, paste the 32-hex node address, choose **Go**. (Addresses in the README such as `abb3ebcd...` "Dublin Hub" and `ea6a715f...` "Frankfurt Hub" belonged to the retired Unsigned.io testnet; **VERIFY**, they may no longer respond.)
- Quit with **Ctrl+Q** (see the on-screen key hints).

**Sideband** (GUI / mobile): the same ecosystem with an app UI; Android install and desktop builds are linked from `https://unsigned.io/sideband` and `https://github.com/markqvist/sideband`. Use it on a phone to message your Pi's NomadNet address. Sideband can also connect to the same backbone entries.

### 0.7 Run rnsd as a service (so the stack survives reboots)

```bash
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/rnsd.service <<'EOF'
[Unit]
Description=Reticulum Network Stack Daemon
After=network-online.target

[Service]
ExecStart=%h/.local/bin/rnsd --service
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
EOF
# venv users: change ExecStart to %h/rns-venv/bin/rnsd --service
sudo loginctl enable-linger "$USER"      # keep user services running without a login
systemctl --user daemon-reload
systemctl --user enable --now rnsd
systemctl --user status rnsd --no-pager
journalctl --user -u rnsd -f             # log (Ctrl+C to stop)
```
(Stop any foreground `rnsd -v` first, or you'll get a port-in-use error.)

**Tier 0 done when:** `rnstatus` shows at least one Up backbone interface, NomadNet's Network tab starts listing nodes, and you've browsed a node page or sent a message.

---

## Tier 1: a real LoRa Reticulum node (RNode)

Target board: **RAK WisBlock RAK4631** (SX1262, nRF52840). Alternative: **Heltec LoRa32 V3** (SX1262, ESP32-S3; listed as supported in the manual; the manual also lists v4.0 and v2.0). Also supported per the manual: T-Beam, T3S3, T-Echo, and others.

> **Antenna first.** Attach a 915 MHz antenna to the LoRa antenna connector *before powering or transmitting*. Never transmit into an open port.

### 1.1 Get the board ready

- RAK4631: WisBlock Core on a base board (e.g., RAK19007), **USB-C to the Pi**. Antenna on the LoRa connector. (Its native USB comes up as a CDC serial device, normally **`/dev/ttyACM0`**.)
- Heltec V3: USB-C to the Pi (also `ttyACM0`/`ttyUSB0`, depending on board).
- The board may currently carry Meshtastic; autoinstall overwrites it. Flashing does **not** touch your Meshtastic HAT node.
- Give your user serial access (Pi OS):
  ```bash
  sudo usermod -aG dialout "$USER"
  # log out/in (or reboot) so the group applies
  ls -l /dev/serial/by-id/      # stable names for the board
  dmesg | tail -20              # confirm the board enumerated after plugging in
  ```
- Careful with **which port** is the board: with `meshtasticd` on the HAT there should be no other serial devices unless you have a GPS/USB node attached. Unplug/plug the board and watch `dmesg` so you pick the right one.

### 1.2 Flash the RNode firmware (autoinstall)

`rnodeconf` comes with `pip install rns` (already there from Tier 0). Needs internet (it downloads the current signed firmware release).

```bash
rnodeconf --autoinstall
```
It is interactive. Answer as follows (exact prompts can change between releases; choose the equivalent):
1. **Select the serial port** the board is on (it lists detected ports; pick the number).
2. **Select the product**: `RAK4631` (or `Heltec LoRa32 v3` for the alternative).
3. **Select the band/model**: `915 MHz` (for RAK4631 the menu offers 433 / 868 / **915** / 923 MHz). Pick the module frequency variant that matches your hardware; a RAK4631 with an 868/915 RAK13300 module is the same silicon; the band choice sets the allowed-frequency range recorded in the RNode's EEPROM.
4. Let it download firmware, flash, and **provision** (it writes the device signature and EEPROM info and then reports success).

Board-specific gotchas (**VERIFY**, from typical experience and the installer's board handling, not the manual):
- **RAK4631 (nRF52)**: it flashes via the board's Adafruit-style USB bootloader. If the flash step cannot see the device or times out, **double-tap the reset button** to force the bootloader (the board re-enumerates as a drive/serial device), then rerun `rnodeconf --autoinstall`. Give it extra time; the installer waits longer for RAK4631 writes.
- **Heltec V3 (ESP32-S3)**: if the port is not detected or flashing fails, put it in bootloader mode: **hold PRG/BOOT, tap RST (or plug in USB while holding PRG)**, release, and rerun.
- If it fails midway, unplug/replug and rerun; the utility is safe to repeat. `rnodeconf -C` clears the cached firmware if you suspect a bad download.

### 1.3 Confirm it came up as an RNode

```bash
rnodeconf /dev/ttyACM0 -i      # use your real port, e.g. /dev/serial/by-id/usb-RAK...
```
You should see device info such as **Product: RAK4631**, firmware version, hardware/frequency range (**779–928 MHz** for the 915 variant per the installer's model table), and a **validated device signature / "Device is provisioned"**-type line. If it says the device is unprovisioned or unknown, rerun autoinstall.

Also useful:
```bash
rnodeconf /dev/ttyACM0 -c      # print current stored configuration
rnodeconf /dev/ttyACM0 -N      # ensure NORMAL mode (this is what RNS wants; not TNC mode)
```

### 1.4 Choose LoRa parameters (US 915)

Both ends must use **identical** frequency, bandwidth, spreading factor and coding rate, or they will not hear each other. There is no single official US default; use a sensible community setting:

| Param | Value | Why |
|---|---|---|
| Frequency | **914875000** Hz (914.875 MHz) | A common US Reticulum community choice (e.g., Chicagoland Reticulum), inside 902–928 MHz. It is **not** Meshtastic's default US LongFast slot (906.875 MHz), so you won't sit directly on the default Meshtastic channel |
| Bandwidth | **125000** | Standard balance of range and speed |
| Spreading factor | **8** | Community-common; 7 = faster/shorter range, 12 = slowest/longest |
| Coding rate | **5** | Fastest; 8 = most robust |
| TX power | **17** dBm to start | RAK4631 module supports up to 22 dBm; with a small antenna 17 is plenty for a bench test and stays well inside US limits. Raise only if you need range, and mind antenna gain |
| Airtime limits | `airtime_limit_long = 1.5`, `airtime_limit_short = 33` | Optional politeness, shown as options in the manual |

**Check the frequency plan with your local operators:** if a local Reticulum group already runs a different frequency/SF, use theirs instead. You are responsible for staying legal for your area (the manual's own warning; 902–928 MHz ISM band rules apply in the US, including power and antenna-gain limits).

### 1.5 Add the RNode as a serial interface

Edit `~/.reticulum/config` (keep or disable the TCP entries; a Pi with both is a fine **bridge** between LoRa and the internet, see note below):

```ini
  [[RNode LoRa Interface]]
    type = RNodeInterface
    enabled = yes
    port = /dev/ttyACM0
    frequency = 914875000
    bandwidth = 125000
    txpower = 17
    spreadingfactor = 8
    codingrate = 5
    # optional, only if you hold an amateur licence (not required for ISM use):
    # id_callsign = MYCALL-0
    # id_interval = 600
    airtime_limit_long = 1.5
    airtime_limit_short = 33
```
Prefer the stable path so the port name doesn't shift:
`port = /dev/serial/by-id/usb-...` (from `ls /dev/serial/by-id/`).

Restart Reticulum:
```bash
systemctl --user restart rnsd          # or: pkill -f rnsd; rnsd -v
journalctl --user -u rnsd -n 50 --no-pager
```
The log should include the RNodeInterface being configured and "powered up" (**VERIFY** wording), with the parameters you set. Errors like "could not open port" mean the wrong port or missing `dialout` membership; "device not provisioned" means rerun autoinstall.

### 1.6 Verify it is live and announcing

```bash
rnstatus
```
You should see a line like `RNodeInterface[RNode LoRa Interface]` with **Status: Up**, plus the configured frequency/bandwidth/SF, and a channel airtime/utilisation figure. `rnstatus rnode` filters to it.

**Announce test.** Any announce leaves through all interfaces including the RNode, so it exercises the radio (TX counters and the board's activity LED should react):

```bash
cat > ~/announce_test.py <<'EOF'
import RNS, time
r = RNS.Reticulum()
d = RNS.Destination(RNS.Identity(), RNS.Destination.IN, RNS.Destination.SINGLE, "wildcat", "testdrive")
d.announce(app_data=b"wildcat-testdrive")
print("announced", RNS.prettyhexrep(d.hash))
time.sleep(5)
EOF
# run with the same Python environment that has rns installed:
#   pipx: ~/.local/share/pipx/venvs/rns/bin/python ~/announce_test.py
#   venv: ~/rns-venv/bin/python ~/announce_test.py
```
Then run `rnstatus` again and compare **TX** bytes/packets on the RNode interface before vs after. (Also: NomadNet's announce action, if present in your version, does the same. **VERIFY** the key in its Guide.)

With **only one RNode** you can confirm: interface Up, TX counters rise, LED activity, and airtime figures. You **cannot** confirm reception by a second radio until you have another RNode (or someone local on the same parameters). Meanwhile, the backbone interfaces keep giving you peers.

**When you add a second RNode:** put it on another machine (or your laptop), same frequency/BW/SF/CR, then use `rnpath`/`rnprobe` or a NomadNet/Sideband message between the two over LoRa alone (unplug the internet interfaces to prove it is the radio path).

### 1.7 Bridging note

A Pi running both the RNode and the backbone interfaces is a natural gateway: internet peers can reach LoRa-only peers through it, if you set `enable_transport = yes` in `[reticulum]`. Leave it off (default) while testing; enabling Transport makes your Pi relay traffic for others, which is fine for a home base but is a deliberate choice.

---

## Coexistence with meshtasticd (sanity checks)

```bash
systemctl status meshtasticd --no-pager     # Meshtastic on the HAT keeps running
ls /dev/ttyACM* /dev/serial/by-id/          # RNode lives here (USB), unrelated to SPI
ls /dev/spidev*                             # HAT's SPI, owned by meshtasticd
```
- Reticulum never touches SPI; the RNode never touches the HAT.
- Do not point `meshtasticd` at the RNode's serial port and do not point RNS at anything meshtasticd owns.
- CPU/RAM on a Pi 3 (1 GB) is fine for both; NomadNet's text UI is the heaviest part. Run it only when you want it.
- Two 915 MHz radios one board-width apart will desense each other if both transmit; keep the two antennas a few feet apart for testing, and don't transmit on both simultaneously at high power during the bench.

## Troubleshooting quick list

| Symptom | Likely cause / fix |
|---|---|
| `pip install rns` says externally-managed-environment | Use pipx or the venv (0.1) |
| `rnstatus`: "Could not get RNS status" | `rnsd` not running or not shared; start it (0.4 / 0.7) |
| Backbone entry Down | Server offline or port blocked; try another entry from the directory |
| Nothing in NomadNet Network tab | Wait several minutes; confirm an interface is Up |
| `rnodeconf` can't find device | Wrong port, cable is charge-only, not in `dialout`, or not in bootloader mode (1.2) |
| RNode won't open in rnsd | Port name changed (use by-id), another process holds it, or device in TNC mode (`rnodeconf <port> -N`) |
| Two RNodes don't hear each other | Any mismatch in frequency / bandwidth / SF / CR; antennas; distance too small (overload) |

## References
- Reticulum manual: https://reticulum.network/manual/ (Getting Started Fast, Interfaces, Hardware/RNode, Using Reticulum)
- Bootstrapping connectivity / directories: https://directory.rns.recipes , https://rmap.world
- RNode firmware: https://github.com/markqvist/RNode_Firmware
- NomadNet: https://github.com/markqvist/NomadNet ; Sideband: https://github.com/markqvist/sideband
- Community LoRa parameter examples: https://chicagolandmesh.org/guides/reticulum/getting-started/configure/
