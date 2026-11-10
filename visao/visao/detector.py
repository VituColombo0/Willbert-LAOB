"""Serviço principal: lê a câmera, detecta e reconhece rostos e registra a presença.

Fluxo de cada ciclo:
  1. pega o quadro mais recente do stream de 720p;
  2. decide se vale analisar (movimento da câmera/ONVIF, pan/tilt girando,
     movimento na imagem, rostos sendo acompanhados ou varredura periódica);
  3. detecta rostos, acompanha cada um entre ciclos e mede a qualidade;
  4. reconhece só os rostos bons que ainda não foram identificados;
  5. atualiza sessões de presença, gera eventos, fotos, clipes e alertas.
"""
import json
import logging
import os
import queue
import shutil
import signal
import threading
import time
import unicodedata
from collections import deque
from datetime import datetime, timedelta

import cv2
import numpy as np

from . import db
from .camera import FrameSource, OnvifWatcher, Recorder
from .config import load, rtsp_url
from .enroll import member_dir, process_pending
from .faces import FaceEngine, Gallery
from .notify import Notifier
from .presence import Presence
from .speaker import Speaker
from .tracker import Tracker

log = logging.getLogger("visao.detector")

GREEN, RED, YELLOW, GRAY = (80, 200, 80), (60, 60, 230), (0, 200, 230), (180, 180, 180)


def cpu_temp():
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return round(int(f.read().strip()) / 1000, 1)
    except Exception:
        return None


def in_window(start, end, dt):
    """True se o horário de dt está entre start e end ("HH:MM"), inclusive janelas que viram o dia."""
    def mins(s):
        h, m = s.split(":")
        return int(h) * 60 + int(m)
    a, b, x = mins(start), mins(end), dt.hour * 60 + dt.minute
    return a <= x < b if a <= b else (x >= a or x < b)


def ascii_label(text):
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


