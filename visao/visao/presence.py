"""Sessões de presença: quando cada membro chegou, foi visto pela última vez e saiu."""
import logging
import time

log = logging.getLogger("visao.presence")


class Presence:
    def __init__(self, con, gap_s):
        self.con = con
        self.gap = float(gap_s)
        self.open = {}  # member_id -> dict da sessão aberta
        self._dirty = set()
        for r in con.execute("SELECT * FROM sessions WHERE end_ts IS NULL"):
            self.open[r["member_id"]] = dict(r)

    def seen(self, member_id, ts, score):
        """Registra que o membro foi reconhecido. Retorna a sessão se uma nova foi aberta."""
        s = self.open.get(member_id)
        if s and ts - s["last_seen_ts"] > self.gap:
            self._close(s)
            s = None
        if s:
            s["last_seen_ts"] = max(s["last_seen_ts"], ts)
            s["sightings"] += 1
            s["best_score"] = max(s["best_score"] or 0.0, score)
            self._dirty.add(member_id)
            return None
        cur = self.con.execute(
            "INSERT INTO sessions(member_id, start_ts, last_seen_ts, sightings, best_score) "
            "VALUES(?, ?, ?, 1, ?)", (member_id, ts, ts, score))
        s = {"id": cur.lastrowid, "member_id": member_id, "start_ts": ts, "last_seen_ts": ts,
             "end_ts": None, "sightings": 1, "best_score": score}
        self.open[member_id] = s
        return s

    def touch(self, member_id, ts):
        """O rosto já identificado continua visível (sem novo reconhecimento)."""
        s = self.open.get(member_id)
        if s and ts - s["last_seen_ts"] <= self.gap:
            s["last_seen_ts"] = max(s["last_seen_ts"], ts)
            self._dirty.add(member_id)

    def flush(self):
        for mid in list(self._dirty):
            s = self.open.get(mid)
            if not s:
                continue
            cur = self.con.execute(
                "UPDATE sessions SET last_seen_ts=?, sightings=?, best_score=? WHERE id=? AND member_id=?",
                (s["last_seen_ts"], s["sightings"], s["best_score"], s["id"], mid))
            if cur.rowcount == 0:  # sessão apagada ou corrigida para outra pessoa na interface web
                self.open.pop(mid, None)
        self._dirty.clear()

    def close_stale(self, now=None):
        now = now or time.time()
        closed = []
        for s in list(self.open.values()):
            if now - s["last_seen_ts"] > self.gap:
                self._close(s)
                closed.append(s)
        return closed

    def _close(self, s):
        self.open.pop(s["member_id"], None)
        self._dirty.discard(s["member_id"])
        exists = self.con.execute("SELECT 1 FROM sessions WHERE id=? AND member_id=?",
                                  (s["id"], s["member_id"])).fetchone()
        if not exists:
            return
        self.con.execute(
            "UPDATE sessions SET end_ts=?, last_seen_ts=?, sightings=?, best_score=? WHERE id=?",
            (s["last_seen_ts"], s["last_seen_ts"], s["sightings"], s["best_score"], s["id"]))


    def drop_missing(self, valid_ids):
        for mid in list(self.open):
            if mid not in valid_ids:
                self.open.pop(mid, None)
                self._dirty.discard(mid)
