#!/usr/bin/env python3
"""Emit Journal Export Format (the input systemd-journal-remote consumes) for one named
fixture row of generators/journal.sh's matrix. Not a manifest artifact itself - journal.sh
pipes this script's stdout into `systemd-journal-remote -o ... -`.

Format: https://systemd.io/JOURNAL_EXPORT_FORMATS/
  NAME=value\\n                         for a UTF-8-safe value
  NAME\\n<uint64-LE byte length><raw bytes>\\n   for a value that isn't UTF-8-safe, or is long
  a blank line ends one entry

__REALTIME_TIMESTAMP/__MONOTONIC_TIMESTAMP/_BOOT_ID are provided explicitly so every fixture
has a deterministic, known timestamp - verify on the first real run that the local (stdin,
non-network) invocation of systemd-journal-remote honors them rather than regenerating them;
the export format is documented as a round-trip of `journalctl -o export`, which implies it
should, but this repo has not yet run systemd-journal-remote for real (see journal.sh).
"""
import struct
import sys

BOOT_ID = "f" * 32  # fixed fake boot id, valid sd_id128 hex


def field(name: str, value) -> bytes:
    if isinstance(value, bytes):
        return name.encode() + b"\n" + struct.pack("<Q", len(value)) + value + b"\n"
    return f"{name}={value}\n".encode()


def entry(realtime_us: int, fields: list[tuple]) -> bytes:
    out = [field("__REALTIME_TIMESTAMP", realtime_us),
           field("__MONOTONIC_TIMESTAMP", realtime_us),
           field("_BOOT_ID", BOOT_ID)]
    for name, value in fields:
        out.append(field(name, value))
    return b"".join(out) + b"\n"


# (unix_us, [(field, value), ...]) - a small, deterministic, realistic-looking session.
BASE_US = 1768464000_000000  # 2026-01-15T08:00:00Z, matches generators/utmp.py's BASE_TS

STANDARD_ENTRIES = [
    (BASE_US, [("MESSAGE", "Starting nginx.service..."), ("PRIORITY", "6"),
               ("SYSLOG_IDENTIFIER", "systemd"), ("_PID", "1"), ("_COMM", "systemd"),
               ("_HOSTNAME", "fw01")]),
    (BASE_US + 1_000_000, [("MESSAGE", "Failed password for invalid user admin from 203.0.113.7 port 51515 ssh2"),
                            ("PRIORITY", "6"), ("SYSLOG_IDENTIFIER", "sshd"), ("_PID", "1234"),
                            ("_COMM", "sshd"), ("_HOSTNAME", "fw01")]),
    (BASE_US + 2_000_000, [("MESSAGE", "session opened for user deploy(uid=1001)"),
                            ("PRIORITY", "6"), ("SYSLOG_IDENTIFIER", "sshd"), ("_PID", "1234"),
                            ("_COMM", "sshd"), ("_HOSTNAME", "fw01")]),
]


def standard() -> bytes:
    return b"".join(entry(us, f) for us, f in STANDARD_ENTRIES)


def non_utf8_field() -> bytes:
    """One entry whose MESSAGE is raw bytes containing an invalid UTF-8 sequence (0xFC lead
    byte, as in generators/utmp.py's non-UTF-8 fixture) - written with the binary field form
    (length-prefixed), which the export format requires for non-UTF-8-safe values."""
    bad = "g\xfcnther: ".encode("latin-1") + b"connection reset"
    return entry(BASE_US, [("MESSAGE", bad), ("PRIORITY", "3"), ("SYSLOG_IDENTIFIER", "app"),
                            ("_PID", "999"), ("_COMM", "app"), ("_HOSTNAME", "fw01")])


def zip_bomb_field(uncompressed_size: int = 64 * 1024 * 1024) -> bytes:
    """One entry with a MESSAGE field that is highly compressible (a run of one byte), well
    over journald's ~512-byte compression threshold. Compresses to a few KiB with any of
    xz/lz4/zstd, so the .journal file stays small while the DATA object's declared
    (decompressed) size is large - exactly the shape a bounded-decompression guard must
    reject-or-cap rather than blindly allocate."""
    payload = b"A" * uncompressed_size
    return entry(BASE_US, [("MESSAGE", payload), ("PRIORITY", "6"),
                            ("SYSLOG_IDENTIFIER", "app"), ("_PID", "999"), ("_COMM", "app"),
                            ("_HOSTNAME", "fw01")])


ROWS = {
    "standard": standard,
    "non-utf8": non_utf8_field,
    "zip-bomb": zip_bomb_field,
}


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in ROWS:
        print(f"usage: {sys.argv[0]} {{{'|'.join(ROWS)}}}", file=sys.stderr)
        raise SystemExit(2)
    sys.stdout.buffer.write(ROWS[sys.argv[1]]())


if __name__ == "__main__":
    main()
