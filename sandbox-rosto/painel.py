"""Painel do celular para o SARndbox (modo Rosto). Só responde em 127.0.0.1 e no IP do
Tailscale do lab2: quem chega aqui já é um aparelho do Victor.
    ~/SARndbox-rosto/venv/bin/python painel.py
"""
import json
import os
import subprocess
import threading
import time

from flask import Flask, jsonify, request, send_file

AQUI = os.path.dirname(os.path.abspath(__file__))
FOTOS = os.path.join(AQUI, "fotos")
ESTADO = os.path.join(AQUI, "estado.json")
PY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "venv", "bin", "python")
AREIA = os.path.expanduser("~/Pictures/Areia")  # a mesma pasta do comando areia-foto
SANDBOX = os.path.join(AQUI, "sandbox.sh")
PADRAO = {"modo": "relevo", "giro": 0, "espelho": False,
          "ajuste": {"zoom": 0.03, "desloc": 0.0, "mistura": 0.0, "sombra": 1.0}}
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024
trava = threading.Lock()


def estado():
    try:
        e = json.load(open(ESTADO))
        return {**PADRAO, **e, "ajuste": {**PADRAO["ajuste"], **e.get("ajuste", {})}}
    except (OSError, ValueError):
        return json.loads(json.dumps(PADRAO))


def salva(e):
    tmp = ESTADO + ".tmp"
    json.dump(e, open(tmp, "w")); os.replace(tmp, ESTADO)


def run(*cmd, timeout=40):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout + r.stderr).strip()


def qual():
    return run(SANDBOX, "estado")[1]


def manda(cmd):
    return run(SANDBOX, "cmd", cmd)


def aplica(e):
    """Coloca o SARndbox no modo do estado (sobe a versão Rosto se precisar)."""
    if e["modo"] == "original":
        return run(SANDBOX, "original", timeout=60)
    if qual() != "rosto":
        rc, out = run(SANDBOX, "rosto", timeout=60)
        if rc != 0:
            return rc, out
    png, dem = os.path.join(FOTOS, "rosto.png"), os.path.join(FOTOS, "rosto.dem")
    if e["modo"] in ("rosto", "esculpir") and not os.path.exists(png):
        return 1, "nenhuma foto ainda: envie uma"
    if e["modo"] == "rosto":
        rc, out = manda(f"rosto {png} {e['giro']} {int(e['espelho'])}")
        a = e["ajuste"]
        manda(f"rostoAjuste {a['zoom']} {a['desloc']} {a['mistura']} {a['sombra']}")
        return rc, out
    if e["modo"] == "esculpir":
        return manda(f"esculpir {dem}")
    return manda("relevo")


def prepara(caminho, e):
    args = [PY, os.path.join(AQUI, "rosto.py"), caminho, "--giro", str(e["giro"])]
    if e["espelho"]:
        args.append("--espelho")
    return run(*args, timeout=60)


@app.get("/")
def pagina():
    return PAGINA


@app.get("/estado")
def ver_estado():
    e = estado(); e["sandbox"] = qual()
    e["tem_foto"] = os.path.exists(os.path.join(FOTOS, "rosto.png"))
    return jsonify(e)


@app.get("/previa.png")
def previa():
    p = os.path.join(FOTOS, "rosto.png")
    if not os.path.exists(p):
        return "", 404
    return send_file(p, mimetype="image/png", max_age=0)


@app.post("/foto")
def foto():
    with trava:
        e = estado()
        if "foto" in request.files and request.files["foto"].filename:
            os.makedirs(AREIA, exist_ok=True)
            ext = os.path.splitext(request.files["foto"].filename)[1].lower() or ".jpg"
            destino = os.path.join(AREIA, time.strftime("celular-%Y-%m-%d-%H%M%S") + ext)
            request.files["foto"].save(destino)
        else:  # câmera do laboratório (Tapo), pelo comando liberado do Willbert
            rc, out = run("sudo", "-n", "/usr/local/sbin/willbert-controle", "foto", timeout=60)
            if rc != 0:
                return jsonify(ok=False, msg=out or "não consegui a foto da câmera"), 500
            destino = os.path.join(FOTOS, "camera.jpg")
        rc, out = prepara(destino, e)
        if rc != 0:
            return jsonify(ok=False, msg=out), 500
        e["modo"] = "rosto" if e["modo"] in ("relevo", "original") else e["modo"]
        e["imagem"] = destino
        salva(e)
        rc2, out2 = aplica(e)
        return jsonify(ok=rc2 == 0, msg=out.splitlines()[-1] + (" · " + out2.splitlines()[-1] if out2 else ""))


@app.post("/modo")
def modo():
    with trava:
        e = estado()
        e["modo"] = request.json.get("modo", "relevo")
        if e["modo"] not in ("relevo", "rosto", "esculpir", "original"):
            return jsonify(ok=False, msg="modo inválido"), 400
        salva(e)
        rc, out = aplica(e)
        return jsonify(ok=rc == 0, msg=out.splitlines()[-1] if out else "")


@app.post("/ajuste")
def ajuste():
    with trava:
        e = estado()
        d = request.json or {}
        for k in ("zoom", "desloc", "mistura", "sombra"):
            if k in d:
                e["ajuste"][k] = float(d[k])
        if "giro" in d or "espelho" in d:
            e["giro"] = int(d.get("giro", e["giro"])) % 360
            e["espelho"] = bool(d.get("espelho", e["espelho"]))
            img = e.get("imagem")  # refaz o relevo com a nova orientação
            if img and os.path.exists(img):
                prepara(img, e)
        salva(e)
        if qual() == "rosto":
            if "giro" in d or "espelho" in d:
                rc, out = aplica(e)
            else:
                a = e["ajuste"]
                rc, out = manda(f"rostoAjuste {a['zoom']} {a['desloc']} {a['mistura']} {a['sombra']}")
            return jsonify(ok=rc == 0)
        return jsonify(ok=True)


