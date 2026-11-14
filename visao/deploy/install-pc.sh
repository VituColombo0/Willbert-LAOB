#!/usr/bin/env bash
# Instala ou atualiza o Willbert (Visão + conversa) num PC Ubuntu/Debian x86_64. Rode como root
# a partir do repositório:
#   sudo bash visao/deploy/install-pc.sh
#
# Detecta o hardware e escolhe sozinho os modelos (ver visao/visao/hardware.py):
#   NVIDIA 10+ GB -> Whisper large-v3-turbo + LLM qwen3:8b na placa
#   NVIDIA 6-10 GB -> Whisper large-v3-turbo + qwen3:4b
#   sem NVIDIA     -> Whisper small (CPU) + qwen3:4b ou 1.7b
# Pode rodar de novo quantas vezes quiser (só baixa o que falta).
set -euo pipefail

SRC="$(cd "$(dirname "$0")/.." && pwd)"
BASE=/opt/visao
ZOO=https://github.com/opencv/opencv_zoo/raw/main/models
GO2RTC_VER="${GO2RTC_VER:-v1.9.15}"
PIPER_REL="${PIPER_REL:-2023.11.14-2}"

[ "$(id -u)" = 0 ] || { echo "rode como root (sudo)"; exit 1; }
[ "$(uname -m)" = x86_64 ] || { echo "este instalador é para PC x86_64"; exit 1; }

echo "[1/9] pacotes do sistema"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq --no-install-recommends ffmpeg python3-venv python3-dev rsync curl ca-certificates \
  espeak-ng pciutils zstd file >/dev/null

echo "[2/9] placa de vídeo"
if lspci | grep -iE "vga|3d" | grep -qi nvidia; then
  if ! command -v nvidia-smi >/dev/null || ! nvidia-smi >/dev/null 2>&1; then
    echo "  placa NVIDIA sem driver: instalando o driver recomendado (ubuntu-drivers)"
    apt-get install -y -qq ubuntu-drivers-common >/dev/null || true
    if command -v ubuntu-drivers >/dev/null; then
      ubuntu-drivers install
      echo
      echo "  >> driver NVIDIA instalado. REINICIE o PC e rode este instalador de novo."
      exit 2
    fi
    echo "  >> instale o driver NVIDIA manualmente e rode de novo (seguindo sem placa por enquanto)"
  else
    nvidia-smi --query-gpu=name,memory.total --format=csv,noheader | sed 's/^/  NVIDIA: /'
  fi
else
  echo "  sem placa NVIDIA: modelos rodam na CPU"
fi

echo "[3/9] usuário de serviço 'visao'"
id visao >/dev/null 2>&1 || useradd --system --home-dir "$BASE" --no-create-home --shell /usr/sbin/nologin visao
usermod -aG audio,video visao
mkdir -p "$BASE"/{app,data,models/whisper,bin,piper,voices}

echo "[4/9] ambiente Python ($(python3 --version))"
[ -x "$BASE/venv/bin/python" ] || python3 -m venv "$BASE/venv"
"$BASE/venv/bin/pip" install -q --upgrade pip
"$BASE/venv/bin/pip" install -q -r "$SRC/requirements-pc.txt"
CC="$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader 2>/dev/null | sort -rn | head -1 || true)"
if [ -n "$CC" ] && awk -v c="$CC" 'BEGIN{exit !(c >= 7.0)}'; then
  # bibliotecas CUDA 12 / cuDNN 9 que o faster-whisper (CTranslate2) usa na placa
  # (placas antigas, compute capability < 7.0, não rodam o CTranslate2 atual: ficam de fora)
  "$BASE/venv/bin/pip" install -q nvidia-cublas-cu12 "nvidia-cudnn-cu12==9.*"
fi

echo "[5/9] modelos de rosto, go2rtc e voz (Piper)"
for f in face_detection_yunet/face_detection_yunet_2023mar.onnx \
         face_recognition_sface/face_recognition_sface_2021dec.onnx; do
  dst="$BASE/models/$(basename "$f")"
  [ -s "$dst" ] || curl -fsSL --retry 5 -o "$dst" "$ZOO/$f"
done
if [ ! -x "$BASE/bin/go2rtc" ] || ! file "$BASE/bin/go2rtc" 2>/dev/null | grep -q x86-64; then
  curl -fsSL --retry 5 -o "$BASE/bin/go2rtc" \
    "https://github.com/AlexxIT/go2rtc/releases/download/$GO2RTC_VER/go2rtc_linux_amd64"
  chmod 755 "$BASE/bin/go2rtc"
fi
if [ ! -x "$BASE/piper/piper" ] || ! file "$BASE/piper/piper" 2>/dev/null | grep -q x86-64; then
  rm -rf "$BASE/piper" && mkdir -p "$BASE/piper"
  curl -fsSL --retry 5 -o /tmp/piper.tgz \
    "https://github.com/rhasspy/piper/releases/download/$PIPER_REL/piper_linux_x86_64.tar.gz"
  tar -xzf /tmp/piper.tgz -C "$BASE/piper" --strip-components=1 && rm -f /tmp/piper.tgz
