# forensic-testenv

The shared test corpus of the [ForensicRS](https://github.com/ForensicRS) crates: real and
synthetic Windows forensic artifacts, pinned by SHA-256, with tooling to download them and hand
them to Rust tests.

* **`manifest/artifacts.toml`** is the source of truth: for every artifact, its origin, hash, size,
  license, whether it may be redistributed, and which crates use it.
* **Hugging Face mirror**: artifacts whose license allows it are mirrored to the dataset
  [`ForensicRS/forensic-test-artifacts`](https://huggingface.co/datasets/ForensicRS/forensic-test-artifacts),
  a stable and fast source that doesn't depend on third-party URLs staying alive.
* **Origin-only**: everything else (e.g. big NIST CFReDS / Digital Corpora images) is downloaded
  from where its publisher hosts it, and verified by hash just the same.
* **Cold copy**: files that may not be mirrored publicly (e.g. CTF data with no stated terms) can
  also be kept in a *private* dataset, `ForensicRS/forensic-test-artifacts-cold`, tried only when
  the origin fails. It exists so tests survive dead links; it is never published.
* **Cases** (`manifest/cases/`): several machines of one scenario (e.g. a DFIR CTF), with *facts*
  (values checked with an independent tool) that tests assert.
* **`forensic-testdata`** (Rust crate) lets tests find a fetched artifact by id, with no network
  access from `cargo test`.

It's meant to sit next to [`forensic-bootstrap`](https://github.com/ForensicRS/forensic-bootstrap),
which adds `forensic-testdata` to the workspace automatically.

## Fetching

```sh
tools/fetch.py --list                          # everything in the manifest + cache status
tools/fetch.py                                 # the `ci` tier: small files every PR needs
tools/fetch.py --crate frnsc-winevt --install  # one crate's artifacts, linked where its tests expect them
tools/fetch.py --tier full                     # large disk images (tens of GB, manual/nightly use)
tools/fetch.py --case unizar-bolas-cocido      # every artifact of a case (~1.2 GB extracted)
tools/fetch.py --verify                        # re-hash everything already cached
```

Files are cached (content-addressed) under `~/.cache/forensic-testdata`, or `$FORENSIC_TESTDATA_DIR`.
`--install` symlinks them to the crate-relative paths in each entry's `install` list, for tests
that read a fixed `artifacts/...` path. Only use it when the test doesn't assert values that are
specific to one copy of the file (many do: exact log positions, stream sizes, ...). Otherwise,
reference the artifact by id with `forensic-testdata`. It looks for crates in `../forensic-bootstrap/crates` (override with `$FORENSIC_CRATES_DIR`).

Only the Python ≥ 3.11 standard library is needed, plus a `7z` binary (7-Zip) for `.7z` archives.

## Using artifacts in Rust tests

```toml
[dev-dependencies]
forensic-testdata = { workspace = true }   # inside forensic-bootstrap
# or, for a crate's standalone CI:
# forensic-testdata = { git = "https://github.com/ForensicRS/forensic-testenv" }
```

```rust
#[test]
fn parses_real_prefetch() {
    let path = forensic_testdata::artifact_or_skip!("prefetch-v30-mam-win10-calc");
    let bytes = std::fs::read(path).unwrap();
    // ...
}
```

A missing artifact skips the test with a message saying how to fetch it. With
`FORENSIC_TESTDATA_STRICT=1` (CI), it fails instead.

## Cases

A case is a scenario with several machines, usually a DFIR challenge: `manifest/cases/<id>.toml`
lists each machine (a *bundle* artifact, and optionally the `root` subdirectory inside it) and the
facts known about them:

```toml
[[machine]]
name = "NaboPC2"
artifact = "triage-unizar-nabopc2"
root = "NABO2_20170520_174706"

[[fact]]
machine = "NaboPC2"
kind = "prefetch-run"
path = "LiveResponseData/CopiedFiles/prefetch/CMD.EXE-89305D47.pf"   # relative to root
expect = { version = 23, executable = "CMD.EXE", run_count = 12, last_run = 131397678976762348 }
verified_by = "dissect.target 3.25.1 prefetch plugin"
```

**Facts come from us, not from write-ups.** Read each value with a tool independent of
ForensicRS (dissect in forensic-lab's `forensicrs-lab-extract` container, EZ tools, the
collector's own live output) and say which one in `verified_by`. A write-up or the CTF's answer
key only tells you where to look. Fact kinds and their `expect` keys:

| kind | `path` | `expect` |
|---|---|---|
| `hive-value` | hive file | `key`, `name`, `value` |
| `evtx-count` | .evtx | `records` (total), `events` (`{ EventID = count }`, the most frequent ones) |
| `prefetch-run` | .pf | `version`, `executable` (up to the first NUL), `run_count`, `last_run` (raw FILETIME) |

Add a kind when a crate needs one, and document it here. In Rust (`features = ["cases"]`):

```rust
let case = forensic_testdata::case_or_skip!("unizar-bolas-cocido");
for fact in case.facts("prefetch-run") {
    let pf = case.fact_path(fact);          // absolute path of the file
    let want = &fact.expect;                // toml::Table
    // ...
}
```

`fetch.py` copies a case to `<cache>/cases/` only once all its artifacts are verified.

## Adding an artifact

1. **Check the license** of the source. Mirroring to Hugging Face requires a license that allows
   redistribution (MIT, Apache-2.0, BSD, CC-BY, public domain, …). If in doubt, mark it
   `redistributable = false`: it's then fetched from its origin URL only.
2. **Never add data from real cases or real people.** Use public corpora, or generate it (see
   `generators/`). Artifacts from real Windows installs (one VM per version) come from
   [forensic-lab](../forensic-lab); register them with `--source forensicrs-lab`.
3. Register it:
   ```sh
   tools/add_artifact.py <url-or-file> --id <format>-<what> --format evtx --source <collection> \
       --license Apache-2.0 --redistributable --used-by frnsc-winevt --description "..."
   ```
   This hashes the file, appends an entry to the manifest and keeps a copy in the cache.
   Pin URLs to a commit or release, never to a moving branch.
   For an archive that tests use as a directory tree (a triage collection), add `--bundle`: it
   is extracted, and every file is pinned in `manifest/bundles/<id>.tsv`. For a file that may
   not be mirrored but whose origin may disappear, add `--cold`.
4. Add an attribution line to `manifest/SOURCES.md` if it's a new source.
5. Mirror redistributable files (maintainers): `pip install -r tools/requirements.txt`, then
   `HF_TOKEN=… tools/publish_hf.py` (dry run) and `tools/publish_hf.py --yes`. This also regenerates
   the dataset card from `hf/README.md`. Cold copies: `tools/publish_cold.py` (dry run), then
   `--yes`. It refuses to upload if the cold dataset is public.

### Manifest fields

| Field | Required | Meaning |
|---|---|---|
| `id` | ✓ | Stable identifier used by tests: `<format>-<what>` |
| `description` | | What is in it and what it is good for testing |
| `format` | ✓ | `evtx`, `hive`, `prefetch`, `esedb`, `ole`, `sqlite`, `image-e01`, … |
| `source` | ✓ | Collection it comes from (`plaso`, `nist-cfreds`, `synthetic`, …) |
| `tier` | | `ci` (small, every PR; the default) or `full` (large, manual/nightly) |
| `sha256` | ✓* | Hash of the final file. (*`md5` alone is accepted only when the publisher gives nothing better) |
| `size` | | Bytes; checked before hashing |
| `license` | ✓ | SPDX id or the publisher's terms |
| `redistributable` | | `true` only if the license allows mirroring |
| `hf_path` | | Path in the HF dataset, `<source>/<format>/<id>/<file>`; only for redistributable files |
| `origin_url` | | Where the publisher hosts it (pinned) |
| `archive` | | `{ format = "zip"\|"tar"\|"7z", member = "path/in/archive", sha256 = "…", password = "…" }` when the origin is an archive |
| `bundle` | | `true`: the file is an archive extracted whole; `sha256`/`size`/`filename` are the archive's, `manifest/bundles/<id>.tsv` pins its files |
| `cold_path` | | Path in the private cold dataset, `<source>/<id>/<file>`; only for non-redistributable files |
| `filename` | | Name in the cache (default: basename of the URL/member) |
| `used_by` | | Crates whose tests use it (`fetch.py --crate`) |
| `install` | | `crate:relative/path` targets for `--install` |

## Layout

```
manifest/artifacts.toml   what exists (source of truth)
manifest/SOURCES.md       attribution and license notes per source
manifest/bundles/         per-file lock of each bundle (sha256, size, path)
manifest/cases/           multi-machine scenarios and their facts
tools/                    fetch.py, add_artifact.py, publish_hf.py, publish_cold.py
hf/README.md              dataset card template (the table is generated)
generators/               scripts producing synthetic artifacts with ground truth
crates/forensic-testdata  Rust helper for tests
```
