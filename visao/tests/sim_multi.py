"""Simulação dos cenários com várias pessoas (sem câmera): python -m tests.sim_multi"""
import logging, time
from datetime import datetime
import numpy as np
from visao import db
from visao.config import load
from visao.detector import Detector
from visao.faces import Gallery, Face
from visao.presence import Presence
from visao.tracker import Tracker

logging.basicConfig(level=logging.INFO, format="%(message)s")
rng = np.random.default_rng(1)
base = {m: v / np.linalg.norm(v) for m, v in {1: rng.normal(size=128), 2: rng.normal(size=128),
                                              3: rng.normal(size=128), 9: rng.normal(size=128)}.items()}

def emb_of(m, sim):
    """Embedding com similaridade ~sim com o membro m (9 = visitante)."""
    noise = rng.normal(size=128)
    q, _ = np.linalg.qr(np.stack(list(base.values())).T)  # base ortonormal dos 4 rostos
    noise -= q @ (q.T @ noise)  # ruído sem semelhança com nenhum membro: teste determinístico
    noise /= np.linalg.norm(noise)
    v = sim * base[m] + np.sqrt(1 - sim ** 2) * noise
    return (v / np.linalg.norm(v)).astype(np.float32)

class FakeSpeaker:
    enabled = True
    def __init__(self):
        import queue
        self.said, self.results, self.now = [], queue.Queue(), 0
    def greet(self, names, ids, people):
        self.said.append((sorted(names), people))
        self.results.put((list(ids), True, self.now))  # falou com sucesso
        return f"{names} p={people}"

class FakeEngine:
    def __init__(self): self.next = {}
    def assess(self, frame, f, dc):
        return None, {"q": 0.8, "w": f.w, "yaw": 0.0, "side": False}, True
    def embed(self, aligned): raise RuntimeError

def make():
    cfg = load("/nonexistent.toml", detect_hardware=False)
    d = Detector.__new__(Detector)
    d.cfg = cfg
    d.con = db.connect(":memory:")
    for i, n in ((1, "Victor"), (2, "Gazziro"), (3, "Mario")):
        d.con.execute("INSERT INTO members(id,name,created_at) VALUES(?,?,0)", (i, n))
    g = Gallery(); g.names = {1: "Victor", 2: "Gazziro", 3: "Mario"}
    g.matrix = np.stack([base[1], base[2], base[3]]).astype(np.float32); g.labels = np.array([1, 2, 3])
    d.gallery = g
    d.tracker = Tracker(ttl=6.0)
    d.presence = Presence(d.con, 1800)
    d.speaker = FakeSpeaker()
    d.greet_batch, d.greeted, d.pending_accepts = None, {}, {}
    from collections import deque
    d.recent_confirmed, d.low, d.recent_unknown, d.pending_unknown = deque(maxlen=60), deque(maxlen=80), deque(maxlen=50), []
    d.recent_uncertain = deque(maxlen=50)
    d.on_entry = lambda *a: None
    d.maybe_auto_enrich = lambda *a: None
    d.learn_hard = lambda *a: None
    d.on_track_end = lambda t: None
    d.capture = None
    d.last_faces = 0
    return d

def face(x, w=60):
    r = np.zeros(15, np.float32); r[:4] = (x, 200, w, w); r[14] = 0.9
    return Face(r)

def step(d, ts, people):
    """people: lista de (x, membro, similaridade). Roda um ciclo."""
    faces = [face(x) for x, _, _ in people]
    matched, expired = d.tracker.update(faces, ts)
    by_x = {round(f.center[0]): (m, s) for f, (x, m, s) in zip(faces, people)}
    for t, f in matched:
        m, s = by_x[round(f.center[0])]
        if t.member_id is None or t.recheck or ts - t.last_rec_ts > 45:
            d.recognize(t, emb_of(m, s), None, {"q": 0.8, "side": False}, None, ts)
    d.speaker.now = ts
    d.flush_greeting(ts)
    d.drain_speaker()

