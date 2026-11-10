#!/bin/bash
# Desfaz o otimizar.sh do lab2.
set -uo pipefail
[ "$(id -u)" = 0 ] || { echo "rode com sudo"; exit 1; }
EST=/etc/willbert-otimizacao
systemctl disable --now cpu-desempenho.service 2>/dev/null; rm -f /etc/systemd/system/cpu-desempenho.service
rm -f /etc/systemd/system/visao-*.service.d/prioridade.conf /etc/systemd/system/ollama.service.d/willbert.conf
[ "$(cat $EST/ollama.antes 2>/dev/null)" = enabled ] && systemctl enable ollama
while read -r u; do [ -n "$u" ] && systemctl enable --now "$u"; done < "$EST/servicos.antes" 2>/dev/null
rm -f /etc/sysctl.d/90-willbert.conf; sysctl -q -w vm.swappiness="$(cat $EST/swappiness.antes 2>/dev/null || echo 60)"
rm -f /etc/apt/apt.conf.d/51willbert-nvidia
if [ -s "$EST/snap.antes" ]; then snap set system refresh.timer="$(cat $EST/snap.antes)"; else snap unset system refresh.timer 2>/dev/null; fi
rm -f /etc/sudoers.d/willbert /usr/local/sbin/willbert-controle
systemctl daemon-reload
echo "desfeito (o Willbert continua instalado; para ligá-lo no boot: systemctl enable visao-detector visao-go2rtc)"
