#!/usr/bin/env python3
"""Download, verify and install the ForensicRS test artifacts listed in manifest/artifacts.toml.

    fetch.py --list                         show every artifact and whether it is cached
    fetch.py --tier ci                      fetch the small artifacts used by every PR (default)
    fetch.py --tier full                    fetch the big disk images (tens of GB)
    fetch.py --crate frnsc-esedb --install  fetch what one crate needs and place it where its tests look
    fetch.py --id evtx-security             fetch specific artifacts
    fetch.py --case unizar-bolas-cocido     fetch every artifact of a case (manifest/cases/)
    fetch.py --verify                       re-hash every cached file
    fetch.py --seed generators/out          take unpublished artifacts from local generator output

Artifacts mirrored to the Hugging Face dataset are downloaded from there first,
then from their origin URL, and last from the private cold copy (only with an
HF_TOKEN that can read it). Every file is checked against the hash in the
manifest before it is used. Bundles (`bundle = true`) are archives extracted
whole; each extracted file is checked against manifest/bundles/<id>.tsv.

Only the standard library is needed (plus a `7z` binary for .7z archives).
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import tomllib
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
MANIFEST = HERE.parent / "manifest" / "artifacts.toml"
CHUNK = 1 << 20


def cache_dir() -> Path:
    """Must match forensic-testdata's `cache_dir()` in crates/forensic-testdata/src/lib.rs."""
    if env := os.environ.get("FORENSIC_TESTDATA_DIR"):
        return Path(env)
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "forensic-testdata"
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "forensic-testdata"


def crates_dir() -> Path:
    if env := os.environ.get("FORENSIC_CRATES_DIR"):
        return Path(env)
    return HERE.parent.parent / "forensic-bootstrap" / "crates"


# --------------------------------------------------------------------------- manifest


class Artifact:
    def __init__(self, raw: dict, manifest_dir: Path = MANIFEST.parent):
        self.raw = raw
        self.id: str = raw["id"]
        self.sha256: str | None = raw.get("sha256", "").lower() or None
        self.md5: str | None = raw.get("md5", "").lower() or None
        self.tier: str = raw.get("tier", "ci")
        # Large images whose publishers only give media (acquisition) hashes can't be pinned
        # until someone downloads them once; they are then checked by size only.
        self.unpinned = not (self.sha256 or self.md5)
        if self.unpinned and self.tier != "full":
            raise ValueError(f"{self.id}: needs sha256 (only tier = \"full\" entries may be unpinned)")
        self.redistributable: bool = bool(raw.get("redistributable", False))
        if raw.get("hf_path") and not self.redistributable:
            raise ValueError(f"{self.id}: has hf_path but is not redistributable; only mirror what the license allows")
        if not raw.get("license"):
            raise ValueError(f"{self.id}: license is required (use \"unknown\" and redistributable = false if unsure)")
        self.size: int | None = raw.get("size")
        self.group: str | None = raw.get("group")
        self.hf_path: str | None = raw.get("hf_path")
        self.origin_url: str | None = raw.get("origin_url")
        self.archive: dict | None = raw.get("archive")
        self.install: list[str] = raw.get("install", [])
        self.used_by: list[str] = raw.get("used_by", [])
        self.cold_path: str | None = raw.get("cold_path")
        if self.cold_path and self.redistributable:
            raise ValueError(f"{self.id}: redistributable files go to the public mirror (hf_path), not cold_path")
        # A bundle is an archive extracted whole into a directory: sha256/size/filename describe the
        # archive, and manifest/bundles/<id>.tsv pins every extracted file.
        self.bundle: bool = bool(raw.get("bundle", False))
        self.lock_file: Path | None = None
        if self.bundle:
            if (self.archive or {}).get("member"):
                raise ValueError(f"{self.id}: a bundle is extracted whole; archive.member makes no sense")
            if self.unpinned:
                raise ValueError(f"{self.id}: bundles need the sha256 of the archive")
            self.lock_file = manifest_dir / "bundles" / f"{self.id}.tsv"
            if not self.lock_file.is_file():
                raise ValueError(f"{self.id}: missing lock file {self.lock_file} (add bundles with add_artifact.py --bundle)")
        self.filename: str = raw.get("filename") or Path(
            (self.archive or {}).get("member") or self.hf_path or self.origin_url or self.id).name

    @property
    def key(self) -> str:
        if self.unpinned:
            return f"unpinned-{self.id}"
        return self.sha256 or f"md5-{self.md5}"

    @property
    def file(self) -> Path:
        """The downloaded file: the artifact itself, or the archive of a bundle."""
        return cache_dir() / self.key / self.filename

    @property
    def path(self) -> Path:
        """What tests use: the file, or the directory a bundle is extracted to."""
        if self.bundle:
            return cache_dir() / self.key / self.id
        return self.file

    def members(self) -> list[tuple[str, int, str]]:
        """(sha256, size, relative path) of every file of a bundle, from its lock file."""
        out = []
        for n, line in enumerate(self.lock_file.read_text(encoding="utf-8").splitlines(), 1):
            if not line or line.startswith("#"):
                continue
            sha, size, rel = line.split("\t", 2)
            if not safe_relpath(rel):
                raise ValueError(f"{self.lock_file}:{n}: unsafe path {rel!r}")
            out.append((sha, int(size), rel))
        return out