def names(d):
    return {t.id: d.gallery.names.get(t.member_id) for t in d.tracker.tracks.values()}

ok = True
def check(label, cond):
    global ok; ok &= bool(cond); print(("OK   " if cond else "FALHA"), label)

# 1) uma pessoa sozinha: cumprimento logo depois de confirmar (2 leituras)
d = make(); t0 = 1000.0
for i in range(4): step(d, t0 + i, [(100, 1, 0.40)])
check("sozinho: Victor cumprimentado pelo nome em <= 2 ciclos", d.speaker.said == [(["Victor"], 1)])

# 2) dois membros juntos: frase com os dois nomes
d = make()
for i in range(5): step(d, t0 + i, [(100, 1, 0.42), (400, 2, 0.42)])
check("dois membros: cumprimento único com os dois nomes", d.speaker.said == [(["Gazziro", "Victor"], 2)])

# 3) membro + visitante: espera e cumprimenta o grupo
d = make()
for i in range(8): step(d, t0 + i, [(100, 1, 0.45), (400, 9, 0.9)])
check("membro + visitante: cumprimento para o grupo", d.speaker.said and d.speaker.said[0][1] == 2)

# 4) Victor já cumprimentado hoje, Mario chega: Mario pelo nome (não "pessoal")
d = make()
for i in range(3): step(d, t0 + i, [(100, 1, 0.55)])
d.greeted[1] = time.strftime("%Y-%m-%d", time.localtime(t0)); d.speaker.said.clear()
for i in range(3, 8): step(d, t0 + i, [(100, 1, 0.55), (400, 3, 0.45)])
check("já cumprimentado + novo membro: só o novo, pelo nome", d.speaker.said == [(["Mario"], 1)])

# 5) troca de pessoa no mesmo lugar: Victor sai, Gazziro senta ali 3 s depois
d = make()
for i in range(3): step(d, t0 + i, [(100, 1, 0.55)])
step(d, t0 + 6, [(100, 2, 0.55)])
check("troca de pessoa no mesmo lugar: nome atualizado", list(names(d).values()) == ["Gazziro"])

# 6) o mesmo membro em dois rostos: fica só no de maior certeza
d = make()
for i in range(3): step(d, t0 + i, [(100, 1, 0.38), (400, 1, 0.60)])
check("mesmo nome em dois rostos: só um fica com o nome",
      sorted(v or "-" for v in names(d).values()) == ["-", "Victor"])

# 7) leitura fraca isolada de um membro não identifica
d = make()
step(d, t0, [(100, 2, 0.34)])
check("uma leitura fraca não basta", list(names(d).values()) == [None])
# 8) visitante que já apareceu antes é reconhecido como "já visto"
d = make()
v_old = emb_of(9, 0.85)
d.con.execute("INSERT INTO events(ts, kind, embedding) VALUES(?, 'unknown', ?)", (t0 - 86400, db.emb_to_blob(v_old)))
seen, first, today = d.visitor_history(emb_of(9, 0.85), t0)
check("visitante de ontem: 'já visto 1 vez', sem marcar aviso de hoje", seen == 1 and not today)
seen, _, _ = d.visitor_history(emb_of(2, 0.9), t0)
check("outra pessoa não é confundida com o visitante", seen == 0)
# 9) quem aprende sozinho: modo cauteloso e fase inicial (poucas amostras, aprende rápido)
d = make(); added = []
d.add_sample = lambda mid, *a: added.append(mid)
d.last_auto = {}; d.data = "/tmp"
d.gallery.matrix = np.stack([emb_of(1, 0.9) for _ in range(25)] + [base[2]]).astype(np.float32)
d.gallery.labels = np.array([1] * 25 + [2])
def fotos(d, mid, n, src="foto", ts=0.0):  # amostras no banco (foto/câmera = cadastro; auto = aprendida)
    for _ in range(n):
        d.con.execute("INSERT INTO samples(member_id, source, status, created_at, embedding) VALUES(?, ?, 'ok', ?, ?)",
                      (mid, src, ts, db.emb_to_blob(emb_of(mid, 0.6))))