fi
VOICE=pt_BR-faber-medium
for ext in onnx onnx.json; do
  [ -s "$BASE/voices/$VOICE.$ext" ] || curl -fsSL --retry 5 -o "$BASE/voices/$VOICE.$ext" \
    "https://huggingface.co/rhasspy/piper-voices/resolve/main/pt/pt_BR/faber/medium/$VOICE.$ext"
done
chmod -R a+rX "$BASE/piper" "$BASE/voices" "$BASE/models"

echo "[6/9] código e configuração"
rsync -a --delete --exclude '__pycache__' "$SRC/visao/" "$BASE/app/visao/"
install -m 755 "$SRC/deploy/visao-cli" /usr/local/bin/visao-cli
if [ ! -f "$BASE/config.toml" ]; then
  cp "$SRC/config.example.toml" "$BASE/config.toml"
  echo "  >> configure a câmera e o site: sudo bash visao/deploy/laob/configurar.sh"
fi
chown root:visao "$BASE/config.toml"; chmod 640 "$BASE/config.toml"
chown -R visao:visao "$BASE/data" "$BASE/models/whisper"; chmod 750 "$BASE/data"
if [ ! -f "$BASE/data/web_auth.json" ]; then
  PW="$("$BASE/venv/bin/python" -c 'import secrets; print(secrets.token_urlsafe(9))')"
  printf '%s\n' "$PW" | visao-cli senha --stdin >/dev/null
  ( umask 077; printf 'Senha inicial da interface do Willbert: %s\nTroque com: visao-cli senha\n' "$PW" \
      > /root/visao-senha-inicial.txt )
  echo "  >> senha inicial da web salva em /root/visao-senha-inicial.txt"
fi
visao-cli hardware || true
read -r PROFILE LLM_MODEL STT_MODEL STT_DEVICE STT_CT < <(VISAO_CONFIG=$BASE/config.toml PYTHONPATH=$BASE/app \
  "$BASE/venv/bin/python" -c "
from visao.config import load
c = load()
print(c.hardware.active, c.llm.model or '-', c.stt.model or '-', c.stt.device, c.stt.compute_type)")

echo "[7/9] modelo de linguagem (Ollama): $LLM_MODEL"
if [ "$LLM_MODEL" != "-" ]; then
  command -v ollama >/dev/null || curl -fsSL https://ollama.com/install.sh | sh
  systemctl enable --now ollama >/dev/null 2>&1 || true
  for _ in $(seq 1 30); do curl -fs http://127.0.0.1:11434/api/version >/dev/null && break; sleep 1; done
  ollama pull "$LLM_MODEL"
fi

echo "[8/9] reconhecimento de fala (Whisper $STT_MODEL em $STT_DEVICE)"
if [ "$STT_MODEL" != "-" ]; then
  CUDA_LIBS=$("$BASE/venv/bin/python" -c "
import glob, os
print(':'.join(sorted({os.path.dirname(p) for p in glob.glob('$BASE/venv/lib/python3*/site-packages/nvidia/*/lib/*.so*')})))")
  sudo -u visao env LD_LIBRARY_PATH="$CUDA_LIBS" HF_HOME="$BASE/models/whisper/hf" "$BASE/venv/bin/python" -c "
from faster_whisper import WhisperModel
WhisperModel('$STT_MODEL', device='$STT_DEVICE', compute_type='$STT_CT', download_root='$BASE/models/whisper')
print('  Whisper pronto')"
  mkdir -p /etc/systemd/system/visao-detector.service.d
  cat > /etc/systemd/system/visao-detector.service.d/pc.conf <<EOF
# gerado pelo install-pc.sh: no PC há memória e CPU de sobra; o reconhecimento de fala e o
# LLM (Ollama) precisam de mais que os limites da TV Box
[Unit]
After=ollama.service visao-go2rtc.service
[Service]
Nice=0
MemoryMax=8G
Environment=LD_LIBRARY_PATH=$CUDA_LIBS
Environment=HF_HOME=$BASE/models/whisper/hf
EOF
fi

echo "[9/9] serviços"
visao-cli go2rtc-config --saida "$BASE/go2rtc.yaml"
chown root:visao "$BASE/go2rtc.yaml"; chmod 640 "$BASE/go2rtc.yaml"
install -m 644 "$SRC/deploy/visao-detector.service" "$SRC/deploy/visao-web.service" \
  "$SRC/deploy/visao-go2rtc.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable visao-go2rtc visao-detector visao-web >/dev/null 2>&1
systemctl restart visao-go2rtc visao-detector visao-web
sleep 5
systemctl --no-pager --lines=0 status visao-go2rtc visao-detector visao-web | grep -E '●|Active:'
IP="$(tailscale ip -4 2>/dev/null | head -1 || true)"
echo "pronto (perfil $PROFILE): http://${IP:-<ip-do-pc>}:8080 · teste a conversa: visao-cli conversar --mudo"
