"""Linha de comando: visao-cli <comando>.

  visao-cli status                     estado do detector e da câmera
  visao-cli membros                    lista os membros e amostras
  visao-cli adicionar "Nome" f1.jpg …  cadastra/adiciona fotos de um membro
  visao-cli relatorio [--dias 7]       resumo de presença no terminal
  visao-cli senha [--stdin]            define a senha da interface web
"""
import argparse
import getpass
import json
import os
import pwd
import secrets
import shutil
import sys
import time
import uuid
from datetime import date, timedelta

from werkzeug.security import generate_password_hash

from . import db, reports
from .config import load
from .enroll import insert_pending, new_photo_path

cfg = load()
DATA = cfg.storage.data_dir
SERVICE_USER = "visao"


def run_as_service_user():
    """Se rodando como root, copia as fotos para a área do serviço e reexecuta como 'visao'."""
    if os.geteuid() != 0:
        return
    try:
        pw = pwd.getpwnam(SERVICE_USER)
    except KeyError:
        return
    stage = os.path.join(DATA, "uploads")
    os.makedirs(stage, exist_ok=True)
    os.chown(stage, pw.pw_uid, pw.pw_gid)
    args = []
    for a in sys.argv[1:]:
        if os.path.isfile(a):
            dst = os.path.join(stage, uuid.uuid4().hex + os.path.splitext(a)[1].lower())
            shutil.copy(a, dst)
            os.chown(dst, pw.pw_uid, pw.pw_gid)
            a = dst
        args.append(a)
    os.execvp("runuser", ["runuser", "-u", SERVICE_USER, "--", sys.executable, "-m", "visao.cli", *args])


def connect():
    return db.connect(os.path.join(DATA, "visao.db"))


def cmd_status(_):
    try:
        with open(os.path.join(cfg.storage.runtime_dir, "status.json")) as f:
            st = json.load(f)
    except FileNotFoundError:
        print("detector não está rodando (sem status)")
        return 1
    age = time.time() - st["ts"]
    print(f"status de {age:.0f} s atrás · modo: {st['mode']}")
    print(f"câmera: {'online' if st['camera_online'] else 'OFFLINE'} {st.get('stream_size')} "
          f"{st['decoder_fps']} qps · ONVIF: {st['onvif']} ({st['onvif_events']} eventos)")
    print(f"ciclo: {st['cycle_ms']} ms · rostos: {st['faces']} · temp: {st['cpu_temp']} °C · carga {st['load']}")
    print(f"galeria: {st['gallery_members']} membros, {st['gallery_samples']} amostras")
    pres = ", ".join(p["name"] for p in st["present"]) or "ninguém"
    print(f"presentes: {pres}")
    return 0


def cmd_membros(_):
    c = connect()
    rows = c.execute(
        "SELECT m.id, m.name, m.active, "
        "SUM(s.status='ok') AS ok, SUM(s.status='pendente') AS pend, SUM(s.status='erro') AS err "
        "FROM members m LEFT JOIN samples s ON s.member_id = m.id GROUP BY m.id ORDER BY m.name").fetchall()
    if not rows:
        print("nenhum membro cadastrado")
    for r in rows:
        print(f"[{r['id']:3d}] {r['name']:<30} amostras ok={r['ok'] or 0} pendentes={r['pend'] or 0} "
              f"erro={r['err'] or 0}{'' if r['active'] else '  (inativo)'}")
    return 0


def cmd_adicionar(a):
    c = connect()
    row = c.execute("SELECT id FROM members WHERE name = ?", (a.nome,)).fetchone()
    mid = row["id"] if row else c.execute("INSERT INTO members(name, created_at) VALUES(?, ?)",
                                          (a.nome, time.time())).lastrowid
    print(f"membro: {a.nome} (id {mid}){'' if row else ' — novo'}")
    ids = []
    for path in a.fotos:
        ext = os.path.splitext(path)[1].lower() or ".jpg"
        dst, rel = new_photo_path(DATA, mid, ext)
        shutil.copy(path, dst)
        if path.startswith(os.path.join(DATA, "uploads")):
            os.remove(path)
        ids.append(insert_pending(c, mid, rel, "foto"))
    if not ids:
        return 0
    print(f"{len(ids)} foto(s) enviada(s); aguardando o detector processar…")
    deadline = time.time() + 20 + 8 * len(ids)
    while time.time() < deadline:
        q = f"SELECT * FROM samples WHERE id IN ({','.join('?' * len(ids))})"
        rows = c.execute(q, ids).fetchall()
        if all(r["status"] != "pendente" for r in rows):
            for r in rows:
                print(f"  amostra {r['id']}: {r['status']} — {r['message']}")
            return 0
        time.sleep(1)
    print("o detector não processou a tempo (está rodando?). As fotos ficam na fila.")
    return 1