PAGINA = """<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Caixa de areia</title>
<style>
:root{--bg:#f6f3ee;--fg:#222;--card:#fff;--muted:#777;--acc:#b5651d;--line:#e3ddd3}
@media (prefers-color-scheme:dark){:root{--bg:#171513;--fg:#eee;--card:#221f1c;--muted:#9a948c;--acc:#e0954a;--line:#37322c}}
body{margin:0;background:var(--bg);color:var(--fg);font:16px system-ui,sans-serif}
main{max-width:520px;margin:auto;padding:16px}
h1{font-size:1.3rem;margin:4px 0 12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px;margin-bottom:12px}
.row{display:flex;gap:8px;flex-wrap:wrap}
button,label.btn{flex:1;min-width:120px;padding:14px 10px;border-radius:10px;border:1px solid var(--line);
 background:var(--bg);color:var(--fg);font-size:1rem;text-align:center;cursor:pointer}
button.on{background:var(--acc);color:#fff;border-color:var(--acc)}
.big{background:var(--acc)!important;color:#fff!important;border-color:var(--acc)!important;font-weight:600}
input[type=file]{display:none}
img{width:100%;border-radius:10px;display:block;margin-top:10px}
.small{color:var(--muted);font-size:.9rem}
input[type=range]{width:100%}
#msg{min-height:1.2em}
</style></head><body><main>
<h1>Caixa de areia</h1>
<div class="card"><div class="small">Projetor: <b id="sb">...</b></div><div id="msg" class="small"></div></div>
<div class="card">
 <div class="row">
  <label class="btn big">Tirar selfie<input id="selfie" type="file" accept="image/*" capture="user"></label>
  <label class="btn">Escolher foto<input id="galeria" type="file" accept="image/*"></label>
 </div>
 <div class="row" style="margin-top:8px"><button id="cam">Foto pela câmera do laboratório</button></div>
 <img id="prev" alt="" hidden>
</div>
<div class="card"><div class="small">Modo</div><div class="row" id="modos">
 <button data-m="relevo">Relevo</button><button data-m="rosto">Rosto</button>
 <button data-m="esculpir">Esculpir</button></div>
 <p class="small">Rosto: a foto se deforma com a areia. Esculpir: vermelho = falta areia, azul = sobra.</p>
 <div class="row"><button data-m="original">SARndbox original</button></div>
</div>
<div class="card"><div class="small">Posição da foto na areia</div><div class="row">
 <button id="girar">Girar 90°</button><button id="esp">Espelhar</button></div></div>
<div class="card"><div class="small">Deformação pela areia: <span id="vz"></span></div>
 <input id="zoom" type="range" min="0" max="0.08" step="0.005">
 <div class="small">Sombra do relevo: <span id="vs"></span></div>
 <input id="sombra" type="range" min="0" max="3" step="0.1">
 <div class="small">Cores do relevo por cima: <span id="vm"></span></div>
 <input id="mistura" type="range" min="0" max="0.8" step="0.05">
</div>
</main><script>
const $=s=>document.querySelector(s);let E={};
function msg(t){$('#msg').textContent=t||''}
async function post(u,b,isForm){msg('enviando...');
 const r=await fetch(u,{method:'POST',body:isForm?b:JSON.stringify(b),headers:isForm?{}:{'Content-Type':'application/json'}});
 let j={};try{j=await r.json()}catch(e){} msg(j.msg||(r.ok?'feito':'erro')); await carrega(); return j}
async function carrega(){E=await (await fetch('/estado')).json();
 $('#sb').textContent={rosto:'versão com Rosto',original:'SARndbox original',nenhum:'desligado'}[E.sandbox]||E.sandbox;
 document.querySelectorAll('#modos button,[data-m=original]').forEach(b=>b.classList.toggle('on',
   E.sandbox==='original'?b.dataset.m==='original':(E.sandbox==='rosto'&&b.dataset.m===E.modo)));
 for(const k of ['zoom','sombra','mistura'])$('#'+k).value=E.ajuste[k];
 $('#vz').textContent=Math.round(E.ajuste.zoom*1000)/10+'%/cm';$('#vs').textContent=E.ajuste.sombra;
 $('#vm').textContent=Math.round(E.ajuste.mistura*100)+'%';
 if(E.tem_foto){$('#prev').hidden=false;$('#prev').src='/previa.png?'+Date.now()}}
function envia(inp){inp.onchange=()=>{if(!inp.files[0])return;const f=new FormData();f.append('foto',inp.files[0]);post('/foto',f,true);inp.value=''}}
envia($('#selfie'));envia($('#galeria'));
$('#cam').onclick=()=>post('/foto',new FormData(),true);
document.querySelectorAll('[data-m]').forEach(b=>b.onclick=()=>post('/modo',{modo:b.dataset.m}));
$('#girar').onclick=()=>post('/ajuste',{giro:(E.giro+90)%360});
$('#esp').onclick=()=>post('/ajuste',{espelho:!E.espelho});
for(const k of ['zoom','sombra','mistura'])$('#'+k).onchange=e=>post('/ajuste',{[k]:e.target.value});
carrega();setInterval(carrega,10000);
</script></body></html>"""


if __name__ == "__main__":
    from waitress import serve
    ips = ["127.0.0.1"]
    try:
        ts = subprocess.run(["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=5).stdout.split()
        ips += ts[:1]
    except (OSError, subprocess.SubprocessError):
        pass
    serve(app, listen=" ".join(f"{ip}:8090" for ip in ips), threads=4)
