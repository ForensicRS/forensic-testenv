#!/usr/bin/env bash
# Generates the .journal fixture matrix with the real systemd-journal-remote (converts Journal
# Export Format text to a genuine journal file), plus a journalctl --output=json oracle for
# every fixture it produces cleanly.
#
#   generators/journal.sh    # -> generators/out/journal/{*.journal, *.oracle.json}
#
# Runs systemd-journal-remote/journalctl from binaries extracted directly out of pinned Debian
# .deb packages (dpkg-deb -x into a local prefix) rather than a container: no root, no docker/
# podman needed - three independent sandboxes that were supposed to run this had none of
# docker, podman or root, but all had plain network egress and dpkg-deb (which just unpacks an
# ar/tar archive - no privilege required). Packages are fetched from snapshot.debian.org's
# content-addressed /file/<sha1> endpoint: a permanent archive keyed by the file's own hash -
# unlike deb.debian.org (which tracks the live suite and will eventually garbage-collect this
# exact package version), a snapshot.debian.org URL for a given hash does not change or
# disappear. Each package below is pinned by exact version + the sha1 snapshot.debian.org
# addresses it by + the sha256 this script itself verifies after download, so a corrupted or
# substituted download is a hard failure, not a silently different fixture.
#
# Resolved 2026-09-30 via:
#   curl -s https://snapshot.debian.org/mr/package/systemd/257.13-1~deb13u1/binfiles/<bin>/257.13-1~deb13u1
#   curl -s https://snapshot.debian.org/mr/binary/libmicrohttpd12t64/1.0.1-4/binfiles
# then sha256sum on the downloaded bytes.
#
# --- what actually controls the on-disk format, verified empirically against the real
# binaries above (not just read from source - the previous version of this script cited
# systemd v255 source for env-var names and was wrong about what they do in 257.13) ---
#
#   SYSTEMD_JOURNAL_COMPRESS=yes|no   (boolean only - confirmed by bisection. Passing an
#       algorithm name like "lz4"/"xz"/"zstd"/"none" does NOT select an algorithm: systemd's
#       boolean parser treats any string other than a recognized false token - no/false/0/off -
#       as true, so "none" silently means "compress=true", and "lz4"/"xz"/"zstd" all mean the
#       same thing too. There is no env var or CLI flag that selects a compression algorithm.
#       --compress[=yes|no] on the command line does the same thing, boolean-only.)
#   SYSTEMD_JOURNAL_COMPACT=0|1       (works as documented: toggles HEADER_INCOMPATIBLE_COMPACT.
#       Default 1.)
#   SYSTEMD_JOURNAL_KEYED_HASH=0|1    (works as documented: toggles HEADER_INCOMPATIBLE_KEYED_HASH.
#       Default 1.)
#
# The practical fallout: this systemd build only ever writes ZSTD-compressed objects (confirmed
# by inspecting DATA object flags byte-for-byte: every compressed object found, including in
# the zip-bomb fixture, carries OBJECT_COMPRESSED_ZSTD and none carry the XZ/LZ4 bits) - there
# is no way to make it emit a genuinely LZ4- or XZ-compressed object. So the fixture matrix
# below has a single compressed/uncompressed axis (journal-compressed.journal /
# journal-uncompressed.journal), not one file per algorithm as originally planned. A parser's
# LZ4/XZ *read* support still needs covering, but that has to come from real old-systemd
# samples (via forensic-testdata) or a hand-built unit fixture, not this generator - see
# README.md.
#
# Also verified empirically: the real compression threshold is a DATA object payload size of
# >503 bytes (not compressed at 503, compressed at 504) - close to the commonly-cited "512
# bytes" figure but pinned by bisection against the real binary rather than assumed. The short
# human-readable messages in STANDARD_ENTRIES never cross it, which is why journal_export.py
# has a separate large-message() row for the compression axis.
#
# NOT byte-reproducible across runs, unlike generators/utmp.py: journal_file_init_header()
# unconditionally calls sd_id128_randomize() for the header's file_id (and seqnum_id, which
# defaults to file_id when there's no template file) - there is no env var or flag to pin it.
# So these fixtures follow the same rule generators/README.md already states for
# chrome_history.py (generated once, hashed, and from then on everyone uses that one pinned
# copy), not the stricter "two runs are byte-identical" bar generators/utmp.py and
# generators/textlogs/ meet. The package pins above are what stay reproducible: re-running this
# script produces a *fresh* journal file with the same content and format, from the same
# systemd build, every time.
#
# journalctl's oracle: captured with --all. Without --all, journalctl's own --output=json
# silently reports any sufficiently large field as JSON `null` instead of its real value
# (confirmed on the zip-bomb fixture's 64 MiB MESSAGE) - an oracle captured without --all would
# under-represent ground truth for exactly the fixtures where the real value matters most.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
out="$here/out/journal"
cache="$here/.debcache"
sysroot="$cache/sysroot"