fotos(d, 1, 3); fotos(d, 2, 1)
d.cfg.detection["min_manual"] = 3   # modo cauteloso
Detector.maybe_auto_enrich(d, 2, emb_of(2, 0.6), None, {"q": 0.9}, 0.6, t0)
check("modo cauteloso (min_manual 3): Gazziro com 1 foto não aprende sozinho", added == [])
d.cfg.detection["min_manual"] = 1   # padrão: aprende desde o cadastro
Detector.maybe_auto_enrich(d, 2, emb_of(2, 0.6), None, {"q": 0.9}, 0.6, t0)
check("fase inicial: Gazziro (1 foto) aprende na hora", added == [2])
Detector.maybe_auto_enrich(d, 2, emb_of(2, 0.6), None, {"q": 0.9}, 0.6, t0 + 10)
check("fase inicial: no máximo 1 a cada 30 s", added == [2])
Detector.maybe_auto_enrich(d, 2, emb_of(2, 0.6), None, {"q": 0.9}, 0.35, t0 + 60)
check("certeza baixa (0,35) não vira amostra", added == [2])

# 9b) depois da fase inicial: até 50, poucas por dia, em momentos sorteados, 2 h de intervalo
def aprendiz(n_amostras, n_auto=0, auto_ts=0.0):
    """Detector com o Victor tendo n_amostras na galeria (n_auto delas aprendidas)."""
    d = make(); d.data = "/tmp"; d.last_auto = {}; d.add_sample_real = d.add_sample = None
    d.gallery.matrix = np.stack([emb_of(1, 0.6) for _ in range(n_amostras)]).astype(np.float32)
    d.gallery.labels = np.array([1] * n_amostras)
    fotos(d, 1, n_amostras - n_auto); fotos(d, 1, n_auto, "auto", auto_ts)
    def add(mid, src, aligned, emb, q, ts, msg):
        d.con.execute("INSERT INTO samples(member_id, source, status, created_at, embedding) VALUES(?, ?, 'ok', ?, ?)",
                      (mid, src, ts, db.emb_to_blob(emb)))
        d.gallery.matrix = np.vstack([d.gallery.matrix, emb[None]]); d.gallery.labels = np.append(d.gallery.labels, mid)
    d.add_sample = add
    return d
def dia(dd, h=0, m=0):
    return time.mktime((2026, 11, dd, h, m, 0, 0, 0, -1))
def n_auto(d, desde=0.0):
    return d.con.execute("SELECT COUNT(*) FROM samples WHERE source='auto' AND created_at >= ?", (desde,)).fetchone()[0]
