#!/bin/bash
# Desinstala o Ollama do laob (o Willbert não usa IA desde 2026-11-11; pedido do Victor em 2026-11-13).
#   sudo bash ~/Willbert/visao/deploy/laob/remover-ollama.sh
# Para voltar a ter: instalar de novo pelo site oficial e baixar o modelo (não recomendado neste PC).
set -uo pipefail
[ "$(id -u)" = 0 ] || { echo "rode com sudo"; exit 1; }
U=${SUDO_USER:-laob}; UH=$(getent passwd "$U" | cut -d: -f6)
antes=$(du -scm /usr/local/bin/ollama /usr/local/lib/ollama /usr/share/ollama "$UH/.ollama" 2>/dev/null | tail -1 | cut -f1)
systemctl disable --now ollama >/dev/null 2>&1 || true
rm -f /etc/systemd/system/ollama.service /etc/systemd/system/multi-user.target.wants/ollama.service
rm -rf /etc/systemd/system/ollama.service.d
systemctl daemon-reload
rm -f /usr/local/bin/ollama
rm -rf /usr/local/lib/ollama /usr/share/ollama "$UH/.ollama"
getent passwd ollama >/dev/null && userdel ollama 2>/dev/null
getent group ollama >/dev/null && groupdel ollama 2>/dev/null
command -v ollama >/dev/null && echo "AVISO: ainda existe um ollama em $(command -v ollama)" || echo "Ollama removido: ${antes:-?} MB liberados"
