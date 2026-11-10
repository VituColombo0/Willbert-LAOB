#!/bin/sh
# Mini servidor como RESERVA do Willbert (PC). Roda a cada 30 s pelo visao-reserva.timer.
#   PC respondendo em /saude  -> o detector da TV Box fica parado (o PC cuida da câmera)
#   PC sem resposta 3x seguidas (~90 s) -> a TV Box liga o detector e assume
# Nunca os dois ao mesmo tempo: senão a câmera falaria duas vezes com cada pessoa.
CONF=/etc/visao-reserva.conf
STATE=/run/visao-reserva.falhas
[ -f "$CONF" ] && . "$CONF"
PC_URL="${PC_URL:?defina PC_URL em $CONF}"
LIMITE="${LIMITE:-3}"

falhas=$(cat "$STATE" 2>/dev/null || echo 0)
if curl -fs -m 5 "$PC_URL/saude" >/dev/null 2>&1; then
  echo 0 > "$STATE"
  if systemctl is-active -q visao-detector; then
    systemctl stop visao-detector visao-go2rtc
    logger -t visao-reserva "PC voltou ($PC_URL): reserva da TV Box desligada"
  fi
else
  falhas=$((falhas + 1))
  echo "$falhas" > "$STATE"
  if [ "$falhas" -ge "$LIMITE" ] && ! systemctl is-active -q visao-detector; then
    systemctl start visao-go2rtc visao-detector
    logger -t visao-reserva "PC sem resposta há $falhas verificações: TV Box assumiu a câmera"
  fi
fi
