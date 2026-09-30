#!/usr/bin/env python3
"""Emit Journal Export Format (the input systemd-journal-remote consumes) for one named
fixture row of generators/journal.sh's matrix. Not a manifest artifact itself - journal.sh
pipes this script's stdout into `systemd-journal-remote -o ... -`.

Format: https://systemd.io/JOURNAL_EXPORT_FORMATS/
  NAME=value\\n                         for a UTF-8-safe value
  NAME\\n<uint64-LE byte length><raw bytes>\\n   for a value that isn't UTF-8-safe, or is long
  a blank line ends one entry

__REALTIME_TIMESTAMP/__MONOTONIC_TIMESTAMP/_BOOT_ID are provided explicitly so every fixture
has a deterministic, known timestamp - confirmed against the real binary (systemd
257.13-1~deb13u1, see journal.sh) that local stdin conversion honors the supplied
__REALTIME_TIMESTAMP exactly (round-tripped byte-for-byte through journalctl --output=json).
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


def large_message() -> bytes:
    """One entry whose MESSAGE is long enough (~2.3 KiB) to cross systemd-journal-remote's real
    compression threshold - empirically bisected against the actual installed binary (systemd
    257.13-1~deb13u1, see journal.sh) at >503 and <=504 bytes of field payload, i.e. close to
    the commonly-cited "512 bytes" figure but confirmed by testing rather than assumed from it.
    STANDARD_ENTRIES' short human messages never cross this threshold, so they're compression-
    axis-blind no matter what compression setting is requested - this row exists so the
    compressed/uncompressed pair of fixtures actually differs on disk."""
    frame = ("  File \"/opt/app/worker.py\", line 142, in process_job\n"
             "    raise ValueError(f\"invalid payload for job {job_id}\")\n")
    msg = "Traceback (most recent call last):\n" + frame * 20 + "ValueError: invalid payload for job 8f3c2a91\n"
    # field() only uses the length-prefixed binary form for bytes values; a multi-line str
    # would go through the single-line NAME=value form and corrupt the Export Format stream
    # (each embedded newline reads as a field/entry boundary) - encode explicitly.
    return entry(BASE_US, [("MESSAGE", msg.encode()), ("PRIORITY", "3"), ("SYSLOG_IDENTIFIER", "worker"),
                            ("_PID", "4242"), ("_COMM", "worker"), ("_HOSTNAME", "fw01")])


def non_utf8_field() -> bytes:
    """One entry whose MESSAGE is raw bytes containing an invalid UTF-8 sequence (0xFC lead
    byte, as in generators/utmp.py's non-UTF-8 fixture) - written with the binary field form
    (length-prefixed), which the export format requires for non-UTF-8-safe values."""
    bad = "g\xfcnther: ".encode("latin-1") + b"connection reset"
    return entry(BASE_US, [("MESSAGE", bad), ("PRIORITY", "3"), ("SYSLOG_IDENTIFIER", "app"),
                            ("_PID", "999"), ("_COMM", "app"), ("_HOSTNAME", "fw01")])


def zip_bomb_field(uncompressed_size: int = 64 * 1024 * 1024) -> bytes:
    """One entry with a MESSAGE field that is highly compressible (a run of one byte), well
    over systemd-journal-remote's real, empirically-measured ~504-byte compression threshold
    (see large_message()). Confirmed on the real binary: the DATA object itself compresses to
    ~2 KiB (ZSTD - the only algorithm this systemd build actually writes with, see journal.sh),
    but the .journal FILE does not stay small - systemd-journal-remote pre-grows the file's
    on-disk arena to ~72 MiB while producing it (verified: not a sparse hole, real allocated
    zero bytes past the tiny compressed object), presumably as scratch space for the
    compression codec rather than a reflection of final content size. So the "zip bomb" shape
    here is two-layered: a parser that trusts the *declared* 64 MiB uncompressed field size
    needs a bounded-decompression guard, and one that trusts the file's own *on-disk* size or
    arena_size as an allocation hint is still over-allocating ~72x what the real content (a few
    KiB) needs. journalctl's own default `--output=json` (without --all) reports this field as
    JSON null rather than the actual value - the oracle for this fixture must be captured with
    `--all` or it silently under-represents ground truth."""
    payload = b"A" * uncompressed_size
    return entry(BASE_US, [("MESSAGE", payload), ("PRIORITY", "6"),
                            ("SYSLOG_IDENTIFIER", "app"), ("_PID", "999"), ("_COMM", "app"),
                            ("_HOSTNAME", "fw01")])


ROWS = {
    "standard": standard,
    "large-message": large_message,
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
