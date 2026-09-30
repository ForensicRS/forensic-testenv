#!/usr/bin/env python3
"""Mirror the redistributable artifacts of the manifest to the Hugging Face dataset.

    publish_hf.py                  dry run: show what would be uploaded (default)
    publish_hf.py --yes            upload missing/changed files and the dataset card
    publish_hf.py --id X --yes     upload specific artifacts only
    publish_hf.py --create --yes   create the dataset repository first

Only entries with `redistributable = true` and an `hf_path` are ever uploaded,
and only after the cached file matches the manifest hash. Needs `pip install
-r tools/requirements.txt` and a write token in HF_TOKEN (or `hf auth login`).
"""
import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from fetch import fetch, human, is_cached, load_manifest  # noqa: E402

CARD_TEMPLATE = HERE.parent / "hf" / "README.md"
TABLE_MARKER = "<!-- ARTIFACT TABLE -->"


def dataset_card(arts) -> str:
    rows = ["| Path | Format | Size | License | Source | Description |", "|---|---|---|---|---|---|"]
    for a in sorted(arts, key=lambda a: a.hf_path):
        src = a.raw.get("source", "")
        if a.origin_url:
            src = f"[{src}]({a.origin_url})"
        desc = a.raw.get("description", "").replace("|", "\\|")
        rows.append(f"| `{a.hf_path}` | {a.raw.get('format', '')} | {human(a.size)} | {a.raw['license']} | {src} | {desc} |")
    return CARD_TEMPLATE.read_text(encoding="utf-8").replace(TABLE_MARKER, "\n".join(rows))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--id", action="append", default=[])
    ap.add_argument("--yes", action="store_true", help="actually upload (default is a dry run)")
    ap.add_argument("--create", action="store_true", help="create the dataset repo if it does not exist")
    ap.add_argument("--private", action="store_true", help="with --create: make the dataset private")
    args = ap.parse_args()

    dataset, arts = load_manifest()
    repo = dataset.get("hf_repo")
    if not repo:
        print("[dataset].hf_repo is not set in the manifest", file=sys.stderr)
        return 2
    mirrored = [a for a in arts if a.hf_path and a.redistributable and (not args.id or a.id in args.id)]
    skipped = [a for a in arts if not a.hf_path and (not args.id or a.id in args.id)]

    # Make sure every file to upload is in the cache and verified (origin download if needed).
    ready = []
    for a in mirrored:
        if not is_cached(a, deep=True):
            if not args.yes:
                print(f"  (dry run: {a.id} is not cached yet; --yes would fetch it from its origin first)")
                ready.append(a)
                continue
            if not a.origin_url:
                print(f"! {a.id}: not in the cache and has no origin_url; add it with add_artifact.py", file=sys.stderr)
                continue
            print(f"* {a.id}: fetching from origin before upload")
            fetch(a, dataset, use_hf=False)
        ready.append(a)

    remote = {}
    try:
        from huggingface_hub import HfApi
        api = HfApi()
        if args.create and args.yes:
            api.create_repo(repo, repo_type="dataset", private=args.private, exist_ok=True)
        info = api.dataset_info(repo, files_metadata=True)
        for s in info.siblings or []:
            remote[s.rfilename] = s.lfs.sha256 if s.lfs else None
    except ImportError:
        if args.yes:
            print("huggingface_hub is missing: pip install -r tools/requirements.txt", file=sys.stderr)
            return 2
        print("(huggingface_hub not installed: cannot compare with the remote dataset)")
    except Exception as e:
        if args.yes and not args.create:
            print(f"cannot read dataset {repo}: {e}\n(use --create to create it)", file=sys.stderr)
            return 1
        print(f"(dataset {repo} not readable: {type(e).__name__}; treating it as empty)")

    todo = [a for a in ready if remote.get(a.hf_path) != a.sha256]
    print(f"\ndataset: {repo}")
    print(f"up to date: {len(ready) - len(todo)}, to upload: {len(todo)} ({human(sum(a.size or 0 for a in todo))})")
    for a in todo:
        print(f"  + {a.hf_path}  ({a.id}, {a.raw['license']})")
    if skipped:
        print(f"not mirrored (origin only): {', '.join(a.id for a in skipped)}")

    card = dataset_card([a for a in arts if a.hf_path and a.redistributable])
    if not args.yes:
        print("\ndry run: nothing uploaded. Re-run with --yes to publish.")
        return 0

    from huggingface_hub import CommitOperationAdd
    ops = [CommitOperationAdd(path_in_repo=a.hf_path, path_or_fileobj=str(a.file)) for a in todo]
    ops.append(CommitOperationAdd(path_in_repo="README.md", path_or_fileobj=card.encode("utf-8")))
    ids = ", ".join(a.id for a in todo) or "dataset card"
    commit = api.create_commit(repo, repo_type="dataset", operations=ops, commit_message=f"Add/update: {ids}")
    print(f"published: {commit.commit_url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
