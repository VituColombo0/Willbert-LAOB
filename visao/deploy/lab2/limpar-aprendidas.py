"""Apaga as amostras que o Willbert aprendeu sozinho (source='auto'), deixando só as fotos
cadastradas. Faz antes uma cópia do banco (visao.db.antes-limpeza-<data>) para poder voltar.
    sudo /opt/visao/venv/bin/python limpar-aprendidas.py
Uso: depois que ele aprendeu o rosto errado de alguém (ex.: Victor guardado como Lucas)."""
import os
import shutil
import sqlite3
import time

DATA = "/opt/visao/data"
DB = os.path.join(DATA, "visao.db")
copia = f"{DB}.antes-limpeza-{time.strftime('%Y%m%d-%H%M%S')}"
shutil.copy2(DB, copia)
con = sqlite3.connect(DB)
linhas = con.execute("SELECT s.id, m.name, s.crop_path FROM samples s JOIN members m ON m.id = s.member_id "
                     "WHERE s.source = 'auto'").fetchall()
por_membro = {}
for sid, nome, crop in linhas:
    por_membro[nome] = por_membro.get(nome, 0) + 1
    if crop:
        try:
            os.remove(os.path.join(DATA, crop))
        except FileNotFoundError:
            pass
con.execute("DELETE FROM samples WHERE source = 'auto'")
con.commit()
print("apagadas:", por_membro or "nenhuma")
for nome, n in con.execute("SELECT m.name, COUNT(s.id) FROM members m LEFT JOIN samples s ON s.member_id = m.id "
                           "AND s.status = 'ok' GROUP BY m.id ORDER BY m.name"):
    print(f"  {nome}: {n} amostra(s) cadastrada(s)")
print("cópia do banco antes da limpeza:", copia)