rm -rf "$out"
mkdir -p "$out" "$cache"

# name -> "snapshot.debian.org sha1 | sha256 of the .deb bytes | filename"
PKG_systemd="24970c2bee6b3392b3969e3e59e3ca5226fd7be0 ee81302a1d5b7434762b6e784a572854d4b6cf6235e33e2d104ad4e1cae71ab4 systemd_257.13-1~deb13u1_amd64.deb"
PKG_systemd_journal_remote="3249db9a3a9109afb6388d8e8fb734fc74cf9484 b50f6c070723a4a9ba3a43163d08d3a63ce9ddd6465a5409bfba24ca80444bd8 systemd-journal-remote_257.13-1~deb13u1_amd64.deb"
PKG_libsystemd_shared="f66c9933019d8dea904f6c0e036a4c91518d897e 2d349824e57f88507e2a33360610d8afc143a4ad5e80ee4bf8f37b826d6f37f9 libsystemd-shared_257.13-1~deb13u1_amd64.deb"
PKG_libmicrohttpd12t64="bc59a2cc72c9bff9282c8bad4342387fec5e9102 e4161f63d26b4835a79b936f50b4cc90fb10b718f3b6623ac609856adde4c76b libmicrohttpd12t64_1.0.1-4_amd64.deb"

fetch_and_verify() {
  local spec="$1" sha1 sha256 fname deb
  read -r sha1 sha256 fname <<<"$spec"
  deb="$cache/$fname"
  if [[ ! -f "$deb" ]] || ! echo "$sha256  $deb" | sha256sum -c - >/dev/null 2>&1; then
    curl -fsSL --max-time 120 "https://snapshot.debian.org/file/$sha1" -o "$deb"
  fi
  echo "$sha256  $deb" | sha256sum -c - >&2
  echo "$deb"
}

echo "Fetching pinned systemd packages from snapshot.debian.org..."
deb_systemd="$(fetch_and_verify "$PKG_systemd")"
deb_journal_remote="$(fetch_and_verify "$PKG_systemd_journal_remote")"
deb_libsystemd_shared="$(fetch_and_verify "$PKG_libsystemd_shared")"
deb_libmicrohttpd="$(fetch_and_verify "$PKG_libmicrohttpd12t64")"

rm -rf "$sysroot"
mkdir -p "$sysroot"
for deb in "$deb_systemd" "$deb_journal_remote" "$deb_libsystemd_shared" "$deb_libmicrohttpd"; do
  dpkg-deb -x "$deb" "$sysroot"
done

export LD_LIBRARY_PATH="$sysroot/usr/lib/x86_64-linux-gnu:$sysroot/usr/lib/x86_64-linux-gnu/systemd"
REMOTE_BIN="$sysroot/usr/lib/systemd/systemd-journal-remote"
JOURNALCTL_BIN="$sysroot/usr/bin/journalctl"
"$JOURNALCTL_BIN" --version >/dev/null   # fail fast if the extracted binary can't even start

oracle() {
  "$JOURNALCTL_BIN" --file="$1" --output=json --all --no-pager > "$1.oracle.json" 2>"$1.oracle.stderr" || true
}
# journal-zip-bomb.journal's MESSAGE is 64 MiB: an --all oracle would be a ~67 MB commit for a
# fixture repo that otherwise measures artifacts in KiB. journalctl's own DEFAULT output (no
# --all) reports the field as JSON null instead of materializing it - which is also exactly
# what a real analyst piping journalctl output would see without deliberately asking for
# --all, so it's the more honest oracle for this one fixture, not a worse one.
oracle_default() {
  "$JOURNALCTL_BIN" --file="$1" --output=json --no-pager > "$1.oracle.json" 2>"$1.oracle.stderr" || true
}

