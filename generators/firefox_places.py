#!/usr/bin/env python3
"""Generate a small Firefox `places.sqlite` with known history and downloads.

Writes generators/out/firefox_places/places.sqlite and places.truth.json. The schema is the
subset of Firefox's (toolkit/components/places/nsPlacesTables.h) that history and download
parsers read: moz_places, moz_historyvisits, moz_anno_attributes, moz_annos. It covers what a
parser must not invent: a never-visited place (NULL title and last_visit_date), a visit typed
by hand, a download's start/end/size, and a failed download with no size.

Deterministic: the same script writes the same bytes (fixed times, VACUUM, no WAL).
"""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

OUT = Path(__file__).resolve().parent / "out" / "firefox_places"


def prtime(ts: str) -> int:
    """PRTime: microseconds since the Unix epoch."""
    dt = datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)
    return int(dt.timestamp()) * 1_000_000 + dt.microsecond


# (url, title, visits as (UTC time, visit_type)) -- 1 LINK, 2 TYPED, 7 DOWNLOAD
PLACES = [
    ("https://github.com/ForensicRS/forensic-rs", "ForensicRS/forensic-rs",
     [("2026-02-01T08:00:00", 2), ("2026-02-01T09:30:00.250000", 1)]),
    ("https://www.mozilla.org/firefox/", None, []),  # bookmarked, never visited
    ("https://example.org/tools/setup.exe", "", [("2026-02-02T10:00:00", 7)]),
    ("https://example.org/broken.zip", "Broken ✗", [("2026-02-03T11:00:00", 7)]),
]

def millis(ts: str) -> int:
    return prtime(ts) // 1000


# place index -> (destination URI, file name, start, metaData JSON). metaData's endTime is in
# milliseconds; a failed download (state 3) records no fileSize.
DOWNLOADS = {
    2: ("file:///C:/Users/alice/Downloads/setup.exe", "setup.exe", "2026-02-02T10:00:01",
        '{"state":1,"endTime":%d,"fileSize":1048576}' % millis("2026-02-02T10:00:05.500000")),
    3: ("file:///C:/Users/alice/Downloads/broken.zip", "broken.zip", "2026-02-03T11:00:02",
        '{"state":3,"endTime":%d}' % millis("2026-02-03T11:00:04")),
}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    db_path = OUT / "places.sqlite"
    db_path.unlink(missing_ok=True)
    con = sqlite3.connect(db_path)
    con.executescript("""
        CREATE TABLE moz_places (id INTEGER PRIMARY KEY, url LONGVARCHAR, title LONGVARCHAR,
            rev_host LONGVARCHAR, visit_count INTEGER DEFAULT 0, hidden INTEGER DEFAULT 0 NOT NULL,
            typed INTEGER DEFAULT 0 NOT NULL, frecency INTEGER DEFAULT -1 NOT NULL,
            last_visit_date INTEGER, guid TEXT);
        CREATE TABLE moz_historyvisits (id INTEGER PRIMARY KEY, from_visit INTEGER,
            place_id INTEGER, visit_date INTEGER, visit_type INTEGER, session INTEGER);
        CREATE TABLE moz_anno_attributes (id INTEGER PRIMARY KEY, name VARCHAR(32) UNIQUE NOT NULL);
        CREATE TABLE moz_annos (id INTEGER PRIMARY KEY, place_id INTEGER NOT NULL,
            anno_attribute_id INTEGER, content LONGVARCHAR, flags INTEGER DEFAULT 0,
            expiration INTEGER DEFAULT 0, type INTEGER DEFAULT 0, dateAdded INTEGER DEFAULT 0,
            lastModified INTEGER DEFAULT 0);
        INSERT INTO moz_anno_attributes(id, name) VALUES
            (1, 'downloads/destinationFileURI'), (2, 'downloads/destinationFileName'),
            (3, 'downloads/metaData'), (4, 'bookmarkProperties/description');
    """)
    truth = {"places": [], "visits": [], "downloads": []}
    place_ids = []
    previous_visit = 0
    for url, title, visits in PLACES:
        stamps = [prtime(t) for t, _ in visits]
        last = max(stamps) if stamps else None
        typed = 1 if any(kind == 2 for _, kind in visits) else 0
        pid = con.execute(
            "INSERT INTO moz_places(url, title, visit_count, typed, last_visit_date) VALUES (?, ?, ?, ?, ?)",
            (url, title, len(visits), typed, last)).lastrowid
        place_ids.append(pid)
        truth["places"].append({"id": pid, "url": url, "title": title, "visit_count": len(visits),
                                "typed": typed, "last_visit_date_prtime": last})
        for (t, kind), stamp in zip(visits, stamps):
            vid = con.execute(
                "INSERT INTO moz_historyvisits(from_visit, place_id, visit_date, visit_type, session) VALUES (?, ?, ?, ?, 0)",
                (previous_visit, pid, stamp, kind)).lastrowid
            truth["visits"].append({"id": vid, "place_id": pid, "from_visit": previous_visit or None,
                                    "visit_date_prtime": stamp, "visit_date_utc": t + "Z", "visit_type": kind})
            previous_visit = vid
    for index, (uri, name, start, meta) in DOWNLOADS.items():
        pid = place_ids[index]
        started = prtime(start)
        con.execute("INSERT INTO moz_annos(place_id, anno_attribute_id, content, dateAdded) VALUES (?, 1, ?, ?)", (pid, uri, started))
        con.execute("INSERT INTO moz_annos(place_id, anno_attribute_id, content, dateAdded) VALUES (?, 2, ?, ?)", (pid, name, started))
        con.execute("INSERT INTO moz_annos(place_id, anno_attribute_id, content, dateAdded) VALUES (?, 3, ?, ?)", (pid, meta, started))
        truth["downloads"].append({"place_id": pid, "destination_uri": uri, "destination_name": name,
                                   "start_prtime": started, "metadata": meta})
    # A non-download annotation, which a downloads reader must skip.
    con.execute("INSERT INTO moz_annos(place_id, anno_attribute_id, content) VALUES (?, 4, 'a note')", (place_ids[1],))
    con.commit()
    con.execute("VACUUM")
    con.close()
    (OUT / "places.truth.json").write_text(json.dumps(truth, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {db_path} and places.truth.json")


if __name__ == "__main__":
    main()
