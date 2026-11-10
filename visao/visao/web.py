"""Interface web: presença de hoje, ao vivo, relatórios, membros e revisão de rostos."""
import csv
import io
import json
import os
import shutil
import sqlite3
import time
from datetime import date, datetime, timedelta
from functools import wraps

from flask import (Flask, Response, abort, flash, g, jsonify, redirect, render_template, request,
                   send_file, send_from_directory, session, url_for)
from werkzeug.security import check_password_hash

from . import db, reports
from .config import load
from .enroll import insert_pending, member_dir, new_photo_path

cfg = load()
DATA = cfg.storage.data_dir
RUN = cfg.storage.runtime_dir
AUTH_FILE = os.path.join(DATA, "web_auth.json")
MEDIA_DIRS = ("events", "clips", "members")
IMG_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
KIND_LABEL = {"entry": "Identificado", "exit": "Saída", "unknown": "Desconhecido", "uncertain": "Não identificado",
              "after_hours": "Fora do horário"}


def read_auth():
    try:
        with open(AUTH_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


app = Flask(__name__)
app.secret_key = (read_auth() or {}).get("secret_key") or os.urandom(32)
app.config.update(SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_HTTPONLY=True,
                  PERMANENT_SESSION_LIFETIME=timedelta(days=30), MAX_CONTENT_LENGTH=80 * 1024 * 1024)
_fails = {}


def con():
    if "db" not in g:
        g.db = db.connect(os.path.join(DATA, "visao.db"))
    return g.db


@app.teardown_appcontext
def _close_db(exc):
    c = g.pop("db", None)
    if c is not None:
        c.close()


def login_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if not session.get("ok"):
            return redirect(url_for("login", next=request.full_path))
        return fn(*a, **kw)
    return wrapper


@app.context_processor
def _ctx():
    n = 0
    if session.get("ok"):
        try:
            n = con().execute("SELECT COUNT(*) FROM events WHERE kind IN ('unknown','uncertain') "
                              "AND resolved = 0").fetchone()[0]
        except sqlite3.Error:
            n = 0
    return {"pending_review": n, "kind_label": KIND_LABEL, "retention_days": cfg.storage.retention_days}


@app.template_filter("hm")
def _hm(ts):
    return datetime.fromtimestamp(ts).strftime("%H:%M") if ts else "—"


@app.template_filter("dt")
def _dt(ts):
    return datetime.fromtimestamp(ts).strftime("%d/%m/%Y %H:%M") if ts else "—"


@app.template_filter("dia")
def _dia(ts):
    return datetime.fromtimestamp(ts).strftime("%d/%m/%Y") if ts else "—"


@app.template_filter("ddmm")
def _ddmm(d):
    return d.strftime("%d/%m")


@app.template_filter("dur")
def _dur(sec):
    return reports.fmt_dur(sec)


@app.template_filter("file_ok")
def _file_ok(rel):
    return bool(rel) and os.path.isfile(os.path.join(DATA, rel))


def read_status():
    """Status publicado pelo detector. Retorna (status, aviso)."""
    try:
        with open(os.path.join(RUN, "status.json")) as f:
            st = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None, "O serviço de detecção não está rodando."
    if time.time() - st.get("ts", 0) > 30:
        return st, "O serviço de detecção parou de responder."
    if not st.get("camera_online"):
        return st, "A câmera está offline ou inacessível."
    return st, None


def back(default="hoje"):
    ref = request.referrer
    return redirect(ref if ref and ref.startswith(request.host_url) else url_for(default))


# ------------------------------------------------------------------ login
@app.route("/login", methods=["GET", "POST"])
def login():
    ip = request.remote_addr or "?"
    now = time.time()
    if len(_fails) > 500:  # não deixa a tabela de tentativas crescer sem limite
        for k in [k for k, v in _fails.items() if not v or now - v[-1] > 600]:
            _fails.pop(k, None)
    fails = [t for t in _fails.get(ip, []) if now - t < 600]
    _fails[ip] = fails
    if request.method == "POST":
        if len(fails) >= 8:
            flash("Muitas tentativas. Aguarde alguns minutos.", "err")
            return render_template("login.html"), 429
        auth = read_auth()
        user_ok = auth and (not auth.get("username")
                            or request.form.get("usuario", "").strip().lower() == auth["username"].lower())
        if user_ok and check_password_hash(auth["password_hash"], request.form.get("senha", "")):
            session.clear()
            session["ok"] = True
            session.permanent = True
            nxt = request.args.get("next", "")
            return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("hoje"))
        fails.append(now)
        flash("Usuário ou senha incorretos.", "err")
    return render_template("login.html")


