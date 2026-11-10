"""Banco SQLite (modo WAL) compartilhado entre o detector, a web e a CLI."""
import sqlite3

import numpy as np

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS members(
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    notes TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL
);

-- Amostras de rosto de cada membro (foto enviada, captura pela câmera,
-- revisão manual ou aprendizado automático).
CREATE TABLE IF NOT EXISTS samples(
    id INTEGER PRIMARY KEY,
    member_id INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    source TEXT NOT NULL,            -- foto | camera | revisao | auto
    status TEXT NOT NULL,            -- pendente | ok | erro
    message TEXT NOT NULL DEFAULT '',
    image_path TEXT,
    crop_path TEXT,
    embedding BLOB,
    quality REAL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS samples_member ON samples(member_id);
CREATE INDEX IF NOT EXISTS samples_status ON samples(status);

-- Uma sessão = período contínuo em que o membro esteve no laboratório.
CREATE TABLE IF NOT EXISTS sessions(
    id INTEGER PRIMARY KEY,
    member_id INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    start_ts REAL NOT NULL,
    last_seen_ts REAL NOT NULL,
    end_ts REAL,                     -- NULL enquanto a pessoa está presente
    sightings INTEGER NOT NULL DEFAULT 1,
    best_score REAL,
    snapshot_path TEXT,
    crop_path TEXT,
    clip_path TEXT
);
CREATE INDEX IF NOT EXISTS sessions_start ON sessions(start_ts);
CREATE INDEX IF NOT EXISTS sessions_member ON sessions(member_id);

CREATE TABLE IF NOT EXISTS events(
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    kind TEXT NOT NULL,              -- entry | exit | unknown | uncertain
    member_id INTEGER REFERENCES members(id) ON DELETE SET NULL,
    session_id INTEGER REFERENCES sessions(id) ON DELETE CASCADE,
    score REAL,
    guess_member_id INTEGER REFERENCES members(id) ON DELETE SET NULL,
    snapshot_path TEXT,
    crop_path TEXT,
    clip_path TEXT,
    embedding BLOB,
    resolved INTEGER NOT NULL DEFAULT 0,
    details TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS events_kind ON events(kind, resolved);

CREATE TABLE IF NOT EXISTS capture_requests(
    id INTEGER PRIMARY KEY,
    member_id INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    created_at REAL NOT NULL,
    until_ts REAL NOT NULL,
    target INTEGER NOT NULL,
    captured INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'ativo'   -- ativo | concluido | expirado | cancelado
);
"""


def connect(path):
    con = sqlite3.connect(path, timeout=30, isolation_level=None, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.executescript(SCHEMA)
    return con


def connect_raw(path):
    """Conexão simples, sem criar o schema (usada como destino de backup)."""
    return sqlite3.connect(path, timeout=30)


def get_meta(con, key, default=None):
    row = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def bump_gallery(con):
    """Avisa o detector que as amostras mudaram e a galeria deve ser recarregada."""
    con.execute(
        "INSERT INTO meta(key, value) VALUES('gallery_version', '1') "
        "ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1"
    )


def emb_to_blob(emb):
    return np.asarray(emb, dtype=np.float32).reshape(-1).tobytes()


def blob_to_emb(blob):
    return np.frombuffer(blob, dtype=np.float32)