def safe_relpath(rel: str) -> bool:
    parts = Path(rel).parts
    return bool(parts) and not Path(rel).is_absolute() and ".." not in parts and "\\" not in rel


def load_manifest(path: Path = MANIFEST) -> tuple[dict, list[Artifact]]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    arts = [Artifact(a, path.parent) for a in data.get("artifact", [])]
    seen, paths = set(), set()
    for a in arts:
        if a.id in seen:
            raise ValueError(f"duplicate artifact id: {a.id}")
        if a.hf_path and a.hf_path in paths:
            raise ValueError(f"duplicate hf_path: {a.hf_path}")
        seen.add(a.id)
        paths.add(a.hf_path)
    return data.get("dataset", {}), arts


def load_cases(arts: list[Artifact], manifest: Path = MANIFEST) -> dict[str, dict]:
    """manifest/cases/<id>.toml: several machines from one scenario, plus facts tests can assert."""
    by_id = {a.id: a for a in arts}
    cases = {}
    for f in sorted((manifest.parent / "cases").glob("*.toml")):
        c = tomllib.loads(f.read_text(encoding="utf-8"))
        cid = c.get("id")
        if cid != f.stem:
            raise ValueError(f"{f}: id must equal the file name ({f.stem})")
        machines = {}
        for m in c.get("machine", []):
            if m.get("name") in machines:
                raise ValueError(f"{f}: duplicate machine {m.get('name')}")
            if m.get("artifact") not in by_id:
                raise ValueError(f"{f}: machine {m.get('name')}: unknown artifact {m.get('artifact')!r}")
            if m.get("root") and not safe_relpath(m["root"]):
                raise ValueError(f"{f}: machine {m['name']}: root must be a relative path inside the artifact")
            machines[m["name"]] = m
        if not machines:
            raise ValueError(f"{f}: a case needs at least one [[machine]]")
        for i, fact in enumerate(c.get("fact", []), 1):
            for key in ("machine", "kind", "expect", "verified_by"):
                if key not in fact:
                    raise ValueError(f"{f}: fact #{i} lacks {key!r} (facts must say how they were verified)")
            if fact["machine"] not in machines:
                raise ValueError(f"{f}: fact #{i}: unknown machine {fact['machine']!r}")
        c["_file"] = f
        c["_artifacts"] = sorted({m["artifact"] for m in machines.values()})
        cases[cid] = c
    return cases


# --------------------------------------------------------------------------- hashing


def hash_file(path: Path) -> tuple[str, str]:
    sha, md5 = hashlib.sha256(), hashlib.md5()
    with open(path, "rb") as f:
        while block := f.read(CHUNK):
            sha.update(block)
            md5.update(block)
    return sha.hexdigest(), md5.hexdigest()


def matches(art: Artifact, path: Path) -> bool:
    if art.size is not None and path.stat().st_size != art.size:
        return False
    if art.unpinned:
        return True
    sha, md5 = hash_file(path)
    return (art.sha256 or sha) == sha and (art.md5 or md5) == md5


def stamp(path: Path) -> Path:
    return path.with_name(path.name + ".verified")


