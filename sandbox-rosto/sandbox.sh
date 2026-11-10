#!/bin/bash
# Troca qual SARndbox está no projetor do lab2 (só um programa usa o Kinect por vez).
#   sandbox.sh rosto          versão com os modos Rosto/Esculpir (aceita comandos)
#   sandbox.sh original       o SARndbox original (o mesmo que abre no login)
#   sandbox.sh estado         qual está rodando
#   sandbox.sh cmd "<texto>"  manda um comando para a versão Rosto (ex.: "relevo")
# Se a versão Rosto não subir, o original volta sozinho.
set -uo pipefail
ORIG=/home/laob/SARndbox-2.8/bin/SARndbox
ROSTO=/home/laob/SARndbox-rosto/bin/SARndbox
FIFO=/home/laob/SARndbox-rosto/controle.fifo
LOG=/home/laob/SARndbox-rosto/sandbox.log
export DISPLAY=:0 XAUTHORITY=/run/user/1000/gdm/Xauthority XDG_RUNTIME_DIR=/run/user/1000 \
       DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus HOME=/home/laob
# janela em tela cheia no projetor (sem isso ela abre no monitor); só se o projetor estiver ligado
TELA=()
if xrandr --display :0 2>/dev/null | grep -q "^HDMI-0 connected"; then
  TELA=(-mergeConfig /home/laob/SARndbox-rosto/projetor.cfg)
fi

qual() {
  if pgrep -f "^$ROSTO" >/dev/null; then echo rosto
  elif pgrep -f "^$ORIG" >/dev/null; then echo original
  else echo nenhum; fi
}

parar() {
  pkill -f "^($ORIG|$ROSTO)" 2>/dev/null
  for _ in $(seq 1 20); do [ "$(qual)" = nenhum ] && return 0; sleep 0.25; done
  pkill -9 -f "^($ORIG|$ROSTO)" 2>/dev/null; sleep 1
}

iniciar_original() {
  ( cd /home/laob && exec setsid nohup "$ORIG" "${TELA[@]}" -uhm -fpv ) >/dev/null 2>&1 < /dev/null &
}

case "${1:-estado}" in
  estado) qual ;;
  original)
    [ "$(qual)" = original ] && { echo original; exit 0; }
    parar; iniciar_original; sleep 3; qual ;;
  rosto)
    [ "$(qual)" = rosto ] && { echo rosto; exit 0; }
    [ -p "$FIFO" ] || { rm -f "$FIFO"; mkfifo -m 600 "$FIFO"; }
    parar
    ( cd /home/laob && exec setsid nohup "$ROSTO" "${TELA[@]}" -uhm -fpv -cp "$FIFO" ) > "$LOG" 2>&1 < /dev/null &
    sleep 8
    if [ "$(qual)" != rosto ]; then
      echo "a versão Rosto não subiu (veja $LOG); voltando ao original"
      iniciar_original; sleep 3; qual; exit 1
    fi
    echo rosto ;;
  cmd)
    [ "$(qual)" = rosto ] || { echo "a versão Rosto não está rodando"; exit 1; }
    timeout 3 bash -c 'printf "%s\n" "$1" > "$2"' _ "${2:?comando}" "$FIFO" || { echo "sem resposta do SARndbox"; exit 1; }
    sleep 0.5; tail -n 3 "$LOG" ;;
  *) echo "uso: sandbox.sh rosto|original|estado|cmd \"<comando>\""; exit 2 ;;
esac
