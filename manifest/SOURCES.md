# Sources, licenses and attribution

Every artifact in `artifacts.toml` names its `source`. This file records, per source, the license
or terms that apply, whether files may be mirrored to the Hugging Face dataset, and the required
attribution. Check the source's terms **before** adding `redistributable = true`.

Upstream repositories license their test data together with their code; none has a separate data
license. Several of these files come from real (test) systems of undocumented origin. The mirror
keeps the upstream license per file and credits the project below.

| source | Publisher / pinned revision | License / terms | Mirrored to HF | Attribution |
|---|---|---|---|---|
| `synthetic` | ForensicRS, `generators/` | MIT | yes | – |
| `plaso` | [log2timeline/plaso](https://github.com/log2timeline/plaso) `test_data/` @ `0635f69` | Apache-2.0 | yes (ship LICENSE with the mirror) | © the plaso authors |
| `omerbenamram-evtx` | [omerbenamram/evtx](https://github.com/omerbenamram/evtx) `samples/` @ `85d7e64` | MIT OR Apache-2.0 | yes | © Omer Ben-Amram and contributors |
| `ez-prefetch` | [EricZimmerman/Prefetch](https://github.com/EricZimmerman/Prefetch) `Prefetch.Test/TestFiles` @ `27d87a0` | MIT | yes | © Eric Zimmerman |
| `ez-registry` | [EricZimmerman/Registry](https://github.com/EricZimmerman/Registry) `Registry.Test/Hives` @ `1b0b3c4` | MIT | yes | © Eric Zimmerman |
| `oletools` | [decalage2/oletools](https://github.com/decalage2/oletools) `tests/test-data` @ `ec10260` | BSD-2-Clause (LICENSE.md) | yes | © Philippe Lagadec |
| `pymsi` | [nightlark/pymsi](https://github.com/nightlark/pymsi) `docs/_static` @ `e2676c4` | MIT | yes | © pymsi authors |
| `evtx-attack-samples` | [sbousseaden/EVTX-ATTACK-SAMPLES](https://github.com/sbousseaden/EVTX-ATTACK-SAMPLES) @ `4ceed2f` | GPL-3.0 | **no**: redistribution is allowed, but kept origin-only so the mirror stays permissively licensed | Samir Bousseaden |
| `nist-cfreds` | [NIST CFReDS](https://cfreds.nist.gov) (download from cfreds-archive.nist.gov) | US Government work, not subject to US copyright ([nist.gov/open/license](https://www.nist.gov/open/license)); acknowledge NIST | not mirrored (size); allowed | "Source: NIST Computer Forensic Reference Data Sets (CFReDS)" |
| `unizar-ctf` | [ctf.unizar.es](https://ctf.unizar.es) DFIR CTFs of the Universidad de Zaragoza (`bolas_cocido`, files dated 2025-01-09) | **none stated** (publicly downloadable, no terms on the site) | **no**; private cold copy only (`cold_path`) | "Source: DFIR CTF, Universidad de Zaragoza (ctf.unizar.es)" |
| `digital-corpora` | [Digital Corpora](https://digitalcorpora.org) | CC0 for original scenario content; software/files inside images keep their own copyright | not mirrored (size) | "Source: Digital Corpora (digitalcorpora.org)" |

## Notes

* **Disk image hashes.** NIST and Digital Corpora publish the MD5/SHA1 of the imaged *media*, not
  of the downloadable E01 segments. Those are stored as `media_md5` / `media_sha1` (verify with
  `ewfverify`). The file-level `sha256` is pinned after a first download (`fetch.py` prints it).
  The Hacking Case and M57 media MD5s were read from the EWF hash section of the files themselves.
  NIST's page publishes none for the Hacking Case.
* **The `.cfreds.nist.gov/images/...` paths** of the new portal return an HTML page with status 200.
  Always use `cfreds-archive.nist.gov`.
* **Considered and rejected:** dfirlabs `*-specimens` (CC-BY-4.0, but they hold only generator
  scripts, no binaries); MarkBaggett/srum-dump (GPL-3.0, no sample); Magnet CTF and DFRWS
  challenge images (no stated terms, unstable hosting). SANS challenge images: not researched yet.
* **CTF / challenge data** usually states no terms. Register it with `license = "unknown"`,
  `redistributable = false` and a `cold_path`: the private cold copy protects against dead links
  but is not permission to redistribute. Ask the organisers before ever mirroring it publicly.
  Facts in `cases/` are read by us with independent tools, never copied from write-ups.
* **Unizar `bolas_cocido` memory dumps** (`RabanoSRV.zip` 116 MiB, `NaboPC2.zip` 480 MiB,
  `BoniatoPC1.zip` 516 MiB, DumpIt) are not registered: no crate parses memory yet.
* **Candidates for later:** CFReDS 2017 Windows Registry dataset (`ugrd-*` sets are 4 MB / 15 KB
  7z files, but `fetch.py` doesn't extract 7z yet); EZ Registry edge-case hives (`SAMBadHBinHeader`,
  `SECURITYNoRoot`, `NotAHive`, `NTUSER slack.DAT`); oletools' encrypted Office samples; plaso
  `windows/ActivitiesCache.db`, `Catalog1.edb`; SOFTWARE hives (37–65 MB, `full` tier).
* **Sources to evaluate** (terms and URLs not checked yet; verify before registering):
  other ctf.unizar.es challenges; DFIR Madness "The Case of the Stolen Szechuan Sauce" (DC and
  desktop disks, memory, pcap); Ali Hadi's DFIR datasets (ashemery.com, Windows E01s); Digital
  Corpora M57-Patents (multi-machine, with RAM) and the NPS test images (`nps-2009-ntfs1`); Brian
  Carrier's DFTT test images (NTFS with documented answers); Yamato Security
  `hayabusa-sample-evtx`, OTRF Security-Datasets and mdecrevoisier/EVTX-to-MITRE-Attack (EVTX
  across many providers); memory, for later: Volatility Foundation samples, MemLabs.
  Preferred over all of these: forensic-lab scenarios, whose facts are exact and which are MIT.
