#!/bin/bash
# Configura o Willbert do zero no laob (sem o mini servidor): câmera, conta Tapo e senha do site.
#   sudo bash ~/Willbert/visao/deploy/laob/configurar.sh
# As senhas são digitadas escondidas, vão só para /opt/visao/config.toml (root:visao 640) e
# para o login do site; nada é mostrado na tela nem fica no histórico. Pode rodar de novo.
# Não liga o Willbert (só: sudo -n willbert-controle ligar).
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "rode com sudo: sudo bash $0"; exit 1; }
CFG=/opt/visao/config.toml
PY=/opt/visao/venv/bin/python
[ -f "$CFG" ] || { echo "falta $CFG (rode o install-pc.sh antes)"; exit 1; }

pede_senha() {  # $1 = pergunta; $2 = "opcional" aceita vazio
  local a b
  while :; do
    read -rsp "$1: " a; echo >&2
    if [ -z "$a" ]; then
      [ "${2:-}" = opcional ] && { printf ''; return; }
      echo "  não pode ficar vazia" >&2; continue
    fi
    read -rsp "  repita: " b; echo >&2
    [ "$a" = "$b" ] && { printf '%s' "$a"; return; }
    echo "  as duas não conferem, de novo" >&2
  done
}

echo "== Willbert: configuração do zero =="
echo
echo "1) Câmera Tapo C200"
read -rp "   IP da câmera [192.168.0.166]: " IP; IP=${IP:-192.168.0.166}
echo "   'Conta da câmera' = a criada no app Tapo > câmera > Configurações > Avançado > Conta da câmera"
read -rp "   usuário da conta da câmera: " CAM_USER
CAM_PASS=$(pede_senha "   senha da conta da câmera")
echo
echo "2) Conta TP-Link/Tapo (a do login no app). Serve para o Willbert falar e ouvir pela câmera."
echo "   Precisa de 'Compatibilidade com terceiros' ligada no app. Enter = manter a que já está."
TAPO_PASS=$(pede_senha "   senha da conta Tapo" opcional)
echo
echo "3) Site do Willbert (http://<laob>:8080), onde se cadastram os membros"
WEB_PASS=""
read -rp "   trocar o usuário/senha do site? [s/N]: " TROCA_SITE
if [ "${TROCA_SITE,,}" = s ]; then
  read -rp "   usuário do site [admin]: " WEB_USER; WEB_USER=${WEB_USER:-admin}
  while :; do
    WEB_PASS=$(pede_senha "   senha do site (mínimo 6)")
    [ ${#WEB_PASS} -ge 6 ] && break
    echo "   curta demais" >&2
  done
fi
echo

cp -p "$CFG" "$CFG.antes-configurar"
export CFG IP CAM_USER CAM_PASS TAPO_PASS
"$PY" - <<'PY'
import json, os, re
p = os.environ["CFG"]
s = open(p).read()

def troca(secao, chave, valor):
    """Troca o valor de chave em [secao] mantendo comentários; acrescenta se não houver."""
    global s
    m = re.search(r"(?ms)^\[" + re.escape(secao) + r"\][^\n]*\n(.*?)(?=^\[|\Z)", s)
    if not m:
        s = s.rstrip("\n") + f"\n\n[{secao}]\n"
        m = re.search(r"(?ms)^\[" + re.escape(secao) + r"\][^\n]*\n(.*?)(?=^\[|\Z)", s)
    corpo = m.group(1)
    v = json.dumps(valor, ensure_ascii=False)  # string TOML válida (aspas e barras escapadas)
    novo, n = re.subn(r'(?m)^(' + re.escape(chave) + r'\s*=\s*)("(?:[^"\\]|\\.)*"|[^\s#]+)',
                      lambda mm: mm.group(1) + v, corpo, count=1)
    if n == 0:
        novo = f"{chave} = {v}\n" + corpo
    s = s[:m.start(1)] + novo + s[m.end(1):]

troca("camera", "host", os.environ["IP"])
troca("camera", "user", os.environ["CAM_USER"])
troca("camera", "password", os.environ["CAM_PASS"])
if os.environ["TAPO_PASS"]:  # vazio = manter a senha da conta Tapo que já estava
    troca("greeting", "tapo_cloud_password", os.environ["TAPO_PASS"])
tmp = p + ".tmp"
with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640), "w") as f:
    f.write(s)
os.replace(tmp, p)
PY
unset CAM_PASS TAPO_PASS
chown root:visao "$CFG"; chmod 640 "$CFG"

# confere se o arquivo continua válido (sem mostrar senhas)
VISAO_CONFIG=$CFG PYTHONPATH=/opt/visao/app "$PY" -c "
from visao.config import load
c = load('$CFG')
print('   câmera', c.camera.host, '· usuário', c.camera.user, '· senha', 'ok' if c.camera.password else 'VAZIA',
      '· conta Tapo', 'ok' if c.greeting.tapo_cloud_password else 'não informada (só vídeo)')
" || { echo "config inválida; voltando a anterior"; cp -p "$CFG.antes-configurar" "$CFG"; exit 1; }

if [ -n "$WEB_PASS" ]; then
  printf '%s\n' "$WEB_PASS" | visao-cli senha --stdin --usuario "$WEB_USER" >/dev/null
  echo "   site: usuário $WEB_USER, senha definida"
else
  echo "   site: usuário e senha mantidos"
fi
unset WEB_PASS

visao-cli go2rtc-config --saida /opt/visao/go2rtc.yaml >/dev/null
chown root:visao /opt/visao/go2rtc.yaml; chmod 640 /opt/visao/go2rtc.yaml
systemctl restart visao-web
echo
echo "4) Teste da conta da câmera (UMA tentativa só: a Tapo bloqueia depois de várias falhas)"
URL=$(VISAO_CONFIG=$CFG PYTHONPATH=/opt/visao/app "$PY" -c "
from visao.config import load, rtsp_url
print(rtsp_url(load('$CFG').camera, 'sub'))")
if timeout 20 ffprobe -v error -rtsp_transport tcp -select_streams v:0 -show_entries stream=width,height \
     -of csv=p=0 "$URL" 2>/dev/null | grep -q '[0-9]'; then
  echo "   conta da câmera OK: o vídeo chegou"
else
  echo "   a câmera NÃO aceitou (usuário/senha da 'Conta da câmera' ou câmera bloqueada por falhas recentes)"
fi
unset URL

echo
echo "Pronto. O Willbert continua DESLIGADO."
echo "Avise o Claude para testar a câmera (uma tentativa só) e cadastrar os membros."
