#!/bin/sh
# Instala o modo reserva no mini servidor (rodar como root na TV Box):
#   sh instalar.sh http://<ip-tailscale-do-pc>:8080
# Desfazer: systemctl disable --now visao-reserva.timer && systemctl enable --now visao-detector
set -e
URL="${1:?uso: $0 http://<ip-do-pc>:8080}"
DIR="$(cd "$(dirname "$0")" && pwd)"
install -m 755 "$DIR/visao-reserva.sh" /usr/local/sbin/visao-reserva
printf 'PC_URL=%s\nLIMITE=3\n' "$URL" > /etc/visao-reserva.conf
cat > /etc/systemd/system/visao-reserva.service <<'EOF'
[Unit]
Description=Visão reserva: assume a câmera se o Willbert (PC) cair
[Service]
Type=oneshot
ExecStart=/usr/local/sbin/visao-reserva
EOF
cat > /etc/systemd/system/visao-reserva.timer <<'EOF'
[Unit]
Description=Confere o Willbert (PC) a cada 30 s
[Timer]
OnBootSec=60
OnUnitActiveSec=30
AccuracySec=5
[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
# o detector da TV Box passa a ser ligado só pela reserva (não sobe sozinho no boot)
systemctl disable visao-detector visao-go2rtc >/dev/null 2>&1 || true
systemctl enable --now visao-reserva.timer
/usr/local/sbin/visao-reserva
echo "reserva instalada (PC: $URL). Detector da TV Box: $(systemctl is-active visao-detector)"
