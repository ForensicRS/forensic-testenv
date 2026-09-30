#!/usr/bin/env python3
"""Keep private cold copies of the non-redistributable artifacts that have a `cold_path`.

    publish_cold.py                  dry run: show what would be uploaded (default)
    publish_cold.py --yes            upload missing/changed files
    publish_cold.py --id X --yes     upload specific artifacts only
    publish_cold.py --create --yes   create the (private) dataset repository first

The cold dataset ([dataset].cold_repo) is private: it exists so that tests keep working when
a publisher's URL dies, not to redistribute anything. It gets no dataset card and must never
be made public. What is uploaded is the file exactly as downloaded from its origin (for a
bundle, its archive), after checking it against the manifest hash. fetch.py tries it last,
and only with an HF_TOKEN that can read it. Needs `pip install -r tools/requirements.txt`
and a write token in HF_TOKEN (or `hf auth login`).
"""
import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from fetch import fetch, human, is_cached, load_manifest, matches  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--id", action="append", default=[])
    ap.add_argument("--yes", action="store_true", help="actually upload (default is a dry run)")
    ap.add_argument("--create", action="store_true", help="create the private dataset repo if it does not exist")
    args = ap.parse_args()

    dataset, arts = load_manifest()
    repo = dataset.get("cold_repo")
    if not repo:
        print("[dataset].cold_repo is not set in the manifest", file=sys.stderr)
        return 2
    cold = [a for a in arts if a.cold_path and (not args.id or a.id in args.id)]

    ready = []
    for a in cold:
        if not (a.file.is_file() and (a.bundle and matches(a, a.file) or not a.bundle and is_cached(a, deep=True))):
            if not args.yes:
                print(f"  (dry run: {a.id} is not cached yet; --yes would fetch it from its origin first)")
                ready.append(a)
                continue
            print(f"* {a.id}: fetching from origin before upload")
            fetch(a, dataset, use_hf=False)
        ready.append(a)

    remote = {}
    try:
        from huggingface_hub import HfApi
        api = HfApi()
        if args.create and args.yes:
            api.create_repo(repo, repo_type="dataset", private=True, exist_ok=True)
        info = api.dataset_info(repo, files_metadata=True)
        if not info.private:
            print(f"refusing: {repo} is public; the cold dataset must stay private", file=sys.stderr)
            return 1
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

    todo = [a for a in ready if remote.get(a.cold_path) != a.sha256]
    print(f"\ncold dataset (private): {repo}")
    print(f"up to date: {len(ready) - len(todo)}, to upload: {len(todo)} ({human(sum(a.size or 0 for a in todo))})")
    for a in todo:
        print(f"  + {a.cold_path}  ({a.id}, {a.raw['license']})")
    if not args.yes:
        print("\ndry run: nothing uploaded. Re-run with --yes to upload.")
        return 0
    if not todo:
        return 0

    from huggingface_hub import CommitOperationAdd
    ops = [CommitOperationAdd(path_in_repo=a.cold_path, path_or_fileobj=str(a.file)) for a in todo]
    commit = api.create_commit(repo, repo_type="dataset", operations=ops,
                               commit_message=f"Cold copy: {', '.join(a.id for a in todo)}")
    print(f"uploaded: {commit.commit_url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
