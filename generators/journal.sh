#!/usr/bin/env bash
# Generates the .journal fixture matrix with the real systemd-journal-remote (converts Journal
# Export Format text to a genuine journal file, no root/capabilities needed - unlike
# ntfs_mkntfs.sh's FUSE mount), plus a journalctl --output=json oracle for every fixture it
# produces cleanly.
#
#   generators/journal.sh    # -> generators/out/journal/{*.journal, *.oracle.json}
#
# Requires docker and network egress to pull the pinned image; this script has NOT been run
# in CI or against a real container yet (the sandbox this was written in has neither docker
# nor root - see the FOR-29 task comment). Before trusting its output or registering fixtures
# from it with tools/add_artifact.py, run it for real and check:
#   - that `journalctl --file=... -o json` on journal-plain.journal actually reproduces the
#     3 STANDARD_ENTRIES from journal_export.py (in particular, that a local stdin conversion
#     honors the supplied __REALTIME_TIMESTAMP - see journal_export.py's docstring)
#   - the env-var effect on-disk: `xxd` the header's incompatible_flags (offset 12, 4 bytes LE)
#     and confirm the expected HEADER_INCOMPATIBLE_* bit per row (values in journal-def.h,
#     transcribed into journal_unlink_entries.py)
#   - that journal-online-stale.journal really lands with header byte 20 (State) == 1 (ONLINE)
#     rather than 2 (ARCHIVED); the timing (`sleep 1` below) may need tuning
#
# NOT byte-reproducible across runs, unlike generators/utmp.py: journal_file_init_header()
# unconditionally calls sd_id128_randomize() for the header's file_id (and seqnum_id, which
# defaults to file_id when there's no template file) - there is no env var or flag to pin it,
# verified by reading journal-file.c itself. So these fixtures follow the same rule
# generators/README.md already states for chrome_history.py (generated once, hashed, and
# from then on everyone uses that one pinned copy), not the stricter "two runs are
# byte-identical" bar generators/utmp.py and generators/textlogs/ meet.
#
# Compression algorithm and format-variant selection is via env vars read directly by
# src/libsystemd/sd-journal/journal-file.c (verified against the systemd v255 source, which
# is the authority here, not the man pages - journald.conf's Compress= is yes/no/threshold
# only and does not select an algorithm):
#   SYSTEMD_JOURNAL_COMPRESS=none|xz|lz4|zstd   (boolean also accepted; default: compiled-in default)
#   SYSTEMD_JOURNAL_COMPACT=0|1                 (default 1 since systemd ~252)
#   SYSTEMD_JOURNAL_KEYED_HASH=0|1              (default 1 since systemd ~246; 0 = legacy Jenkins hash)
# All three are read by journal_file_init_header(), shared by journald AND
# systemd-journal-remote (both go through libsystemd's journal-file.c) - not journald-only.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
out="$here/out/journal"
rm -rf "$out"
mkdir -p "$out"

# debian:bookworm-slim, pinned by digest (resolved 2026-09-30 via the registry v2 API, not
# `docker pull` + `docker inspect`, since this sandbox has no docker - re-resolve if this
# digest is ever rotated out of the registry's garbage-collection window):
#   curl -s $(TOKEN...) https://registry-1.docker.io/v2/library/debian/manifests/bookworm-slim -D- -o/dev/null | grep -i docker-content-digest
IMAGE="debian@sha256:3783cc01769c7b2b1b83a5c5ad96c815348e28ed7da68e2e3687004faa906251"