def passa_o_dia(d, dd, cada_min=10):
    """O Victor fica o dia todo na frente da câmera, reconhecido com certeza alta a cada 10 min."""
    for k in range(0, 24 * 60, cada_min):
        ts = dia(dd, k // 60, k % 60)
        Detector.maybe_auto_enrich(d, 1, emb_of(1, 0.6), None, {"q": 0.9}, 0.6, ts)
    return n_auto(d, dia(dd))

d = aprendiz(20)
mom = d.momentos_do_dia(1, datetime.fromtimestamp(dia(16)))
hoje = passa_o_dia(d, 16)
check(f"dia inteiro na frente da câmera: aprende {hoje} = momentos sorteados ({len(mom)}), máx. 3", hoje == len(mom) and 1 <= hoje <= 3)
ts_aut = [r[0] for r in d.con.execute("SELECT created_at FROM samples WHERE source='auto' ORDER BY created_at")]
check("entre duas amostras do mesmo dia: pelo menos 2 h", all(b - a >= 7200 for a, b in zip(ts_aut, ts_aut[1:])))
check("nenhuma antes do primeiro momento sorteado nem fora das 07h-23h",
      all(7 * 60 <= (datetime.fromtimestamp(t).hour * 60 + datetime.fromtimestamp(t).minute) < 23 * 60 for t in ts_aut)
      and datetime.fromtimestamp(ts_aut[0]).hour * 60 + datetime.fromtimestamp(ts_aut[0]).minute >= int(mom[0]))
dias = [d.momentos_do_dia(1, datetime.fromtimestamp(dia(x))) for x in range(1, 15)]
check("os momentos mudam de um dia para o outro", len({tuple(round(m) for m in x) for x in dias}) > 10)
check("a quantidade por dia também varia (1 a 3)", {len(x) for x in dias} - {1, 2, 3} == set() and len({len(x) for x in dias}) > 1)
check("membros diferentes têm momentos diferentes no mesmo dia",
      d.momentos_do_dia(1, datetime.fromtimestamp(dia(16))) != d.momentos_do_dia(2, datetime.fromtimestamp(dia(16))))
check("reiniciar o serviço não muda o sorteio",
      make().momentos_do_dia(1, datetime.fromtimestamp(dia(16))) == mom)
d2 = aprendiz(20); d2.last_auto = {}
fotos(d2, 1, 1, "auto", dia(16, 6))   # aprendeu às 06h (antes de reiniciar)
Detector.maybe_auto_enrich(d2, 1, emb_of(1, 0.6), None, {"q": 0.9}, 0.6, dia(16, 7, 30))
check("intervalo de 2 h vale mesmo depois de reiniciar (lê do banco)", n_auto(d2, dia(16, 7)) == 0)

# 9c) base cheia (50): não para; troca no máximo 1 por dia, só aprendida, a mais repetida entre as antigas
d = aprendiz(50, n_auto=40, auto_ts=dia(1, 12))
for x in range(16, 20): passa_o_dia(d, x)
tot = d.con.execute("SELECT COUNT(*) FROM samples WHERE status='ok'").fetchone()[0]
cad = d.con.execute("SELECT COUNT(*) FROM samples WHERE source!='auto'").fetchone()[0]
check(f"base cheia: continua com 50 ({tot}) e trocou 1 por dia em 4 dias ({n_auto(d, dia(16))})",
      tot == 50 and n_auto(d, dia(16)) == 4)
check("as 10 fotos do cadastro nunca são trocadas", cad == 10)
d = aprendiz(50, n_auto=40, auto_ts=dia(1, 12))
rep = emb_of(1, 0.6)  # duas amostras antigas iguais: uma delas é a primeira a sair
ids = [r[0] for r in d.con.execute("SELECT id FROM samples WHERE source='auto' ORDER BY id LIMIT 2")]
for i in ids:
    d.con.execute("UPDATE samples SET embedding=? WHERE id=?", (db.emb_to_blob(rep), i))
sai = d.escolhe_para_trocar(1, d.con.execute("SELECT id, crop_path, embedding FROM samples WHERE source='auto' "
                                             "ORDER BY created_at").fetchall(), 1)
check("troca a amostra antiga mais repetida", [r["id"] for r in sai][0] in ids)
# 10) modo "1 por dia": na sala há 2 h (passou da meia-noite), não cumprimenta no "novo dia"
d = make(); d.cfg.greeting["once_per"] = "dia"
d.presence.seen(1, t0 - 7200, 0.6)
for k in range(12): d.presence.touch(1, t0 - 7200 + 600 * (k + 1))  # visto o tempo todo
for i in range(3): step(d, t0 + i, [(100, 1, 0.55)])
check("modo 1 por dia: já estava na sala há 2 h, não cumprimenta", d.speaker.said == [])

# 11) frases de dupla e de prewarm fazem sentido (com a classe real do Speaker)
from visao.speaker import Speaker
import tempfile
sp = Speaker(load("/nonexistent.toml").greeting, tempfile.mkdtemp())
f1 = sp.phrase(["Victor", "Gazziro"], 2, 15, 0); f2 = sp.phrase(["Gazziro", "Victor"], 2, 15, 0)
check(f"dupla com ordem fixa dos nomes: '{f1}'", f1 == f2 == "Boa tarde, Gazziro e Victor!")
check("sozinho de manhã: " + sp.phrase(["Mario"], 1, 9, 0), sp.phrase(["Mario"], 1, 9, 0) == "Bom dia, Mario! Que bom te ver.")
check("3 pessoas à noite: " + sp.phrase(["Mario"], 3, 20, 0), sp.phrase(["Mario"], 3, 20, 0) == "Boa noite, pessoal! Que bom ver vocês.")
# 12) Gazziro só com a foto do cadastro: leituras baixas (0,31) mas consistentes identificam
d = make(); d.speaker.said.clear()
for i in range(4): step(d, t0 + i, [(100, 2, 0.31)])  # 0,31: 0,30 exato fica abaixo da média em float32
check("poucas amostras: 3+ leituras consistentes identificam o Gazziro", list(names(d).values()) == ["Gazziro"])

# 13) visitante com leituras baixas e espalhadas não vira membro por consenso
d = make()
for i in range(8): step(d, t0 + i, [(100, 9, 0.9)])
check("visitante não é identificado por consenso", list(names(d).values()) == [None])

# 14) revisão automática: rosto pendente que agora bate com um membro é resolvido sozinho
d = make(); d.data = "/tmp"; added = []
d.add_sample = lambda mid, *a: added.append(mid)
d.con.execute("INSERT INTO events(ts, kind, embedding) VALUES(?, 'uncertain', ?)", (t0, db.emb_to_blob(emb_of(3, 0.6))))
d.con.execute("INSERT INTO events(ts, kind, embedding) VALUES(?, 'uncertain', ?)", (t0, db.emb_to_blob(emb_of(9, 0.9))))
d.reassess_review(t0)
res = d.con.execute("SELECT member_id, resolved FROM events ORDER BY id").fetchall()
check("revisão automática: Mario resolvido, visitante continua pendente",
      [tuple(r) for r in res] == [(3, 1), (None, 0)])
# 15) um cumprimento por período: bom dia, depois boa tarde, mas não de novo à meia-noite
d = make()
day = time.mktime((2026, 11, 10, 9, 0, 0, 0, 0, -1))
for i in range(3): step(d, day + i, [(100, 1, 0.55)])
for i in range(3): step(d, day + 3600 + i, [(100, 1, 0.55)])            # 10h: mesma manhã
for i in range(3): step(d, day + 5 * 3600 + i, [(100, 1, 0.55)])        # 14h: tarde
for i in range(3): step(d, day + 11 * 3600 + i, [(100, 1, 0.55)])       # 20h: noite
for i in range(3): step(d, day + 16 * 3600 + i, [(100, 1, 0.55)])       # 01h: mesma noite
check("um cumprimento por período (manhã, tarde, noite; madrugada = mesma noite)",
      len(d.speaker.said) == 3)
# 16) saída anotada: some por mais que o intervalo -> evento 'exit' na hora em que foi visto pela última vez
d = make()
d.presence.seen(2, t0, 0.6)
d.presence.touch(2, t0 + 600)
d.presence.close_stale(t0 + 600 + 31 * 60)
ev = d.con.execute("SELECT kind, ts, member_id, session_id FROM events WHERE kind = 'exit'").fetchall()
fim = d.con.execute("SELECT end_ts FROM sessions WHERE member_id = 2").fetchone()[0]
check("saída anotada na hora em que foi visto pela última vez",
      len(ev) == 1 and ev[0]["ts"] == t0 + 600 and ev[0]["member_id"] == 2 and fim == t0 + 600)
# 17) voltou depois de muito tempo: a visita anterior ganha a saída e começa outra
d = make()
d.presence.seen(3, t0, 0.6)
d.presence.seen(3, t0 + 2 * 3600, 0.6)
n_ex = d.con.execute("SELECT COUNT(*) FROM events WHERE kind = 'exit' AND member_id = 3").fetchone()[0]
n_s = d.con.execute("SELECT COUNT(*) FROM sessions WHERE member_id = 3").fetchone()[0]
check("voltou depois de 2 h: uma saída anotada e uma visita nova", n_ex == 1 and n_s == 2)
print("TUDO OK" if ok else "HÁ FALHAS")
