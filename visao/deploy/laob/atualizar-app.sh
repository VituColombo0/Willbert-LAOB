#!/bin/bash
# Atualiza no laob o Willbert já instalado com a cópia de ~/Willbert (repositório). Rodar com sudo:
#   sudo bash ~/Willbert/visao/deploy/laob/atualizar-app.sh
# Copia o app, os modelos que faltarem, o willbert-controle e os atalhos do usuário; reinicia o detector.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "rode com sudo"; exit 1; }
R=$(cd "$(dirname "$0")/../.." && pwd)      # .../Willbert/visao
U=${SUDO_USER:-laob}; UH=$(getent passwd "$U" | cut -d: -f6)
rsync -a --delete --exclude __pycache__ --chown=root:root --chmod=D755,F644 "$R/visao/" /opt/visao/app/visao/
for m in u2net_human_seg.onnx; do   # recorte de fundo da foto da areia (o mesmo do Projetor-LAOB)
  [ -s "/opt/visao/models/$m" ] || { [ -s "$UH/SARndbox-rosto/modelos/$m" ] && install -o root -g root -m 644 "$UH/SARndbox-rosto/modelos/$m" /opt/visao/models/; }
done
[ -s /opt/visao/models/palm_detection_mediapipe_2023feb.onnx ] || echo "AVISO: falta o modelo do gesto (palm_detection_mediapipe_2023feb.onnx)"
# 2026-11-13: até 50 amostras por membro, espalhadas no tempo. Um enrich_until/auto_enrich_max
# antigo escrito no config.toml seguraria o limite velho: vira comentário (cópia guardada; para
# desfazer: sudo cp -p /opt/visao/config.toml.antes-50 /opt/visao/config.toml).
C=/opt/visao/config.toml
if [ -f "$C" ] && grep -qE '^[[:space:]]*(enrich_until|auto_enrich_max)[[:space:]]*=' "$C"; then
  [ -f "$C.antes-50" ] || cp -p "$C" "$C.antes-50"
  sed -i -E 's/^([[:space:]]*)(enrich_until|auto_enrich_max)([[:space:]]*=.*)$/\1# \2\3   # antigo: o padrão agora é 50/' "$C"
  echo "config: limite antigo de amostras desativado (cópia em $C.antes-50)"
fi
install -o root -g root -m 755 "$R/deploy/laob/willbert-controle" /usr/local/sbin/willbert-controle
for c in willbert willbert-janela; do install -o "$U" -g "$U" -m 755 "$R/deploy/laob/$c" "$UH/.local/bin/$c"; done
# (a pasta "6 - Willbert" da Área de Trabalho saiu em 13/11: o Willbert é ligado pelo painel da areia)
if systemctl is-active -q visao-detector; then systemctl restart visao-detector; fi
echo INSTALADO-OK
