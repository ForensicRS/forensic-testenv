# Synthetic artifacts

Scripts that generate artifacts ourselves, with **known ground truth**: every value in the file is
also written to a `<name>.truth.json`, so tests can assert exact results instead of "doesn't crash".
Generated files contain no personal data and are always redistributable (MIT).

Output isn't byte-reproducible across tool versions (e.g. SQLite writes its library version into
the file header). So an artifact is generated **once**, registered and published, and from then on
everyone uses the published copy pinned by its SHA-256:

```sh
python3 generators/chrome_history.py                 # -> generators/out/chrome_history/History (+ truth json)
tools/add_artifact.py generators/out/chrome_history/History \
    --id sqlite-chrome-history-synthetic --format sqlite --source synthetic \
    --license MIT --redistributable --used-by frnsc-sqlite \
    --description "Chrome History DB with 5 known URLs/visits"
tools/add_artifact.py generators/out/chrome_history/History.truth.json \
    --id sqlite-chrome-history-synthetic-truth --format json --source synthetic \
    --license MIT --redistributable --used-by frnsc-sqlite
tools/publish_hf.py --id sqlite-chrome-history-synthetic --id sqlite-chrome-history-synthetic-truth --yes
```

Good candidates for more generators: registry hives written on a throwaway Windows VM with a
scripted set of keys, EVTX exported after a scripted set of actions (`wevtutil epl`), Prefetch
from running known binaries. Commit the script that produced them next to this README.

## Linux fixtures (FOR-29)

### `utmp.py` - wtmp/utmp/btmp records

Pure `struct.pack`, no container needed. Run directly: `python3 generators/utmp.py`. Layout
names and byte offsets match `crates/frnsc-linux/src/unix/utmp.rs` (`UtmpLayout::Narrow32` /
`::Wide64`) exactly - that module is the actual, already-reviewed consumer of these fixtures.

| file | exercises |
|---|---|
| `wtmp_narrow32.bin` | the 384-byte layout (`session`/`tv_sec`/`tv_usec` as 4-byte fields) that the overwhelming majority of real x86/x86_64 Linux evidence uses. Also registered as `frnsc-linux-utmp-sample`, the id `frnsc-linux/tests/real_samples.rs` already calls `artifact_or_skip!` with. 6 records: BOOT_TIME, RUN_LVL, LOGIN_PROCESS, 2x USER_PROCESS, DEAD_PROCESS |
| `wtmp_wide64.bin` | the same 6 records, 400-byte layout (`session`/`tv_sec`/`tv_usec` widened to 8 bytes) - `UtmpLayout::Wide64`'s only non-unit-test fixture |
| `wtmp_bad_utf8.bin` | one record's `ut_user` is not valid UTF-8 (a raw Latin-1 byte, not a UTF-8 continuation byte) - the rest of the record is untouched, so a correct parser isolates the failure to one field, not the whole record |
| `wtmp_truncated.bin` | 6 good Narrow32 records followed by a dangling record cut off at 200 of 384 bytes - the stream must yield 6 good records and one `Err`, not panic or silently drop the tail |
| `wtmp_wide64_truncated.bin` | one good Wide64 record (400 bytes) plus 50 trailing bytes = 450, a multiple of neither 384 nor 400 - the real-file shape of `frnsc-linux`'s pinned regression test `a_wide64_file_truncated_mid_record_is_misdetected_as_narrow32`: `detect_layout` defaults to Narrow32, so `ut_user` (before offset 336) still reads right but `tv_sec` comes out wrong |

### `journal.sh` - the `.journal` format matrix

**Run for real** (2026-09-30) - no docker/podman/root needed, contrary to the original plan:
it extracts `systemd`/`systemd-journal-remote`/`libsystemd-shared`/`libmicrohttpd12t64`
straight out of pinned Debian `.deb` packages (`dpkg-deb -x` into a local prefix, no privilege
required) fetched by content hash from `snapshot.debian.org`, and runs them against the host's
own already-present libc/libcap/etc. See the script's header comment for exactly how, and for
two things the *first* real run found that the original (source-only) design got wrong:

1. **`SYSTEMD_JOURNAL_COMPRESS` only takes yes/no, not an algorithm name.** The original plan
   (read from systemd v255 source, never executed) assumed `lz4`/`xz`/`zstd`/`none` selected
   among algorithms. Empirically, against the real 257.13-1~deb13u1 binary, any non-"no" string
   means "compress=true" - so `lz4`/`xz`/`zstd`/`none` all behaved identically. Worse: this
   build never writes anything but ZSTD when compression is on (verified by inspecting the
   DATA object flags byte). There is no working way to make it write a genuinely LZ4- or
   XZ-compressed object, so the matrix has one compressed/uncompressed axis
   (`journal-uncompressed.journal` / `journal-compressed.journal`), not one file per algorithm.
   A parser's LZ4/XZ *read* path still needs covering, but from real old-systemd samples or a
   hand-built unit fixture - not this generator.
2. **`journalctl --output=json` needs `--all` for ground truth**, or it silently reports large
   field values as JSON `null` (confirmed: without `--all`, the zip-bomb fixture's 64 MiB
   `MESSAGE` comes back `null`). Every oracle here is captured with `--all` *except*
   `journal-zip-bomb.journal.oracle.json`, which deliberately uses the default (no `--all`)
   output - the alternative is a ~67 MB committed JSON file for a repo that otherwise measures
   fixtures in KiB, and the default output is also literally what an analyst piping journalctl
   without extra flags would see.

