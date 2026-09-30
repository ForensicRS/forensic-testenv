#!/usr/bin/env python3
"""Post-process a clean, closed .journal file to unlink one or more entries from the main
entry-array chain, while leaving the ENTRY objects themselves physically intact on disk. This
is the recovery-path fixture in generators/journal.sh's matrix: a parser that only walks the
array chain must miss the unlinked entries; a parser with a carving/recovery fallback must
still find them.

Unlinks from the TAIL of the chain (the last N populated item slots), not the head. Verified
empirically against the real journalctl (systemd 257.13-1~deb13u1, see journal.sh) that this
matters: zeroing a slot in the *middle* of an array (e.g. item 0 of a 3-item array) makes
journalctl's own sequential reader drop every entry *after* the hole too - not just the
targeted one - because its forward iteration apparently treats the first zero slot it meets
as an implicit end-of-valid-data marker (the shape a still-filling array legitimately has),
not as "this one entry is missing". That cascading-loss behavior is itself a real forensic
finding worth knowing about (a naive recovery parser must expect "entries after a hole may
also be unreachable via the index, not just the hole itself"), but it makes for a confusing
*primary* recovery fixture. Unlinking the tail instead gives the clean, predictable shape the
task actually asked for: N-1 entries visible through the index, 1 physically present but
unreachable.

Deliberately does NOT update Header.n_entries (or any hash table) to match: the mismatch
between the header's claimed entry count and what the array walk actually reaches is itself
a forensically useful tell, not something to paper over.

Binary format below is transcribed from systemd's own
src/libsystemd/sd-journal/journal-def.h and cross-checked against the real, installed
systemd 257.13-1~deb13u1 binaries (see journal.sh for how they're obtained). Only supports
the non-compact, regular-item (8-byte offsets) EntryArrayObject layout; refuses (exit 1) a
compact-mode file rather than silently doing the wrong thing.

Usage: journal_unlink_entries.py IN.journal OUT.journal N_ENTRIES_TO_UNLINK
"""
import struct
import sys

OBJ_HEADER_SIZE = 16  # type:u8, flags:u8, reserved[6], size:le64
OBJECT_ENTRY_ARRAY = 6
OBJECT_ENTRY = 3

HEADER_INCOMPATIBLE_COMPACT = 1 << 4


def read_header(data: bytes) -> dict:
    if data[0:8] != b"LPKSHHRH":
        raise ValueError("not a journal file (bad signature)")
    (_compatible_flags, incompatible_flags) = struct.unpack_from("<II", data, 8)
    # skip state(1)+reserved(7)+file_id(16)+machine_id(16)+tail_entry_boot_id(16)+seqnum_id(16)
    off = 8 + 8 + 8 + 16 * 4
    # header_size, arena_size, {data,field}_hash_table_{offset,size}, tail_object_offset,
    # n_objects, n_entries, tail_entry_seqnum, head_entry_seqnum, entry_array_offset, ...
    (_header_size, _arena_size, _dht_off, _dht_size, _fht_off, _fht_size, _tail_obj,
     _n_objects, n_entries, _tail_seq, _head_seq, entry_array_offset) = \
        struct.unpack_from("<" + "Q" * 12, data, off)
    return {
        "incompatible_flags": incompatible_flags,
        "entry_array_offset": entry_array_offset,
        "n_entries": n_entries,
    }


def obj_header(data: bytes, offset: int) -> tuple[int, int, int]:
    obj_type, flags = data[offset], data[offset + 1]
    (size,) = struct.unpack_from("<Q", data, offset + 8)
    return obj_type, flags, size


def unlink(data: bytearray, n_to_unlink: int) -> int:
    h = read_header(bytes(data))
    if h["incompatible_flags"] & HEADER_INCOMPATIBLE_COMPACT:
        print("refusing: compact-mode file (4-byte items) not supported by this script",
              file=sys.stderr)
        raise SystemExit(1)

    # Walk the whole chain first and collect every populated item slot's offset, in order -
    # so we can unlink from the tail instead of the head (see module docstring for why).
    populated_slots = []
    array_offset = h["entry_array_offset"]
    while array_offset:
        obj_type, _flags, size = obj_header(bytes(data), array_offset)
        if obj_type != OBJECT_ENTRY_ARRAY:
            raise ValueError(f"expected ENTRY_ARRAY at {array_offset:#x}, got type {obj_type}")
        # EntryArrayObject: ObjectHeader(16) + next_entry_array_offset(8) + items[](8 each)
        (next_array,) = struct.unpack_from("<Q", data, array_offset + OBJ_HEADER_SIZE)
        items_off = array_offset + OBJ_HEADER_SIZE + 8
        n_items = (size - OBJ_HEADER_SIZE - 8) // 8
        for i in range(n_items):
            item_off = items_off + i * 8
            (entry_offset,) = struct.unpack_from("<Q", data, item_off)
            if entry_offset == 0:
                continue  # unused slot
            obj_type, _flags, _size = obj_header(bytes(data), entry_offset)
            if obj_type != OBJECT_ENTRY:
                continue
            populated_slots.append(item_off)
        array_offset = next_array

    to_unlink = populated_slots[-n_to_unlink:] if n_to_unlink else []
    for item_off in to_unlink:
        struct.pack_into("<Q", data, item_off, 0)  # unlink: zero the slot, keep the ENTRY object's bytes untouched
    return len(to_unlink)


def main() -> None:
    if len(sys.argv) != 4:
        print(__doc__, file=sys.stderr)
        raise SystemExit(2)
    src, dst, n = sys.argv[1], sys.argv[2], int(sys.argv[3])
    data = bytearray(open(src, "rb").read())
    unlinked = unlink(data, n)
    if unlinked != n:
        print(f"warning: asked to unlink {n} entries, only found {unlinked} reachable via the array chain",
              file=sys.stderr)
    open(dst, "wb").write(data)
    print(f"wrote {dst}: unlinked {unlinked} entr{'y' if unlinked == 1 else 'ies'} "
          f"from the array chain (header n_entries left untouched, on purpose)")


if __name__ == "__main__":
    main()
