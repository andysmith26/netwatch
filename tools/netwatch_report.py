#!/usr/bin/env python3
import csv
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from statistics import median

# ---------- config knobs ----------
LOG_DIR = os.environ.get("NETWATCH_DIR", "/var/log/netwatch")
# classify as OUTAGE only if >=2 consecutive samples or >=20s duration
MIN_ROWS_FOR_OUTAGE = int(os.environ.get("NETWATCH_MIN_ROWS", "2"))
MIN_OUTAGE_SEC = int(os.environ.get("NETWATCH_MIN_SEC", "20"))
# ----------------------------------

TS_PARSE = datetime.fromisoformat
OUTAGE_STATUSES   = {"LINK_DOWN","NO_GATEWAY","GW_DOWN","WAN_DOWN","DNS_DOWN"}
DEGRADED_STATUSES = {"WAN_DEGRADED"}

def month_tags(ref: datetime):
    cur = ref.strftime("%Y-%m")
    prev = (ref.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    return cur, prev

def _f(row, key, default=None, cast=float):
    v = row.get(key, "")
    if v is None or v == "":
        return default
    try:
        return cast(v)
    except Exception:
        return default

def read_rows(path: Path):
    """Yield rows with parsed timestamp and convenient numeric fields."""
    with path.open(newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                row["_ts"] = TS_PARSE(row["timestamp"])
            except Exception:
                continue
            # normalize fields the new logger writes; tolerate old logs
            row["_status"] = row.get("status", "").strip()
            row["_wan_loss"] = _f(row, "wan_loss_pct", default=None)
            row["_wan_rtt"]  = _f(row, "wan_rtt_avg_ms", default=None)
            # optional alternate-target columns (if you added them)
            row["_wan_alt_loss"] = _f(row, "wan_alt_loss_pct", default=None)
            row["_wan_alt_rtt"]  = _f(row, "wan_alt_rtt_avg_ms", default=None)
            yield row

def as_bucket(status: str) -> str:
    if status in OUTAGE_STATUSES:   return "OUTAGE"
    if status in DEGRADED_STATUSES: return "DEGRADED"
    return "OK"

def group_segments(rows):
    """Group consecutive rows by bucket (OK/DEGRADED/OUTAGE)."""
    segs, cur = [], None
    for r in rows:
        bucket = as_bucket(r["_status"])
        if cur is None or bucket != cur["bucket"]:
            if cur: segs.append(cur)
            cur = {
                "bucket": bucket,
                "start": r["_ts"],
                "end":   r["_ts"],
                "rows":  1,
                "first_status": r["_status"],
            }
        else:
            cur["end"] = r["_ts"]
            cur["rows"] += 1
    if cur: segs.append(cur)
    return segs

def classify_segments(segs):
    """Split into outages, blips (filtered), degraded segments."""
    outages, blips, degraded = [], [], []
    for s in segs:
        dur = s["end"] - s["start"]
        s = {**s, "dur": dur}
        if s["bucket"] == "DEGRADED":
            degraded.append(s); continue
        if s["bucket"] == "OK":
            continue
        # OUTAGE bucket
        if s["rows"] >= MIN_ROWS_FOR_OUTAGE or dur.total_seconds() >= MIN_OUTAGE_SEC:
            outages.append(s)
        else:
            blips.append(s)
    return outages, blips, degraded

def human(td: timedelta):
    secs = int(td.total_seconds())
    h, secs = divmod(secs, 3600)
    m, s = divmod(secs, 60)
    if h: return f"{h}h {m}m {s}s"
    if m: return f"{m}m {s}s"
    return f"{s}s"

def availability_from_rows(rows):
    """Compute % of time in OK vs non-OK using timestamp deltas."""
    if len(rows) < 2:
        return 100.0, timedelta(0), timedelta(0)
    ok = timedelta(0)
    total = timedelta(0)
    for prev, cur in zip(rows, rows[1:]):
        dt = cur["_ts"] - prev["_ts"]
        if dt.total_seconds() < 0:   # guard logs with clock jumps
            continue
        total += dt
        if as_bucket(prev["_status"]) == "OK":
            ok += dt
    if total.total_seconds() == 0:
        return 100.0, total, ok
    return (ok/total)*100.0, total, ok

def wan_quality(rows):
    """Summaries over WAN stats when WAN is 'ok' in that row."""
    rtts = [r["_wan_rtt"] for r in rows if r["_wan_rtt"] is not None and r["_status"] not in {"WAN_DOWN","GW_DOWN","NO_GATEWAY","LINK_DOWN"}]
    losses = [r["_wan_loss"] for r in rows if r["_wan_loss"] is not None and r["_status"] not in {"WAN_DOWN","GW_DOWN","NO_GATEWAY","LINK_DOWN"}]
    if not rtts and not losses:
        return None
    p50 = median(rtts) if rtts else None
    p95 = None
    if rtts:
        sorted_r = sorted(rtts)
        idx = max(0, int(round(0.95*(len(sorted_r)-1))))
        p95 = sorted_r[idx]
    mean_loss = sum(losses)/len(losses) if losses else None
    return {"rtt_p50_ms": p50, "rtt_p95_ms": p95, "loss_mean_pct": mean_loss, "samples": len(rtts)}

def per_day_summary(rows, outages, degraded):
    """Return a dict: day -> {'outage_dur':timedelta, 'degraded_dur':timedelta, 'outages':int, 'degraded':int}"""
    from collections import defaultdict
    daily = defaultdict(lambda: {"outage_dur": timedelta(0), "degraded_dur": timedelta(0), "outages": 0, "degraded": 0})
    for seg in outages:
        day = seg["start"].date().isoformat()
        daily[day]["outages"] += 1
        daily[day]["outage_dur"] += seg["dur"]
    for seg in degraded:
        day = seg["start"].date().isoformat()
        daily[day]["degraded"] += 1
        daily[day]["degraded_dur"] += seg["dur"]
    # include days seen in raw rows (even if no events)
    for r in rows:
        day = r["_ts"].date().isoformat()
        daily.setdefault(day, daily[day])
    return dict(sorted(daily.items()))

def report_month(tag: str, per_day=False, log_dir=LOG_DIR):
    path = Path(log_dir) / f"netwatch_{tag}.csv"
    if not path.exists():
        print(f"[{tag}] no log file: {path}")
        return
    rows = list(read_rows(path))
    if not rows:
        print(f"\n=== {tag} — {path} ===\nNo data.")
        return

    segs = group_segments(rows)
    outages, blips, degraded = classify_segments(segs)
    avail_pct, observed, ok_time = availability_from_rows(rows)
    wan = wan_quality(rows)

    print(f"\n=== {tag} — {path} ===")
    # Outages
    if outages:
        for i, o in enumerate(outages, 1):
            print(f"{i:02d}. {o['first_status']:<11} from {o['start']} to {o['end']}  (dur {human(o['dur'])})")
    else:
        print("No qualifying outages (>= "
              f"{MIN_ROWS_FOR_OUTAGE} rows or {MIN_OUTAGE_SEC}s).")

    tot_down = sum((o["dur"] for o in outages), timedelta(0))
    longest = max((o["dur"] for o in outages), default=timedelta(0))

    print(f"— Total outages: {len(outages)}")
    print(f"— Cumulative downtime: {human(tot_down)}")
    print(f"— Longest single outage: {human(longest)}")
    if blips:
        print(f"— Blips filtered (<{MIN_OUTAGE_SEC}s or single row): {len(blips)}")
    if degraded:
        tot_deg = sum((d["dur"] for d in degraded), timedelta(0))
        print(f"— Degraded periods: {len(degraded)} (total {human(tot_deg)})")

    print(f"— Observed window: {human(observed)}  |  Availability: {avail_pct:.3f}%")

    if wan:
        r50 = f"{wan['rtt_p50_ms']:.2f} ms" if wan['rtt_p50_ms'] is not None else "n/a"
        r95 = f"{wan['rtt_p95_ms']:.2f} ms" if wan['rtt_p95_ms'] is not None else "n/a"
        lm  = f"{wan['loss_mean_pct']:.2f} %" if wan['loss_mean_pct'] is not None else "n/a"
        print(f"— WAN RTT p50: {r50} | p95: {r95} | mean loss: {lm} (samples: {wan['samples']})")

    if per_day:
        print("\nPer-day summary:")
        daily = per_day_summary(rows, outages, degraded)
        for day, d in daily.items():
            od, dd = human(d["outage_dur"]), human(d["degraded_dur"])
            print(f"{day}: outages={d['outages']} ({od}), degraded={d['degraded']} ({dd})")

def main():
    import argparse
    p = argparse.ArgumentParser(description="Summarize netwatch CSVs (current + previous month).")
    p.add_argument("--month", help="Specific month YYYY-MM (overrides current+previous).")
    p.add_argument("--dir", default=LOG_DIR, help="Log directory (default: /var/log/netwatch)")
    p.add_argument("--per-day", action="store_true", help="Include per-day summary.")
    args = p.parse_args()

    log_dir = args.dir

    if args.month:
        report_month(args.month, per_day=args.per_day, log_dir=log_dir)
    else:
        now = datetime.now()
        for tag in month_tags(now):
            report_month(tag, per_day=args.per_day, log_dir=log_dir)

if __name__ == "__main__":
    main()
