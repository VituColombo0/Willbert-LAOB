#!/bin/bash
# Comandos do Willbert para o usuário do laob (sem sudo): "willbert" e "willbert-janela".
#   bash instalar-atalhos.sh
# Desde 13/11/2026 o Willbert é ligado/desligado pelo painel da caixa de areia (http://localhost:8090);
# a pasta "6 - Willbert" da Área de Trabalho saiu (este script a remove se ainda existir).
# O manual continua em area-de-trabalho/6-willbert.txt (texto de referência).
set -euo pipefail
DIR=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$HOME/.local/bin"
install -m 755 "$DIR/willbert" "$HOME/.local/bin/willbert"
install -m 755 "$DIR/willbert-janela" "$HOME/.local/bin/willbert-janela"
MESA=$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop"); [ "$MESA" = "$HOME" ] && MESA=$HOME/Desktop
if [ -d "$MESA/6 - Willbert" ]; then
  rm -f "$MESA/6 - Willbert"/*.desktop "$MESA/6 - Willbert/COMO USAR.txt"
  rmdir "$MESA/6 - Willbert" 2>/dev/null || echo "  ficou '$MESA/6 - Willbert' (tem outros arquivos)"
fi
echo "ok: comandos willbert e willbert-janela (liga/desliga também pelo painel da caixa de areia)"