def is_cached(art: Artifact, deep: bool = False) -> bool:
    """A file is trusted if a previous run hashed it and it has not changed size/mtime since."""
    if art.bundle:
        return bundle_ok(art, art.path, deep)
    p = art.path
    if not p.is_file():
        return False
    st = p.stat()
    marker = f"{st.st_size} {st.st_mtime_ns}"
    s = stamp(p)
    if not deep and s.is_file() and s.read_text() == marker:
        return True
    if matches(art, p):
        s.write_text(marker)
        return True
    return False


def bundle_ok(art: Artifact, root: Path, deep: bool = False) -> bool:
    """Every file of the lock is present with its size (and, when deep, its hash). Extra files fail too."""
    if not root.is_dir():
        return False
    members = art.members()
    marker = hashlib.sha256(art.lock_file.read_bytes()).hexdigest()
    s = stamp(root)
    for _, size, rel in members:
        p = root / rel
        if not p.is_file() or p.stat().st_size != size:
            return False
    if not deep and s.is_file() and s.read_text() == marker:
        return True
    want = {rel: sha for sha, _, rel in members}
    have = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
    if have != set(want):
        return False
    if any(hash_file(root / rel)[0] != sha for rel, sha in want.items()):
        return False
    s.write_text(marker)
    return True


def hash_tree(root: Path) -> list[tuple[str, int, str]]:
    """Lock-file rows for every file under root, sorted by path."""
    rows = []
    for p in sorted(root.rglob("*")):
        if p.is_file():
            rows.append((hash_file(p)[0], p.stat().st_size, p.relative_to(root).as_posix()))
    return rows


# --------------------------------------------------------------------------- download


def download(url: str, dest: Path) -> None:
    headers = {"User-Agent": "forensic-testenv-fetch/1"}
    token = os.environ.get("HF_TOKEN")
    if token and "huggingface.co" in url:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    tmp = dest.with_name(dest.name + ".part")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(req, timeout=60) as resp, open(tmp, "wb") as out:
        total = int(resp.headers.get("Content-Length") or 0)
        done, last = 0, 0.0
        while block := resp.read(CHUNK):
            out.write(block)
            done += len(block)
            if sys.stderr.isatty() and total > 8 * CHUNK and time.monotonic() - last > 0.5:
                last = time.monotonic()
                print(f"\r    {done >> 20}/{total >> 20} MiB", end="", file=sys.stderr)
        if sys.stderr.isatty() and total > 8 * CHUNK:
            print(file=sys.stderr)
    tmp.replace(dest)


def sources(art: Artifact, dataset: dict, use_hf: bool) -> list[tuple[str, str]]:
    """Where to download from, in order: public mirror, origin, private cold copy."""
    out = []
    if use_hf and art.hf_path and dataset.get("hf_repo"):
        rev = dataset.get("revision", "main")
        out.append(("hf", f"https://huggingface.co/datasets/{dataset['hf_repo']}/resolve/{rev}/{art.hf_path}"))
    if art.origin_url:
        out.append(("origin", art.origin_url))
    # The cold copy is a private dataset: without a token it can only answer 401.
    if use_hf and art.cold_path and dataset.get("cold_repo") and os.environ.get("HF_TOKEN"):
        out.append(("cold", f"https://huggingface.co/datasets/{dataset['cold_repo']}/resolve/main/{art.cold_path}"))
    return out


def archive_kind(archive_path: Path, declared: str | None = None) -> str:
    if declared:
        return declared
    name = archive_path.name.lower()
    if name.endswith(".zip"):
        return "zip"
    if name.endswith(".7z"):
        return "7z"
    return "tar"


def sevenzip() -> str:
    for exe in ("7zz", "7z", "7za"):
        if path := shutil.which(exe):
            return path
    raise RuntimeError("extracting .7z needs the 7-Zip command line (7zz / 7z / 7za) on PATH")


def extract(art: Artifact, archive_path: Path, dest: Path) -> None:
    """Extract the one member named in `archive` to dest."""
    member = art.archive["member"]
    kind = archive_kind(archive_path, art.archive.get("format"))
    pwd = art.archive.get("password")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    if kind == "zip":
        with zipfile.ZipFile(archive_path) as z, z.open(member, pwd=pwd.encode() if pwd else None) as src, open(tmp, "wb") as out:
            shutil.copyfileobj(src, out, CHUNK)
    elif kind == "tar":
        with tarfile.open(archive_path) as t:
            src = t.extractfile(member)
            if src is None:
                raise ValueError(f"{member} is not a regular file in {archive_path.name}")
            with src, open(tmp, "wb") as out:
                shutil.copyfileobj(src, out, CHUNK)
    elif kind == "7z":
        with open(tmp, "wb") as out:
            subprocess.run([sevenzip(), "e", "-so", f"-p{pwd or ''}", str(archive_path), member],
                           stdout=out, stderr=subprocess.PIPE, check=True)
    else:
        raise ValueError(f"unsupported archive format {kind!r} (zip, tar or 7z)")
    tmp.replace(dest)


