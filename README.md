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

## A false positive, and how it was caught

The first version of this tool failed any image whose LittleFS didn't mount. That was
**wrong**, and checking it against real silicon is what proved it:

- The emulator, given a **blank** filesystem partition, reports `Corrupted dir pair` →
  `Failed to initialize LittleFS` and gives up.
- Real ESP32-S3 silicon, given the same blank partition, **formats it and mounts
  successfully**. Confirmed by dumping the partition off a board afterwards and finding a
  valid LittleFS superblock (`littlefs` magic at offset `0x8`).

So a perfectly good shipping image was being rejected. A mount failure is now
**informational by default**, and only a failure with `--expect-filesystem`, which says
*"I supplied a filesystem image, so a mount failure is a real defect."*

| Invocation | Verdict |
|---|---|
| `factory.bin` alone | **PASS**, with a note that LittleFS didn't mount |
| `factory.bin` alone + `--expect-filesystem` | **FAIL** — you claimed an FS, it didn't mount |
| `factory.bin` + `ota.bin` + `littlefs.bin` at their offsets | **PASS**, clean mount |

This is the whole reason the tool reports what it *cannot* verify. An emulator is a model,
models diverge from silicon, and the divergences are not always in the safe direction.
**Validate the harness against hardware before trusting it**, which is advice this project
had to take before it was a day old.

## What it checks

**Failure markers** — panic / `Guru Meditation` / `abort()`, reset for a bad reason
(PANIC/WDT/BROWNOUT), NVS init failure, partition table rejected. LittleFS mount failure
only with `--expect-filesystem` (see above).

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

**Verified against hardware:** the blank-filesystem divergence described above (M5Stack
Cardputer ADV, ESP32-S3, Meshtastic 2.8.2). Config written over the CLI survived a reboot,
and the filesystem partition read back off the board contained a valid LittleFS superblock
— so the board had formatted and mounted it while the emulator had not.

**Still unverified:** every other respect in which the emulator may differ from silicon.
One divergence found in the first day of use is not a reason to assume it's the only one.

## License

MIT.
