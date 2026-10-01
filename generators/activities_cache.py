#!/usr/bin/env python3
"""Generate a small Windows Timeline `ActivitiesCache.db` with known activities.

Writes generators/out/activities_cache/ActivitiesCache.db and ActivitiesCache.truth.json. The
schema is Windows 10 1903's (tables Activity, ActivityOperation, Activity_PackageId, Metadata),
with its declared types: GUID columns hold 16-byte blobs, DATETIME columns Unix seconds, Payload
a UTF-8 JSON blob. It covers what a parser must not invent:

- an "open" activity (type 5) of a Win32 app, its file in the payload, no end time (0);
- an "in focus" activity (type 6) with its active duration and an end time;
- a UWP app's in-focus activity the user deleted from the timeline (status 3);
- a clipboard activity (type 10) with its ClipboardPayload and a NULL Payload;
- a copy activity (type 16) in group "Copy";
- an activity type no reader knows (99), which must be kept as a number;
- two queued operations: an upload of the copy activity, and the deletion of an activity no longer
  in Activity, which only ActivityOperation still records.

Deterministic: the same script writes the same bytes (fixed times and ids, VACUUM, no WAL).

It also writes generators/out/activities_cache/wal/ActivitiesCache.db and its -wal, copied from a
live connection the way Windows leaves them: the database above checkpointed, then one more
activity (Notepad opening todo.txt, 2026-03-02T08:00:00) committed into the log only. That pair is
not byte-reproducible (SQLite draws the log's salts at random): pin it once by hash.
"""
import shutil
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

OUT = Path(__file__).resolve().parent / "out" / "activities_cache"

SYSTEM32 = "{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}"
DEVICE = "aGVhZGxlc3MtZGV2aWNlLWlk"


def secs(ts: str) -> int:
    return int(datetime.fromisoformat(ts).replace(tzinfo=timezone.utc).timestamp())


def guid(n: int) -> bytes:
    return uuid.UUID(int=0x0B5E55ED_0000_4000_8000_000000000000 + n).bytes_le


def app_id(*pairs) -> str:
    return json.dumps([{"application": a, "platform": p} for p, a in pairs], separators=(",", ":"))


def payload(obj) -> bytes:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


NOTEPAD = app_id(("windows_win32", "Microsoft.Windows.Notepad"),
                 ("x_exe_path", SYSTEM32 + "\\notepad.exe"),
                 ("packageId", SYSTEM32 + "\\notepad.exe"))
CALCULATOR = app_id(("windows_universal", "Microsoft.WindowsCalculator_8wekyb3d8bbwe!App"),
                    ("packageId", "Microsoft.WindowsCalculator_8wekyb3d8bbwe"))
EXPLORER = app_id(("windows_win32", "Microsoft.Windows.Explorer"),
                  ("x_exe_path", "{F38BF404-1D43-42F2-9305-67DE0B28FC23}\\explorer.exe"))

# (n, AppId, AppActivityId, ActivityType, ActivityStatus, Group, start, end, Payload, ClipboardPayload)
ACTIVITIES = [
    (1, NOTEPAD, "ECB32AF3-1440-4086-94E3-5311F97F89C4\\notes.txt", 5, 1, None,
     "2026-03-01T09:00:00", None,
     payload({"displayText": "notes.txt", "appDisplayName": "Notepad",
              "description": "C:\\Users\\alice\\Documents\\notes.txt",
              "activationUri": "ms-shellactivity:",
              "contentUri": "file:///C:/Users/alice/Documents/notes.txt?VolumeId={7c2e1d1e-0000-0000-0000-100000000000}",
              "backgroundColor": "black"}),
     None),
    (2, NOTEPAD, "ECB32AF3-1440-4086-94E3-5311F97F89C4", 6, 1, None,
     "2026-03-01T09:00:05", "2026-03-01T09:02:05",
     payload({"type": "UserEngaged", "reportingApp": "ShellActivityMonitor",
              "activeDurationSeconds": 120, "shellContentDescription": {"MergedGap": 7200},
              "userTimezone": "Europe/Madrid"}),
     None),
    (3, CALCULATOR, "ECB32AF3-1440-4086-94E3-5311F97F89C4", 6, 3, None,
     "2026-03-01T10:15:00", "2026-03-01T10:15:30",
     payload({"type": "UserEngaged", "reportingApp": "ShellActivityMonitor",
              "activeDurationSeconds": 30, "displayText": "Calculadora ✓",
              "userTimezone": "Europe/Madrid"}),
     None),
    (4, EXPLORER, "{c5c9cb46-0000-0000-0000-000000000004}", 10, 1, None,
     "2026-03-01T11:00:00", None, None,
     json.dumps([{"content": "aGVsbG8gd29ybGQ=", "formatName": "Text"}], separators=(",", ":"))),
    (5, EXPLORER, "{c5c9cb46-0000-0000-0000-000000000005}", 16, 1, "Copy",
     "2026-03-01T11:00:00", None,
     payload({"clipboardDataId": "{6d1f8b0c-0000-0000-0000-000000000005}", "gdprType": "Text"}),
     None),
    (6, EXPLORER, "{c5c9cb46-0000-0000-0000-000000000006}", 99, 1, None,
     "2026-03-01T12:00:00", None, None, None),
]