def extract_all(archive_path: Path, dest: Path, kind: str | None = None, pwd: str | None = None) -> None:
    """Extract every regular file of the archive under dest, refusing paths that escape it."""
    kind = archive_kind(archive_path, kind)
    dest.mkdir(parents=True)
    if kind == "zip":
        with zipfile.ZipFile(archive_path) as z:
            for info in z.infolist():
                if info.is_dir():
                    continue
                if not safe_relpath(info.filename):
                    raise ValueError(f"unsafe path in archive: {info.filename!r}")
                target = dest / info.filename
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(info, pwd=pwd.encode() if pwd else None) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out, CHUNK)
    elif kind == "tar":
        with tarfile.open(archive_path) as t:
            t.extractall(dest, filter="data")
    elif kind == "7z":
        listing = subprocess.run([sevenzip(), "l", "-slt", "-ba", f"-p{pwd or ''}", str(archive_path)],
                                 capture_output=True, text=True, check=True).stdout
        for line in listing.splitlines():
            if line.startswith("Path = ") and not safe_relpath(line[7:].replace("\\", "/")):
                raise ValueError(f"unsafe path in archive: {line[7:]!r}")
        subprocess.run([sevenzip(), "x", "-y", f"-p{pwd or ''}", f"-o{dest}", str(archive_path)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True)
    else:
        raise ValueError(f"unsupported archive format {kind!r} (zip, tar or 7z)")
    # Directory entries and symlinks carry no data; tests only see regular files.
    for p in sorted(dest.rglob("*"), reverse=True):
        if p.is_symlink():
            p.unlink()


def fetch_bundle(art: Artifact, origin: str, url: str) -> None:
    """Download a bundle's archive (kept next to it for publish_cold.py) and extract it whole."""
    if not (art.file.is_file() and matches(art, art.file)):
        print(f"    downloading archive from {origin}: {url}")
        download(url, art.file)
        if not matches(art, art.file):
            art.file.unlink(missing_ok=True)
            raise ValueError("archive hash mismatch")
    shutil.rmtree(art.path, ignore_errors=True)
    tmp = art.path.with_name(art.path.name + ".part")
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        extract_all(art.file, tmp, (art.archive or {}).get("format"), (art.archive or {}).get("password"))
        if not bundle_ok(art, tmp, deep=True):
            raise ValueError(f"extracted files do not match {art.lock_file.name}")
        tmp.replace(art.path)
        stamp(tmp).replace(stamp(art.path))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def fetch(art: Artifact, dataset: dict, use_hf: bool) -> None:
    cache_dir().mkdir(parents=True, exist_ok=True)
    errors = []
    for origin, url in sources(art, dataset, use_hf):
        # A mirrored copy on HF is always the final file; an origin URL may be an archive.
        # A bundle is an archive wherever it comes from.
        is_archive = origin == "origin" and art.archive is not None
        try:
            if art.bundle:
                fetch_bundle(art, origin, url)
                return
            if is_archive:
                with tempfile.TemporaryDirectory(dir=cache_dir()) as tmpdir:
                    arch = Path(tmpdir) / Path(url).name
                    print(f"    downloading archive from {origin}: {url}")
                    download(url, arch)
                    want = art.archive.get("sha256")
                    if want and hash_file(arch)[0] != want.lower():
                        raise ValueError("archive hash mismatch")
                    extract(art, arch, art.path)
            else:
                print(f"    downloading from {origin}: {url}")
                download(url, art.path)
            if not is_cached(art, deep=True):
                art.path.unlink(missing_ok=True)
                raise ValueError("hash mismatch after download")
            return
        except Exception as e:  # try the next source
            errors.append(f"{origin}: {e}")
    raise RuntimeError("; ".join(errors) or "no source available (no hf_path, origin_url or readable cold_path)")


class Seeds:
    """Local files that may be artifacts nobody has published yet (e.g. generators/out/).
    A file is only ever used when its size and hash match the manifest."""

    def __init__(self, dirs: list[Path]):
        self.files = sorted(p for d in dirs for p in d.rglob("*") if p.is_file() and not p.name.endswith(".verified"))
        self.hashes: dict[Path, tuple[str, str]] = {}

    def find(self, art: Artifact) -> Path | None:
        if art.bundle or art.unpinned:
            return None  # a bundle is a whole archive tree; an unpinned file can't be verified
        for p in self.files:
            if art.size is not None and p.stat().st_size != art.size:
                continue
            if p not in self.hashes:
                self.hashes[p] = hash_file(p)
            sha, md5 = self.hashes[p]
            if (art.sha256 or sha) == sha and (art.md5 or md5) == md5:
                return p
        return None


def seed(art: Artifact, seeds: Seeds) -> Path | None:
    """Copy a matching local file into the cache, as if it had been downloaded."""
    src = seeds.find(art)
    if src is None:
        return None
    art.path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, art.path)
    if not is_cached(art, deep=True):
        art.path.unlink(missing_ok=True)
        return None
    return src