@app.route("/sair")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ------------------------------------------------------------------ páginas
@app.route("/")
@login_required
def index():
    return redirect(url_for("hoje"))


@app.route("/hoje")
@login_required
def hoje():
    today = date.today()
    d = reports.parse_date(request.args.get("d"), today)
    t0, t1 = reports.day_bounds(d)
    c = con()
    sess = reports.sessions_between(c, t0, t1)
    events = [dict(r) for r in c.execute(
        "SELECT e.*, m.name, gm.name AS guess_name FROM events e "
        "LEFT JOIN members m ON m.id = e.member_id LEFT JOIN members gm ON gm.id = e.guess_member_id "
        "WHERE e.ts >= ? AND e.ts < ? AND e.kind IN ('entry','unknown','uncertain','after_hours') "
        "ORDER BY e.ts DESC LIMIT 300", (t0, t1))]
    vistos = {}
    for s in sess:
        v = vistos.setdefault(s["member_id"], {"member_id": s["member_id"], "name": s["name"],
                                               "first": s["start_ts"], "last": 0.0, "entries": 0,
                                               "crop": None})
        v["entries"] += 1
        v["last"] = max(v["last"], min(reports.session_end(s), t1))
        v["crop"] = s["crop_path"] or v["crop"]
    st, warn = read_status()
    membros = c.execute("SELECT id, name FROM members ORDER BY name COLLATE NOCASE").fetchall()
    return render_template(
        "hoje.html", d=d, is_today=d == today,
        titulo="Hoje" if d == today else d.strftime("%d/%m/%Y"),
        prev=(d - timedelta(days=1)).isoformat(), next=(d + timedelta(days=1)).isoformat(),
        vistos=sorted(vistos.values(), key=lambda v: v["first"]), events=events, membros=membros,
        n_unknown=sum(1 for e in events if e["kind"] in ("unknown", "uncertain") and not e["member_id"]),
        warn=warn)


@app.route("/eventos/<int:eid>/corrigir", methods=["POST"])
@login_required
def evento_corrigir(eid):
    """O sistema identificou a pessoa errada: move o registro e desfaz o que ele aprendeu."""
    c = con()
    e = c.execute("SELECT * FROM events WHERE id = ? AND kind = 'entry'", (eid,)).fetchone()
    if e is None:
        abort(404)
    new_mid = request.form.get("member_id", type=int)
    if new_mid is None:
        flash("Escolha quem é a pessoa.", "err")
        return back()
    wrong = e["member_id"]
    s = c.execute("SELECT * FROM sessions WHERE id = ?", (e["session_id"],)).fetchone()
    # amostras aprendidas automaticamente durante essa identificação errada
    if s is not None and wrong:
        for smp in c.execute("SELECT * FROM samples WHERE member_id = ? AND source = 'auto' "
                             "AND created_at BETWEEN ? AND ?",
                             (wrong, s["start_ts"] - 5, s["last_seen_ts"] + 5)).fetchall():
            if smp["crop_path"]:
                try:
                    os.remove(os.path.join(DATA, smp["crop_path"]))
                except FileNotFoundError:
                    pass
            c.execute("DELETE FROM samples WHERE id = ?", (smp["id"],))
    emb = None
    if e["crop_path"] and os.path.isfile(os.path.join(DATA, e["crop_path"])):
        emb = e["embedding"]
    if new_mid == 0:  # não é membro: vira um "não identificado" para revisão
        if s is not None:
            c.execute("DELETE FROM sessions WHERE id = ?", (s["id"],))  # apaga o evento junto
        c.execute("INSERT INTO events(ts, kind, score, guess_member_id, snapshot_path, crop_path, "
                  "clip_path, embedding, resolved, details) VALUES(?, 'uncertain', ?, ?, ?, ?, ?, ?, 1, "
                  "'corrigido: não é membro')",
                  (e["ts"], e["score"], wrong, e["snapshot_path"], e["crop_path"], e["clip_path"], emb))
        flash("Corrigido: registrado como pessoa não cadastrada.", "ok")
    else:
        if s is not None:
            c.execute("UPDATE sessions SET member_id = ? WHERE id = ?", (new_mid, s["id"]))
        c.execute("UPDATE events SET member_id = ?, details = 'corrigido' WHERE id = ?", (new_mid, eid))
        if e["crop_path"] and os.path.isfile(os.path.join(DATA, e["crop_path"])):
            dst = os.path.join(member_dir(DATA, new_mid), f"correcao_{eid}.jpg")
            shutil.copy(os.path.join(DATA, e["crop_path"]), dst)
            if emb:
                c.execute("INSERT INTO samples(member_id, source, status, message, crop_path, embedding, "
                          "created_at) VALUES(?, 'revisao', 'ok', 'correção de identificação', ?, ?, ?)",
                          (new_mid, os.path.relpath(dst, DATA), emb, time.time()))
        name = c.execute("SELECT name FROM members WHERE id = ?", (new_mid,)).fetchone()["name"]
        flash(f"Corrigido para {name}. O sistema aprendeu com a correção.", "ok")
    db.bump_gallery(c)
    return back()


