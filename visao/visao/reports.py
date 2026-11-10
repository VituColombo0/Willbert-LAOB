"""Consultas e cálculos dos relatórios de presença (usados pela web e pela CLI)."""
from datetime import date, datetime, timedelta


def day_bounds(d):
    t0 = datetime.combine(d, datetime.min.time()).timestamp()
    t1 = datetime.combine(d + timedelta(days=1), datetime.min.time()).timestamp()
    return t0, t1


def session_end(s):
    return s["end_ts"] if s["end_ts"] is not None else s["last_seen_ts"]


def fmt_dur(sec):
    if sec is None:
        return "—"
    if sec < 60:
        return "< 1 min"
    h, m = divmod(int(round(sec / 60)), 60)
    return f"{h}h {m:02d}min" if h else f"{m} min"


def sessions_between(con, t0, t1, member_id=None):
    q = ("SELECT s.*, m.name FROM sessions s JOIN members m ON m.id = s.member_id "
         "WHERE s.start_ts < ? AND COALESCE(s.end_ts, s.last_seen_ts) >= ?")
    args = [t1, t0]
    if member_id is not None:
        q += " AND s.member_id = ?"
        args.append(member_id)
    return [dict(r) for r in con.execute(q + " ORDER BY s.start_ts", args)]


def _empty(name):
    return {"name": name, "entries": 0, "total": 0.0, "days": set(), "first": None,
            "last": None, "open": False}


def summarize(con, sessions, t0, t1, include_all=True):
    per = {}
    if include_all:
        for r in con.execute("SELECT id, name FROM members WHERE active = 1 ORDER BY name"):
            per[r["id"]] = _empty(r["name"])
    for s in sessions:
        a, b = max(s["start_ts"], t0), min(session_end(s), t1)
        p = per.setdefault(s["member_id"], _empty(s["name"]))
        p["entries"] += 1
        p["total"] += max(0.0, b - a)
        p["days"].add(datetime.fromtimestamp(a).date())
        p["first"] = a if p["first"] is None else min(p["first"], a)
        p["last"] = b if p["last"] is None else max(p["last"], b)
        p["open"] = p["open"] or s["end_ts"] is None
    out = []
    for mid, p in per.items():
        p["member_id"] = mid
        p["n_days"] = len(p["days"])
        p["avg"] = p["total"] / p["n_days"] if p["n_days"] else 0.0
        out.append(p)
    out.sort(key=lambda p: (-p["total"], p["name"].lower()))
    return out


def timeline(sessions, t0, t1):
    """Barras de presença por membro ao longo do dia (posições em %)."""
    lo, hi = t0 + 7 * 3600, t0 + 19 * 3600
    for s in sessions:
        lo = min(lo, max(t0, s["start_ts"]))
        hi = max(hi, min(t1, session_end(s)))
    lo = t0 + 3600 * int((lo - t0) // 3600)
    hi = t0 + 3600 * min(24, int(-(-(hi - t0) // 3600)))
    span = max(hi - lo, 3600)
    step = 1 if span <= 12 * 3600 else 2
    hours, h = [], int((lo - t0) // 3600)
    while t0 + h * 3600 <= hi:
        hours.append({"left": round((t0 + h * 3600 - lo) / span * 100, 3), "label": f"{h:02d}h"})
        h += step
    rows = {}
    for s in sessions:
        a = max(s["start_ts"], lo)
        b = max(min(session_end(s), hi), a)
        r = rows.setdefault(s["member_id"], {"name": s["name"], "total": 0.0, "segs": []})
        r["total"] += b - a
        r["segs"].append({"left": round((a - lo) / span * 100, 3),
                          "width": round(max((b - a) / span * 100, 0.6), 3),
                          "start": a, "end": b, "open": s["end_ts"] is None, "id": s["id"]})
    return {"hours": hours, "rows": sorted(rows.values(), key=lambda r: r["name"].lower())}


def first_seen_matrix(con, sessions, d0, d1):
    """Horário da primeira identificação de cada membro em cada dia (None = não visto)."""
    days = []
    d = d0
    while d <= d1:
        days.append(d)
        d += timedelta(days=1)
    index = {x: i for i, x in enumerate(days)}
    rows = {r["id"]: {"name": r["name"], "cells": [None] * len(days)}
            for r in con.execute("SELECT id, name FROM members WHERE active = 1")}
    for s in sessions:
        r = rows.setdefault(s["member_id"], {"name": s["name"], "cells": [None] * len(days)})
        i = index.get(datetime.fromtimestamp(s["start_ts"]).date())
        if i is not None and (r["cells"][i] is None or s["start_ts"] < r["cells"][i]):
            r["cells"][i] = s["start_ts"]
    return days, sorted(rows.values(), key=lambda r: r["name"].lower())


def parse_date(text, default=None):
    try:
        return date.fromisoformat(text) if text else default
    except ValueError:
        return default
