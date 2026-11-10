#!/bin/bash
# Atalhos do Willbert para o usuário do lab2 (sem sudo): comando "willbert" e a pasta
# "6 - Willbert" com o manual na Área de Trabalho.   bash instalar-atalhos.sh
set -euo pipefail
DIR=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$HOME/.local/bin"
install -m 755 "$DIR/willbert" "$HOME/.local/bin/willbert"
MESA=$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop"); [ "$MESA" = "$HOME" ] && MESA=$HOME/Desktop
mkdir -p "$MESA/6 - Willbert"
cp "$DIR/area-de-trabalho/6-willbert.txt" "$MESA/6 - Willbert/COMO USAR.txt"
echo "ok: comando willbert e pasta '6 - Willbert' na Área de Trabalho"