def annotate(frame, items):
    img = frame.copy()
    for face, label, color in items:
        x, y, w, h = face.box
        cv2.rectangle(img, (x, y), (x + w, y + h), color, 2)
        if label:
            label = ascii_label(label)
            (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
            y0 = max(th + 8, y)
            cv2.rectangle(img, (x, y0 - th - 8), (x + tw + 6, y0), color, -1)
            cv2.putText(img, label, (x + 3, y0 - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (20, 20, 20), 2)
    return img


class Detector:
    def __init__(self, cfg):
        self.cfg = cfg
        st, dc = cfg.storage, cfg.detection
        self.data, self.run_dir = st.data_dir, st.runtime_dir
        os.makedirs(self.data, exist_ok=True)
        os.makedirs(self.run_dir, exist_ok=True)
        self.db_path = os.path.join(self.data, "visao.db")
        self.con = db.connect(self.db_path)
        self.engine = FaceEngine(st.models_dir, dc.det_width, dc.det_score_min, dc.threads,
                                 classic=dc.classic_engine)
        self.gallery, self.gallery_version = Gallery(), None
        self.tracker = Tracker(ttl=dc.track_ttl_s)
        self.presence = Presence(self.con, cfg.presence.session_gap_min * 60)
        self.notifier = Notifier(cfg.telegram.token, cfg.telegram.chat_id)
        self.notifier.start()
        self.src = FrameSource(rtsp_url(cfg.camera, "sub"), dc.frame_every)
        self.src.start()
        self.onvif = OnvifWatcher(cfg.camera)
        self.onvif.start()
        self.rec = None
        if st.record_clips:
            self.rec = Recorder(rtsp_url(cfg.camera, "main"), os.path.join(self.run_dir, "segments"))
            self.rec.start()
        self.low = deque(maxlen=80)            # leituras com similaridade baixa (possível desconhecido)
        self.recent_unknown = deque(maxlen=50)  # (ts, embedding) de alertas já enviados
        self.recent_uncertain = deque(maxlen=50)
        self.pending_unknown = []                 # alertas esperando confirmação (45 s)
        self.recent_confirmed = deque(maxlen=60)  # (ts, embedding, member_id) reconhecidos
        self.pending_accepts = {}  # member_id -> ts da última leitura aceita ainda sem confirmação
        # frases ficam no disco: a voz natural (Piper) é lenta para gerar e não deve se perder
        self.speaker = Speaker(cfg.greeting, os.path.join(self.data, "tts"))
        self.speaker.start()
        self.greet_batch = None  # cumprimento esperando a janela para juntar o grupo
        self.greeted = {}       # member_id -> dia em que já foi cumprimentado (ou tentativa recente)
        self._temp, self._temp_ts, self._thermal_level = 0.0, 0.0, 0
        self.last_security = 0.0
        self.clip_q = queue.Queue(maxsize=30)
        threading.Thread(target=self._clip_worker, daemon=True, name="clips").start()
        self.prev_small, self.sw_motion_ts = None, 0.0
        self.last_scan = self.last_live = self.last_status = 0.0
        self.cycle_ms, self.last_faces = 0.0, 0
        self.capture, self.capture_embs, self.last_capture_ts = None, [], 0.0
        self.last_auto = {}
        self._timers = {}
        self.mode = None
        self.stopping = False

    # ------------------------------------------------------------------ loop
    def every(self, name, period):
        now = time.time()
        if now - self._timers.get(name, 0.0) >= period:
            self._timers[name] = now
            return True
        return False

    def run(self):
        log.info("detector iniciado")
        last_seq = -1
        while not self.stopping:
            now = time.time()
            try:
                self.housekeeping(now)
            except Exception:
                log.exception("erro na manutenção")
            frame, fts, seq = self.src.latest()
            if frame is None or seq == last_seq:
                self.write_status(now, self.mode if self.src.online else None)
                time.sleep(0.05)
                continue
            last_seq = seq
            self.update_sw_motion(frame, now)
            self.check_security(frame, now)
            mode = self.mode = self.activity(now)
            # gente chegando (ou cumprimento esperando): CPU toda para reconhecer e falar
            self.speaker.set_busy(mode == "ativo" or self.greet_batch is not None)
            dc = self.cfg.detection
            interval = {"ativo": 0.0, "estavel": dc.stable_interval_s,
                        "acompanhando": dc.idle_track_interval_s, "ocioso": dc.heartbeat_s}[mode]
            interval = max(interval, self.thermal_interval(now))
            if now - self.last_scan < interval:
                if now - self.last_live > 10:
                    self.write_live(frame, [], now)
                self.write_status(now, mode)
                time.sleep(0.05)
                continue
            t0 = time.perf_counter()
            try:
                self.process(frame, fts)
            except Exception:
                log.exception("erro ao processar quadro")
            self.cycle_ms = (time.perf_counter() - t0) * 1000
            self.last_scan = now
            self.write_status(now, mode)
        self.shutdown()

    def thermal_interval(self, now):
        """Proteção térmica: com a CPU quente, espaça as análises em vez de travar o servidor."""
        if now - self._temp_ts > 10:
            self._temp_ts, self._temp = now, cpu_temp() or 0.0
        dc = self.cfg.detection
        level = 2 if self._temp >= dc.temp_critical else 1 if self._temp >= dc.temp_hot else 0
        if level != self._thermal_level:
            log.warning("temperatura %.1f °C: %s", self._temp,
                        ["normal, análise em ritmo normal", "quente, análise mais espaçada",
                         "crítica, análise bem espaçada"][level])
            self._thermal_level = level
        return (0.0, 2.5, 8.0)[level]

    def activity(self, now):
        moving = (self.onvif.motion_recent(8) or self.onvif.ptz_recent(4)
                  or now - self.sw_motion_ts < 4)
        if self.capture is not None:
            return "ativo"
        # rosto novo ainda sem nome: análise contínua mesmo parado, para identificar rápido
        if any(t.member_id is None and now - t.first_ts < 15 and now - t.last_ts < 3
               for t in self.tracker.tracks.values()):
            return "ativo"
        if moving:
            tracks = [t for t in self.tracker.tracks.values() if now - t.last_ts < 4]
            # todos os rostos visíveis já identificados: basta olhar de vez em quando
            if tracks and all(t.member_id is not None for t in tracks):
                return "estavel"
            return "ativo"
        if self.tracker.alive(now, self.cfg.detection.track_ttl_s):
            return "acompanhando"
        return "ocioso"

    def update_sw_motion(self, frame, now):
        # amostragem simples (1 a cada 8 px): barata e sem acionar o pool de threads do OpenCV
        step = max(1, frame.shape[1] // 160)
        small = np.ascontiguousarray(frame[::step, ::step, 1])
        small = cv2.GaussianBlur(small, (5, 5), 0)
        if self.prev_small is not None:
            diff = cv2.absdiff(small, self.prev_small)
            if np.count_nonzero(diff > 25) / diff.size > 0.01:
                self.sw_motion_ts = now
        self.prev_small = small

    # ----------------------------------------------------------- manutenção
    def housekeeping(self, now):
        if self.every("gallery", 3):
            v = db.get_meta(self.con, "gallery_version", "0")
            if v != self.gallery_version:
                self.gallery.load(self.con)
                self.gallery_version = v
                valid = set(self.gallery.names)
                self.presence.drop_missing(valid)
                self.tracker.forget_member(valid)
                self.speaker.prewarm(sorted(self.gallery.names.values()))
                log.info("galeria carregada: %d amostras de %d membros",
                         len(self.gallery), len(set(self.gallery.labels.tolist())))
        if self.every("pending", 3):
            process_pending(self.con, self.engine, self.data)
        if self.every("capture", 2):
            self.poll_capture(now)
        if self.every("presence", 15):
            self.presence.flush()
            for s in self.presence.close_stale(now):
                log.info("%s saiu (visto pela última vez às %s)", self.gallery.names.get(s["member_id"]),
                         datetime.fromtimestamp(s["last_seen_ts"]).strftime("%H:%M"))
        if self.every("retention", 3600):
            self.retention(now)
        if self.every("nightly", 600):
            self.nightly(now)
        if self.every("reassess", 1800):
            self.reassess_review(now)
        self.flush_greeting(now)
        self.drain_speaker()
        if self.pending_unknown:
            self.flush_unknown(now)

    def poll_capture(self, now):
        if self.capture is not None:
            row = self.con.execute("SELECT status FROM capture_requests WHERE id=?",
                                   (self.capture["id"],)).fetchone()
            if row is None or row["status"] != "ativo":
                self.capture = None
            elif now > self.capture["until_ts"]:
                self.finish_capture("expirado")
            return
        row = self.con.execute(
            "SELECT * FROM capture_requests WHERE status='ativo' ORDER BY id LIMIT 1").fetchone()
        if row is None:
            return
        if row["until_ts"] < now:
            self.con.execute("UPDATE capture_requests SET status='expirado' WHERE id=?", (row["id"],))
            return
        self.capture, self.capture_embs = dict(row), []
        log.info("captura de amostras iniciada para o membro %s", row["member_id"])

    def finish_capture(self, status):
        c = self.capture
        self.con.execute("UPDATE capture_requests SET status=?, captured=? WHERE id=?",
                         (status, c["captured"], c["id"]))
        log.info("captura %s: %d amostras", status, c["captured"])
        self.capture = None

    def retention(self, now):
        st = self.cfg.storage
        limit = now - st.retention_days * 86400
        cutoff = datetime.fromtimestamp(limit).strftime("%Y-%m-%d")
        for sub in ("events", "clips"):
            base = os.path.join(self.data, sub)
            if os.path.isdir(base):
                for d in sorted(os.listdir(base)):
                    if d < cutoff:
                        shutil.rmtree(os.path.join(base, d), ignore_errors=True)
        # histórico inteiro mais antigo que o limite: presenças, eventos e pedidos de captura
        # (as amostras dos membros ficam, pois são o cadastro)
        n_s = self.con.execute("DELETE FROM sessions WHERE end_ts IS NOT NULL AND end_ts < ?",
                               (limit,)).rowcount
        n_e = self.con.execute("DELETE FROM events WHERE ts < ?", (limit,)).rowcount
        self.con.execute("DELETE FROM capture_requests WHERE created_at < ?", (limit,))
        if n_s or n_e:
            log.info("retenção: apagados %d presenças e %d eventos anteriores a %s", n_s, n_e, cutoff)
        clips = os.path.join(self.data, "clips")
        while shutil.disk_usage(self.data).free / 1e6 < st.min_free_mb and os.path.isdir(clips):
            days = sorted(os.listdir(clips))
            if not days:
                break
            log.warning("pouco espaço em disco; apagando clipes de %s", days[0])
            shutil.rmtree(os.path.join(clips, days[0]), ignore_errors=True)

    # -------------------------------------------------------------- análise
    def process(self, frame, ts):
        dc = self.cfg.detection
        faces = self.engine.detect(frame)
        self.last_faces = len(faces)
        matched, expired = self.tracker.update(faces, ts)
        for t in expired:
            self.on_track_end(t)
        cands = []
        capturing = self.capture is not None
        for t, f in matched:
            if t.member_id is not None:
                self.presence.touch(t.member_id, ts)
            if f.w < dc.min_face_px * 0.8:
                continue
            fresh = len(t.attempts) < dc.max_attempts or ts - t.last_rec_ts > dc.retry_s
            needs = (capturing or (t.member_id is None and fresh) or t.recheck
                     or (t.member_id is not None and ts - t.last_rec_ts > dc.reverify_s))
            if not needs:
                continue  # rosto já identificado e conferido há pouco: não gasta CPU com ele
            aligned, q, ok = self.engine.assess(frame, f, dc)
            t.cur = (aligned, q, ok)
            if not ok:
                continue
            if t.member_id is None:
                if fresh:
                    cands.append((0, -q["q"], t))
            elif t.recheck:
                cands.append((0, -q["q"], t))
            elif ts - t.last_rec_ts > dc.reverify_s:
                cands.append((1, -q["q"], t))
        cands.sort(key=lambda c: (c[0], c[1]))
        for _, _, t in cands[: dc.max_rec_per_cycle]:
            aligned, q, _ = t.cur
            self.recognize(t, self.engine.embed(aligned), aligned, q, frame, ts)
        if self.capture is not None:
            self.capture_step(matched, ts)
        if ts - self.last_live >= 1.0:  # a página Ao vivo atualiza a cada 2 s: não precisa a cada ciclo
            self.write_live(frame, matched, ts)

    def recognize(self, t, emb, aligned, q, frame, ts):
        dc = self.cfg.detection
        t.last_rec_ts, t.last_emb, t.last_emb_ts = ts, emb, ts
        matches = self.gallery.match(emb)
        m1, s1 = matches[0] if matches else (None, -1.0)
        s2 = matches[1][1] if len(matches) > 1 else -1.0
        side = q.get("side", False)
        t.attempts.append((m1, s1, q["q"], s1 - s2, side))
        # guarda a leitura "difícil" deste rosto: se ele for confirmado depois, vira amostra
        if t.member_id is None and m1 is not None and 0.12 <= s1 < dc.accept and q["q"] >= 0.3 \
                and (t.hard is None or s1 < t.hard["score"]):
            t.hard = {"score": s1, "aligned": aligned, "emb": emb, "q": q["q"], "guess": m1, "ts": ts,
                      "learn": q.get("learn", False)}
        if t.best is None or q["q"] > t.best["q"]:
            t.best = {"q": q["q"], "aligned": aligned, "emb": emb, "frame": frame, "face": t.face,
                      "ts": ts, "guess": m1, "score": s1}
        accepted = (m1 is not None and s1 >= (dc.accept_side if side else dc.accept)
                    and (s1 - s2) >= dc.margin * (2 if side else 1))
        if t.recheck:
            t.recheck = False
            if t.member_id is not None and not (accepted and m1 == t.member_id) and not side:
                # reapareceu e não bate mais com quem era: trata como rosto novo
                log.info("track %d deixou de ser %s (reapareceu com %.2f)", t.id,
                         self.gallery.names.get(t.member_id), s1)
                t.member_id, t.member_score = None, 0.0
        boot = False
        if not accepted and not side and m1 is not None and self.gallery.count(m1) < dc.few_samples:
            # membro com poucas amostras (ex.: só a foto de celular do cadastro): a câmera
            # dá similaridades mais baixas para ele. Várias leituras do mesmo rosto apontando
            # para ele, com folga sobre os outros, bastam para identificar e aprender sozinho.
            agree = [a for a in t.attempts if a[0] == m1 and not a[4]
                     and a[1] >= dc.boot_min and a[3] >= dc.boot_margin]
            if len(agree) >= dc.boot_readings and \
                    sum(a[1] for a in agree) / len(agree) >= dc.boot_mean:
                accepted = boot = True
                log.info("track %d: %s identificado por consenso (%d leituras, média %.2f)", t.id,
                         self.gallery.names.get(m1), len(agree), sum(a[1] for a in agree) / len(agree))
        if accepted and not boot and not self.confirmed(t, m1, s1, ts):
            # uma leitura só, sem muita certeza: espera a próxima confirmar (menos erros)
            log.debug("track %d: %s (%.2f) aguardando confirmação", t.id, self.gallery.names.get(m1), s1)
            return
        if accepted and not self.claim(t, m1, s1, ts):
            # outro rosto visível agora já é essa pessoa, com mais certeza
            if t.member_id == m1:
                t.member_id, t.member_score = None, 0.0
            return
        if accepted:
            if t.member_id is None or (t.member_id != m1 and s1 >= t.member_score + 0.05):
                t.member_id, t.member_score = m1, s1
            if t.member_id != m1:
                return  # leitura aponta outra pessoa, mas sem força para trocar: ignora
            t.member_score = max(t.member_score, s1)
            self.recent_confirmed.append((ts, emb, m1))
            new = self.presence.seen(t.member_id, ts, s1)
            if new:
                self.on_entry(new, t, frame, aligned, ts, s1, emb)
            self.maybe_greet(t.member_id, ts, t.first_ts)
            self.maybe_auto_enrich(t.member_id, emb, aligned, q, s1, ts, boot)
            self.learn_hard(t, emb, ts)
            log.debug("track %d = %s (%.2f)", t.id, self.gallery.names.get(t.member_id), s1)
        elif side:
            pass  # leitura de lado fraca não serve de prova de "desconhecido"
        elif t.member_id is not None and s1 < dc.unknown:
            # a reconfirmação falhou feio: provavelmente outra pessoa entrou no lugar
            t.member_id, t.member_score = None, 0.0
        elif t.member_id is None and len(self.gallery) and s1 < dc.unknown:
            self.low_reading(t, emb, aligned, frame, ts, s1, m1)

    def greet_key(self, ts):
        """Período do cumprimento. Padrão: manhã (5-12h), tarde (12-18h) e noite (18-5h); a
        madrugada conta como a noite que começou no dia anterior (ninguém ouve 'boa noite' de
        novo à meia-noite). Com once_per = "dia", um cumprimento por dia."""
        dt = datetime.fromtimestamp(ts)
        if str(self.cfg.greeting.once_per).lower() == "dia":
            return dt.strftime("%Y-%m-%d")
        if dt.hour < 5:
            return (dt - timedelta(days=1)).strftime("%Y-%m-%d") + "/noite"
        return dt.strftime("%Y-%m-%d") + ("/manha" if dt.hour < 12 else "/tarde" if dt.hour < 18 else "/noite")

    def maybe_greet(self, member_id, ts, seen_ts=None):
        """Cumprimenta o membro na primeira identificação de cada período (bom dia / boa tarde /
        boa noite); vale também após reiniciar."""
        if not self.speaker.enabled:
            return
        today = self.greet_key(ts)
        mark = self.greeted.get(member_id)
        if mark == today:
            return
        if isinstance(mark, float) and ts - mark < 300:
            return  # tentativa recente falhou ou ainda está na fila: espera antes de repetir
        if db.get_meta(self.con, f"greeted:{member_id}") == today:
            self.greeted[member_id] = today
            return
        # só faz sentido cumprimentar quem está chegando: se a pessoa já está na sala há muito
        # tempo (passou da meia-noite trabalhando, ou ficou sem ser reconhecida), não fala
        s = self.presence.open.get(member_id)
        if (str(self.cfg.greeting.once_per).lower() == "dia" and s
                and ts - s["start_ts"] > self.cfg.greeting.arrival_window_min * 60):
            self.greeted[member_id] = today
            return
        self.greeted[member_id] = ts
        # se houver mais rostos na cena, espera um pouco para juntar quem chegou junto
        if self.greet_batch is None:
            self.greet_batch = {"start": ts, "due": ts + self.cfg.greeting.group_window_s,
                                "members": [], "people": 0, "seen": seen_ts or ts}
        if member_id not in self.greet_batch["members"]:
            self.greet_batch["members"].append(member_id)

    def flush_greeting(self, now):
        """Cumprimenta quem foi identificado na janela: 1 pessoa pelo nome, 2 pelos nomes,
        3 ou mais (ou gente não identificada junto) como grupo."""
        b = self.greet_batch
        if b is None:
            return
        dc = self.cfg.detection
        visible = [t for t in self.tracker.tracks.values() if now - t.last_ts < 2.5]
        # quem conta para o cumprimento: os membros a cumprimentar + rostos próximos ainda sem
        # nome (visitante ou membro ainda sendo identificado). Quem já foi cumprimentado hoje e
        # rostos pequenos lá no fundo não mudam a frase.
        near_unknown = [t for t in visible if t.member_id is None and t.face.w >= dc.min_face_px]
        b["people"] = max(b["people"], len(b["members"]) + len(near_unknown))
        # sozinho na cena (ou todos já identificados): fala já; com rosto ainda sem nome,
        # espera até group_window_s para ver se é outro membro e cumprimentar os dois/o grupo
        waiting = bool(near_unknown)
        if now < b["due"] and (waiting or now - b["start"] < self.cfg.greeting.group_min_s):
            return
        self.greet_batch = None
        names = [self.gallery.names.get(m, "") for m in b["members"]]
        text = self.speaker.greet(names, b["members"], b["people"])
        log.info("cumprimento enfileirado %.1f s depois de o rosto aparecer "
                 "(%d identificado(s), %d pessoa(s) na cena): %s",
                 now - b["seen"], len(names), b["people"], text)

    def drain_speaker(self):
        """Só marca 'cumprimentado hoje' quando a câmera de fato falou."""
        while True:
            try:
                member_ids, ok, ts = self.speaker.results.get_nowait()
            except queue.Empty:
                return
            if not member_ids or not ok:
                continue
            if not isinstance(member_ids, (list, tuple)):
                member_ids = [member_ids]
            today = self.greet_key(ts)
            for member_id in member_ids:
                self.greeted[member_id] = today
                self.con.execute("INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) "
                                 "DO UPDATE SET value=excluded.value", (f"greeted:{member_id}", today))

    def check_security(self, frame, now):
        """Modo segurança: movimento dentro do horário vigiado vira evento com foto e vídeo."""
        sc = self.cfg.security
        if not sc.enabled or now - self.last_security < sc.cooldown_min * 60:
            return
        if not in_window(sc.start, sc.end, datetime.fromtimestamp(now)):
            return
        onvif_ok = self.onvif.state == "ok"
        moving = (self.onvif.motion_on and now - self.onvif.motion_ts < 120) if onvif_ok \
            else now - self.sw_motion_ts < 2
        if not moving or self.onvif.ptz_recent(2) and not onvif_ok:
            return
        self.last_security = now
        d = self.day_dir("events", now)
        snap = os.path.join(d, f"{datetime.fromtimestamp(now).strftime('%H%M%S')}_fora_do_horario.jpg")
        cv2.imwrite(snap, frame, [cv2.IMWRITE_JPEG_QUALITY, 82])
        rel = os.path.relpath(snap, self.data)
        cur = self.con.execute("INSERT INTO events(ts, kind, snapshot_path) VALUES(?, 'after_hours', ?)",
                               (now, rel))
        self.schedule_clip(now, cur.lastrowid, None)
        log.warning("movimento fora do horário (%s–%s)", sc.start, sc.end)
        if self.cfg.telegram.token:
            self.notifier.send(f"🚨 Movimento no laboratório fora do horário às "
                               f"{datetime.fromtimestamp(now).strftime('%H:%M')}", snap)

    def reassess_review(self, now):
        """Rostos que ficaram para revisar são conferidos de novo com a galeria atual (que vai
        crescendo sozinha). Os que agora batem claramente com um membro são resolvidos sem
        ninguém precisar abrir a página Revisão; de membros com poucas amostras, viram amostra."""
        dc = self.cfg.detection
        if not len(self.gallery):
            return
        rows = self.con.execute(
            "SELECT id, ts, kind, embedding, crop_path FROM events WHERE kind IN ('unknown','uncertain') "
            "AND resolved = 0 AND embedding IS NOT NULL ORDER BY ts DESC LIMIT 300").fetchall()
        done, learned = 0, {}
        for r in rows:
            emb = db.blob_to_emb(r["embedding"])
            matches = self.gallery.match(emb)
            m1, s1 = matches[0]
            s2 = matches[1][1] if len(matches) > 1 else -1.0
            if s1 < dc.accept + 0.04 or s1 - s2 < dc.boot_margin:
                continue
            self.con.execute("UPDATE events SET resolved = 1, member_id = ?, details = ? WHERE id = ?",
                             (m1, f"identificado depois, automaticamente ({s1:.2f})", r["id"]))
            done += 1
            # não vira amostra: o recorte guardado pode ser borrado ou de cima (sem como medir)
        if done:
            log.info("revisão automática: %d rosto(s) resolvido(s), %d virou(aram) amostra",
                     done, sum(learned.values()))

    def nightly(self, now):
        """Uma vez por dia (madrugada): backup do banco e otimização."""
        dt = datetime.fromtimestamp(now)
        today = dt.strftime("%Y-%m-%d")
        if dt.hour < 3 or db.get_meta(self.con, "nightly") == today:
            return
        bdir = os.path.join(self.data, "backup")
        os.makedirs(bdir, exist_ok=True)
        dst = db.connect_raw(os.path.join(bdir, f"visao-{today}.db"))
        try:
            self.con.backup(dst)
        finally:
            dst.close()
        for old in sorted(os.listdir(bdir))[:-3]:
            os.remove(os.path.join(bdir, old))
        yesterday = (dt - timedelta(days=1)).strftime("%Y-%m-%d")
        self.con.execute("DELETE FROM meta WHERE key LIKE 'greeted:%' AND value < ?", (yesterday,))
        self.con.execute("PRAGMA optimize")
        self.con.execute("VACUUM")
        self.con.execute("INSERT INTO meta(key, value) VALUES('nightly', ?) ON CONFLICT(key) "
                         "DO UPDATE SET value=excluded.value", (today,))
        log.info("manutenção noturna: backup e otimização do banco feitos")

    def claim(self, t, m1, s1, ts):
        """Uma pessoa não pode estar em dois rostos ao mesmo tempo. Se outro rosto visível
        agora já é m1, fica com quem tem mais certeza; o outro volta a ser 'sem nome'."""
        for o in self.tracker.tracks.values():
            if o is t or o.member_id != m1:
                continue
            if ts - o.last_ts > 1.0:
                o.member_id, o.member_score = None, 0.0  # resto de um acompanhamento que se perdeu
                continue
            if o.member_score >= s1:
                return False
            log.info("%s: trocou do rosto %d para o %d (%.2f > %.2f)", self.gallery.names.get(m1),
                     o.id, t.id, s1, o.member_score)
            o.member_id, o.member_score, o.recheck = None, 0.0, False
        return True

    def confirmed(self, t, m1, s1, ts):
        """Aceita a identificação com certeza alta, ou quando duas leituras concordam."""
        dc = self.cfg.detection
        if t.member_id == m1 or s1 >= dc.strong_accept:
            return True
        margin = t.attempts[-1][3] if t.attempts and len(t.attempts[-1]) > 3 else 0.0
        if s1 >= dc.quick_accept and margin >= dc.quick_margin:
            return True  # uma leitura boa e com folga grande sobre os outros membros basta
        prev = self.pending_accepts.get(m1)
        self.pending_accepts[m1] = (ts, t.id)
        agree_track = sum(1 for a in t.attempts[:-1] if a[0] == m1 and a[1] >= dc.accept - 0.04)
        if agree_track >= 1:
            return True
        # leitura anterior em outro rosto: só vale se aquele rosto já sumiu (a pessoa reapareceu
        # num acompanhamento novo); com os dois na cena, podem ser duas pessoas parecidas
        if prev is None or ts - prev[0] > 20:
            return False
        other = self.tracker.tracks.get(prev[1])
        return other is None or other is t or ts - other.last_ts > 1.0

    def low_reading(self, t, emb, aligned, frame, ts, s1, m1):
        """Junta leituras ruins parecidas entre si; com várias, alerta 'desconhecido'."""
        dc = self.cfg.detection
        while self.low and ts - self.low[0]["ts"] > 90:
            self.low.popleft()
        similar = sum(1 for x in self.low if float(x["emb"] @ emb) >= dc.same_person)
        self.low.append({"ts": ts, "emb": emb})
        if t.reported or similar + 1 < dc.unknown_attempts:
            return
        t.reported = True
        for uts, uemb in self.recent_unknown:
            if ts - uts < dc.unknown_cooldown_s and float(uemb @ emb) >= dc.same_person:
                return  # mesma pessoa já gerou alerta há pouco
        self.recent_unknown.append((ts, emb))
        # não alerta na hora: se logo depois (ou pouco antes) um membro parecido for reconhecido,
        # era ele num ângulo ruim, e o alerta é descartado em flush_unknown()
        self.pending_unknown.append({"due": ts + 45, "ts": ts, "emb": emb, "aligned": aligned,
                                     "frame": frame, "face": t.face, "track": t.id,
                                     "s1": s1, "m1": m1})

    def flush_unknown(self, now):
        keep = []
        for p in self.pending_unknown:
            if now < p["due"]:
                keep.append(p)
                continue
            match = next((r for r in self.recent_confirmed
                          if abs(r[0] - p["ts"]) <= 90 and float(r[1] @ p["emb"]) >= 0.30), None)
            if match is not None:
                log.info("alerta de desconhecido descartado: era %s num ângulo ruim",
                         self.gallery.names.get(match[2], "?"))
                continue
            self.emit_unknown(p)
        self.pending_unknown = keep

    def visitor_history(self, emb, ts):
        """Procura a mesma pessoa não identificada nos dias anteriores (até a retenção)."""
        seen, first, today_alerted = 0, None, False
        day0 = datetime.fromtimestamp(ts).replace(hour=0, minute=0, second=0).timestamp()
        for r in self.con.execute("SELECT ts, embedding FROM events WHERE kind='unknown' "
                                  "AND embedding IS NOT NULL AND ts < ? ORDER BY ts", (ts - 60,)):
            if float(db.blob_to_emb(r["embedding"]) @ emb) >= self.cfg.detection.same_visitor:
                seen += 1
                first = first or r["ts"]
                today_alerted = today_alerted or r["ts"] >= day0
        return seen, first, today_alerted

    def emit_unknown(self, p):
        ts, emb, s1, m1 = p["ts"], p["emb"], p["s1"], p["m1"]
        seen, first, today_alerted = self.visitor_history(emb, ts)
        details = (f"já visto {seen} vez(es) antes; a primeira em "
                   f"{datetime.fromtimestamp(first).strftime('%d/%m %H:%M')}") if seen else "primeira vez"
        snap, crop = self.save_images("desconhecido", p["frame"], p["face"], p["aligned"], ts,
                                      p["track"], "Desconhecido", RED)
        cur = self.con.execute(
            "INSERT INTO events(ts, kind, score, guess_member_id, snapshot_path, crop_path, embedding, details) "
            "VALUES(?, 'unknown', ?, ?, ?, ?, ?, ?)", (ts, s1, m1, snap, crop, db.emb_to_blob(emb), details))
        self.schedule_clip(ts, cur.lastrowid, None)
        log.warning("pessoa não identificada (similaridade máx. %.2f; %s)", s1, details)
        if self.cfg.telegram.notify_unknown and not today_alerted:  # mesma pessoa: 1 aviso por dia
            hora = datetime.fromtimestamp(ts).strftime("%H:%M:%S")
            self.notifier.send(f"⚠️ Pessoa não identificada no laboratório às {hora} ({details})",
                               os.path.join(self.data, snap))

    def on_entry(self, s, t, frame, aligned, ts, score, emb):
        name = self.gallery.names.get(s["member_id"], "?")
        snap, crop = self.save_images("entrada", frame, t.face, aligned, ts, t.id, name, GREEN)
        cur = self.con.execute(
            "INSERT INTO events(ts, kind, member_id, session_id, score, snapshot_path, crop_path, embedding) "
            "VALUES(?, 'entry', ?, ?, ?, ?, ?, ?)",
            (ts, s["member_id"], s["id"], score, snap, crop, db.emb_to_blob(emb)))
        self.con.execute("UPDATE sessions SET snapshot_path=?, crop_path=? WHERE id=?",
                         (snap, crop, s["id"]))
        self.schedule_clip(ts, cur.lastrowid, s["id"])
        log.info("entrada: %s (%.2f)", name, score)
        if self.cfg.telegram.notify_entries:
            hora = datetime.fromtimestamp(ts).strftime("%H:%M")
            self.notifier.send(f"✅ {name} chegou às {hora}", os.path.join(self.data, snap))

    def on_track_end(self, t):
        """Rosto sumiu sem ser identificado: guarda a melhor leitura para revisão."""
        if t.member_id is not None or t.reported or t.best is None:
            return
        b = t.best
        # Pedaço de acompanhamento de alguém que acabou de ser reconhecido (virou o rosto,
        # andou): não é uma pessoa nova, só ruído.
        guess = self.presence.open.get(b["guess"]) if b["guess"] is not None else None
        if guess and b["score"] >= 0.12 and abs(b["ts"] - guess["last_seen_ts"]) < 120:
            return
        # o mais parecido é um membro que está na sala (sessão aberta) e já tem a base
        # completa: é ele de lado/de longe; não guarda mais fotos dele para revisar
        if (guess and b["score"] >= 0.20
                and self.gallery.member_sims(b["guess"], b["emb"]).size >= self.cfg.detection.enrich_until):
            return
        # Mesma pessoa não identificada vista há pouco: um registro basta.
        for uts, uemb in self.recent_uncertain:
            if b["ts"] - uts < 300 and float(uemb @ b["emb"]) >= self.cfg.detection.same_person:
                return
        self.recent_uncertain.append((b["ts"], b["emb"]))
        snap, crop = self.save_images("revisar", b["frame"], b["face"], b["aligned"], b["ts"], t.id,
                                      "?", YELLOW)
        cur = self.con.execute(
            "INSERT INTO events(ts, kind, score, guess_member_id, snapshot_path, crop_path, embedding) "
            "VALUES(?, 'uncertain', ?, ?, ?, ?, ?)",
            (b["ts"], b["score"], b["guess"], snap, crop, db.emb_to_blob(b["emb"])))
        self.schedule_clip(b["ts"], cur.lastrowid, None)  # vídeo também de quem não foi identificado

    def learn_hard(self, t, emb, ts):
        """O mesmo rosto acompanhado foi confirmado: a leitura difícil de antes (ângulo ruim,
        longe, de lado) vira amostra. É o que mais melhora o reconhecimento nesses casos."""
        h, dc = t.hard, self.cfg.detection
        t.hard = None
        if (not dc.hard_learn or h is None or not h.get("learn") or h["guess"] != t.member_id
                or ts - h["ts"] > 20):
            return
        if self.gallery.member_sims(t.member_id, emb).size >= dc.enrich_until:
            return  # base do membro já completa
        if float(h["emb"] @ emb) < 0.20:
            return  # pouco parecido com a leitura confirmada: o acompanhamento pode ter trocado de pessoa
        sims = self.gallery.member_sims(t.member_id, h["emb"])
        if sims.size and float(sims.max()) >= 0.80:
            return
        self.add_sample(t.member_id, "auto", h["aligned"], h["emb"], h["q"], ts,
                        f"ângulo difícil aprendido (era {h['score']:.2f})")
        log.info("aprendeu ângulo difícil de %s (%.2f)", self.gallery.names.get(t.member_id), h["score"])

    def maybe_auto_enrich(self, member_id, emb, aligned, q, score, ts, boot=False):
        dc = self.cfg.detection
        sims = self.gallery.member_sims(member_id, emb)
        if sims.size >= dc.enrich_until:
            return  # base do membro já completa: para de capturar o rosto dele
        # membro com poucas amostras (só a foto do cadastro): aprende mais rápido, com um
        # limiar um pouco menor, até ter uma base boa
        few = sims.size < dc.few_samples
        min_score = dc.boot_min if boot else dc.auto_enrich_score - (0.05 if few else 0.0)
        if not dc.auto_enrich or score < min_score or not q.get("learn", True):
            return
        if ts - self.last_auto.get(member_id, 0.0) < (30 if few else 120):
            return
        if sims.size and float(sims.max()) >= 0.80:
            return  # já existe uma amostra quase igual
        autos = self.con.execute("SELECT id, crop_path FROM samples WHERE member_id=? AND source='auto' "
                                 "AND status='ok' ORDER BY created_at", (member_id,)).fetchall()
        # cheio: troca a amostra automática mais antiga (acompanha mudanças de cabelo, barba, óculos)
        for old in autos[: max(0, len(autos) - dc.auto_enrich_max + 1)]:
            if old["crop_path"]:
                try:
                    os.remove(os.path.join(self.data, old["crop_path"]))
                except FileNotFoundError:
                    pass
            self.con.execute("DELETE FROM samples WHERE id=?", (old["id"],))
        self.add_sample(member_id, "auto", aligned, emb, q["q"], ts,
                        f"aprendida por consenso de leituras ({score:.2f})" if boot else
                        f"aprendida automaticamente (similaridade {score:.2f})")
        self.last_auto[member_id] = ts

    def capture_step(self, matched, ts):
        c, dc = self.capture, self.cfg.detection
        if len(matched) != 1 or ts - self.last_capture_ts < 1.2:
            return
        t, _ = matched[0]
        if t.cur is None or not t.cur[2]:
            return
        aligned, q, _ = t.cur
        emb = t.last_emb if t.last_emb_ts == ts else self.engine.embed(aligned)
        if any(float(e @ emb) >= 0.92 for e in self.capture_embs):
            return
        m = self.gallery.match(emb)
        if m and m[0][0] != c["member_id"] and m[0][1] >= dc.accept + 0.05:
            return  # é outro membro já cadastrado na frente da câmera
        self.add_sample(c["member_id"], "camera", aligned, emb, q["q"], ts, "capturada pela câmera")
        self.capture_embs.append(emb)
        self.last_capture_ts = ts
        c["captured"] += 1
        self.con.execute("UPDATE capture_requests SET captured=? WHERE id=?", (c["captured"], c["id"]))
        if c["captured"] >= c["target"]:
            self.finish_capture("concluido")

    def add_sample(self, member_id, source, aligned, emb, quality, ts, message):
        d = member_dir(self.data, member_id)
        path = os.path.join(d, f"{source}_{datetime.fromtimestamp(ts).strftime('%Y%m%d-%H%M%S')}"
                               f"_{int(ts * 1000) % 1000:03d}.jpg")
        cv2.imwrite(path, aligned)
        self.con.execute(
            "INSERT INTO samples(member_id, source, status, message, crop_path, embedding, quality, created_at) "
            "VALUES(?, ?, 'ok', ?, ?, ?, ?, ?)",
            (member_id, source, message, os.path.relpath(path, self.data), db.emb_to_blob(emb), quality, ts))
        db.bump_gallery(self.con)

    # ------------------------------------------------------- arquivos/status
    def day_dir(self, sub, ts):
        d = os.path.join(self.data, sub, datetime.fromtimestamp(ts).strftime("%Y-%m-%d"))
        os.makedirs(d, exist_ok=True)
        return d

    def save_images(self, kind, frame, face, aligned, ts, track_id, label, color):
        d = self.day_dir("events", ts)
        stem = f"{datetime.fromtimestamp(ts).strftime('%H%M%S')}_{kind}_{track_id}"
        snap, crop = os.path.join(d, stem + ".jpg"), os.path.join(d, stem + "_rosto.jpg")
        cv2.imwrite(snap, annotate(frame, [(face, label, color)]), [cv2.IMWRITE_JPEG_QUALITY, 82])
        cv2.imwrite(crop, aligned)
        return os.path.relpath(snap, self.data), os.path.relpath(crop, self.data)

    def schedule_clip(self, ts, event_id, session_id):
        if self.rec is None:
            return
        due = ts + self.cfg.storage.clip_after_s + 15
        try:
            self.clip_q.put_nowait((due, ts, event_id, session_id))
        except queue.Full:
            log.warning("fila de clipes cheia; evento %s fica só com foto", event_id)

    def _clip_worker(self):
        """Monta um clipe por vez (vários ffmpeg ao mesmo tempo pesariam demais na TV Box)."""
        while True:
            due, ts, event_id, session_id = self.clip_q.get()
            wait = due - time.time()
            if wait > 0:
                time.sleep(wait)
            if time.time() - ts > 150:  # os segmentos de vídeo na RAM já foram descartados
                log.warning("clipe do evento %s atrasado demais; ficou só a foto", event_id)
                continue
            self._clip_job(ts, event_id, session_id)

    def _clip_job(self, ts, event_id, session_id):
        st = self.cfg.storage
        out = os.path.join(self.day_dir("clips", ts),
                           f"{datetime.fromtimestamp(ts).strftime('%H%M%S')}_evento{event_id}.mp4")
        try:
            if not self.rec.make_clip(ts - st.clip_before_s, ts + st.clip_after_s, out):
                log.warning("não foi possível montar o clipe do evento %s", event_id)
                return
            rel = os.path.relpath(out, self.data)
            con = db.connect(self.db_path)
            try:
                con.execute("UPDATE events SET clip_path=? WHERE id=?", (rel, event_id))
                if session_id:
                    con.execute("UPDATE sessions SET clip_path=? WHERE id=?", (rel, session_id))
            finally:
                con.close()
        except Exception:
            log.exception("erro ao montar clipe")

    def write_live(self, frame, matched, ts):
        items = []
        for t, f in matched:
            if t.member_id is not None:
                items.append((f, self.gallery.names.get(t.member_id, "?"), GREEN))
            elif t.reported:
                items.append((f, "Desconhecido", RED))
            else:
                items.append((f, "", YELLOW if t.attempts else GRAY))
        img = annotate(frame, items) if items else frame
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 70])
        if ok:
            tmp = os.path.join(self.run_dir, "live.jpg.tmp")
            with open(tmp, "wb") as f:
                f.write(buf.tobytes())
            os.replace(tmp, os.path.join(self.run_dir, "live.jpg"))
        self.last_live = ts

    def write_status(self, now, mode):
        if now - self.last_status < 2:
            return
        self.last_status = now
        names = self.gallery.names
        status = {
            "ts": now,
            "camera_online": self.src.online,
            "decoder_fps": round(self.src.fps, 1),
            "stream_size": self.src.size,
            "recorder_online": bool(self.rec and self.rec.online),
            "onvif": self.onvif.state,
            "onvif_events": self.onvif.event_count,
            "motion": self.onvif.motion_recent(8),
            "ptz_moving": self.onvif.ptz_recent(4),
            "sw_motion": now - self.sw_motion_ts < 4,
            "mode": mode or "aguardando câmera",
            "cycle_ms": round(self.cycle_ms),
            "faces": self.last_faces,
            "tracks": [{"id": t.id, "name": names.get(t.member_id) if t.member_id else None,
                        "score": round(t.member_score, 2), "attempts": len(t.attempts)}
                       for t in self.tracker.tracks.values()],
            "present": [{"member_id": m, "name": names.get(m, "?"), "since": s["start_ts"],
                         "last_seen": s["last_seen_ts"]} for m, s in self.presence.open.items()],
            "gallery_samples": len(self.gallery),
            "gallery_members": len(set(self.gallery.labels.tolist())),
            "capture": ({"member_id": self.capture["member_id"], "captured": self.capture["captured"],
                         "target": self.capture["target"]} if self.capture else None),
            "speaker": {"enabled": self.speaker.enabled, "spoken": self.speaker.spoken,
                        "last_ok": self.speaker.last_ok, "last_error": self.speaker.last_error,
                        "last_route": self.speaker.last_route, "output": self.speaker.output,
                        "engine": self.speaker.engine(), "pending": self.speaker.pending()},
            "cpu_temp": cpu_temp(),
            "load": round(os.getloadavg()[0], 2),
        }
        tmp = os.path.join(self.run_dir, "status.json.tmp")
        with open(tmp, "w") as f:
            json.dump(status, f)
        os.replace(tmp, os.path.join(self.run_dir, "status.json"))

    def shutdown(self):
        log.info("encerrando")
        try:
            self.presence.flush()
        except Exception:
            log.exception("erro ao salvar presença")
        self.src.stop()
        if self.rec:
            self.rec.stop()
        self.onvif.stop()


def main():
    logging.basicConfig(level=os.environ.get("VISAO_LOG", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("zeep").setLevel(logging.WARNING)
    det = Detector(load())

    def _stop(*_):
        det.stopping = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    det.run()


if __name__ == "__main__":
    main()
