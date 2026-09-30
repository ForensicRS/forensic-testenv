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

Pure `struct.pack`, no container needed. Run directly: `python3 generators/utmp.py`.

| file | exercises |
|---|---|
| `wtmp_lp64.bin` | the baseline 384-byte glibc record (BOOT_TIME, RUN_LVL, LOGIN_PROCESS, 2x USER_PROCESS, DEAD_PROCESS) |
| `wtmp_ilp32.bin` | byte-identical to `wtmp_lp64.bin` on purpose - glibc fixes `ut_session`/`ut_tv` at 32 bits on every word size specifically so 32- and 64-bit processes share one wtmp format (see the script's docstring). A parser that infers record size from reported host word size rather than trusting the fixed 384-byte layout will misparse this pair inconsistently with itself. |
| `wtmp_bad_utf8.bin` | one record's `ut_user` is not valid UTF-8 (a raw Latin-1 byte, not a UTF-8 continuation byte) - the rest of the record is untouched, so a correct parser isolates the failure to one field, not the whole record |
| `wtmp_truncated.bin` | 6 good records followed by a dangling record cut off at 200 of 384 bytes - the stream must yield 6 good records and one `Err`, not panic or silently drop the tail |

### `journal.sh` - the `.journal` format matrix

**Needs docker and network egress; has not been run for real yet** (written and
syntax-checked, but not executed - see the warning at the top of the script for exactly what
to double-check on the first real run). Once it has been run and its output inspected against
the `journalctl --output=json` oracle it captures alongside each file, register each
`*.journal` (`format = "journal"`) and `*.journal.oracle.json` (`format = "json"`) with
`tools/add_artifact.py --source synthetic --used-by frnsc-linux`.

| file | exercises |
|---|---|
| `journal-plain.journal` | no compression, non-compact (8-byte offsets), non-keyed (Jenkins) hash - the oldest/simplest on-disk shape |
| `journal-lz4.journal` / `-zstd.journal` / `-xz.journal` | each of the three compression algorithms the format supports (`SYSTEMD_JOURNAL_COMPRESS`) |
| `journal-compact.journal` | `HEADER_INCOMPATIBLE_COMPACT`: 4-byte offsets in DATA/ENTRY objects instead of 8 |
| `journal-keyed-hash.journal` | `HEADER_INCOMPATIBLE_KEYED_HASH`: siphash24 instead of Jenkins |
| `journal-online-stale.journal` | `systemd-journal-remote` killed mid-write, before it closes the file: header `State` byte stays `ONLINE` (1) rather than `ARCHIVED` (2), with tail pointers as they stood at the last write, not a clean shutdown - what imaging a running host actually looks like |
| `journal-truncated.journal` | `journal-plain.journal` cut off 37 bytes short - mid-object at EOF |
| `journal-unlinked-entries.journal` | one ENTRY object deliberately unreachable via the main entry-array chain (the array slot zeroed, the object's bytes untouched) - the recovery-path fixture. `journal_unlink_entries.py` leaves the header's `n_entries` unchanged on purpose, so the mismatch between the claimed count and what a chain-walk reaches is itself a signal, and a parser needs a carving/recovery fallback to find this entry at all |
| `journal-non-utf8.journal` | one entry's `MESSAGE` field is not valid UTF-8 |
| `journal-zip-bomb.journal` | one entry's `MESSAGE` field is 64 MiB of a single repeated byte - compresses to a few KiB, so the file stays small while the DATA object's declared (decompressed) size is large; exercises a bounded-decompression guard rather than a naive "allocate declared size" decompressor |

Every file above is `ARCHIVED` state on a clean run except `journal-online-stale.journal`.
`journal-plain.journal` (non-keyed hash, i.e. Jenkins) also stands in for the "non-keyed"
half of the KEYED_HASH/non-keyed pair the design doc asks for, rather than duplicating a
whole extra file that would only differ in one flag already covered by
`journal-keyed-hash.journal`.

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