@app.route("/ao-vivo")
@login_required
def ao_vivo():
    st, warn = read_status()
    usage = shutil.disk_usage(DATA)
    return render_template("ao_vivo.html", st=st, warn=warn,
                           disk_free=usage.free / 1e9, disk_total=usage.total / 1e9)


@app.route("/ao-vivo.jpg")
@login_required
def live_jpg():
    p = os.path.join(RUN, "live.jpg")
    if not os.path.exists(p):
        abort(404)
    r = send_file(p, mimetype="image/jpeg", max_age=0)
    r.headers["Cache-Control"] = "no-store"
    return r


@app.route("/saude")
def saude():
    """Sem login e sem dados pessoais: só diz se o detector está vivo e vendo a câmera.
    O mini servidor (reserva) consulta isto para saber se precisa assumir."""
    st, warn = read_status()
    vivo = bool(st) and time.time() - st.get("ts", 0) <= 30
    body = {"ok": vivo and bool(st.get("camera_online")), "detector": vivo,
            "camera": bool(st and st.get("camera_online")), "perfil": (st or {}).get("hardware")}
    return jsonify(body), (200 if body["ok"] else 503)


@app.route("/api/status")
@login_required
def api_status():
    st, warn = read_status()
    return jsonify({"status": st, "warn": warn})


@app.route("/relatorio")
@login_required
def relatorio():
    today = date.today()
    d1 = reports.parse_date(request.args.get("fim"), today)
    d0 = reports.parse_date(request.args.get("inicio"), d1 - timedelta(days=6))
    if d0 > d1:
        d0, d1 = d1, d0
    t0, t1 = reports.day_bounds(d0)[0], reports.day_bounds(d1)[1]
    c = con()
    sess = reports.sessions_between(c, t0, t1)
    days, mat = (reports.first_seen_matrix(c, sess, d0, d1) if (d1 - d0).days <= 31 else (None, None))
    return render_template("relatorio.html", d0=d0, d1=d1, sessoes=list(reversed(sess)),
                           resumo=reports.summarize(c, sess, t0, t1), days=days, mat=mat)


