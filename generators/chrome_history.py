#!/usr/bin/env python3
"""Generate a small Chrome/Chromium `History` SQLite database with known contents.

Writes generators/out/chrome_history/History and History.truth.json (the expected
rows, with timestamps both as WebKit microseconds and ISO-8601 UTC).
"""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

OUT = Path(__file__).resolve().parent / "out" / "chrome_history"
WEBKIT_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)

# (url, title, visit times UTC, transition)  -- transition 0 = LINK, 1 = TYPED
ROWS = [
    ("https://github.com/ForensicRS/forensic-rs", "ForensicRS/forensic-rs", ["2026-01-10T09:15:00", "2026-01-11T10:00:30"], 1),
    ("https://www.nist.gov/itl/ssd/software-quality-group/computer-forensics-tool-testing-program-cftt", "CFTT | NIST", ["2026-01-10T09:20:12"], 0),
    ("https://huggingface.co/datasets/ForensicRS/forensic-test-artifacts", "ForensicRS test artifacts", ["2026-01-12T18:45:59"], 1),
    ("http://example.com/download/setup.exe", "", ["2026-01-13T23:59:59"], 0),
    ("https://example.org/ünïcödé?q=%E2%9C%93", "Ünïcödé title ✓", ["2026-01-14T00:00:00"], 0),
]


def webkit(ts: str) -> int:
    dt = datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)
    delta = dt - WEBKIT_EPOCH
    return (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    db_path = OUT / "History"
    db_path.unlink(missing_ok=True)
    con = sqlite3.connect(db_path)
    # Subset of the real Chromium schema (components/history/core/browser/url_database.cc, visit_database.cc).
    con.executescript("""
        CREATE TABLE meta(key LONGVARCHAR NOT NULL UNIQUE PRIMARY KEY, value LONGVARCHAR);
        CREATE TABLE urls(id INTEGER PRIMARY KEY AUTOINCREMENT, url LONGVARCHAR, title LONGVARCHAR,
            visit_count INTEGER DEFAULT 0 NOT NULL, typed_count INTEGER DEFAULT 0 NOT NULL,
            last_visit_time INTEGER NOT NULL, hidden INTEGER DEFAULT 0 NOT NULL);
        CREATE TABLE visits(id INTEGER PRIMARY KEY, url INTEGER NOT NULL, visit_time INTEGER NOT NULL,
            from_visit INTEGER, transition INTEGER DEFAULT 0 NOT NULL, segment_id INTEGER,
            visit_duration INTEGER DEFAULT 0 NOT NULL);
        INSERT INTO meta VALUES ('version', '70'), ('last_compatible_version', '16');
    """)
    truth = {"urls": [], "visits": []}
    for url, title, times, transition in ROWS:
        stamps = [webkit(t) for t in times]
        cur = con.execute(
            "INSERT INTO urls(url, title, visit_count, typed_count, last_visit_time) VALUES (?, ?, ?, ?, ?)",
            (url, title, len(times), len(times) if transition == 1 else 0, max(stamps)))
        url_id = cur.lastrowid
        truth["urls"].append({"id": url_id, "url": url, "title": title, "visit_count": len(times),
                              "last_visit_time_webkit": max(stamps), "last_visit_time_utc": max(times) + "Z"})
        for t, s in zip(times, stamps):
            vid = con.execute("INSERT INTO visits(url, visit_time, transition) VALUES (?, ?, ?)",
                              (url_id, s, transition)).lastrowid
            truth["visits"].append({"id": vid, "url_id": url_id, "visit_time_webkit": s,
                                    "visit_time_utc": t + "Z", "transition": transition})
    con.commit()
    con.execute("VACUUM")
    con.close()
    (OUT / "History.truth.json").write_text(json.dumps(truth, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {db_path} and {db_path}.truth.json")


if __name__ == "__main__":
    main()
