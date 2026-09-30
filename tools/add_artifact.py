#!/usr/bin/env python3
"""Hash a file (local path or URL) and append its entry to manifest/artifacts.toml.

    add_artifact.py https://example.org/Security.evtx --id evtx-security-foo \\
        --format evtx --source example --license MIT --redistributable \\
        --used-by frnsc-winevt --description "Security log with 4624/4625 logons"

    add_artifact.py ./generators/out/tiny.hve --id hive-synthetic-tiny --format hive \\
        --source synthetic --license MIT --redistributable

    add_artifact.py https://ctf.example.org/data/PC1_triage.zip --bundle --cold \\
        --id triage-example-pc1 --format triage --source example-ctf --license unknown

For a local file with no public origin, set --redistributable and let it be mirrored
to Hugging Face (hf_path is filled in automatically). Review the appended entry before
committing: check the license yourself, never copy it from a guess.

--bundle registers an archive that is extracted whole (e.g. a triage collection): the
manifest pins the archive, and manifest/bundles/<id>.tsv pins every file in it.
--cold keeps a private copy of a file that may not be mirrored publicly (see publish_cold.py).
"""
import argparse
import hashlib
import sys
import tempfile
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import shutil  # noqa: E402

from fetch import MANIFEST, cache_dir, extract_all, hash_tree, load_manifest  # noqa: E402

CI_LIMIT = 50 * 1024 * 1024


def toml_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def toml_list(items: list[str]) -> str:
    return "[" + ", ".join(toml_str(i) for i in items) + "]"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", help="local file or http(s) URL")
    ap.add_argument("--id", required=True)
    ap.add_argument("--format", required=True, help="evtx, hive, prefetch, esedb, ole, sqlite, image-e01, ...")
    ap.add_argument("--source", dest="src", required=True, help="origin collection, e.g. plaso, nist-cfreds, synthetic")
    ap.add_argument("--license", required=True)
    ap.add_argument("--redistributable", action="store_true", help="the license allows mirroring it to Hugging Face")
    ap.add_argument("--description", default="")
    ap.add_argument("--used-by", action="append", default=[])
    ap.add_argument("--install", action="append", default=[], help="crate:relative/path where tests expect it")
    ap.add_argument("--tier", choices=["ci", "full"], help="default: ci when < 50 MiB")
    ap.add_argument("--filename", help="name in the cache (default: basename of the source)")
    ap.add_argument("--bundle", action="store_true", help="the source is an archive to extract whole")
    ap.add_argument("--cold", action="store_true", help="keep a private cold copy (not redistributable only)")
    args = ap.parse_args()

    if args.cold and args.redistributable:
        print("--cold is for files that may not be mirrored; redistributable ones go to the public dataset",
              file=sys.stderr)
        return 1
    _, existing = load_manifest()
    if any(a.id == args.id for a in existing):
        print(f"id {args.id} already exists", file=sys.stderr)
        return 1

    is_url = args.source.startswith(("http://", "https://"))
    with tempfile.TemporaryDirectory() as tmp:
        if is_url:
            path = Path(tmp) / "download"
            print(f"downloading {args.source}")
            req = urllib.request.Request(args.source, headers={"User-Agent": "forensic-testenv-add/1"})
            with urllib.request.urlopen(req, timeout=60) as r, open(path, "wb") as out:
                while block := r.read(1 << 20):
                    out.write(block)
        else:
            path = Path(args.source)
        sha = hashlib.sha256()
        with open(path, "rb") as f:
            while block := f.read(1 << 20):
                sha.update(block)
        size = path.stat().st_size
        filename = args.filename or Path(args.source.split("?")[0]).name
        # Keep a copy in the cache: fetch.py sees it as already downloaded, publish_hf.py uploads it from there.
        cached = cache_dir() / sha.hexdigest() / filename
        cached.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, cached)
        if args.bundle:
            lock = MANIFEST.parent / "bundles" / f"{args.id}.tsv"
            dest = cache_dir() / sha.hexdigest() / args.id
            shutil.rmtree(dest, ignore_errors=True)
            extract_all(cached, dest)
            rows = hash_tree(dest)
            lock.parent.mkdir(parents=True, exist_ok=True)
            lock.write_text(f"# sha256\tsize\tpath: files of {filename} (sha256 {sha.hexdigest()})\n"
                            + "".join(f"{h}\t{n}\t{r}\n" for h, n, r in rows), encoding="utf-8")
            print(f"extracted {len(rows)} files to {dest}; lock written to {lock}")

    tier = args.tier or ("ci" if size < CI_LIMIT and not args.bundle else "full")
    lines = [
        "",
        "[[artifact]]",
        f"id = {toml_str(args.id)}",
        f"description = {toml_str(args.description)}",
        f"format = {toml_str(args.format)}",
        f"source = {toml_str(args.src)}",
        f"tier = {toml_str(tier)}",
    ]
    if is_url:
        lines.append(f"origin_url = {toml_str(args.source)}")
    if args.redistributable:
        lines.append(f"hf_path = {toml_str(f'{args.src}/{args.format}/{args.id}/{filename}')}")
    if args.cold:
        lines.append(f"cold_path = {toml_str(f'{args.src}/{args.id}/{filename}')}")
    if args.bundle:
        lines.append("bundle = true")
    lines += [
        f"filename = {toml_str(filename)}",
        f"sha256 = {toml_str(sha.hexdigest())}",
        f"size = {size}",
        f"license = {toml_str(args.license)}",
        f"redistributable = {'true' if args.redistributable else 'false'}",
        f"used_by = {toml_list(args.used_by)}",
    ]
    if args.install:
        lines.append(f"install = {toml_list(args.install)}")
    if not is_url and not args.redistributable and not args.cold:
        print("warning: a local file that is not redistributable has no way to be downloaded by others",
              file=sys.stderr)

    with open(MANIFEST, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"\nappended to {MANIFEST}")
    print(f"cached at {cached}")
    if args.redistributable:
        print("mirror it with: tools/publish_hf.py --id " + args.id)
    if args.cold:
        print("keep the cold copy with: tools/publish_cold.py --id " + args.id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
