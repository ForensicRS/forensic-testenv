#!/usr/bin/env python3
"""Generate Linux utmp/wtmp/btmp-format fixtures with known contents.

All three files (utmp, wtmp, btmp) share one binary record format, in one of two layouts.
Field offsets and both layouts match `crates/frnsc-linux/src/unix/utmp.rs` exactly (that
module cites libyal/dtformats's "Utmp login records format" as its reference) - this
generator exists to give that crate's `real_samples.rs` (`artifact_or_skip!("frnsc-linux-utmp-sample")`)
and its `UtmpLayout::Wide64` path something real to run against, not just the inline byte
arrays its unit tests build.

* Narrow32 (RECORD_SIZE_32 = 384 bytes): session/tv_sec/tv_usec as 4-byte fields. Used by
  both 32-bit Linux and by 64-bit x86/x86_64 glibc, which deliberately keeps these fields
  32-bit-wide on every word size (see bits/utmp.h's comment on `ut_session`) so the format
  stays byte-identical between 32- and 64-bit readers. This is what the overwhelming
  majority of real (x86/x86_64 Linux) evidence uses - verified here by compiling a
  throwaway C program against this host's glibc and comparing its raw `fwrite(&u,
  sizeof(u), 1, f)` output, byte for byte, against NARROW32_FMT's `struct.pack` output.
* Wide64 (RECORD_SIZE_64 = 400 bytes): session/tv_sec/tv_usec widened to 8 bytes each,
  reserved tail grown from 20 to 24 bytes. Not produced by current Linux/glibc on this
  host, but frnsc-linux's module doc is explicit that "architectures that do not keep the
  32-bit-compatible layout" produce this - not exercisable against this sandbox's own
  glibc, so unlike NARROW32_FMT this layout is NOT cross-checked against a real C struct
  here; it is transcribed directly from frnsc-linux's own byte-offset table, which is the
  authoritative spec for this repo's consumer.

Writes to generators/out/utmp/:
  wtmp_narrow32.bin / .truth.json      6-record login/logout/boot sequence, Narrow32
  wtmp_wide64.bin / .truth.json        the same 6 records, Wide64
  wtmp_bad_utf8.bin / .truth.json      same sequence (Narrow32), one record's ut_user is
                                        not valid UTF-8 (a raw Latin-1 byte, not a UTF-8
                                        continuation byte)
  wtmp_truncated.bin / .truth.json     the 6 good Narrow32 records followed by a dangling
                                        partial record (200 of 384 bytes) at EOF
  wtmp_wide64_truncated.bin / .truth.json
                                        one good Wide64 record followed by 50 trailing
                                        bytes - the exact shape of frnsc-linux's pinned
                                        regression test
                                        `a_wide64_file_truncated_mid_record_is_misdetected_as_narrow32`:
                                        length (450) is a multiple of neither 384 nor 400,
                                        so `detect_layout` defaults to Narrow32 and misreads
                                        the tail of the one real record as a second, garbage
                                        one
"""
import json
import socket
import struct
from pathlib import Path

OUT = Path(__file__).resolve().parent / "out" / "utmp"

# Offsets 0-335 are identical between both layouts. '<' = explicit little-endian, no
# compiler padding assumptions.
#   i    ut_type                     i    ut_pid
#   32s  ut_line    4s  ut_id    32s  ut_user    256s  ut_host
#   h h  termination_status, exit_status
NARROW32_FMT = "<ii32s4s32s256shh" + "iii" + "4i" + "20s"   # session/tv_sec/tv_usec: i32 each
WIDE64_FMT = "<ii32s4s32s256shh" + "qqq" + "4i" + "24s"     # session/tv_sec/tv_usec: i64 each
RECORD_SIZE_32 = struct.calcsize(NARROW32_FMT)
RECORD_SIZE_64 = struct.calcsize(WIDE64_FMT)
assert RECORD_SIZE_32 == 384
assert RECORD_SIZE_64 == 400

EMPTY, RUN_LVL, BOOT_TIME, LOGIN_PROCESS, USER_PROCESS, DEAD_PROCESS = 0, 1, 2, 6, 7, 8
TYPE_NAMES = {0: "EMPTY", 1: "RUN_LVL", 2: "BOOT_TIME", 3: "NEW_TIME", 4: "OLD_TIME",
              5: "INIT_PROCESS", 6: "LOGIN_PROCESS", 7: "USER_PROCESS", 8: "DEAD_PROCESS"}

BASE_TS = 1768464000  # 2026-01-15T08:00:00Z


def ipv4_addr_v6(dotted: str | None) -> tuple[int, int, int, int]:
    if not dotted:
        return (0, 0, 0, 0)
    raw = socket.inet_aton(dotted)
    return (struct.unpack("<i", raw)[0], 0, 0, 0)


