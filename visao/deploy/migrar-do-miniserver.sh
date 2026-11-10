#!/usr/bin/env bash
# Traz do mini servidor para o PC: cadastros de rosto, histórico, frases prontas e o
# config.toml (com as senhas da câmera). Roda numa máquina que acessa os dois por SSH:
#   bash visao/deploy/migrar-do-miniserver.sh <ssh-do-pc> [ssh-do-miniserver]
# ex.: bash visao/deploy/migrar-do-miniserver.sh willbert miniserver-root
#
# O PC precisa ter rodado o install-pc.sh antes. O mini servidor não perde nada: só é lido.
set -euo pipefail
PC="${1:?uso: $0 <ssh-do-pc> [ssh-do-miniserver]}"
MINI="${2:-miniserver-root}"
B=/opt/visao

echo "[1/4] cópia consistente do banco no mini servidor (sem parar o detector)"
ssh "$MINI" "python3 -c \"
import sqlite3
src = sqlite3.connect('file:$B/data/visao.db?mode=ro', uri=True)
dst = sqlite3.connect('$B/data/migracao.db')
src.backup(dst); dst.close(); src.close()
print('  banco copiado')\""

echo "[2/4] enviando cadastros, histórico e configuração para o PC ($PC)"
ssh "$MINI" "tar -C $B -cf - config.toml data/migracao.db data/web_auth.json data/members data/events \
    data/tts 2>/dev/null" | ssh "$PC" "sudo mkdir -p $B/migracao && sudo tar -C $B/migracao -xf -"
ssh "$MINI" "rm -f $B/data/migracao.db"

echo "[3/4] instalando no PC"
ssh "$PC" "sudo bash -s" <<EOF
set -e
systemctl stop visao-detector visao-web 2>/dev/null || true
M=$B/migracao
[ -f $B/config.toml ] && cp $B/config.toml $B/config.toml.antes-da-migracao
cp \$M/config.toml $B/config.toml
mkdir -p $B/data
rm -f $B/data/visao.db $B/data/visao.db-wal $B/data/visao.db-shm
mv \$M/data/migracao.db $B/data/visao.db
cp -a \$M/data/web_auth.json $B/data/ 2>/dev/null || true
rsync -a \$M/data/members \$M/data/events $B/data/
rsync -a \$M/data/tts/ $B/data/tts/ 2>/dev/null || true
rm -rf \$M
chown root:visao $B/config.toml && chmod 640 $B/config.toml
chown -R visao:visao $B/data
# ajustes da TV Box que não fazem sentido no PC: o perfil do hardware decide
python3 - <<'PY'
import re
p = "$B/config.toml"
s = open(p).read()
for k in ("det_width", "frame_every", "max_rec_per_cycle", "threads", "classic_engine"):
    s = re.sub(rf"(?m)^\s*{k}\s*=.*\n", "", s)
open(p, "w").write(s)
PY
visao-cli go2rtc-config --saida $B/go2rtc.yaml >/dev/null
chown root:visao $B/go2rtc.yaml && chmod 640 $B/go2rtc.yaml
systemctl restart visao-go2rtc visao-detector visao-web
EOF

echo "[4/4] conferindo"
sleep 15
ssh "$PC" "visao-cli status | head -20; curl -s http://127.0.0.1:8080/saude; echo"
echo
echo "Migração feita. Para o mini servidor virar reserva (só assume se o PC cair):"
echo "  scp -r visao/deploy/reserva $MINI:/tmp/ && ssh $MINI 'bash /tmp/reserva/instalar.sh http://<ip-tailscale-do-pc>:8080'"