def cmd_relatorio(a):
    c = connect()
    d1 = date.today()
    d0 = d1 - timedelta(days=a.dias - 1)
    t0, t1 = reports.day_bounds(d0)[0], reports.day_bounds(d1)[1]
    sess = reports.sessions_between(c, t0, t1)
    print(f"Identificações de {d0:%d/%m} a {d1:%d/%m/%Y}")
    print(f"{'Membro':<28} {'Dias':>4} {'Vezes':>6}  Última vez visto")
    for p in reports.summarize(c, sess, t0, t1):
        last = time.strftime("%d/%m %H:%M", time.localtime(p["last"])) if p["last"] else "—"
        print(f"{p['name']:<28} {p['n_days']:>4} {p['entries']:>6}  {last}")
    return 0


def cmd_senha(a):
    if a.stdin:
        pw = sys.stdin.readline().strip()
    else:
        pw = getpass.getpass("Nova senha da interface web: ")
        if pw != getpass.getpass("Repita: "):
            print("as senhas não conferem")
            return 1
    if len(pw) < 6:
        print("use pelo menos 6 caracteres")
        return 1
    path = os.path.join(DATA, "web_auth.json")
    old = {}
    if os.path.exists(path):
        with open(path) as f:
            old = json.load(f)
    data = {"username": (a.usuario or old.get("username") or "admin").strip(),
            "password_hash": generate_password_hash(pw),
            "secret_key": old.get("secret_key") or secrets.token_hex(32)}
    tmp = path + ".tmp"
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)
    print("senha definida (vale no próximo login; quem já está logado continua logado)")
    return 0


def cmd_falar(a):
    """Testa o alto-falante da câmera com uma frase."""
    from .speaker import Speaker
    sp = Speaker(cfg.greeting, os.path.join(cfg.storage.data_dir, "tts"))
    path = sp.synth(a.texto, a.voz)
    print(f"áudio gerado: {path} (saída configurada: {sp.output})")
    try:
        route = sp.play(path)
    except Exception as e:
        print(f"falhou ao tocar: {type(e).__name__}: {e}")
        return 1
    print(f"tocado pela {route}")
    return 0


def tapo_url(g, host, auth=None):
    """URL tapo:// do go2rtc. Firmwares novos da C200 pedem admin + SHA256 MAIÚSCULO da senha."""
    import hashlib
    from urllib.parse import quote
    pw = g.tapo_cloud_password
    auth = (auth or g.tapo_auth).lower()
    if auth == "sha256":
        return f"tapo://admin:{hashlib.sha256(pw.encode()).hexdigest().upper()}@{host}"
    if auth == "md5":
        return f"tapo://admin:{hashlib.md5(pw.encode()).hexdigest().upper()}@{host}"
    return f"tapo://{quote(pw, safe='')}@{host}"