def pack_record(layout: str, ut_type: int, pid: int, line: bytes, ident: bytes, user: bytes,
                 host: bytes, e_term: int, e_exit: int, session: int, tv_sec: int,
                 tv_usec: int, addr: str | None) -> bytes:
    a0, a1, a2, a3 = ipv4_addr_v6(addr)
    fmt = NARROW32_FMT if layout == "narrow32" else WIDE64_FMT
    return struct.pack(fmt, ut_type, pid, line, ident, user, host, e_term, e_exit,
                        session, tv_sec, tv_usec, a0, a1, a2, a3, b"")  # 's' fields zero-pad


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


def pack_all(layout: str, records) -> bytes:
    return b"".join(pack_record(layout, *r) for r in records)


def truth_for(layout: str, records) -> dict:
    rows = []
    for ut_type, pid, line, ident, user, host, e_term, e_exit, session, tv_sec, tv_usec, addr in records:
        rows.append({
            "ut_type": ut_type, "ut_type_name": TYPE_NAMES[ut_type], "ut_pid": pid,
            "ut_line": line.decode("ascii"), "ut_id": ident.decode("ascii"),
            "ut_user": user.decode("ascii"), "ut_host": host.decode("ascii"),
            "e_termination": e_term, "e_exit": e_exit, "ut_session": session,
            "tv_sec": tv_sec, "tv_usec": tv_usec, "addr_v4": addr,
        })
    size = RECORD_SIZE_32 if layout == "narrow32" else RECORD_SIZE_64
    return {"layout": layout, "record_size": size, "records": rows}


def write(name: str, data: bytes, truth: dict | None) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_bytes(data)
    if truth is not None:
        truth_name = name.rsplit(".", 1)[0] + ".truth.json"
        (OUT / truth_name).write_text(json.dumps(truth, indent=2, ensure_ascii=False) + "\n",
                                       encoding="utf-8")


def main() -> None:
    narrow_good = pack_all("narrow32", RECORDS)
    write("wtmp_narrow32.bin", narrow_good, truth_for("narrow32", RECORDS))

    wide_good = pack_all("wide64", RECORDS)
    write("wtmp_wide64.bin", wide_good, truth_for("wide64", RECORDS))

    # 0xFC is not a valid UTF-8 leading byte (max valid lead is 0xF4); everything else in the
    # record is untouched so a parser must isolate the failure to this one field.
    bad_user = b"g\xfcnther"
    bad_records = list(RECORDS)
    r = list(bad_records[3])
    r[4] = bad_user.ljust(32, b"\x00")
    bad_records[3] = tuple(r)
    bad_data = pack_all("narrow32", bad_records)
    bad_truth = truth_for("narrow32", RECORDS)
    bad_truth["records"][3]["ut_user"] = None
    bad_truth["records"][3]["ut_user_raw_hex"] = bad_user.hex()
    bad_truth["records"][3]["note"] = "ut_user is not valid UTF-8 (0xFC is not a valid lead byte)"
    write("wtmp_bad_utf8.bin", bad_data, bad_truth)

    # 6 good Narrow32 records, then a dangling record cut off at 200 of 384 bytes (mid ut_host).
    trailing = pack_record("narrow32", *RECORDS[0])[:200]
    trunc_data = narrow_good + trailing
    trunc_truth = truth_for("narrow32", RECORDS)
    trunc_truth["trailing_partial_record_bytes"] = len(trailing)
    write("wtmp_truncated.bin", trunc_data, trunc_truth)

    # One good Wide64 record + 50 trailing bytes = 450 bytes: a multiple of neither 384 nor
    # 400, so frnsc-linux's detect_layout() defaults to Narrow32 (see its docs) and misreads
    # this as one garbage-tailed Narrow32 record instead of "one Wide64 record, truncated".
    wide_trailing = pack_record("wide64", *RECORDS[3])[:50]
    wide_trunc_data = pack_record("wide64", *RECORDS[3]) + wide_trailing
    assert len(wide_trunc_data) == 450
    wide_trunc_truth = {
        "layout_written_as": "wide64",
        "layout_detected_by_frnsc_linux": "narrow32 (see detect_layout's docs - this is the pinned misdetection case)",
        "bytes": len(wide_trunc_data),
        "one_whole_wide64_record": truth_for("wide64", [RECORDS[3]])["records"][0],
        "note": "real length (450) is a multiple of neither RECORD_SIZE_32 (384) nor "
                "RECORD_SIZE_64 (400); frnsc-linux's detect_layout defaults to Narrow32, so "
                "fields before offset 332 (ut_user etc.) still read correctly but tv_sec and "
                "everything after come out wrong",
    }
    write("wtmp_wide64_truncated.bin", wide_trunc_data, wide_trunc_truth)

    for f in sorted(OUT.iterdir()):
        print(f, f.stat().st_size, "bytes")


if __name__ == "__main__":
    main()