# --------------------------------------------------------------------------- install / index


def install(art: Artifact, copy: bool) -> list[str]:
    """Place the cached file at the crate-relative paths listed in `install`."""
    done = []
    if art.bundle and art.install:
        return ["skipped install: bundles are used through forensic-testdata, not crate paths"]
    for spec in art.install:
        crate, _, rel = spec.partition(":")
        crate_root = crates_dir() / crate
        if not crate_root.is_dir():
            done.append(f"skipped {spec} (no checkout at {crate_root})")
            continue
        target = crate_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_symlink() or target.exists():
            if target.resolve() == art.path.resolve():
                continue
            if not target.is_symlink():
                done.append(f"kept existing file {target}")
                continue
            target.unlink()
        if copy:
            shutil.copy2(art.path, target)
        else:
            try:
                target.symlink_to(art.path)
            except OSError:  # e.g. Windows without developer mode
                shutil.copy2(art.path, target)
        done.append(f"installed {target}")
    return done


def write_index(arts: list[Artifact], cases: dict[str, dict]) -> None:
    """index.tsv is what the forensic-testdata crate reads: id, size ("-" for a bundle directory),
    absolute path. Cases whose artifacts are all present are copied to <cache>/cases/."""
    idx = cache_dir() / "index.tsv"
    rows = {}
    if idx.is_file():
        for line in idx.read_text(encoding="utf-8").splitlines():
            parts = line.split("\t")
            if len(parts) == 3:
                rows[parts[0]] = line
    for a in arts:
        if a.bundle and a.path.is_dir() and stamp(a.path).is_file():
            rows[a.id] = f"{a.id}\t-\t{a.path}"
        elif not a.bundle and a.path.is_file():
            rows[a.id] = f"{a.id}\t{a.path.stat().st_size}\t{a.path}"
        else:
            rows.pop(a.id, None)
    idx.parent.mkdir(parents=True, exist_ok=True)
    idx.write_text("".join(f"{r}\n" for _, r in sorted(rows.items())), encoding="utf-8")
    case_dir = cache_dir() / "cases"
    for cid, c in cases.items():
        dest = case_dir / f"{cid}.toml"
        if all(r in rows for r in c["_artifacts"]):
            case_dir.mkdir(exist_ok=True)
            shutil.copyfile(c["_file"], dest)
        else:
            dest.unlink(missing_ok=True)


# --------------------------------------------------------------------------- cli


