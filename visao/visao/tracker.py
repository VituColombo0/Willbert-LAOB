"""Acompanha rostos entre ciclos para não reconhecer a mesma pessoa toda hora."""
import itertools
from dataclasses import dataclass, field


@dataclass
class Track:
    id: int
    face: object
    first_ts: float
    last_ts: float
    hits: int = 1
    member_id: int | None = None
    member_score: float = 0.0
    last_rec_ts: float = 0.0
    attempts: list = field(default_factory=list)  # (member_id, score, qualidade, margem, de_lado)
    best: dict | None = None                       # melhor leitura (para revisão)
    hard: dict | None = None                       # leitura de boa qualidade que não passou (ângulo difícil)
    reported: bool = False                         # já virou alerta de desconhecido
    cur: tuple | None = None                       # (recorte, qualidade, apto) do ciclo atual
    last_emb: object = None
    last_emb_ts: float = 0.0
    recheck: bool = False                          # sumiu por um tempo: conferir se ainda é a mesma pessoa


class Tracker:
    def __init__(self, ttl=6.0):
        self.tracks = {}
        self.ttl = ttl
        self.recheck_gap = 1.5
        self._ids = itertools.count(1)

    def update(self, faces, now):
        """Associa os rostos do ciclo aos tracks existentes. Retorna (pares, expirados)."""
        pairs = []
        for fi, f in enumerate(faces):
            fx, fy = f.center
            for tid, t in self.tracks.items():
                tx, ty = t.face.center
                size = max(f.w, t.face.w, 1.0)
                dist = ((fx - tx) ** 2 + (fy - ty) ** 2) ** 0.5 / size
                ratio = f.w / max(t.face.w, 1.0)
                if dist < 2.0 and 0.6 < ratio < 1.6:
                    pairs.append((dist, fi, tid))
        pairs.sort()
        used_f, used_t, out = set(), set(), []
        for _, fi, tid in pairs:
            if fi in used_f or tid in used_t:
                continue
            used_f.add(fi)
            used_t.add(tid)
            t = self.tracks[tid]
            # o rosto ficou sumido por um tempo e reapareceu no mesmo lugar: pode ser outra
            # pessoa que sentou/parou ali; a identidade precisa ser conferida de novo
            if t.member_id is not None and now - t.last_ts > self.recheck_gap:
                t.recheck = True
            t.face, t.last_ts, t.hits, t.cur = faces[fi], now, t.hits + 1, None
            out.append((t, faces[fi]))
        for fi, f in enumerate(faces):
            if fi not in used_f:
                t = Track(next(self._ids), f, now, now)
                self.tracks[t.id] = t
                out.append((t, f))
        expired = [t for t in self.tracks.values() if now - t.last_ts > self.ttl]
        for t in expired:
            del self.tracks[t.id]
        return out, expired

    def alive(self, now, within):
        return any(now - t.last_ts < within for t in self.tracks.values())

    def forget_member(self, member_ids_valid):
        for t in self.tracks.values():
            if t.member_id is not None and t.member_id not in member_ids_valid:
                t.member_id, t.member_score = None, 0.0
