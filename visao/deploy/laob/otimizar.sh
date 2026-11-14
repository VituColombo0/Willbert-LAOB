#!/bin/bash
# Otimiza o laob para o SARndbox (projetor) + Willbert. Tudo reversível: sudo bash desfazer.sh
#   sudo bash otimizar.sh
# Não mexe no driver de vídeo, não remove pacotes, não toca na interface gráfica.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "rode com sudo"; exit 1; }
DIR="$(cd "$(dirname "$0")" && pwd)"
EST=/etc/willbert-otimizacao; mkdir -p "$EST"
# guarda o "antes" só na primeira vez: rodar de novo não pode perder o estado original
antes() { [ -e "$EST/$1" ] || cat > "$EST/$1"; }

echo "[1/8] processador em desempenho máximo (PC na tomada)"
cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null | antes governor.antes || true
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

echo "[2/8] SARndbox primeiro: Willbert e LLM com prioridade menor"
for u in visao-detector visao-web visao-go2rtc; do mkdir -p /etc/systemd/system/$u.service.d; done
cat > /etc/systemd/system/visao-detector.service.d/prioridade.conf <<UNIT
# willbert-otimizacao: o projetor (SARndbox) nunca deve engasgar por causa do Willbert
[Service]
Nice=10
CPUWeight=40
IOWeight=40
UNIT
cp /etc/systemd/system/visao-detector.service.d/prioridade.conf /etc/systemd/system/visao-web.service.d/prioridade.conf
# CPUQuota: o go2rtc usa ~1-6% de um núcleo; por um defeito dele (laço sem fim na leitura da Tapo)
# já ficou 2 dias a 100%. O detector reinicia quando vê isso; o teto vale mesmo com ele desligado.
printf '[Service]\nNice=5\nCPUWeight=60\nCPUQuota=40%%\n' > /etc/systemd/system/visao-go2rtc.service.d/prioridade.conf

echo "[3/8] Ollama só na CPU e desligado (só se ainda estiver instalado; no laob saiu em 2026-11-13)"
if systemctl cat ollama >/dev/null 2>&1; then
mkdir -p /etc/systemd/system/ollama.service.d
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
{ systemctl is-enabled ollama 2>/dev/null || true; } | antes ollama.antes || true
systemctl daemon-reload
systemctl disable --now ollama >/dev/null 2>&1 || true
fi

echo "[4/8] serviços que este PC não usa (impressora, modem, relatórios de erro, descoberta na rede)"
# avahi: anúncio na rede local (o acesso é pelo Tailscale); kerneloops/apport: relatórios de erro;
# switcheroo: troca entre duas placas de vídeo (aqui só há uma)
touch "$EST/servicos.antes"
for u in cups.service cups.socket cups.path cups-browsed.service ModemManager.service \
         kerneloops.service apport.service avahi-daemon.socket avahi-daemon.service switcheroo-control.service; do
  st=$(systemctl is-enabled "$u" 2>/dev/null || true)
  if [ "$st" = enabled ]; then
    grep -qx "$u" "$EST/servicos.antes" || echo "$u" >> "$EST/servicos.antes"
    systemctl disable --now "$u" >/dev/null 2>&1 || true; echo "  desligado: $u"
  fi
done

echo "[5/8] memória: usar swap só em último caso"
antes swappiness.antes < /proc/sys/vm/swappiness
echo "vm.swappiness=10" > /etc/sysctl.d/90-willbert.conf
sysctl -q -p /etc/sysctl.d/90-willbert.conf

echo "[6/8] atualizações sem surpresa: driver NVIDIA não é atualizado sozinho; snaps só de madrugada"
cat > /etc/apt/apt.conf.d/51willbert-nvidia <<CONF
// willbert-otimizacao: atualizar o driver de vídeo no meio do uso derruba o projetor
// (atualize à mão quando quiser: sudo apt upgrade)
Unattended-Upgrade::Package-Blacklist { "nvidia-"; "libnvidia-"; "xserver-xorg-video-nvidia"; };
CONF
[ -e "$EST/snap.antes" ] || snap get system refresh.timer > "$EST/snap.antes" 2>/dev/null || true
snap set system refresh.timer=03:00-05:00 2>/dev/null || true
# o kernel continua atualizando sozinho; o PC só inicia num kernel que tenha o driver NVIDIA
bash "$DIR/kernel-com-driver.sh" | sed 's/^/  /'

echo "[7/8] sem avisos por cima do projetor: atualização, relatório da Ubuntu, alarmes, backup"
# (as atualizações de segurança continuam automáticas; só a janela "há atualizações" some)
U=${SUDO_USER:-laob}; UH=$(getent passwd "$U" | cut -d: -f6)
if [ -n "$UH" ] && [ -d "$UH" ]; then
  sudo -u "$U" mkdir -p "$UH/.config/autostart"
  for a in update-notifier ubuntu-advantage-notification ubuntu-report-on-upgrade \
           org.gnome.Evolution-alarm-notify org.gnome.DejaDup.Monitor; do
    f="$UH/.config/autostart/$a.desktop"
    [ -f "/etc/xdg/autostart/$a.desktop" ] || continue
    [ -e "$f" ] && continue   # já existe (do usuário ou de antes): não mexe
    printf '[Desktop Entry]\nType=Application\nName=%s\nHidden=true\n# willbert-otimizacao\n' "$a" | sudo -u "$U" tee "$f" >/dev/null
    echo "$f" >> "$EST/autostart.criados"; echo "  escondido: $a"
  done
  pkill -u "$U" -x update-notifier 2>/dev/null || true
fi

echo "[8/8] liga/desliga do Willbert sem senha (só o comando willbert-controle)"
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