def human(n: int | None) -> str:
    if n is None:
        return "?"
    for unit in ("B", "KiB", "MiB", "GiB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} TiB"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", type=Path, default=MANIFEST)
    ap.add_argument("--tier", choices=["ci", "full", "all"], help="default: ci (unless --id/--crate/--format given)")
    ap.add_argument("--id", action="append", default=[], help="artifact id (repeatable)")
    ap.add_argument("--crate", action="append", default=[], help="artifacts used by this crate (repeatable)")
    ap.add_argument("--format", action="append", default=[], help="artifact format, e.g. evtx (repeatable)")
    ap.add_argument("--group", action="append", default=[], help="artifact group, e.g. a multi-segment image (repeatable)")
    ap.add_argument("--case", action="append", default=[], help="every artifact of a case in manifest/cases/ (repeatable)")
    ap.add_argument("--list", action="store_true", help="list artifacts and cache status, download nothing")
    ap.add_argument("--verify", action="store_true", help="re-hash cached files even if already verified")
    ap.add_argument("--install", action="store_true", help="link artifacts into the crate paths from `install`")
    ap.add_argument("--copy", action="store_true", help="with --install: copy instead of symlink")
    ap.add_argument("--no-hf", action="store_true", help="ignore the Hugging Face mirror and cold copy, use origin URLs only")
    ap.add_argument("--seed", action="append", default=[], type=Path, metavar="DIR",
                    help="before downloading, take any file under DIR whose hash matches the manifest "
                         "(e.g. generators/out for artifacts not published yet; repeatable)")
    args = ap.parse_args()
    seeds = Seeds(args.seed) if args.seed else None

    dataset, arts = load_manifest(args.manifest)
    cases = load_cases(arts, args.manifest)
    tier = args.tier or ("all" if (args.id or args.crate or args.format or args.group or args.case) else "ci")
    unknown = set(args.id) - {a.id for a in arts}
    if unknown:
        print(f"unknown artifact id(s): {', '.join(sorted(unknown))}", file=sys.stderr)
        return 2
    if unknown := set(args.case) - set(cases):
        print(f"unknown case(s): {', '.join(sorted(unknown))}", file=sys.stderr)
        return 2
    case_ids = {i for c in args.case for i in cases[c]["_artifacts"]}
    sel = [a for a in arts
           if (tier == "all" or a.tier == tier)
           and (not args.id or a.id in args.id)
           and (not args.case or a.id in case_ids)
           and (not args.crate or set(args.crate) & set(a.used_by))
           and (not args.format or a.raw.get("format") in args.format)
           and (not args.group or a.group in args.group)]

    if args.list:
        print(f"cache: {cache_dir()}")
        print(f"{'id':<40} {'tier':<5} {'format':<9} {'size':>10}  {'mirror':<6} status")
        for a in sel:
            status = "cached" if is_cached(a) else "-"
            if a.unpinned:
                status += " (unpinned)"
            mirror = "hf" if a.hf_path else "cold" if a.cold_path else "origin"
            print(f"{a.id:<40} {a.tier:<5} {a.raw.get('format', ''):<9} {human(a.size):>10}  {mirror:<6} {status}")
        if cases and not (args.id or args.crate or args.format or args.group) or args.case:
            print(f"\n{'case':<40} {'machines':<30} facts  status")
            for cid, c in cases.items():
                if args.case and cid not in args.case:
                    continue
                names = ",".join(m["name"] for m in c["machine"])
                ready = all(is_cached(a) for a in arts if a.id in c["_artifacts"])
                print(f"{cid:<40} {names:<30} {len(c.get('fact', [])):>5}  {'cached' if ready else '-'}")
        return 0

    if not sel:
        print("nothing selected")
        return 0
    total = sum(a.size or 0 for a in sel if not a.path.exists())
    print(f"{len(sel)} artifact(s), up to {human(total)} to download, cache: {cache_dir()}")

    failed = []
    for a in sel:
        print(f"* {a.id}")
        try:
            if is_cached(a, deep=args.verify):
                print("    ok (cached)")
            elif seeds and (src := seed(a, seeds)):
                print(f"    ok (seeded from {src}) -> {a.path}")
            elif args.no_hf and not a.origin_url:
                print("    skipped (only available from the Hugging Face mirror)")
                continue
            else:
                if a.path.is_dir():
                    print("    extracted bundle does not match its lock file, extracting again")
                elif a.path.exists():
                    print("    cached copy does not match the manifest hash, downloading again")
                    a.path.unlink()
                fetch(a, dataset, use_hf=not args.no_hf)
                print(f"    ok -> {a.path}")
                if a.unpinned:
                    print(f"    UNPINNED: verified by size only. Pin it in the manifest with:\n"
                          f"      sha256 = \"{hash_file(a.path)[0]}\"")
            if args.install:
                for line in install(a, args.copy):
                    print(f"    {line}")
        except Exception as e:
            print(f"    FAILED: {e}", file=sys.stderr)
            failed.append(a.id)

    write_index(arts, cases)
    if failed:
        print(f"\n{len(failed)} failed: {', '.join(failed)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
