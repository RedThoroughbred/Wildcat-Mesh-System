# Wildcat "Mega Node" — a DIY Station-G2-class high-power Meshtastic node

*Build spec, 2026-09-14. A 1 W (30 dBm) ESP32-S3 + Ebyte E22-900M30S node with a
low-noise receive front end, tuned with the NanoVNA. Written for the Den's backbone
role: a fixed, mains-powered node that hears more and reaches further than a
handheld. Read "Legal" before you key it up.*

---

## 0. First, the honest comparison — should you build this at all?

| | **DIY mega node (this spec)** | **B&Q Station G2** | **B&Q Station G3** |
|---|---|---|---|
| Exists / status | you build it | out of stock since Sep 2025 ([Tindie](https://www.tindie.com/products/neilhao/meshtastic-mesh-device-station-g2/)) | **yes, it exists** — successor to the G2, modular (motherboard + RF + MCU daughterboards), sold by BQ Voyage ([store](https://store.bqvoy.com/product/meshtastic-mesh-device-station-edition/), [wiki](https://wiki.bqvoy.com/en/devkits/station-g3)) |
| MCU | ESP32-S3 N16R8 | ESP32-S3 16 MB / 8 MB | ESP32-S3 16 MB / 8 MB |
| TX | E22-900M30S: **30 dBm (1 W)** rated | 36.5 dBm (4.5 W) PA | 36.8 ±2 dBm (~4.7 W) US915 |
| RX front end | E22's own LNA + optional external LNA (this spec) | LNA 18.5 dB gain / 1.8 dB NF | LNA 10–16 dB **dynamic** gain / 1.5 dB NF, bypass for high-noise sites |
| Price | ≈ $65–95 without the external LNA stage, ≈ $110–170 with it | $109 | **$109 sale (list $159)**; next batch listed for 22-Sep-2026 |
| What you learn | everything | nothing | nothing |

**Straight answer:** for the Den's *backbone* node, the Station G3 at $109 is the
better radio than anything you'll solder from modules — its receiver (1.5 dB NF,
gain that backs off when a nearby cell tower would overload it) is the part that
actually makes a base station hear the edge of the mesh, and that part is hard to
DIY well. **Build this spec because you want to** (the VNA work, the understanding,
a second high-power node for a different site), not because it's cheaper per dB.
The G3 numbers above are from the vendor's pages as of today; confirm them on the
store page before ordering — stock has been intermittent and specs are marked
"±2 dBm".

> Sources: [Station G2 on Tindie](https://www.tindie.com/products/neilhao/meshtastic-mesh-device-station-g2/) ·
> [BQ Station G3 store page](https://store.bqvoy.com/product/meshtastic-mesh-device-station-edition/) ·
> [NWI Mesh's G3 notes](https://nwimesh.net/station-g3) · [Meshtastic firmware PR #3282 (Station G2)](https://github.com/meshtastic/firmware/pull/3282)

---

## 1. Parts list (rough US prices, Sep 2026)

### Core (≈ $65–95)

| # | Part | Notes | ≈ Price |
|---|---|---|---|
| 1 | **ESP32-S3-DevKitC-1 N16R8** (16 MB flash, 8 MB PSRAM) | the Station-G class MCU; Wi-Fi for MQTT/Den uplink; existing community variants target exactly this board | $9–14 |
| 2 | **Ebyte E22-900M30S** | SX1262 + PA + LNA + RF switch, 30 dBm, 850–930 MHz, SPI, 32 MHz TCXO, IPEX antenna pad ([Ebyte](https://www.cdebyte.com/products/E22-900M30S)). Buy from Ebyte's official AliExpress/Amazon store — clones exist. **Not** the M33S (different drive level; the M30S code will burn an M33S) | $9–20 |
| 3 | IPEX/U.FL → SMA-female bulkhead pigtail, 10–15 cm | RG178/1.13 mm; keep it short | $3–5 |
| 4 | 5 V ≥ 3 A supply: USB-C PD trigger board (5 V) **or** 12 V → 5 V buck (≥ 3 A) | the PA pulls ~0.65–0.7 A bursts at 30 dBm; USB from a laptop port is NOT enough | $5–10 |
| 5 | Decoupling: 100 nF ceramic + 10 µF ceramic + **1000–2200 µF** low-ESR electrolytic, right at the E22 VCC pin | the single most common cause of "resets on TX" | $2 |
| 6 | 915 MHz antenna: 5.8 dBi fiberglass omni (N-female) or 3 dBi rubber duck for the bench | see Legal for gain vs power | $20–45 |
| 7 | Coax: LMR-400 (or LMR-240 for < 5 m) with N/SMA ends | LMR-400 ≈ 0.13 dB/m at 915 MHz; RG-58 ≈ 0.5 dB/m — don't | $1–2/ft |
| 8 | IP65 enclosure, cable glands, SMA bulkhead | outdoor if the antenna is | $12–25 |
| 9 | Dupont/JST wiring, 2.54 mm headers, small perfboard | or design a carrier PCB later | $5 |
| 10 | 30 dB / 5 W SMA attenuator | for measuring the transmitter safely (never key 1 W into a VNA or SDR) | $10–15 |

### Receive front end, Phase 2 (≈ +$45–75) — read §4 before buying

| # | Part | Notes | ≈ Price |
|---|---|---|---|
| 11 | 915 MHz **SAW band-pass filter** board (e.g. an 902–928 MHz SAW on an SMA breakout) | keeps LTE/700–800 MHz cell out of the LNA | $8–15 |
| 12 | **LNA**: SPF5189Z-based board (50–4000 MHz, ~0.6 dB NF, ~19 dB gain @ 900 MHz) or a purpose 915 MHz LNA | wideband → the SAW in front is mandatory | $6–25 |
| 13 | **T/R switch rated for ≥ 1 W**: a small SMA coax relay (12 V) **or** a solid-state SPDT rated ≥ +33 dBm | most $5 SPDT boards (PE4259 class) are 0.5 W parts — they will die | $15–60 |
| 14 | 12 V relay driver (transistor + flyback diode) or the switch's own logic input | driven from the TXEN line | $2 |

**Skip on the first build:** the E22-900M30S already has an LNA behind its own RF
switch. An external stage only pays when the coax run is long enough to eat the
noise figure (mast-mounted LNA, ≥ 3 dB of cable) or when you want G3-class NF and
are prepared to protect it. §4 explains the trap.

---

## 2. Wiring — ESP32-S3-DevKitC-1 ↔ E22-900M30S

E22 S-series pin table (SPI variants share the footprint; confirm against the
E22-900M30S datasheet PDF in Ebyte's downloads before soldering — pin *numbers*
below are from the M22S sibling, the *names* are what matter):

| E22 pin | Name | → ESP32-S3 GPIO | Function |
|---|---|---|---|
| 1–5, 10–12, 20, 22 | GND | GND | ground — use several, short |
| 9 | VCC | **5 V** (not 3V3) | 3.3–5.5 V; 5 V for full 30 dBm; decouple here |
| 19 | NSS | GPIO 10 | SPI chip select |
| 18 | SCK | GPIO 11 | SPI clock |
| 17 | MOSI | GPIO 12 | SPI |
| 16 | MISO | GPIO 7 | SPI |
| 15 | NRST | GPIO 6 | reset (active low) |
| 14 | BUSY | GPIO 5 | SX1262 busy |
| 13 | DIO1 | GPIO 4 | IRQ |
| 8 | DIO2 | — | drives the module's internal PA/LNA switch (firmware: `DIO2_AS_RF_SWITCH`); leave unconnected |
| 6 | RXEN | GPIO 13 | receive enable |
| 7 | TXEN | GPIO 14 | transmit enable — **also** the control line for the Phase-2 T/R switch |
| 21 | ANT | IPEX → SMA | antenna |

Notes
- The SX1262's DIO3 supplies the TCXO on the E22 (1.8 V); nothing to wire, but the
  firmware must say so (`SX126X_DIO3_TCXO_VOLTAGE 1.8`).
- RXEN/TXEN are complementary; the Meshtastic SX126x driver drives both when both
  are defined. Without them the module neither receives nor transmits properly.
- GPIO numbers are the adamko-space community variant's mapping for CS/SCK/MOSI/
  MISO/RESET/BUSY/DIO1; RXEN=13 / TXEN=14 are this spec's choice. **Whatever you
  wire must match `variant.h` exactly** — that file is the truth, not this table.
- Keep the ESP32-S3's strapping pins (GPIO 0, 3, 45, 46) out of it.
- Wire the antenna **before** power. A 1 W PA into an open IPEX pad is how E22s die.

> Sources: [adamko-space ESP32-S3 + E22-900M30S variant](https://github.com/adamko-space/Meshtastic-esp32s3-ebyte-e22900m30s) ·
> [Muskegon Mesh inexpensive 1 W node](https://muskegonmesh.org/technical-info/inexpensive-1w-node) ·
> [E22 S-series pin discussion](https://mischianti.org/forums/topic/e22-900m22s-wiring-dio1-dio2-and-busy/) ·
> [Meshtastic discussion #3948 (E22 modules)](https://github.com/meshtastic/firmware/discussions/3948)

---

## 3. Firmware — a custom Meshtastic variant

Build from the Meshtastic firmware repo with PlatformIO (the Web Flasher's
"DIY V1" binary is for the classic ESP32-WROOM pinout and does **not** work on the
S3 — Muskegon Mesh learned that the hard way).

```
firmware/
  variants/esp32s3_e22_mega/
    variant.h
    platformio.ini
    pins_arduino.h        # copy from esp32s3 devkit variant, adjust nothing radio-related
```

`variant.h` — the load-bearing lines (start from `variants/nrf52_promicro_diy_tcxo`
and the adamko-space variant; keep their extras, replace the radio block with this):

```c
#define USE_SX1262
#define SX126X_CS      10
#define SX126X_SCK     11
#define SX126X_MOSI    12
#define SX126X_MISO    7
#define SX126X_RESET   6
#define SX126X_BUSY    5
#define SX126X_DIO1    4
#define SX126X_RXEN    13
#define SX126X_TXEN    14
#define SX126X_DIO2_AS_RF_SWITCH
#define SX126X_DIO3_TCXO_VOLTAGE 1.8
#define SX126X_MAX_POWER 22        // drive INTO the E22's PA, not the antenna power
#define TX_GAIN_LORA 7             // newer firmware: PA gain so the app's dBm reads honestly (community-measured)
#define LORA_SCK SX126X_SCK
#define LORA_MISO SX126X_MISO
#define LORA_MOSI SX126X_MOSI
#define LORA_CS SX126X_CS
```

`platformio.ini`:

```ini
[env:esp32s3-e22-mega]
extends = esp32s3_base
board = esp32-s3-devkitc-1
board_build.partitions = default_16MB.csv
board_upload.flash_size = 16MB
build_flags = ${esp32s3_base.build_flags} -D PRIVATE_HW -I variants/esp32s3_e22_mega
              -DBOARD_HAS_PSRAM -DARDUINO_USB_CDC_ON_BOOT=1
```

Build and flash: `pio run -e esp32s3-e22-mega -t upload` (erase flash first on a
fresh board). Then in the app / CLI:

| Setting | Value | Why |
|---|---|---|
| Region | `US` | 902–928 MHz; firmware caps TX at 30 dBm; **no duty-cycle limit** in the US region ([Meshtastic LoRa config](https://meshtastic.org/docs/configuration/radio/lora/)) |
| TX power | **22** (`SX126X_MAX_POWER`) | on the M30S this is the SX1262's drive into the module's ~8–10 dB PA → ≈ 30 dBm out. Pushing the setting higher = distortion, spurs, a hot module — the community measured it, don't |
| Modem preset | `LongFast` (or whatever the Wildcat mesh runs) | must match the mesh; the frequency slot is derived from the channel name — Meshtastic does **not** frequency-hop |
| Role | `ROUTER` for a hilltop relay, `CLIENT_BASE` if it's the Den's own radio with a BBS behind it | see docs/DECISIONS.md D-011 on roles |
| Hop limit | 3 (default) | a strong node with hop limit 7 floods the region |
| Node info | long name with your callsign if operating under Part 97 (§5) | |

**Don't:** run the E22 from the DevKit's 3V3 pin (brown-outs at TX), set TX power
above 22 on the M30S, or leave `SX126X_DIO3_TCXO_VOLTAGE` out (the radio "works"
then drifts off frequency and nobody hears you).

---

## 4. The receive front end — LNA + T/R switch, and why it's Phase 2

```
            Phase 1 (build this first)                 Phase 2 (mast-mount / long coax only)

 ANT ──── E22-900M30S ──── ESP32-S3           ANT ──┬─ T/R switch ──(TX)────────────── E22 ANT
        (PA + LNA + switch inside)                   │   ▲ TXEN
                                                     └───┘(RX)── SAW 915 ── LNA ──┘
```

**What the E22 already gives you.** The module contains its own LNA and an RF
switch; RXEN/TXEN steer it. A second LNA in front of it adds gain the SX1262
doesn't need and *only* improves the system noise figure if it sits **before** a
lossy cable — i.e. at the antenna, not in the box next to the module. Inside the
box, on a 30 cm pigtail, an external LNA gains you almost nothing and adds one
more thing to overload from the cell tower down the road (the G3's "dynamic gain"
and "bypass" exist precisely because of that).

**The trap.** 1 W of TX into an LNA input destroys it instantly. So an external
LNA needs a T/R switch that (a) is rated well above +30 dBm on the TX path and
(b) has switched to TX **before** the PA ramps. Meshtastic's SX126x driver
raises TXEN and then transmits with no configurable settling delay — fine for
the microsecond-class solid-state switch inside the E22, marginal for a
mechanical coax relay (milliseconds). Options, honestly ranked:

1. **Skip it** (Phase 1). Measure first. Most of the "hears more" comes from
   antenna height, a clean feedline and a filtered receiver, not from NF.
2. **Mast-mounted RX-only LNA with a high-power solid-state SPDT** (≥ +33 dBm
   rated, e.g. a Mini-Circuits-class switch, not a PE4259 board) driven by TXEN
   through a level shifter; SAW filter before the LNA; LNA powered via the coax or
   a separate line. This is the design worth doing — budget $60–120 for the RF
   parts and do the VNA/attenuator measurements in §6 before connecting the LNA.
3. **Coax relay** driven by TXEN: cheap and 1 W-proof, but the settling-time
   race means the first milliseconds of every packet may hit the LNA. Only with a
   limiter (back-to-back PIN diodes) on the LNA input, and only if you accept
   the risk. Not recommended for an unattended node.

Whatever you pick: **the TX path must be a straight, low-loss shot** from E22 to
antenna through the switch — every 0.5 dB of insertion loss there is 0.5 dB of
your 30 dBm gone.

---

## 5. Legal — US915, honestly

*Not legal advice; this is the operator's responsibility.*

- **FCC Part 15.247 (unlicensed, what commercial Meshtastic gear is certified
  under):** maximum **1 W (+30 dBm) conducted** into the antenna, antenna gain up
  to **6 dBi** free; for point-to-multipoint systems above 6 dBi, **reduce TX by
  1 dB for every dB over 6 dBi** (so a 9 dBi collinear means 27 dBm max). No
  US duty-cycle limit — but the mesh has one (airtime), see the dashboard's
  channel-utilization brake. 15.247 digital-modulation operation expects a
  6 dB bandwidth ≥ 500 kHz; a 250 kHz LongFast LoRa signal is narrower than that,
  which is one reason commercial radios are certified *as products*. A DIY 1 W
  node is **not certified at all** — you are the responsible party, and the
  answer to "is this legal?" for a home-built 1 W node is "arguably, if you
  stay within the 30 dBm / 6 dBi envelope, and nobody has tested it." Keep
  the TX setting at 22 (≈ 30 dBm), keep the antenna ≤ 6 dBi or back the power
  off, keep it clean (SAW filter, decoupling), and keep a 30 dB attenuator on
  hand to look at your own spectrum.
- **Part 97 (amateur, if you're licensed):** 902–928 MHz is an amateur band;
  power far above 1 W is legal **but encryption is not** — a Part 97 Meshtastic
  node must run with the channel PSK set to none (Meshtastic's "licensed
  operator" mode, callsign as the node name, ID as required). That also means
  every message on that channel is plain text to everyone. Decide which regime
  the Wildcat mesh is on before building a 4 W anything.
- **The E22's rated 30 dBm is already the Part 15 ceiling.** The Station G2/G3's
  36+ dBm are legal only under Part 97 (or by turning them down); the same is
  true of any "add a 4 W PA" plan.

> Sources: [Meshtastic LoRa configuration](https://meshtastic.org/docs/configuration/radio/lora/) ·
> [Meshtastic radio settings](https://meshtastic.org/docs/overview/radio-settings/) ·
> [FCC Part 15.247 LoRa test report example](https://fcc.report/FCC-ID/2AN2JLTRF8888A1/3980234.pdf) ·
> [LoRa FCC certification guide](https://www.sunfiretesting.com/LoRa-FCC-Certification-Guide/)

---

## 6. First things to do with the NanoVNA (it arrives today)

Assumes a NanoVNA-H4 (or -H / V2). Frequencies: US Meshtastic LongFast on the
default slot sits at **906.875 MHz**; the band is 902–928 MHz. Sweep **850–1000 MHz**
for antennas, **700–1100 MHz** when looking at filters.

**Setup (once)**
1. Update firmware, then **calibrate** with the SOL kit at the end of the SMA
   cable you'll measure through (not at the VNA port). Save to slot 0. Re-cal
   when you change cables/adapters. 101 points is fine; 201–401 for filters.
2. Set stimulus 850–1000 MHz, marker 1 at 906.875 MHz, marker 2 at 915 MHz,
   marker 3 at 928 MHz. Display S11 SWR + Smith, S21 logmag.
3. **Never connect a transmitting radio to the VNA.** The VNA measures passive
   things (and powered LNAs at milliwatt levels). Radios go through the 30 dB
   attenuator into an SDR, not into the VNA.

**Checklist, in order**
- [ ] **The pigtail + SMA adapters:** S11 with a 50 Ω load on the far end → SWR
      ≈ 1.0–1.1 across the band. Anything worse = a bad crimp/adapter. Replace, don't tune around it.
- [ ] **Each antenna, in free space (arm's length from you, 1 m from metal):**
      SWR at 906.875 and 915 MHz < 1.5 (ideal < 1.3), resonance dip centred in
      902–928. Note the *bandwidth* over which SWR < 2 — a narrow dip means a
      high-Q antenna that will detune when mounted.
- [ ] **The same antenna at the actual mounting spot** (mast, wall, box lid,
      near the enclosure): the dip moves. Ground-plane-dependent monopoles
      (rubber ducks, sleeve dipoles on a metal box) move the most. If the dip
      slides below 902, the element is electrically too long → trim 1–2 mm at
      a time (fiberglass omnis: don't; pick a different mount). Log it.
- [ ] **Coax run:** S21 through the full run (both ends at the VNA) → loss at
      915 MHz. LMR-400: ~0.13 dB/m; if you see 1 dB on 3 m, a connector is bad.
      Also S11 with the far end open vs shorted to find a damaged spot (TDR mode
      on the H4 will show the distance).
- [ ] **SAW filter (Phase 2):** S21 pass-band 902–928 MHz should be flat within
      ~2–3 dB insertion loss; check it's ≥ 30–40 dB down at 850 and 960 MHz
      (LTE bands). A cheap board that's 6 dB lossy in-band isn't worth its NF cost.
- [ ] **LNA (Phase 2), powered, small-signal:** S21 gain (expect ~18–20 dB for
      an SPF5189Z at 915), S11/S22 match. Set the VNA output to its lowest level
      and add 10–20 dB of attenuation on the LNA output so you don't compress
      it — the VNA's port 2 also doesn't want +10 dBm.
- [ ] **T/R switch (Phase 2), both states:** S21 insertion loss on the TX path
      (< 0.5 dB or it costs you real power) and isolation to the RX port with the
      switch in TX (> 25 dB, better 40) — that isolation is what protects the LNA.
- [ ] **The whole receive chain assembled** (antenna port → switch → SAW → LNA →
      E22 ANT pad): one S21 sweep tells you the net gain and where the band edges are.
- [ ] Write every number into `docs/` next to this file with date, cable, cal
      slot and mounting notes — the next antenna will be compared against it.

**Reading the numbers**
- SWR 1.5 = 4 % of power reflected (−0.18 dB) — fine. SWR 2 = 11 % (−0.5 dB) — the
  E22's PA will tolerate it; SWR 3+ (25 %) — fix it before keying 1 W.
- Antenna gain is *not* on the VNA. A good match and a bad antenna are compatible.
  Gain you verify with A/B RSSI tests against a known node (the Den's node card
  keeps SNR/RSSI history for exactly this).

---

## 7. Bring-up order

1. Bench: DevKit + E22 on a 3 dBi duck, 5 V from the PD trigger, decoupling in place. Flash. Confirm the node appears on the Den map **solid (RF)**, not hollow.
2. Set TX 22, check the node's own reported air-util and that the Den hears it with the SNR you'd expect at desk range (it should be pinned high).
3. VNA the outdoor antenna + coax (§6). Install. Repeat the SNR check from the far side of town using the coverage layer.
4. Only then decide on Phase 2 from the measurements — if the Den already hears everything the handhelds do, spend the money on height instead.

*Open questions to confirm before ordering: (a) current E22-900M30S datasheet
values for supply current at 30 dBm and RX sensitivity (Ebyte's product page
omits them); (b) that the adamko-space variant still builds on the Meshtastic
release you target (it tracked 2.7.9–10 alpha); (c) G3 batch date and price on
the day you buy.*
