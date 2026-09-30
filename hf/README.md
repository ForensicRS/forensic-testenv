---
license: other
license_name: per-file
license_link: https://github.com/ForensicRS/forensic-testenv/blob/main/manifest/SOURCES.md
pretty_name: ForensicRS test artifacts
tags:
  - forensics
  - dfir
  - windows
  - test-data
size_categories:
  - n<1K
---

# ForensicRS test artifacts

Windows forensic artifacts (event logs, registry hives, Prefetch, ESE databases, OLE files, ...)
used by the test suites of the [ForensicRS](https://github.com/ForensicRS) Rust crates.

**This file is generated** by `tools/publish_hf.py` from
[`manifest/artifacts.toml`](https://github.com/ForensicRS/forensic-testenv/blob/main/manifest/artifacts.toml).
Don't edit it on the Hub; change the manifest instead.

## Use

Don't download by hand. From a ForensicRS workspace:

```sh
forensic-testenv/tools/fetch.py --tier ci --install
```

`fetch.py` verifies every file against the SHA-256 recorded in the manifest.

## Licensing

Every file keeps the license of its original source, listed per file below. Only files whose
license permits redistribution are mirrored here. Everything else (e.g. large NIST CFReDS or
Digital Corpora images) is downloaded from its origin. See
[SOURCES.md](https://github.com/ForensicRS/forensic-testenv/blob/main/manifest/SOURCES.md)
for attribution.

The artifacts come from test systems and public forensic corpora. They may contain
malware samples or attack traces on purpose: handle them in an analysis environment.

## Files

<!-- ARTIFACT TABLE -->
