"""Traz de volta as amostras aprendidas sozinho (source='auto') de uma cópia feita pelo
limpar-aprendidas.py. As miniaturas (crop) apagadas não voltam; o reconhecimento só usa o
vetor do rosto, que está na cópia.
    sudo /opt/visao/venv/bin/python voltar-aprendidas.py [cópia]   (padrão: a mais recente)"""
import glob
import os
import sqlite3
import sys

DB = "/opt/visao/data/visao.db"
copias = sorted(c for c in glob.glob(DB + ".antes-limpeza-*") if not c.endswith(("-wal", "-shm", "-journal")))
origem = sys.argv[1] if len(sys.argv) > 1 else (copias[-1] if copias else None)
if not origem or not os.path.exists(origem):
    sys.exit("nenhuma cópia antes-limpeza encontrada")
con = sqlite3.connect(DB)
con.execute("ATTACH DATABASE ? AS copia", (origem,))
if not con.execute("SELECT 1 FROM copia.sqlite_master WHERE name = 'samples'").fetchone():
    sys.exit(f"a cópia {os.path.basename(origem)} não tem as amostras (feita só do arquivo principal, sem o WAL)")
linhas = con.execute("SELECT c.member_id, m.name, c.status, c.message, c.embedding, c.quality, c.created_at "
                     "FROM copia.samples c JOIN members m ON m.id = c.member_id WHERE c.source = 'auto' "
                     "AND NOT EXISTS (SELECT 1 FROM samples s WHERE s.source = 'auto' AND s.member_id = c.member_id "
                     "AND s.created_at = c.created_at)").fetchall()
por = {}
for mid, nome, st, msg, emb, q, ts in linhas:
    con.execute("INSERT INTO samples(member_id, source, status, message, embedding, quality, created_at) "
                "VALUES(?, 'auto', ?, ?, ?, ?, ?)", (mid, st, msg + " (restaurada)", emb, q, ts))
    por[nome] = por.get(nome, 0) + 1
con.commit()
print("restauradas de", os.path.basename(origem), ":", por or "nenhuma")