@app.route("/relatorio.csv")
@login_required
def relatorio_csv():
    today = date.today()
    d1 = reports.parse_date(request.args.get("fim"), today)
    d0 = reports.parse_date(request.args.get("inicio"), d1 - timedelta(days=6))
    t0, t1 = reports.day_bounds(min(d0, d1))[0], reports.day_bounds(max(d0, d1))[1]
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["data", "horario", "membro", "certeza"])
    for s in reports.sessions_between(con(), t0, t1):
        w.writerow([datetime.fromtimestamp(s["start_ts"]).strftime("%d/%m/%Y"),
                    datetime.fromtimestamp(s["start_ts"]).strftime("%H:%M"), s["name"],
                    f"{s['best_score']:.2f}" if s["best_score"] is not None else ""])
    name = f"identificacoes_{min(d0, d1).isoformat()}_{max(d0, d1).isoformat()}.csv"
    return Response("﻿" + buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={name}"})


@app.route("/sessoes/<int:sid>/excluir", methods=["POST"])
@login_required
def sessao_excluir(sid):
    con().execute("DELETE FROM sessions WHERE id = ?", (sid,))
    flash("Identificação excluída.", "ok")
    return back()


# ------------------------------------------------------------------ membros
def save_uploads(c, mid):
    n, bad = 0, 0
    for f in request.files.getlist("fotos"):
        if not f or not f.filename:
            continue
        ext = os.path.splitext(f.filename)[1].lower()
        if ext not in IMG_EXT:
            bad += 1
            continue
        abs_path, rel = new_photo_path(DATA, mid, ext)
        f.save(abs_path)
        insert_pending(c, mid, rel, "foto")
        n += 1
    if bad:
        flash(f"{bad} arquivo(s) ignorado(s): envie JPG ou PNG.", "warn")
    return n


@app.route("/membros", methods=["GET", "POST"])
@login_required
def membros():
    c = con()
    if request.method == "POST":
        name = request.form.get("nome", "").strip()
        if not name:
            flash("Informe o nome.", "err")
            return redirect(url_for("membros"))
        try:
            mid = c.execute("INSERT INTO members(name, created_at) VALUES(?, ?)",
                            (name, time.time())).lastrowid
        except sqlite3.IntegrityError:
            flash("Já existe um membro com esse nome.", "err")
            return redirect(url_for("membros"))
        n = save_uploads(c, mid)
        flash(f"{name} cadastrado(a). " + (f"{n} foto(s) em processamento." if n else
              "Agora adicione fotos ou capture pela câmera."), "ok")
        return redirect(url_for("membros_detalhe", mid=mid))
    rows = c.execute(
        "SELECT m.*, "
        "(SELECT COUNT(*) FROM samples s WHERE s.member_id = m.id AND s.status = 'ok') AS n_ok, "
        "(SELECT COUNT(*) FROM samples s WHERE s.member_id = m.id AND s.status = 'pendente') AS n_pend, "
        "(SELECT MAX(last_seen_ts) FROM sessions x WHERE x.member_id = m.id) AS last_seen, "
        "(SELECT crop_path FROM samples s WHERE s.member_id = m.id AND s.status = 'ok' "
        "   ORDER BY (s.source = 'foto') DESC, s.id LIMIT 1) AS thumb "
        "FROM members m ORDER BY m.active DESC, m.name COLLATE NOCASE").fetchall()
    present = {r["member_id"] for r in c.execute("SELECT member_id FROM sessions WHERE end_ts IS NULL")}
    return render_template("membros.html", membros=rows, present=present)


@app.route("/membros/<int:mid>")
@login_required
def membros_detalhe(mid):
    c = con()
    m = c.execute("SELECT * FROM members WHERE id = ?", (mid,)).fetchone()
    if m is None:
        abort(404)
    samples = c.execute("SELECT * FROM samples WHERE member_id = ? ORDER BY id DESC", (mid,)).fetchall()
    cap = c.execute("SELECT * FROM capture_requests WHERE member_id = ? ORDER BY id DESC LIMIT 1",
                    (mid,)).fetchone()
    t1 = time.time()
    t0 = t1 - cfg.storage.retention_days * 86400
    sess = reports.sessions_between(c, t0, t1, mid)
    resumo = reports.summarize(c, sess, t0, t1, include_all=False)
    busy = any(s["status"] == "pendente" for s in samples) or (cap is not None and cap["status"] == "ativo")
    return render_template("membro.html", m=m, samples=samples, cap=cap, busy=busy,
                           sessoes=list(reversed(sess))[:40], resumo=resumo[0] if resumo else None)


@app.route("/membros/<int:mid>/fotos", methods=["POST"])
@login_required
def membros_fotos(mid):
    n = save_uploads(con(), mid)
    if n:
        flash(f"{n} foto(s) em processamento.", "ok")
    return redirect(url_for("membros_detalhe", mid=mid))


@app.route("/membros/<int:mid>/capturar", methods=["POST"])
@login_required
def membros_capturar(mid):
    c = con()
    now = time.time()
    c.execute("UPDATE capture_requests SET status = 'cancelado' WHERE status = 'ativo'")
    c.execute("INSERT INTO capture_requests(member_id, created_at, until_ts, target) VALUES(?, ?, ?, ?)",
              (mid, now, now + 90, 12))
    flash("Captura iniciada por 90 s: a pessoa deve ficar sozinha na frente da câmera, olhar "
          "para ela e virar o rosto devagar.", "ok")
    return redirect(url_for("membros_detalhe", mid=mid))


@app.route("/membros/<int:mid>/capturar/cancelar", methods=["POST"])
@login_required
def membros_capturar_cancelar(mid):
    con().execute("UPDATE capture_requests SET status = 'cancelado' WHERE member_id = ? AND status = 'ativo'",
                  (mid,))
    return redirect(url_for("membros_detalhe", mid=mid))


@app.route("/membros/<int:mid>/editar", methods=["POST"])
@login_required
def membros_editar(mid):
    c = con()
    name = request.form.get("nome", "").strip()
    try:
        c.execute("UPDATE members SET name = ?, notes = ?, active = ? WHERE id = ?",
                  (name, request.form.get("notas", "").strip(),
                   1 if request.form.get("ativo") else 0, mid))
    except sqlite3.IntegrityError:
        flash("Já existe um membro com esse nome.", "err")
        return redirect(url_for("membros_detalhe", mid=mid))
    db.bump_gallery(c)
    flash("Dados salvos.", "ok")
    return redirect(url_for("membros_detalhe", mid=mid))


@app.route("/membros/<int:mid>/excluir", methods=["POST"])
@login_required
def membros_excluir(mid):
    c = con()
    c.execute("DELETE FROM members WHERE id = ?", (mid,))
    c.execute("DELETE FROM meta WHERE key = ?", (f"greeted:{mid}",))
    shutil.rmtree(os.path.join(DATA, "members", str(mid)), ignore_errors=True)
    db.bump_gallery(c)
    flash("Membro excluído, com fotos, amostras e histórico de presença.", "ok")
    return redirect(url_for("membros"))


@app.route("/amostras/<int:sid>/excluir", methods=["POST"])
@login_required
def amostra_excluir(sid):
    c = con()
    s = c.execute("SELECT * FROM samples WHERE id = ?", (sid,)).fetchone()
    if s is None:
        abort(404)
    for p in (s["image_path"], s["crop_path"]):
        if p:
            try:
                os.remove(os.path.join(DATA, p))
            except FileNotFoundError:
                pass
    c.execute("DELETE FROM samples WHERE id = ?", (sid,))
    db.bump_gallery(c)
    return redirect(url_for("membros_detalhe", mid=s["member_id"]))


# ------------------------------------------------------------------ revisão
@app.route("/revisao")
@login_required
def revisao():
    c = con()
    rows = c.execute(
        "SELECT e.*, gm.name AS guess_name FROM events e LEFT JOIN members gm ON gm.id = e.guess_member_id "
        "WHERE e.kind IN ('unknown','uncertain') AND e.resolved = 0 ORDER BY e.ts DESC LIMIT 150").fetchall()
    members = c.execute("SELECT id, name FROM members WHERE active = 1 ORDER BY name COLLATE NOCASE").fetchall()
    grupos = c.execute(
        "SELECT e.guess_member_id AS mid, gm.name, COUNT(*) AS n, ROUND(AVG(e.score), 2) AS media "
        "FROM events e JOIN members gm ON gm.id = e.guess_member_id "
        "WHERE e.kind IN ('unknown','uncertain') AND e.resolved = 0 AND e.score >= 0.15 "
        "GROUP BY e.guess_member_id HAVING COUNT(*) >= 2 ORDER BY n DESC").fetchall()
    return render_template("revisao.html", eventos=rows, membros=members, grupos=grupos)


@app.route("/revisao/atribuir-palpite", methods=["POST"])
@login_required
def revisao_atribuir_palpite():
    """Atribui de uma vez todos os não identificados cujo palpite é o membro escolhido."""
    import numpy as np
    c = con()
    mid = request.form.get("member_id", type=int)
    m = c.execute("SELECT name FROM members WHERE id = ?", (mid,)).fetchone()
    if m is None:
        abort(404)
    rows = c.execute("SELECT * FROM events WHERE kind IN ('unknown','uncertain') AND resolved = 0 "
                     "AND guess_member_id = ? AND score >= 0.15 ORDER BY score DESC", (mid,)).fetchall()
    kept, added = [], 0
    for e in rows:
        if e["embedding"] and e["crop_path"] and os.path.isfile(os.path.join(DATA, e["crop_path"])):
            emb = db.blob_to_emb(e["embedding"])
            if all(float(k @ emb) < 0.90 for k in kept):  # evita amostras quase iguais
                dst = os.path.join(member_dir(DATA, mid), f"revisao_{e['id']}.jpg")
                shutil.copy(os.path.join(DATA, e["crop_path"]), dst)
                c.execute("INSERT INTO samples(member_id, source, status, message, crop_path, embedding, "
                          "created_at) VALUES(?, 'revisao', 'ok', 'atribuída em lote na revisão', ?, ?, ?)",
                          (mid, os.path.relpath(dst, DATA), e["embedding"], time.time()))
                kept.append(np.asarray(emb))
                added += 1
        c.execute("UPDATE events SET resolved = 1, member_id = ? WHERE id = ?", (mid, e["id"]))
    db.bump_gallery(c)
    flash(f"{len(rows)} rostos atribuídos a {m['name']} ({added} viraram amostras novas).", "ok")
    return redirect(url_for("revisao"))


@app.route("/revisao/<int:eid>/atribuir", methods=["POST"])
@login_required
def revisao_atribuir(eid):
    c = con()
    e = c.execute("SELECT * FROM events WHERE id = ?", (eid,)).fetchone()
    if e is None:
        abort(404)
    mid = request.form.get("member_id", type=int)
    new_name = request.form.get("novo_nome", "").strip()
    if new_name:
        try:
            mid = c.execute("INSERT INTO members(name, created_at) VALUES(?, ?)",
                            (new_name, time.time())).lastrowid
        except sqlite3.IntegrityError:
            mid = c.execute("SELECT id FROM members WHERE name = ?", (new_name,)).fetchone()["id"]
    if not mid:
        flash("Escolha um membro.", "err")
        return back("revisao")
    rel = None
    if e["crop_path"] and os.path.isfile(os.path.join(DATA, e["crop_path"])):
        dst = os.path.join(member_dir(DATA, mid), f"revisao_{eid}.jpg")
        shutil.copy(os.path.join(DATA, e["crop_path"]), dst)
        rel = os.path.relpath(dst, DATA)
    if e["embedding"]:
        c.execute("INSERT INTO samples(member_id, source, status, message, crop_path, embedding, created_at) "
                  "VALUES(?, 'revisao', 'ok', 'atribuída na revisão', ?, ?, ?)",
                  (mid, rel, e["embedding"], time.time()))
    c.execute("UPDATE events SET resolved = 1, member_id = ? WHERE id = ?", (mid, eid))
    db.bump_gallery(c)
    name = c.execute("SELECT name FROM members WHERE id = ?", (mid,)).fetchone()["name"]
    flash(f"Rosto atribuído a {name}; o reconhecimento dessa pessoa fica mais preciso.", "ok")
    return back("revisao")


@app.route("/revisao/<int:eid>/ignorar", methods=["POST"])
@login_required
def revisao_ignorar(eid):
    con().execute("UPDATE events SET resolved = 1 WHERE id = ?", (eid,))
    return back("revisao")


@app.route("/revisao/ignorar-todos", methods=["POST"])
@login_required
def revisao_ignorar_todos():
    con().execute("UPDATE events SET resolved = 1 WHERE kind IN ('unknown','uncertain') AND resolved = 0")
    return redirect(url_for("revisao"))


@app.route("/eventos/<int:eid>/excluir", methods=["POST"])
@login_required
def evento_excluir(eid):
    c = con()
    e = c.execute("SELECT * FROM events WHERE id = ?", (eid,)).fetchone()
    if e is not None:
        for p in (e["snapshot_path"], e["crop_path"], e["clip_path"]):
            if p:
                try:
                    os.remove(os.path.join(DATA, p))
                except FileNotFoundError:
                    pass
        c.execute("DELETE FROM events WHERE id = ?", (eid,))
    return back("revisao")


@app.route("/media/<path:p>")
@login_required
def media(p):
    if p.split("/", 1)[0] not in MEDIA_DIRS:
        abort(404)
    return send_from_directory(DATA, p, max_age=3600)


def main():
    from waitress import serve
    if read_auth() is None:
        raise SystemExit(f"Senha da interface não definida ({AUTH_FILE}). Rode: visao-cli senha")
    serve(app, host=cfg.web.host, port=int(cfg.web.port), threads=4, ident="visao")


if __name__ == "__main__":
    main()