# (OperationOrder, n, OperationType, ActivityType, AppId, start, Payload) -- n 7 is not in Activity.
OPERATIONS = [
    (1, 5, 1, 16, EXPLORER, "2026-03-01T11:00:00", ACTIVITIES[4][8]),
    (2, 7, 3, 5, NOTEPAD, "2026-02-27T16:20:00",
     payload({"displayText": "secret.txt", "appDisplayName": "Notepad",
              "description": "C:\\Users\\alice\\Desktop\\secret.txt"})),
]

EXPIRY_DAYS = 30


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    db_path = OUT / "ActivitiesCache.db"
    db_path.unlink(missing_ok=True)
    con = sqlite3.connect(db_path)
    con.executescript("""
        CREATE TABLE [Metadata]([Key] TEXT PRIMARY KEY NOT NULL, [Value] TEXT);
        CREATE TABLE [Activity]([Id] GUID PRIMARY KEY NOT NULL, [AppId] TEXT NOT NULL,
            [PackageIdHash] TEXT, [AppActivityId] TEXT, [ActivityType] INT NOT NULL,
            [ActivityStatus] INT NOT NULL, [ParentActivityId] GUID, [Tag] TEXT, [Group] TEXT,
            [MatchId] TEXT, [LastModifiedTime] DATETIME NOT NULL, [ExpirationTime] DATETIME,
            [Payload] BLOB, [Priority] INT, [IsLocalOnly] INT, [PlatformDeviceId] TEXT,
            [CreatedInCloud] DATETIME, [StartTime] DATETIME, [EndTime] DATETIME,
            [LastModifiedOnClient] DATETIME, [GroupAppActivityId] TEXT, [ClipboardPayload] TEXT,
            [EnterpriseId] TEXT, [OriginalPayload] BLOB, [OriginalLastModifiedOnClient] DATETIME,
            [ETag] INT NOT NULL);
        CREATE TABLE [ActivityOperation]([OperationOrder] INTEGER PRIMARY KEY ASC NOT NULL,
            [Id] GUID NOT NULL, [OperationType] INT NOT NULL, [AppId] TEXT NOT NULL,
            [PackageIdHash] TEXT, [AppActivityId] TEXT, [ActivityType] INT NOT NULL,
            [ParentActivityId] GUID, [Tag] TEXT, [Group] TEXT, [MatchId] TEXT,
            [LastModifiedTime] DATETIME NOT NULL, [ExpirationTime] DATETIME, [Payload] BLOB,
            [Priority] INT, [CreatedTime] DATETIME, [Attachments] TEXT, [PlatformDeviceId] TEXT,
            [CreatedInCloud] DATETIME, [StartTime] DATETIME NOT NULL, [EndTime] DATETIME,
            [LastModifiedOnClient] DATETIME NOT NULL, [CorrelationVector] TEXT,
            [GroupAppActivityId] TEXT, [ClipboardPayload] TEXT, [EnterpriseId] TEXT,
            [OriginalPayload] BLOB, [OriginalLastModifiedOnClient] DATETIME, [ETag] INT NOT NULL);
        CREATE TABLE [Activity_PackageId]([ActivityId] GUID NOT NULL, [Platform] TEXT NOT NULL,
            [PackageName] TEXT NOT NULL, [ExpirationTime] DATETIME NOT NULL);
        INSERT INTO [Metadata] VALUES ('Version', '2');
    """)
    truth = {"activities": [], "operations": []}
    for n, app, aaid, kind, status, group, start, end, body, clip in ACTIVITIES:
        s = secs(start)
        e = secs(end) if end else 0
        modified = e or s
        expires = s + EXPIRY_DAYS * 86_400
        con.execute(
            "INSERT INTO [Activity]([Id], [AppId], [AppActivityId], [ActivityType], [ActivityStatus], [Group], "
            "[LastModifiedTime], [ExpirationTime], [Payload], [Priority], [IsLocalOnly], [PlatformDeviceId], "
            "[StartTime], [EndTime], [LastModifiedOnClient], [ClipboardPayload], [ETag]) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0, ?, ?, ?, ?, ?, ?)",
            (guid(n), app, aaid, kind, status, group, modified, expires, body, DEVICE, s, e, modified, clip, n))
        for platform in ("packageid", "x_exe_path"):
            name = json.loads(app)[-1]["application"]
            con.execute("INSERT INTO [Activity_PackageId] VALUES (?, ?, ?, ?)", (guid(n), platform, name, expires))
        truth["activities"].append({
            "id_hex": guid(n).hex(), "app_id": app, "app_activity_id": aaid, "activity_type": kind,
            "activity_status": status, "group": group, "start_unix": s, "end_unix": e,
            "last_modified_unix": modified, "expiration_unix": expires,
            "payload": body.decode("utf-8") if body else None, "clipboard_payload": clip,
        })
    for order, n, op, kind, app, start, body in OPERATIONS:
        s = secs(start)
        con.execute(
            "INSERT INTO [ActivityOperation]([OperationOrder], [Id], [OperationType], [AppId], [ActivityType], "
            "[LastModifiedTime], [ExpirationTime], [Payload], [CreatedTime], [StartTime], [EndTime], "
            "[LastModifiedOnClient], [ETag]) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)",
            (order, guid(n), op, app, kind, s, s + EXPIRY_DAYS * 86_400, body, s, s, s, order))
        truth["operations"].append({
            "operation_order": order, "id_hex": guid(n).hex(), "operation_type": op, "activity_type": kind,
            "app_id": app, "start_unix": s, "payload": body.decode("utf-8") if body else None,
        })
    con.commit()
    con.execute("VACUUM")
    con.close()
    write_wal_variant(db_path)
    (OUT / "ActivitiesCache.truth.json").write_text(
        json.dumps(truth, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {db_path} and ActivitiesCache.truth.json")


def write_wal_variant(db_path: Path) -> None:
    out = OUT / "wal"
    out.mkdir(parents=True, exist_ok=True)
    live = out / "live.db"
    shutil.copyfile(db_path, live)
    for p in (Path(f"{live}-wal"), Path(f"{live}-shm")):
        p.unlink(missing_ok=True)
    con = sqlite3.connect(live, isolation_level=None)
    assert con.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    con.execute("PRAGMA wal_autocheckpoint=0")
    s = secs("2026-03-02T08:00:00")
    con.execute(
        "INSERT INTO [Activity]([Id], [AppId], [AppActivityId], [ActivityType], [ActivityStatus], "
        "[LastModifiedTime], [ExpirationTime], [Payload], [StartTime], [EndTime], [LastModifiedOnClient], [ETag]) "
        "VALUES (?, ?, ?, 5, 1, ?, ?, ?, ?, 0, ?, 8)",
        (guid(8), NOTEPAD, "ECB32AF3-1440-4086-94E3-5311F97F89C4\\todo.txt", s, s + EXPIRY_DAYS * 86_400,
         payload({"displayText": "todo.txt", "appDisplayName": "Notepad",
                  "description": "C:\\Users\\alice\\Documents\\todo.txt"}), s, s))
    shutil.copyfile(live, out / "ActivitiesCache.db")
    shutil.copyfile(f"{live}-wal", out / "ActivitiesCache.db-wal")
    con.close()
    for p in (live, Path(f"{live}-wal"), Path(f"{live}-shm")):
        p.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
