#!/usr/bin/env python3
"""Post-process a clean, closed .journal file to unlink one or more entries from the main
entry-array chain, while leaving the ENTRY objects themselves physically intact on disk. This
is the recovery-path fixture in generators/journal.sh's matrix: a parser that only walks the
array chain must miss the unlinked entries; a parser with a carving/recovery fallback must
still find them.

Deliberately does NOT update Header.n_entries (or any hash table) to match: the mismatch
between the header's claimed entry count and what the array walk actually reaches is itself
a forensically useful tell, not something to paper over.

Binary format below is transcribed from systemd's own
src/libsystemd/sd-journal/journal-def.h (fetched from the systemd v255 tag; re-check against
the version actually installed in the container before trusting this against anything other
than the file this script's author last verified it against - see journal.sh). Only supports
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

    unlinked = 0
    array_offset = h["entry_array_offset"]
    while array_offset and unlinked < n_to_unlink:
        obj_type, _flags, size = obj_header(bytes(data), array_offset)
        if obj_type != OBJECT_ENTRY_ARRAY:
            raise ValueError(f"expected ENTRY_ARRAY at {array_offset:#x}, got type {obj_type}")
        # EntryArrayObject: ObjectHeader(16) + next_entry_array_offset(8) + items[](8 each)
        (next_array,) = struct.unpack_from("<Q", data, array_offset + OBJ_HEADER_SIZE)
        items_off = array_offset + OBJ_HEADER_SIZE + 8
        n_items = (size - OBJ_HEADER_SIZE - 8) // 8
        for i in range(n_items):
            if unlinked >= n_to_unlink:
                break
            item_off = items_off + i * 8
            (entry_offset,) = struct.unpack_from("<Q", data, item_off)
            if entry_offset == 0:
                continue  # unused slot
            obj_type, _flags, _size = obj_header(bytes(data), entry_offset)
            if obj_type != OBJECT_ENTRY:
                continue
            struct.pack_into("<Q", data, item_off, 0)  # unlink: zero the slot, keep the ENTRY object's bytes untouched
            unlinked += 1
        array_offset = next_array
    return unlinked


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
