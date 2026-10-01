#!/usr/bin/env python3
"""Generate SQLite databases whose newest data lives only in their write-ahead log.

Writes generators/out/sqlite_wal/<scenario>/{wal.db, wal.db-wal} and sqlite_wal.truth.json. Each
pair is copied while the writing connection is still open, as a collection copies a live
database: closing it would checkpoint the log into the database and delete it.

- committed: the table is created after switching to WAL mode, so the database file alone
  doesn't even have it. Four transactions (inserts, an update, a delete) are committed, and then a
  transaction that never commits spills frames into the log (a tiny page cache forces it): a
  reader must not apply them.
- restarted: rows written and checkpointed with RESTART, then one more row committed. The next
  writer restarts the log with new salts and overwrites it from the start, so frames of the earlier
  generation follow the live ones: a reader must stop at them.

Not byte-reproducible: SQLite draws the log's salts at random. Generate once and pin the files by
hash, like the journal fixtures.
"""
import json
import shutil
import sqlite3
from pathlib import Path

OUT = Path(__file__).resolve().parent / "out" / "sqlite_wal"


def connect(path: Path) -> sqlite3.Connection:
    for p in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        p.unlink(missing_ok=True)
    con = sqlite3.connect(path, isolation_level=None)
    con.execute("PRAGMA page_size=4096")
    assert con.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    con.execute("PRAGMA wal_autocheckpoint=0")
    return con


def snapshot(src: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest / "wal.db")
    shutil.copyfile(f"{src}-wal", dest / "wal.db-wal")


def rows(con: sqlite3.Connection) -> list:
    return [list(r) for r in con.execute("SELECT id, v FROM t ORDER BY id")]


def committed(work: Path) -> dict:
    db = work / "committed.db"
    con = connect(db)
    con.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    con.execute("INSERT INTO t(id, v) VALUES (1, 'one'), (2, 'two'), (3, 'three')")
    con.execute("INSERT INTO t(id, v) VALUES (4, 'four'), (5, 'five ✓')")
    con.execute("UPDATE t SET v = 'one, updated' WHERE id = 1")
    con.execute("DELETE FROM t WHERE id = 2")
    visible = rows(con)
    con.execute("PRAGMA cache_size=1")
    con.execute("BEGIN")
    con.executemany("INSERT INTO t(id, v) VALUES (?, ?)", [(100 + i, "x" * 2000) for i in range(200)])
    snapshot(db, OUT / "committed")
    con.execute("ROLLBACK")
    con.close()
    return {"visible_rows": visible, "transactions": 5}


def restarted(work: Path) -> dict:
    db = work / "restarted.db"
    con = connect(db)
    con.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    for i in range(10):
        con.execute("INSERT INTO t(id, v) VALUES (?, ?)", (i + 1, f"row {i + 1} " + "y" * 3000))
    busy, log, checkpointed = con.execute("PRAGMA wal_checkpoint(RESTART)").fetchone()
    assert busy == 0 and log == checkpointed, (busy, log, checkpointed)
    con.execute("INSERT INTO t(id, v) VALUES (11, 'after the restart')")
    visible = [[i, v if i == 11 else v[:len(f"row {i} ")]] for i, v in rows(con)]
    snapshot(db, OUT / "restarted")
    con.close()
    return {"visible_rows_prefix": visible, "transactions": 1}


def main() -> None:
    work = OUT / "work"
    work.mkdir(parents=True, exist_ok=True)
    truth = {"committed": committed(work), "restarted": restarted(work)}
    shutil.rmtree(work)
    (OUT / "sqlite_wal.truth.json").write_text(json.dumps(truth, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
