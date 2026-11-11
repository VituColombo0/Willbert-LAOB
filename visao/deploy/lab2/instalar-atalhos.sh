#!/bin/bash
# Atalhos do Willbert para o usuário do lab2 (sem sudo): comandos "willbert" e "willbert-janela" e a pasta
# "6 - Willbert" com o manual na Área de Trabalho.   bash instalar-atalhos.sh
set -euo pipefail
DIR=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$HOME/.local/bin"
install -m 755 "$DIR/willbert" "$HOME/.local/bin/willbert"
install -m 755 "$DIR/willbert-janela" "$HOME/.local/bin/willbert-janela"
MESA=$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop"); [ "$MESA" = "$HOME" ] && MESA=$HOME/Desktop
mkdir -p "$MESA/6 - Willbert"
cp "$DIR/area-de-trabalho/6-willbert.txt" "$MESA/6 - Willbert/COMO USAR.txt"
rm -f "$MESA/6 - Willbert"/*.desktop
for at in "$DIR"/area-de-trabalho/*.desktop; do   # atalhos de dois cliques
  destino="$MESA/6 - Willbert/$(basename "$at")"
  sed "s#/home/laob#$HOME#g" "$at" > "$destino"
  chmod +x "$destino"
  gio set "$destino" metadata::trusted true 2>/dev/null || true
done
echo "ok: comando willbert e pasta '6 - Willbert' na Área de Trabalho"
