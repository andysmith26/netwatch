#!/usr/bin/env python3
import csv
import os
from datetime import datetime, timedelta
from pathlib import Path

LOG_DIR = os.environ.get("NETWATCH_DIR", "/var/log/netwatch")
TS_PARSE = datetime.fromisoformat  # ISO 8601 timestamps

def month_tags(ref: datetime):
    cur = ref.strftime("%Y-%m")
    prev_anchor = (ref.replace(day=1) - timedelta(days=1))
    prev = prev_anchor.strftime("%Y-%m")
    return cur, prev

def outages_from_csv(path: Path):
    """Return list of outage dicts: consecutive rows where status != OK."""
    if not path.exists():
        return []

    res = []
    current = None

    with path.open(newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                ts = TS_PARSE(row["timestamp"])
            except Exception:
                # skip malformed
                continue

            status = row["status"].strip()
            if status != "OK":
                if current is None:
                    current = {
                        "start": ts,
                        "end": ts,
                        "first_status": status,
                        "statuses": {status},
                        "rows": 1,
                    }
                else:
                    current["end"] = ts
                    current["rows"] += 1
                    current["statuses"].add(status)
            else:
                if current is not None:
                    res.append(current)
                    current = None

        if current is not None:
            res.append(current)

    return res

def human_duration(td: timedelta) -> str:
    total_seconds = int(td.total_seconds())
    h, r = divmod(total_seconds, 3600)
    m, s = divmod(r, 60)
    if h: return f"{h}h {m}m {s}s"
    if m: return f"{m}m {s}s"
    return f"{s}s"

def report(tag: str):
    path = Path(LOG_DIR) / f"netwatch_{tag}.csv"
    if not path.exists():
        print(f"[{tag}] no log file: {path}")
        return

    outages = outages_from_csv(path)
    print(f"\n=== {tag} — {path} ===")
    if not outages:
        print("No outages detected (all rows OK).")
        return

    total_down = timedelta(0)
    longest = timedelta(0)

    for i, o in enumerate(outages, 1):
        dur = o["end"] - o["start"]
        total_down += dur
        if dur > longest:
            longest = dur
        status_str = o["first_status"] if len(o["statuses"]) == 1 else f"{o['first_status']} (+{len(o['statuses'])-1} more)"
        print(f"{i:02d}. {status_str:<10} from {o['start']} to {o['end']}  (dur {human_duration(dur)})")

    print(f"— Total outages: {len(outages)}")
    print(f"— Cumulative downtime: {human_duration(total_down)}")
    print(f"— Longest single outage: {human_duration(longest)}")

if __name__ == "__main__":
    now = datetime.now()
    cur, prev = month_tags(now)
    for tag in (cur, prev):
        report(tag)
