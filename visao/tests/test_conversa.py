"""Testes da conversa (sem câmera, sem Whisper, sem Ollama de verdade):
    python -m tests.test_conversa
"""
import json
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from visao import fala, hardware
from visao.assistente import Assistente, acha_nome, norm
from visao.config import load
from visao.llm import LLM

ok = True


def check(label, cond, extra=""):
    global ok
    ok &= bool(cond)
    print(("OK   " if cond else "FALHA"), label, extra)


class FakeSpeaker:
    def __init__(self):
        self.falas = []

    def speak_now(self, t):
        self.falas.append(t)


AGORA = datetime(2026, 11, 10, 14, 30)


def fatos():
    t = AGORA.timestamp()
    return {"agora": AGORA, "membros": ["Gazziro", "Lucas", "Mario", "Victor"],
            "na_camera": ["Victor"], "pessoa_falando": "Victor",
            "presentes": [("Victor", t - 60), ("Lucas", t - 900)],
            "hoje": [("Victor", t - 5 * 3600), ("Lucas", t - 3 * 3600)],
            "ultima_vez": {"Victor": t - 60, "Lucas": t - 900, "Mario": t - 3 * 86400}}


cfg = load("/nonexistent.toml", detect_hardware=False)
cfg.llm["model"] = ""  # sem LLM nos testes de respostas prontas
sp = FakeSpeaker()
a = Assistente(cfg, sp, fatos, LLM(cfg.llm))

# --- nome do assistente (como o Whisper costuma escrever)
for frase in ("Willbert, que horas são?", "Wilbert que horas são", "Uilbert, que horas são",
              "Will Bert, que horas são?", "ei wilber que horas sao"):
    achou, resto = acha_nome(norm(frase), cfg.assistant.wake_words)
    check(f"acha o nome em {frase!r}", achou and "horas" in resto, f"-> {resto!r}")
achou, _ = acha_nome(norm("Gilberto, me passa o cabo"), cfg.assistant.wake_words)
check("não confunde 'Gilberto' com o nome", not achou)
achou, _ = acha_nome(norm("que horas são?"), cfg.assistant.wake_words)
check("frase sem o nome não chama o assistente", not achou)

# --- respostas prontas
casos = [
    ("Willbert, que horas são?", "São duas e meia da tarde."),
    ("Willbert, que dia é hoje?", "Hoje é terça-feira, dez de novembro."),
    ("Willbert, quem está aqui?", "Estão por aqui: Victor e Lucas."),
    ("Willbert, quem chegou hoje?", "Hoje vi Victor às nove e meia da manhã e Lucas às onze e meia da manhã."),
    ("Willbert, o Mario veio hoje?", "Hoje não vi o Mario. A última vez foi há três dias."),
    ("Willbert, cadê o Lucas?", "O Lucas está por aqui. Vi ele há quinze minutos."),
    ("Willbert, quem sou eu?", "Você é o Victor!"),
    ("Willbert", "Oi, Victor! Pode falar."),
    ("Willbert, obrigado", "De nada!"),
]
for pergunta, esperado in casos:
    a.follow_until = 0
    r = a.ouviu(pergunta, ts=time.time())
    check(f"{pergunta!r}", r == esperado, f"-> {r!r}")

# --- conversa de outras pessoas é ignorada; continuação logo depois é aceita
a.follow_until = 0
check("ignora conversa sem o nome", a.ouviu("passa o café aí", ts=time.time()) is None)
a.ouviu("Willbert, que horas são?", ts=time.time())
check("continuação sem repetir o nome", a.ouviu("e que dia é hoje?", ts=time.time()) is not None)

# --- sem LLM: resposta honesta
a.follow_until = 0
r = a.ouviu("Willbert, qual a capital da França?", ts=time.time())
check("sem LLM responde que não sabe", r == "Desculpe, ainda não sei responder isso.", f"-> {r!r}")


# --- LLM falso (imita o /api/chat do Ollama)
class Ollama(BaseHTTPRequestHandler):
    pedidos = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Ollama.pedidos.append(body)
        if self.path == "/api/chat":
            out = {"message": {"role": "assistant",
                               "content": "<think>x</think>**Paris** é a capital da França! 😀 Fica a 1000 km daqui."}}
        else:
            out = {"done": True}
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


srv = ThreadingHTTPServer(("127.0.0.1", 0), Ollama)
threading.Thread(target=srv.serve_forever, daemon=True).start()
cfg.llm["url"] = f"http://127.0.0.1:{srv.server_address[1]}"
cfg.llm["model"] = "qwen3:4b"
a = Assistente(cfg, sp, fatos, LLM(cfg.llm))
r = a.ouviu("Willbert, qual a capital da França?", ts=time.time())
check("resposta do LLM fica falável (sem markdown/emoji/think, número por extenso)",
      r == "Paris é a capital da França! Fica a mil km daqui.", f"-> {r!r}")
req = Ollama.pedidos[-1]
check("pede sem 'pensar' (think=false) e com fatos do laboratório",
      req.get("think") is False and "Victor" in req["messages"][0]["content"]
      and "chegaram hoje" in req["messages"][0]["content"])
check("o que foi falado é o mesmo que foi respondido", sp.falas[-1] == r)

# --- LLM fora do ar: não trava, responde que não conseguiu
cfg.llm["url"] = "http://127.0.0.1:9"
a = Assistente(cfg, sp, fatos, LLM(cfg.llm))
t0 = time.time()
r = a.ouviu("Willbert, conta uma piada", ts=time.time())
check("LLM fora do ar: resposta de desculpas, rápido", r.startswith("Desculpe") and time.time() - t0 < 3,
      f"-> {r!r}")
r = a.ouviu("Willbert, que horas são?", ts=time.time())
check("LLM fora do ar não atrapalha as respostas prontas", r == "São duas e meia da tarde.")

# --- cumprimento pelo LLM precisa citar quem chegou
cfg.llm["url"] = f"http://127.0.0.1:{srv.server_address[1]}"
a = Assistente(cfg, sp, fatos, LLM(cfg.llm))
check("cumprimento do LLM que não cita a pessoa é descartado (usa a frase padrão)",
      a.cumprimento(["Mario"], 1, fatos(), 2) is None)

# --- perfis de hardware
h = {"arch": "x86_64", "cpu": "x", "cores": 4, "ram_gb": 8, "gpus": [], "vram_gb": 0}
check("PC fraco sem placa -> cpu", hardware.choose(h) == "cpu")
check("PC 8 núcleos/16 GB -> cpu-forte", hardware.choose(dict(h, cores=8, ram_gb=16)) == "cpu-forte")
check("RTX 3060 12 GB -> gpu-forte", hardware.choose(dict(h, gpus=[("RTX 3060", 12)], vram_gb=12)) == "gpu-forte")
check("GTX 1660 6 GB -> gpu", hardware.choose(dict(h, gpus=[("GTX 1660", 6)], vram_gb=6)) == "gpu")
check("TV Box -> tvbox", hardware.choose(dict(h, arch="armv7l", ram_gb=1)) == "tvbox")
tv = load("/nonexistent.toml", detect_hardware=False)
check("sem perfil, padrões continuam os da TV Box", tv.detection.det_width == 576 and not tv.assistant.enabled)

# --- texto falado
check("hora falada", fala.hora(datetime(2026, 1, 1, 13, 0)) == "uma hora da tarde")
check("número por extenso", fala.numero(2026) == "dois mil e vinte e seis")
srv.shutdown()
print("TUDO OK" if ok else "HÁ FALHAS")