# --- format-variant axis, against a compress=no/compact=0/keyed_hash=0 baseline ---
python3 "$here/journal_export.py" standard \
  | env SYSTEMD_JOURNAL_COMPACT=0 SYSTEMD_JOURNAL_KEYED_HASH=0 "$REMOTE_BIN" \
      --output="$out/journal-plain.journal" --split-mode=none --compress=no -
python3 "$here/journal_export.py" large-message \
  | env SYSTEMD_JOURNAL_COMPACT=0 SYSTEMD_JOURNAL_KEYED_HASH=0 "$REMOTE_BIN" \
      --output="$out/journal-uncompressed.journal" --split-mode=none --compress=no -
python3 "$here/journal_export.py" large-message \
  | env SYSTEMD_JOURNAL_COMPACT=0 SYSTEMD_JOURNAL_KEYED_HASH=0 "$REMOTE_BIN" \
      --output="$out/journal-compressed.journal" --split-mode=none --compress=yes -
python3 "$here/journal_export.py" standard \
  | env SYSTEMD_JOURNAL_COMPACT=1 SYSTEMD_JOURNAL_KEYED_HASH=0 "$REMOTE_BIN" \
      --output="$out/journal-compact.journal" --split-mode=none --compress=no -
python3 "$here/journal_export.py" standard \
  | env SYSTEMD_JOURNAL_COMPACT=0 SYSTEMD_JOURNAL_KEYED_HASH=1 "$REMOTE_BIN" \
      --output="$out/journal-keyed-hash.journal" --split-mode=none --compress=no -

# --- content-shaped fixtures, plain/baseline format ---
python3 "$here/journal_export.py" non-utf8 \
  | env SYSTEMD_JOURNAL_COMPACT=0 SYSTEMD_JOURNAL_KEYED_HASH=0 "$REMOTE_BIN" \
      --output="$out/journal-non-utf8.journal" --split-mode=none --compress=no -
python3 "$here/journal_export.py" zip-bomb \
  | env SYSTEMD_JOURNAL_COMPACT=0 SYSTEMD_JOURNAL_KEYED_HASH=0 "$REMOTE_BIN" \
      --output="$out/journal-zip-bomb.journal" --split-mode=none --compress=yes -

for f in "$out"/journal-plain.journal "$out"/journal-uncompressed.journal "$out"/journal-compressed.journal \
         "$out"/journal-compact.journal "$out"/journal-keyed-hash.journal \
         "$out"/journal-non-utf8.journal; do
  oracle "$f"
done
oracle_default "$out/journal-zip-bomb.journal"

# --- state=ONLINE with stale tail pointers: kill systemd-journal-remote mid-write, before
# it reaches EOF and closes (closing is what flips the header State byte to ARCHIVED) ---
fifo="$(mktemp -u "$cache/online.fifo.XXXXXX")"
mkfifo "$fifo"
env SYSTEMD_JOURNAL_COMPACT=0 SYSTEMD_JOURNAL_KEYED_HASH=0 "$REMOTE_BIN" \
  --output="$out/journal-online-stale.journal" --split-mode=none --compress=no "$fifo" &
remote_pid=$!
exec 3>"$fifo"
python3 "$here/journal_export.py" standard >&3
sleep 1   # let it land the entries and update the header before we cut it off
kill -KILL "$remote_pid" 2>/dev/null || true
exec 3>&-
wait "$remote_pid" 2>/dev/null || true
rm -f "$fifo"
oracle "$out/journal-online-stale.journal"

# --- truncated mid-object: cut off a clean file partway through its last object ---
size=$(stat -c%s "$out/journal-plain.journal")
cp "$out/journal-plain.journal" "$out/journal-truncated.journal"
truncate -s $((size - 37)) "$out/journal-truncated.journal"
oracle "$out/journal-truncated.journal"

# --- entries unlinked from the entry arrays: the recovery-path fixture ---
python3 "$here/journal_unlink_entries.py" "$out/journal-plain.journal" "$out/journal-unlinked-entries.journal" 1
oracle "$out/journal-unlinked-entries.journal"

ls -la "$out"
echo
echo "Next: register each *.journal (and its *.oracle.json) with tools/add_artifact.py," \
     "format=journal / format=json, source=synthetic, --used-by frnsc-linux. See README.md" \
     "for which row exercises which parser path."
