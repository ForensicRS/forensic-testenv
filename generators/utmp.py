#!/usr/bin/env python3
"""Generate Linux utmp/wtmp/btmp-format fixtures with known contents.

All three files (utmp, wtmp, btmp) share one binary record format defined by glibc's
`struct utmp` (bits/utmp.h); only the conventional path and the ut_type values written to it
differ. See UTMP_FMT below for the verified field layout.

## Why there is no real 32-bit/64-bit difference

glibc deliberately keeps `ut_session` and `ut_tv` at a fixed 32-bit width on every
architecture and word size, specifically so utmp/wtmp/btmp files are binary-compatible
between 32-bit and 64-bit processes on the same (multiarch) system - see the comment above
`ut_session` in bits/utmp.h: "The ut_session and ut_tv fields must be the same size when
compiled 32- and 64-bit. This allows data files and shared memory to be shared between 32-
and 64-bit applications." Verified here by compiling a throwaway C program against the glibc
on this host and comparing its raw `fwrite(&u, sizeof(u), 1, f)` output, byte for byte,
against UTMP_FMT's `struct.pack` output (both sizeof == 384, identical bytes for identical
field values).

So `wtmp_lp64.bin` and `wtmp_ilp32.bin` below are intentionally byte-identical: the fixture
pair exists to pin that invariant (a parser must not key its record layout off a reported
word size) rather than to exercise two different byte layouts. A parser bug that decides
record size from ELF bitness metadata instead of trusting the fixed 384-byte record would
pass one and quietly misparse the other only if it also mis-detected word size from
context - this pair catches the "assumed 400-byte record on 64-bit" mistake some ports of
older non-Linux utmp readers make.

Writes to generators/out/utmp/:
  wtmp_lp64.bin / wtmp_lp64.truth.json      6-record login/logout/boot sequence
  wtmp_ilp32.bin                            byte-identical to wtmp_lp64.bin (see above)
  wtmp_bad_utf8.bin / .truth.json           same sequence, one record's ut_user is not
                                             valid UTF-8 (a raw Latin-1 byte, not a UTF-8
                                             continuation byte)
  wtmp_truncated.bin / .truth.json          the 6 good records followed by a dangling
                                             partial record (200 of 384 bytes) at EOF
"""
import json
import socket
import struct
from pathlib import Path

OUT = Path(__file__).resolve().parent / "out" / "utmp"

# Verified byte-exact against a real `struct utmp` from this host's glibc (see module
# docstring). '<' = explicit little-endian, no compiler padding assumptions.
#   h    ut_type                    2x   4-byte alignment pad before ut_pid
#   i    ut_pid                     32s  ut_line   4s  ut_id   32s  ut_user   256s  ut_host
#   h h  ut_exit.{e_termination,e_exit}
#   i    ut_session (int32, not `long` - see above)
#   I i  ut_tv.{tv_sec (uint32), tv_usec (int32)}
#   4i   ut_addr_v6                 20s  __glibc_reserved
UTMP_FMT = "<h2xi32s4s32s256shhiIi4i20s"
RECORD_SIZE = struct.calcsize(UTMP_FMT)
assert RECORD_SIZE == 384

EMPTY, RUN_LVL, BOOT_TIME, LOGIN_PROCESS, USER_PROCESS, DEAD_PROCESS = 0, 1, 2, 6, 7, 8
TYPE_NAMES = {0: "EMPTY", 1: "RUN_LVL", 2: "BOOT_TIME", 3: "NEW_TIME", 4: "OLD_TIME",
              5: "INIT_PROCESS", 6: "LOGIN_PROCESS", 7: "USER_PROCESS", 8: "DEAD_PROCESS"}

BASE_TS = 1768464000  # 2026-01-15T08:00:00Z


def ipv4_addr_v6(dotted: str | None) -> tuple[int, int, int, int]:
    if not dotted:
        return (0, 0, 0, 0)
    raw = socket.inet_aton(dotted)
    return (struct.unpack("<i", raw)[0], 0, 0, 0)


