# esp-emu-smoke

Boot an ESP32-S3 flash image in [esp-emulator](https://github.com/espressif/esp-emulator)
and report **what was actually verified** — on a free GitHub-hosted runner, with no board
attached.

It exists to catch one specific, nasty class of bug: **firmware that builds, flashes, boots,
renders a UI, and only fails on the first config write** because it landed in a partition
layout it didn't expect. That failure is invisible to every build-only CI, and it is exactly
what motivated this tool.

## The design rule

> Anything the backend cannot model is reported `NOT_VERIFIED`, never `PASS`.
> A run that never reaches application code is `CANNOT_VERIFY`, not `PASS`.

"No failure markers" is meaningless if the firmware never got far enough to fail. A green
check that quietly covers less than it claims is worse than no check at all, so this tool
prints the untested surface on every run, including successful ones.

## Usage

```sh
# assemble components at their partition offsets, then boot
./emu_smoke.py \
  --part 0x0=firmware.factory.bin \
  --part 0x340000=ota.bin \
  --part 0x670000=littlefs.bin \
  --timeout 45 --json report.json

# or boot a prebuilt full image
./emu_smoke.py --image full-8mb.bin
```

Exit codes: `0` PASS · `1` FAIL · `2` CANNOT_VERIFY.

Overlapping or overrunning components are rejected before anything boots.

## Worked example — the bug this was built for

A Meshtastic build installed into the wrong partition layout boots fine and dies on the
first setting you change. Here is the discriminator, using the same unmodified vendor
binary both times:

| Image | Verdict |
|---|---|
| `factory.bin` alone — filesystem partition blank | **FAIL** · `esp_littlefs: mount failed (-84)` |
| `factory.bin` + `ota.bin` @ `0x340000` + `littlefs.bin` @ `0x670000` | **PASS** |

No firmware modification, no CI-specific build, no hardware.

## What it checks

**Failure markers** — LittleFS mount failure, panic / `Guru Meditation` / `abort()`,
reset for a bad reason (PANIC/WDT/BROWNOUT), NVS init failure, partition table rejected.

**Liveness** — IDF or Arduino log output, i.e. proof application code was reached. Without
it the verdict is `CANNOT_VERIFY`.

**Never verified here** (reported on every run): LoRa/SPI radio, GNSS, physical keyboard
scan, BLE pairing and bonding, display output, real-time behaviour (emulation is not cycle
accurate).

## Why esp-emulator and not QEMU

Espressif's QEMU fork boots the ROM but was unusable for the board this was developed
against:

- **Console is USB-CDC** on boards built with `ARDUINO_USB_CDC_ON_BOOT=1`. QEMU lists USB
  as unsupported, so everything after the ROM stage is invisible — the firmware ran and
  flash was byte-identical 40 s later, with no way to see why.
- **I²C unsupported**, and the target initialises an I²C keyboard controller during boot.

esp-emulator's `--console auto` follows the firmware when it switches to USB-Serial/JTAG,
and `--i2c-dev` can answer I²C addresses. It runs the **real shipping binary** rather than
a CI-only variant — which matters, because a test of a differently-built binary is only as
good as the argument that the difference is orthogonal to what you're testing.

## Requirements

Python 3.9+, and the `esp-emu` binary from
[espressif/esp-emulator releases](https://github.com/espressif/esp-emulator/releases)
(Apache-2.0). Point at it with `--emu`, or place it at
`./esp-emu/esp-emu-<ver>-x86_64-unknown-linux-gnu/esp-emu`.

## Status

Early. Developed against an M5Stack Cardputer ADV (ESP32-S3, 8 MiB) running Meshtastic
2.8.2, with both a positive and a negative control verified. The marker lists are
deliberately small and conservative; additions welcome, but a marker that produces false
failures is worse than a missing one.

**Known unverified:** whether the emulator's LittleFS behaviour matches real silicon in
every case. The negative control above is a genuinely incomplete image, so failing it is
correct — but "the emulator agrees with hardware" is an untested claim, and this README
will not pretend otherwise.

## License

MIT.
