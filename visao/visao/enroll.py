"""Cadastro de amostras de rosto (fotos enviadas pela web ou pela CLI)."""
import logging
import os
import time
import uuid

import cv2

from . import db

log = logging.getLogger("visao.enroll")


def member_dir(data_dir, member_id):
    d = os.path.join(data_dir, "members", str(member_id))
    os.makedirs(d, exist_ok=True)
    return d


def new_photo_path(data_dir, member_id, ext):
    """Caminho (absoluto, relativo ao data_dir) para guardar uma foto nova."""
    ext = ext.lower() if ext.startswith(".") else "." + ext.lower()
    name = f"foto_{time.strftime('%Y%m%d-%H%M%S')}_{uuid.uuid4().hex[:6]}{ext}"
    abs_path = os.path.join(member_dir(data_dir, member_id), name)
    return abs_path, os.path.relpath(abs_path, data_dir)


def insert_pending(con, member_id, image_rel, source="foto"):
    cur = con.execute(
        "INSERT INTO samples(member_id, source, status, image_path, created_at) "
        "VALUES(?, ?, 'pendente', ?, ?)", (member_id, source, image_rel, time.time()))
    return cur.lastrowid


def process_sample(con, engine, data_dir, row):
    """Detecta o rosto na foto, gera o embedding e marca a amostra como ok/erro."""
    sid, mid = row["id"], row["member_id"]
    img = cv2.imread(os.path.join(data_dir, row["image_path"] or ""))
    if img is None:
        con.execute("UPDATE samples SET status='erro', message=? WHERE id=?",
                    ("não foi possível ler a imagem (use JPG ou PNG)", sid))
        return False
    try:
        aligned, emb, info = engine.enroll_image(img)
    except ValueError as e:
        con.execute("UPDATE samples SET status='erro', message=? WHERE id=?", (str(e), sid))
        return False
    crop_abs = os.path.join(member_dir(data_dir, mid), f"amostra_{sid}_rosto.jpg")
    cv2.imwrite(crop_abs, aligned)
    msg = f"rosto de {int(info['w'])} px"
    # Aviso se a foto parece muito com outro membro (provável foto trocada).
    best, best_name = -1.0, None
    for r in con.execute(
            "SELECT s.embedding, m.name FROM samples s JOIN members m ON m.id = s.member_id "
            "WHERE s.status = 'ok' AND s.member_id <> ? AND s.embedding IS NOT NULL", (mid,)):
        sim = float(db.blob_to_emb(r["embedding"]) @ emb)
        if sim > best:
            best, best_name = sim, r["name"]
    if best >= 0.45:
        msg += f" — atenção: muito parecido com {best_name} ({best:.2f})"
    con.execute(
        "UPDATE samples SET status='ok', message=?, crop_path=?, embedding=?, quality=? WHERE id=?",
        (msg, os.path.relpath(crop_abs, data_dir), db.emb_to_blob(emb), info["sharp"], sid))
    return True


def process_pending(con, engine, data_dir, limit=3):
    rows = con.execute("SELECT * FROM samples WHERE status='pendente' ORDER BY id LIMIT ?",
                       (limit,)).fetchall()
    for r in rows:
        ok = process_sample(con, engine, data_dir, r)
        log.info("amostra %s do membro %s: %s", r["id"], r["member_id"], "ok" if ok else "erro")
    if rows:
        db.bump_gallery(con)
    return len(rows)
