#!/usr/bin/env bash
# Instala ou atualiza o Visão no mini servidor. Rode como root a partir do repositório:
#   sudo bash visao/deploy/install.sh
set -euo pipefail

SRC="$(cd "$(dirname "$0")/.." && pwd)"
BASE=/opt/visao
PIP_INDEX=(--index-url https://www.piwheels.org/simple --extra-index-url https://pypi.org/simple)
ZOO=https://github.com/opencv/opencv_zoo/raw/main/models

[ "$(id -u)" = 0 ] || { echo "rode como root (sudo)"; exit 1; }

echo "[1/7] pacotes do sistema"
apt-get install -y --no-install-recommends ffmpeg python3-venv rsync curl \
  libopenblas0-pthread libwebpdemux2 libopenexr-3-1-30 \
  espeak-ng mbrola mbrola-br1 mbrola-br3 mbrola-br4 alsa-utils >/dev/null

echo "[2/7] usuário de serviço 'visao'"
id visao >/dev/null 2>&1 || useradd --system --home-dir "$BASE" --no-create-home --shell /usr/sbin/nologin visao
usermod -aG audio visao   # tocar o cumprimento na saída de som da TV Box
mkdir -p "$BASE"/{app,data,models}

echo "[3/7] ambiente Python (pode demorar na primeira vez)"
[ -x "$BASE/venv/bin/python" ] || python3 -m venv "$BASE/venv"
"$BASE/venv/bin/pip" install -q --resume-retries 10 --timeout 120 "${PIP_INDEX[@]}" \
  --prefer-binary -r "$SRC/requirements.txt"

echo "[4/7] modelos de rosto (OpenCV Zoo)"
for f in face_detection_yunet/face_detection_yunet_2023mar.onnx \
         face_recognition_sface/face_recognition_sface_2021dec.onnx; do
  dst="$BASE/models/$(basename "$f")"
  [ -s "$dst" ] || curl -fsSL --retry 5 -o "$dst" "$ZOO/$f"
done

echo "[4b/7] go2rtc (áudio para o alto-falante da câmera)"
mkdir -p "$BASE/bin"
if [ ! -x "$BASE/bin/go2rtc" ]; then
  GO2RTC_VER="${GO2RTC_VER:-v1.9.15}"
  curl -fsSL --retry 5 -o "$BASE/bin/go2rtc" \
    "https://github.com/AlexxIT/go2rtc/releases/download/$GO2RTC_VER/go2rtc_linux_arm"
  chmod 755 "$BASE/bin/go2rtc"
fi

echo "[4c/7] voz natural (Piper, pt-BR)"
mkdir -p "$BASE/piper" "$BASE/voices"
if [ ! -x "$BASE/piper/piper" ]; then
  curl -fsSL --retry 5 -o /tmp/piper.tgz \
    "https://github.com/rhasspy/piper/releases/download/2023.11.14-2/piper_linux_armv7l.tar.gz"
  tar -xzf /tmp/piper.tgz -C "$BASE/piper" --strip-components=1 && rm -f /tmp/piper.tgz
fi
for spk in faber; do   # outras vozes pt_BR do Piper: jeff, cadu (Piper 1.5; o Visão adapta)
  VOICE=pt_BR-$spk-medium
  for ext in onnx onnx.json; do
    [ -s "$BASE/voices/$VOICE.$ext" ] || curl -fsSL --retry 5 -o "$BASE/voices/$VOICE.$ext" \
      "https://huggingface.co/rhasspy/piper-voices/resolve/main/pt/pt_BR/$spk/medium/$VOICE.$ext"
  done
done

echo "[5/7] código"
rsync -a --delete --exclude '__pycache__' "$SRC/visao/" "$BASE/app/visao/"
install -m 755 "$SRC/deploy/visao-cli" /usr/local/bin/visao-cli

echo "[6/7] configuração e permissões"
if [ ! -f "$BASE/config.toml" ]; then
  cp "$SRC/config.example.toml" "$BASE/config.toml"
  echo "  >> edite $BASE/config.toml com o IP e a conta da câmera"
fi
chown root:visao "$BASE/config.toml"; chmod 640 "$BASE/config.toml"
chown -R visao:visao "$BASE/data"; chmod 750 "$BASE/data"
if [ ! -f "$BASE/data/web_auth.json" ]; then
  PW="$("$BASE/venv/bin/python" -c 'import secrets; print(secrets.token_urlsafe(9))')"
  printf '%s\n' "$PW" | visao-cli senha --stdin >/dev/null
  ( umask 077; printf 'Senha inicial da interface do Visão: %s\nTroque com: visao-cli senha\n' "$PW" \
      > /root/visao-senha-inicial.txt )
  echo "  >> senha inicial da web salva em /root/visao-senha-inicial.txt"
fi

visao-cli go2rtc-config --saida "$BASE/go2rtc.yaml"
chown root:visao "$BASE/go2rtc.yaml"; chmod 640 "$BASE/go2rtc.yaml"

echo "[6b/7] watchdog: reinicia a TV Box sozinha se o sistema travar"
mkdir -p /etc/systemd/system.conf.d
printf '[Manager]\nRuntimeWatchdogSec=30s\nRebootWatchdogSec=2min\n' > /etc/systemd/system.conf.d/watchdog.conf
printf 'kernel.panic = 10\n' > /etc/sysctl.d/90-visao-panic.conf
sysctl -q -p /etc/sysctl.d/90-visao-panic.conf || true
systemctl daemon-reexec

echo "[7/7] serviços"
install -m 644 "$SRC/deploy/visao-detector.service" "$SRC/deploy/visao-web.service" \
  "$SRC/deploy/visao-go2rtc.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable visao-go2rtc visao-detector visao-web >/dev/null 2>&1
systemctl restart visao-go2rtc visao-detector visao-web
sleep 3
systemctl --no-pager --lines=0 status visao-go2rtc visao-detector visao-web | grep -E '●|Active:'
IP="$(tailscale ip -4 2>/dev/null | head -1 || true)"
echo "pronto: http://${IP:-<ip-do-servidor>}:8080"
