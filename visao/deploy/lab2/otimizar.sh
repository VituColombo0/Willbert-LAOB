#!/bin/bash
# Otimiza o lab2 para o SARndbox (projetor) + Willbert. Tudo reversível: sudo bash desfazer.sh
#   sudo bash otimizar.sh
# Não mexe no driver de vídeo, não remove pacotes, não toca na interface gráfica.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "rode com sudo"; exit 1; }
DIR="$(cd "$(dirname "$0")" && pwd)"
EST=/etc/willbert-otimizacao; mkdir -p "$EST"

echo "[1/7] processador em desempenho máximo (PC na tomada)"
cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor > "$EST/governor.antes" 2>/dev/null || true
cat > /etc/systemd/system/cpu-desempenho.service <<UNIT
[Unit]
Description=Processador em desempenho máximo (willbert-otimizacao; desfazer: desfazer.sh)
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/sh -c 'for g in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do echo performance > \$\$g; done'
ExecStop=/bin/sh -c 'for g in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do echo $(cat "$EST/governor.antes" 2>/dev/null || echo schedutil) > \$\$g; done'
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now cpu-desempenho.service >/dev/null
echo "  governor: $(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor)"

echo "[2/7] SARndbox primeiro: Willbert e LLM com prioridade menor"
for u in visao-detector visao-web visao-go2rtc ollama; do mkdir -p /etc/systemd/system/$u.service.d; done
cat > /etc/systemd/system/visao-detector.service.d/prioridade.conf <<UNIT
# willbert-otimizacao: o projetor (SARndbox) nunca deve engasgar por causa do Willbert
[Service]
Nice=10
CPUWeight=40
IOWeight=40
UNIT
cp /etc/systemd/system/visao-detector.service.d/prioridade.conf /etc/systemd/system/visao-web.service.d/prioridade.conf
printf '[Service]\nNice=5\nCPUWeight=60\n' > /etc/systemd/system/visao-go2rtc.service.d/prioridade.conf

echo "[3/7] Ollama só na CPU (a placa de vídeo é do SARndbox) e só liga com o Willbert"
cat > /etc/systemd/system/ollama.service.d/willbert.conf <<UNIT
# willbert-otimizacao
[Service]
Nice=10
CPUWeight=40
Environment=CUDA_VISIBLE_DEVICES=-1
Environment=GGML_VK_VISIBLE_DEVICES=-1
Environment=OLLAMA_VULKAN=0
Environment=OLLAMA_NUM_PARALLEL=1
Environment=OLLAMA_MAX_LOADED_MODELS=1
UNIT
systemctl is-enabled ollama > "$EST/ollama.antes" 2>/dev/null || true
systemctl daemon-reload
systemctl disable --now ollama >/dev/null 2>&1 || true

echo "[4/7] serviços que este PC não usa (sem impressora, sem modem)"
: > "$EST/servicos.antes"
for u in cups.service cups.socket cups.path cups-browsed.service ModemManager.service; do
  st=$(systemctl is-enabled "$u" 2>/dev/null || true)
  if [ "$st" = enabled ]; then echo "$u" >> "$EST/servicos.antes"; systemctl disable --now "$u" >/dev/null 2>&1 || true; echo "  desligado: $u"; fi
done

echo "[5/7] memória: usar swap só em último caso"
cat /proc/sys/vm/swappiness > "$EST/swappiness.antes"
echo "vm.swappiness=10" > /etc/sysctl.d/90-willbert.conf
sysctl -q -p /etc/sysctl.d/90-willbert.conf

echo "[6/7] atualizações sem surpresa: driver NVIDIA não é atualizado sozinho; snaps só de madrugada"
cat > /etc/apt/apt.conf.d/51willbert-nvidia <<CONF
// willbert-otimizacao: atualizar o driver de vídeo no meio do uso derruba o projetor
// (atualize à mão quando quiser: sudo apt upgrade)
Unattended-Upgrade::Package-Blacklist { "nvidia-"; "libnvidia-"; "xserver-xorg-video-nvidia"; };
CONF
snap get system refresh.timer > "$EST/snap.antes" 2>/dev/null || true
snap set system refresh.timer=03:00-05:00 2>/dev/null || true

echo "[7/7] liga/desliga do Willbert sem senha (só o comando willbert-controle)"
install -o root -g root -m 755 "$DIR/willbert-controle" /usr/local/sbin/willbert-controle
TMP=$(mktemp)
echo "laob ALL=(root) NOPASSWD: /usr/local/sbin/willbert-controle" > "$TMP"
if visudo -cf "$TMP" >/dev/null; then install -o root -g root -m 440 "$TMP" /etc/sudoers.d/willbert; else echo "  regra inválida: não instalada"; fi
rm -f "$TMP"
visudo -c >/dev/null && echo "  sudoers ok"
# o Willbert não liga sozinho no boot (só quando pedir); a página web pode ficar
systemctl disable visao-detector visao-go2rtc >/dev/null 2>&1 || true
systemctl daemon-reload
systemctl restart visao-web
echo
echo "pronto. Desfazer tudo: sudo bash $DIR/desfazer.sh"