def cmd_testar_camera(a):
    """UMA tentativa de login de áudio na câmera (evita bloquear a conta); se der certo, fala."""
    import re
    import subprocess
    import tempfile
    g, cam = cfg.greeting, cfg.camera
    if not g.tapo_cloud_password:
        print("preencha greeting.tapo_cloud_password no /opt/visao/config.toml")
        return 1
    auth = a.modo or g.tapo_auth
    with tempfile.TemporaryDirectory(dir=cfg.storage.runtime_dir) as d:
        conf = os.path.join(d, "g2t.yaml")
        with open(os.open(conf, os.O_WRONLY | os.O_CREAT, 0o600), "w") as f:
            f.write("api:\n  listen: \"127.0.0.1:1985\"\nrtsp:\n  listen: \"127.0.0.1:8555\"\n"
                    "webrtc:\n  listen: \"\"\nlog:\n  level: info\n"
                    f"streams:\n  camera:\n    - \"{tapo_url(g, cam.host, auth)}\"\n")
        p = subprocess.Popen(["/opt/visao/bin/go2rtc", "-config", conf], stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True)
        try:
            time.sleep(2)
            r = subprocess.run(["timeout", "25", "ffprobe", "-v", "error", "-rtsp_transport", "tcp",
                                "-show_entries", "stream=codec_type,codec_name", "-of", "csv=p=0",
                                "rtsp://127.0.0.1:8555/camera"], capture_output=True, text=True)
        finally:
            p.terminate()
            out = p.communicate(timeout=10)[0]
    streams = r.stdout.strip().replace("\n", " ")
    if not streams:
        errs = [re.sub(r"tapo://[^@ ]*@", "tapo://***@", l) for l in out.splitlines()
                if "WRN" in l or "ERR" in l]
        print(f"[{auth}] câmera recusou: {errs[-1][-80:] if errs else 'sem resposta'}")
        return 1
    print(f"[{auth}] login aceito! streams: {streams}")
    return 0


def cmd_go2rtc_config(a):
    """Gera a configuração do go2rtc (stream da câmera com áudio de duas vias)."""
    from urllib.parse import quote
    cam, g = cfg.camera, cfg.greeting
    if g.tapo_cloud_password:
        src = tapo_url(g, cam.host)
    else:
        src = f"rtsp://{quote(cam.user, safe='')}:{quote(cam.password, safe='')}@{cam.host}:{cam.rtsp_port}/{cam.sub_stream}"
    text = ("# gerado por: visao-cli go2rtc-config (não edite; mude o /opt/visao/config.toml)\n"
            "api:\n  listen: \"127.0.0.1:1984\"\n"
            "rtsp:\n  listen: \"\"\n"
            "webrtc:\n  listen: \"\"\n"
            "log:\n  level: warn\n"
            f"streams:\n  {g.stream}:\n    - \"{src}\"\n")
    out = a.saida
    tmp = out + ".tmp"
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640), "w") as f:
        f.write(text)
    os.replace(tmp, out)
    print(f"{out} gerado ({'tapo, com áudio de duas vias' if g.tapo_cloud_password else 'rtsp, sem senha da conta Tapo: só vídeo'})")
    return 0


def main():
    if sys.argv[1:2] != ["go2rtc-config"]:
        run_as_service_user()
    p = argparse.ArgumentParser(prog="visao-cli", description="Visão — presença por reconhecimento facial")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    sub.add_parser("membros").set_defaults(fn=cmd_membros)
    sp = sub.add_parser("adicionar")
    sp.add_argument("nome")
    sp.add_argument("fotos", nargs="*")
    sp.set_defaults(fn=cmd_adicionar)
    sp = sub.add_parser("relatorio")
    sp.add_argument("--dias", type=int, default=7)
    sp.set_defaults(fn=cmd_relatorio)
    sp = sub.add_parser("senha")
    sp.add_argument("--stdin", action="store_true")
    sp.add_argument("--usuario", help="nome de usuário do login (padrão: mantém o atual)")
    sp.set_defaults(fn=cmd_senha)
    sp = sub.add_parser("go2rtc-config")
    sp.add_argument("--saida", default="/opt/visao/go2rtc.yaml")
    sp.set_defaults(fn=cmd_go2rtc_config)
    sp = sub.add_parser("testar-camera", help="uma tentativa de login de áudio na Tapo")
    sp.add_argument("--modo", choices=["sha256", "md5", "plain"])
    sp.set_defaults(fn=cmd_testar_camera)
    sp = sub.add_parser("falar")
    sp.add_argument("texto")
    sp.add_argument("--voz", choices=["piper", "espeak"], help="motor de voz (padrão: o configurado)")
    sp.set_defaults(fn=cmd_falar)
    a = p.parse_args()
    sys.exit(a.fn(a))


if __name__ == "__main__":
    main()