Unlike `utmp.py`, these files are **not** byte-reproducible run to run: every real journal
file's header carries a randomized `file_id` (and `seqnum_id`, which defaults to it) with no
way to pin it - see the script's header comment. They follow the same rule this README
already states for `chrome_history.py`: generate once, register that one copy, pin it by hash.

| file | exercises |
|---|---|
| `journal-plain.journal` | baseline: no compression, non-compact (8-byte offsets), non-keyed (Jenkins) hash, 3 short entries - the oldest/simplest on-disk shape |
| `journal-uncompressed.journal` | a 2320-byte compressible `MESSAGE` (a repeated traceback, well over the real ~504-byte compression threshold - bisected against the real binary, not assumed) written with `--compress=no`: DATA object lands uncompressed at 2392 bytes on disk |
| `journal-compressed.journal` | the same 2320-byte `MESSAGE`, written with `--compress=yes`: DATA object carries `OBJECT_COMPRESSED_ZSTD` and shrinks to 229 bytes on disk - the genuine on-disk A/B this pairs with `journal-uncompressed.journal` to demonstrate |
| `journal-compact.journal` | `HEADER_INCOMPATIBLE_COMPACT`: 4-byte offsets in DATA/ENTRY objects instead of 8 |
| `journal-keyed-hash.journal` | `HEADER_INCOMPATIBLE_KEYED_HASH`: siphash24 instead of Jenkins |
| `journal-online-stale.journal` | `systemd-journal-remote` killed mid-write, before it closes the file: header `State` byte stays `ONLINE` (1) rather than `ARCHIVED` (2), with tail pointers as they stood at the last write, not a clean shutdown - what imaging a running host actually looks like. Confirmed by reading the header byte directly |
| `journal-truncated.journal` | `journal-plain.journal` cut off 37 bytes short - mid-object at EOF. `journalctl` itself refuses it outright (`Failed to open files: No data available`), captured as the `.oracle.stderr` alongside an empty `.oracle.json` - a parser needs its own recovery path, the real tool doesn't have one here |
| `journal-unlinked-entries.journal` | entry #3 of 3 deliberately unreachable via the main entry-array chain (the array's last populated slot zeroed, the ENTRY object's bytes untouched) - the recovery-path fixture. `journal_unlink_entries.py` leaves the header's `n_entries` unchanged on purpose, so the mismatch between the claimed count (3) and what a chain-walk reaches (2) is itself a signal. Confirmed against the real `journalctl`: it shows only entries 1-2. **Deliberately unlinks the tail, not an earlier entry** - unlinking entry #1 of 3 was tried first and found to make `journalctl`'s own sequential reader drop entry #3 too (a zeroed slot followed by populated ones apparently reads as "end of valid data", not "one hole"); that cascading-loss behavior is real and worth knowing for a recovery parser, but makes a confusing *primary* fixture, so it's documented in `journal_unlink_entries.py`'s docstring instead of shipped as the main case |
| `journal-non-utf8.journal` | one entry's `MESSAGE` field is not valid UTF-8. `journalctl`'s own JSON oracle represents it as an array of raw byte integers, not a string - useful to know what the reference tool does with this case |
| `journal-zip-bomb.journal` | one entry's `MESSAGE` field is 64 MiB of a single repeated byte, ZSTD-compresses to 229 bytes on disk - but the **file itself is not small**: `systemd-journal-remote` pre-grows the on-disk arena to 72 MiB while producing it (confirmed non-sparse: real allocated zero bytes past the tiny compressed object, not a hole). So this is a two-layer bounded-decompression case: a parser trusting the *declared* 64 MiB field size needs a decompression guard, and one trusting the file's own *size* or `arena_size` as an allocation hint is still over-allocating ~70x what the real content needs |

Every file above is `ARCHIVED` state on a clean run except `journal-online-stale.journal`.
`journal-plain.journal` (non-keyed hash, i.e. Jenkins, uncompressed by construction since its
messages never cross the compression threshold) also stands in for the "non-keyed" half of the
KEYED_HASH/non-keyed pair the design doc asks for, rather than duplicating a whole extra file
that would only differ in one flag already covered by `journal-keyed-hash.journal`.

### Text logs (`textlogs/`)

Hand-written, committed directly (not generated, not gitignored - they're tens of bytes each).
Register each with `tools/add_artifact.py --source synthetic --used-by frnsc-linux`.

| file | exercises |
|---|---|
| `syslog-rfc3164.log` | classic BSD syslog: `<PRI>Mon DD HH:MM:SS host tag[pid]: msg`, no year |
| `syslog-rfc5424.log` | structured syslog: `VERSION`, ISO-8601 timestamp with offset, a structured-data element, and a NILVALUE (`-`) structured-data field |
| `auth.log` | Debian/Ubuntu auth log shape: cron PAM session, sshd pubkey accept + session open/close, sudo command line |
| `audit.log` | Linux audit: `type=... msg=audit(epoch.msec:serial): ...` across USER_AUTH/USER_LOGIN/SYSCALL |
| `bash_history_plain` | bash history with no `HISTTIMEFORMAT`: bare commands only |
| `bash_history_histtimeformat` | bash history with `HISTTIMEFORMAT` set: a `#<unix-epoch>` comment line precedes each command |
| `docker-json-file.log` | docker's `json-file` logging driver: one `{"log","stream","time"}` JSON object per line |
| `containerd-cri.log` | containerd/CRI log line format: `<rfc3339nano> <stream> <F\|P> <content>`, including a partial(`P`)+full(`F`) split-line pair |