docker run --rm \
  -v "$out:/out" \
  -v "$here/journal_export.py:/journal_export.py:ro" \
  -v "$here/journal_unlink_entries.py:/journal_unlink_entries.py:ro" \
  "$IMAGE" bash -euo pipefail -c '
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq >/dev/null
    apt-get install -y -qq systemd systemd-journal-remote python3 >/dev/null

    remote() { /usr/lib/systemd/systemd-journal-remote --output="$1" --split-mode=none -; }
    oracle() { journalctl --file="$1" --output=json --no-pager > "$1.oracle.json" 2>"$1.oracle.stderr" || true; }

    # --- one axis at a time, against a compress=none/compact=0/keyed_hash=0 baseline ---
    python3 /journal_export.py standard | SYSTEMD_JOURNAL_COMPRESS=none SYSTEMD_JOURNAL_COMPACT=0 SYSTEMD_JOURNAL_KEYED_HASH=0 \
      remote /out/journal-plain.journal   # also the non-keyed/Jenkins-hash, no-compression baseline
    python3 /journal_export.py standard | SYSTEMD_JOURNAL_COMPRESS=lz4  SYSTEMD_JOURNAL_COMPACT=0 SYSTEMD_JOURNAL_KEYED_HASH=0 \
      remote /out/journal-lz4.journal
    python3 /journal_export.py standard | SYSTEMD_JOURNAL_COMPRESS=zstd SYSTEMD_JOURNAL_COMPACT=0 SYSTEMD_JOURNAL_KEYED_HASH=0 \
      remote /out/journal-zstd.journal
    python3 /journal_export.py standard | SYSTEMD_JOURNAL_COMPRESS=xz   SYSTEMD_JOURNAL_COMPACT=0 SYSTEMD_JOURNAL_KEYED_HASH=0 \
      remote /out/journal-xz.journal
    python3 /journal_export.py standard | SYSTEMD_JOURNAL_COMPRESS=none SYSTEMD_JOURNAL_COMPACT=1 SYSTEMD_JOURNAL_KEYED_HASH=0 \
      remote /out/journal-compact.journal
    python3 /journal_export.py standard | SYSTEMD_JOURNAL_COMPRESS=none SYSTEMD_JOURNAL_COMPACT=0 SYSTEMD_JOURNAL_KEYED_HASH=1 \
      remote /out/journal-keyed-hash.journal

    # --- content-shaped fixtures, plain/baseline format ---
    python3 /journal_export.py non-utf8 | SYSTEMD_JOURNAL_COMPRESS=none SYSTEMD_JOURNAL_COMPACT=0 SYSTEMD_JOURNAL_KEYED_HASH=0 \
      remote /out/journal-non-utf8.journal
    python3 /journal_export.py zip-bomb | SYSTEMD_JOURNAL_COMPRESS=zstd SYSTEMD_JOURNAL_COMPACT=0 SYSTEMD_JOURNAL_KEYED_HASH=0 \
      remote /out/journal-zip-bomb.journal

    for f in /out/journal-plain.journal /out/journal-lz4.journal /out/journal-zstd.journal \
             /out/journal-xz.journal /out/journal-compact.journal /out/journal-keyed-hash.journal \
             /out/journal-non-utf8.journal /out/journal-zip-bomb.journal; do
      oracle "$f"
    done

    # --- state=ONLINE with stale tail pointers: kill systemd-journal-remote mid-write, before
    # it reaches EOF and closes (closing is what flips the header State byte to ARCHIVED) ---
    mkfifo /tmp/online.fifo
    SYSTEMD_JOURNAL_COMPRESS=none SYSTEMD_JOURNAL_COMPACT=0 SYSTEMD_JOURNAL_KEYED_HASH=0 \
      /usr/lib/systemd/systemd-journal-remote --output=/out/journal-online-stale.journal --split-mode=none /tmp/online.fifo &
    remote_pid=$!
    exec 3>/tmp/online.fifo
    python3 /journal_export.py standard >&3
    sleep 1   # let it land the entries and update the header before we cut it off
    kill -KILL "$remote_pid" 2>/dev/null || true
    exec 3>&-
    wait "$remote_pid" 2>/dev/null || true
    oracle /out/journal-online-stale.journal

    # --- truncated mid-object: cut off a clean file partway through its last object ---
    size=$(stat -c%s /out/journal-plain.journal)
    cp /out/journal-plain.journal /out/journal-truncated.journal
    truncate -s $((size - 37)) /out/journal-truncated.journal
    oracle /out/journal-truncated.journal

    # --- entries unlinked from the entry arrays: the recovery-path fixture ---
    python3 /journal_unlink_entries.py /out/journal-plain.journal /out/journal-unlinked-entries.journal 1
    oracle /out/journal-unlinked-entries.journal

    chmod a+rw /out/*
  '

ls -la "$out"
echo
echo "Next: register each *.journal (and its *.oracle.json) with tools/add_artifact.py," \
     "format=journal / format=json, source=synthetic, --used-by frnsc-linux. See README.md" \
     "for which row exercises which parser path."
