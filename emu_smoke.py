#!/usr/bin/env python3
"""Boot an ESP32-S3 flash image in esp-emulator and report what was actually verified.

Design rule, borrowed the hard way from 2026-11-21: anything the backend cannot model is
reported NOT_VERIFIED, never PASS. A run that crashes or never starts is CANNOT_VERIFY.
A green result that quietly covers less than it claims is worse than no harness.

Usage:
  emu_smoke.py --image full.bin
  emu_smoke.py --part 0x0=factory.bin --part 0x670000=littlefs.bin --part 0x340000=ota.bin
  emu_smoke.py --image full.bin --json report.json
"""

import argparse
import json
import pathlib
import re
import subprocess
import sys

FLASH_BYTES = 0x800000
DEFAULT_EMU = (pathlib.Path(__file__).parent
               / "esp-emu/esp-emu-1.2.0-x86_64-unknown-linux-gnu/esp-emu")

# (id, regex, human description). Any hit fails the run.
FAILURE_MARKERS = [
    ("littlefs_mount", r"esp_littlefs:.*(mount failed|Failed to initialize)",
     "LittleFS did not mount - filesystem partition missing, wrong offset, or wrong size"),
    ("panic", r"Guru Meditation|panic'ed|abort\(\) was called", "Firmware panicked"),
    ("boot_loop", r"rst:0x[0-9a-f]+ \(.*(PANIC|WDT|BROWNOUT)", "Reset for a bad reason"),
    ("nvs_fail", r"nvs_flash_init failed|NVS partition.*not found", "NVS unavailable"),
    ("part_fail", r"partition table.*(invalid|not found)|Failed to verify partition",
     "Partition table rejected by the bootloader"),
]

# Evidence the firmware really reached application code. Absent => CANNOT_VERIFY,
# because "no failure markers" is meaningless if it never got far enough to fail.
LIVENESS_MARKERS = [
    ("app_running", r"\[\s*\d+\]\[[IWE]\]|I \(\d+\)", "reached IDF/Arduino logging"),
]

# Things this backend structurally cannot observe. Always reported, never "passed".
NOT_VERIFIED = [
    "LoRa radio (SX1262 over SPI) - transmit, receive, link budget",
    "GNSS fix and NMEA parsing",
    "Physical keyboard input (TCA8418 scan)",
    "BLE pairing, bonding and identity separation",
    "Display output (ST7789)",
    "Real-time behaviour - emulation is not cycle accurate",
]


def assemble(parts):
    img = bytearray(b"\xff" * FLASH_BYTES)
    placed = []
    for spec in parts:
        off_s, _, path_s = spec.partition("=")
        off = int(off_s, 0)
        data = pathlib.Path(path_s).read_bytes()
        if off + len(data) > FLASH_BYTES:
            sys.exit(f"ERROR: {path_s} at {off:#x} overruns {FLASH_BYTES:#x}")
        for o, n, p in placed:
            if off < o + n and o < off + len(data):
                sys.exit(f"ERROR: {path_s} at {off:#x} overlaps {p} at {o:#x}")
        img[off:off + len(data)] = data
        placed.append((off, len(data), path_s))
    return bytes(img), placed


def run(emu, image_path, chip, timeout_s, extra):
    cmd = [str(emu), "--chip", chip, "--firmware", str(image_path),
           "--timeout", f"{timeout_s}s", *extra]
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          timeout=timeout_s + 60)
    return cmd, proc.stdout + proc.stderr


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--image", type=pathlib.Path, help="Prebuilt full flash image")
    ap.add_argument("--part", action="append", default=[],
                    metavar="OFFSET=FILE", help="Component to place, e.g. 0x670000=littlefs.bin")
    ap.add_argument("--emu", type=pathlib.Path, default=DEFAULT_EMU)
    ap.add_argument("--chip", default="esp32s3")
    ap.add_argument("--timeout", type=int, default=45, help="Emulated seconds (default 45)")
    ap.add_argument("--json", type=pathlib.Path, help="Write a machine-readable report")
    ap.add_argument("--log", type=pathlib.Path, help="Write the full captured console")
    ap.add_argument("--emu-arg", action="append", default=[],
                    help="Extra arg passed through to esp-emu (repeatable)")
    args = ap.parse_args()

    if bool(args.image) == bool(args.part):
        sys.exit("ERROR: give exactly one of --image or --part")

    # Image assembly is validated before the emulator is required, so layout mistakes are
    # caught without a working emulator install (and so CI can test assembly cheaply).
    if args.image:
        raw = args.image.read_bytes()
        if len(raw) != FLASH_BYTES:
            raw = raw + b"\xff" * (FLASH_BYTES - len(raw))
        placed = [(0, len(args.image.read_bytes()), str(args.image))]
        img = raw
    else:
        img, placed = assemble(args.part)

    if not args.emu.exists():
        sys.exit(f"ERROR: emulator not found at {args.emu} "
                 f"(get it from https://github.com/espressif/esp-emulator/releases, "
                 f"or pass --emu)")

    tmp = pathlib.Path("/tmp/emu_smoke_flash.bin")
    tmp.write_bytes(img)

    try:
        cmd, out = run(args.emu, tmp, args.chip, args.timeout, args.emu_arg)
    except subprocess.TimeoutExpired:
        print("VERDICT: CANNOT_VERIFY - emulator did not exit")
        sys.exit(2)

    if args.log:
        args.log.write_text(out)

    failures = [(i, d) for i, rx, d in FAILURE_MARKERS if re.search(rx, out)]
    alive = [i for i, rx, _ in LIVENESS_MARKERS if re.search(rx, out)]

    print("=" * 70)
    for off, n, p in placed:
        print(f"  placed {pathlib.Path(p).name:<48} @ {off:#09x} ({n} bytes)")
    print("=" * 70)

    if not alive:
        verdict = "CANNOT_VERIFY"
        print("VERDICT: CANNOT_VERIFY")
        print("  Firmware produced no application-level logging - it never reached app code.")
        print("  Nothing below was actually exercised; this is NOT a pass.")
    elif failures:
        verdict = "FAIL"
        print("VERDICT: FAIL")
        for i, d in failures:
            print(f"  [{i}] {d}")
    else:
        verdict = "PASS"
        print("VERDICT: PASS (for the checks this backend can make)")
        print("  Reached application code; no failure markers.")

    print("\nNOT VERIFIED by this backend - these are untested, not passing:")
    for item in NOT_VERIFIED:
        print(f"  - {item}")

    if args.json:
        args.json.write_text(json.dumps({
            "verdict": verdict,
            "command": cmd,
            "placed": [{"offset": hex(o), "bytes": n, "file": p} for o, n, p in placed],
            "failures": [{"id": i, "description": d} for i, d in failures],
            "liveness": alive,
            "not_verified": NOT_VERIFIED,
        }, indent=2) + "\n")

    sys.exit({"PASS": 0, "FAIL": 1, "CANNOT_VERIFY": 2}[verdict])


if __name__ == "__main__":
    main()