def pack_record(ut_type: int, pid: int, line: bytes, ident: bytes, user: bytes, host: bytes,
                 e_term: int, e_exit: int, session: int, tv_sec: int, tv_usec: int,
                 addr: str | None) -> bytes:
    a0, a1, a2, a3 = ipv4_addr_v6(addr)
    return struct.pack(UTMP_FMT, ut_type, pid, line, ident, user, host, e_term, e_exit,
                        session, tv_sec, tv_usec, a0, a1, a2, a3, b"")


# (ut_type, pid, line, id, user, host, e_term, e_exit, session, tv_sec, tv_usec, addr)
RECORDS = [
    (BOOT_TIME, 0, b"~", b"~~  ", b"reboot", b"6.8.0-generic", 0, 0, 0, BASE_TS, 0, None),
    (RUN_LVL, 0, b"~", b"~~  ", b"runlevel", b"", 0, 0, 0, BASE_TS + 4, 0, None),
    (LOGIN_PROCESS, 512, b"tty1", b"1", b"LOGIN", b"", 0, 0, 512, BASE_TS + 6, 0, None),
    (USER_PROCESS, 512, b"tty1", b"1", b"alice", b"", 0, 0, 512, BASE_TS + 7, 250000, None),
    (USER_PROCESS, 1500, b"pts/0", b"s/0", b"bob", b"10.0.0.7", 0, 0, 1500, BASE_TS + 900, 0,
     "10.0.0.7"),
    (DEAD_PROCESS, 512, b"tty1", b"1", b"", b"", 0, 0, 512, BASE_TS + 3600, 0, None),
]


def truth_for(records) -> dict:
    rows = []
    for ut_type, pid, line, ident, user, host, e_term, e_exit, session, tv_sec, tv_usec, addr in records:
        rows.append({
            "ut_type": ut_type, "ut_type_name": TYPE_NAMES[ut_type], "ut_pid": pid,
            "ut_line": line.decode("ascii"), "ut_id": ident.decode("ascii"),
            "ut_user": user.decode("ascii"), "ut_host": host.decode("ascii"),
            "e_termination": e_term, "e_exit": e_exit, "ut_session": session,
            "tv_sec": tv_sec, "tv_usec": tv_usec, "addr_v4": addr,
        })
    return {"record_size": RECORD_SIZE, "records": rows}


def write(name: str, data: bytes, truth: dict | None) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_bytes(data)
    if truth is not None:
        truth_name = name.rsplit(".", 1)[0] + ".truth.json"
        (OUT / truth_name).write_text(json.dumps(truth, indent=2, ensure_ascii=False) + "\n",
                                       encoding="utf-8")


def main() -> None:
    good = b"".join(pack_record(*r) for r in RECORDS)
    write("wtmp_lp64.bin", good, truth_for(RECORDS))
    # See module docstring: intentionally byte-identical to wtmp_lp64.bin.
    write("wtmp_ilp32.bin", good, None)

    # 0xFC is not a valid UTF-8 leading byte (max valid lead is 0xF4); everything else in the
    # record is untouched so a parser must isolate the failure to this one field.
    bad_user = b"g\xfcnther"
    bad_records = list(RECORDS)
    r = list(bad_records[3])
    r[4] = bad_user.ljust(32, b"\x00")
    bad_records[3] = tuple(r)
    bad_data = b"".join(pack_record(*r) for r in bad_records)
    bad_truth = truth_for(RECORDS)
    bad_truth["records"][3]["ut_user"] = None
    bad_truth["records"][3]["ut_user_raw_hex"] = bad_user.hex()
    bad_truth["records"][3]["note"] = "ut_user is not valid UTF-8 (0xFC is not a valid lead byte)"
    write("wtmp_bad_utf8.bin", bad_data, bad_truth)

    # 6 good records, then a dangling record cut off at 200 of 384 bytes (mid ut_host).
    trailing = pack_record(*RECORDS[0])[:200]
    trunc_data = good + trailing
    trunc_truth = truth_for(RECORDS)
    trunc_truth["trailing_partial_record_bytes"] = len(trailing)
    write("wtmp_truncated.bin", trunc_data, trunc_truth)

    for f in sorted(OUT.iterdir()):
        print(f, f.stat().st_size, "bytes")


if __name__ == "__main__":
    main()
