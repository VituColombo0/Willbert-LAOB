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

    def speak_fixed(self, t):  # frase fixa: voz do cache
        self.falas.append(t)
        self.fixas = getattr(self, "fixas", []) + [t]


AGORA = datetime(2026, 11, 10, 14, 30)


def fatos():
    t = AGORA.timestamp()
    return {"agora": AGORA, "membros": ["Gazziro", "Lucas", "Mario", "Victor"],
            "na_camera": ["Victor"], "pessoa_falando": "Victor",
            "presentes": [("Victor", t - 60), ("Lucas", t - 900)],
            "hoje": [("Gazziro", t - 6 * 3600), ("Victor", t - 5 * 3600), ("Lucas", t - 3 * 3600)],
            "ultima_vez": {"Victor": t - 60, "Lucas": t - 900, "Gazziro": t - 2 * 3600, "Mario": t - 3 * 86400},
            "saidas_hoje": [("Gazziro", t - 2 * 3600)], "ontem": [("Victor", t - 86400 - 4 * 3600)],
            "dias_semana": {"Victor": 5, "Lucas": 1, "Gazziro": 2}, "visitantes_hoje": 2}


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
check("Whisper errando 'que aora são' ainda vira pergunta de hora",
      norm("Willbert, que aora são.") == "willbert que hora sao" and norm("embora agora") == "embora agora")
achou, _ = acha_nome(norm("que horas são?"), cfg.assistant.wake_words)
check("frase sem o nome não chama o assistente", not achou)

# --- respostas prontas
casos = [
    ("Willbert, que horas são?", "São duas e meia da tarde."),
    ("Willbert, que dia é hoje?", "Hoje é terça-feira, dez de novembro."),
    ("Willbert, quem está aqui?", "Estão por aqui: Victor e Lucas."),
    ("Willbert, quem chegou hoje?",
     "Hoje vi Gazziro às oito e meia da manhã, Victor às nove e meia da manhã e Lucas às onze e meia da manhã."),
    ("Willbert, o Mario veio hoje?", "Hoje não vi o Mario. A última vez foi há três dias."),
    ("Willbert, cadê o Lucas?", "O Lucas está por aqui. Vi ele há quinze minutos."),
    ("Willbert, quem sou eu?", "Você é o Victor!"),
    ("Willbert", "Oi, Victor! Pode falar."),
    ("Willbert, obrigado", "De nada!"),
    # novas (todas locais, sem LLM)
    ("Willbert, quem saiu?", "Saíram hoje: Gazziro ao meio-dia e meia."),
    ("Willbert, quem já foi embora?", "Saíram hoje: Gazziro ao meio-dia e meia."),
    ("Willbert, o Gazziro já foi embora?", "O Gazziro saiu ao meio-dia e meia."),
    ("Willbert, o Victor já saiu?", "O Victor ainda está por aqui."),
    ("Willbert, o Gazziro veio hoje?", "O Gazziro chegou hoje às oito e meia da manhã, e saiu ao meio-dia e meia."),
    ("Willbert, que horas o Lucas chegou?", "O Lucas chegou hoje às onze e meia da manhã."),
    ("Willbert, quem chegou primeiro?", "O primeiro a chegar foi o Gazziro, às oito e meia da manhã."),
    ("Willbert, quem foi o último a chegar?", "O último a chegar foi o Lucas, às onze e meia da manhã."),
    ("Willbert, quem veio ontem?", "Ontem vi Victor."),
    ("Willbert, o Mario veio ontem?", "Ontem não vi o Mario."),
    ("Willbert, quantas vezes o Victor veio essa semana?", "Nos últimos sete dias o Victor veio cinco dias."),
    ("Willbert, quantas pessoas tem aqui?", "Tem dois membros por aqui agora."),
    ("Willbert, apareceu algum visitante hoje?", "Hoje a câmera viu alguém que não é membro duas vezes."),
    ("Willbert, que dia da semana é hoje?", "Hoje é terça-feira."),
    ("Willbert, que dia é amanhã?", "Amanhã é quarta-feira, onze de novembro."),
    ("Willbert, em que mês estamos?", "Estamos em novembro de dois mil e vinte e seis."),
    ("Willbert, quanto é 7 x 8?", "Dá cinquenta e seis."),
    ("Willbert, quanto é 10 dividido por 4?", "Dá dois vírgula cinco."),
    ("Willbert, quanto é 2 + 3?", "Dá cinco."),
    ("Willbert, quem é você?", "Eu sou o Willbert, o assistente do laboratório. Reconheço quem chega e respondo perguntas."),
    ("Willbert, boa tarde", "Boa tarde, Victor!"),
    ("Willbert, tudo bem?", "Tudo ótimo por aqui! E com você?"),
]
for pergunta, esperado in casos:
    a.follow_until = 0
    r = a.ouviu(pergunta, ts=time.time())
    check(f"{pergunta!r}", r == esperado, f"-> {r!r}")

# --- "Willbert, grave o meu rosto": foto de quem fala vai para a caixa de areia
pedidos = []
ar = Assistente(cfg, sp, fatos, LLM(cfg.llm), acoes={"areia_rosto": lambda n: pedidos.append(n) or True})
for frase in ("Willbert, grave o meu rosto", "Willbert, tira uma foto minha", "Willbert, me coloca na areia",
              "Willbert, grava meu rosto na areia"):
    ar.follow_until = 0
    r = ar.ouviu(frase)
    check(f"{frase!r} -> foto para a areia", r == "Sorria, Victor! Em alguns segundos você aparece na areia.",
          f"-> {r!r}")
check("a foto é de quem está falando", pedidos == ["Victor"] * 4, f"-> {pedidos}")
check("sem a caixa de areia ligada, avisa", a.ouviu("Willbert, grave o meu rosto") ==
      "A caixa de areia não está ligada a mim agora.")
f_sem = dict(fatos(), na_camera=[], pessoa_falando=None)
ar2 = Assistente(cfg, sp, lambda: f_sem, LLM(cfg.llm), acoes={"areia_rosto": lambda n: True})
check("ninguém na câmera: pede para olhar", ar2.ouviu("Willbert, grave o meu rosto").startswith("Não estou te vendo"))
f_visita = dict(fatos(), na_camera=[], pessoa_falando=None, rostos=1)
pv = []
ar4 = Assistente(cfg, sp, lambda: f_visita, LLM(cfg.llm), acoes={"areia_rosto": lambda n: pv.append(n) or True})
check("pessoa não cadastrada também vai para a areia",
      ar4.ouviu("Willbert, grave o meu rosto") == "Sorria! Em alguns segundos você aparece na areia." and pv == [None])
ar3 = Assistente(cfg, sp, fatos, LLM(cfg.llm), acoes={"areia_rosto": lambda n: False})
check("falha ao mandar: avisa", ar3.ouviu("Willbert, grave o meu rosto").startswith("Não consegui mandar"))
from email.parser import BytesParser
from visao.detector import multipart_foto
corpo, borda = multipart_foto(b"\xff\xd8JPEG", "willbert-Victor.jpg")
msg = BytesParser().parsebytes(f"Content-Type: multipart/form-data; boundary={borda}\r\n\r\n".encode() + corpo)
parte = msg.get_payload()[0]
check("corpo multipart com o campo foto", parte.get_param("name", header="content-disposition") == "foto"
      and parte.get_filename() == "willbert-Victor.jpg" and parte.get_payload(decode=True) == b"\xff\xd8JPEG")
a.follow_until = 0

# --- conversa de outras pessoas é ignorada; continuação logo depois é aceita
a.follow_until = 0
check("ignora conversa sem o nome", a.ouviu("passa o café aí", ts=time.time()) is None)
a.ouviu("Willbert, que horas são?", ts=time.time())
check("continuação sem repetir o nome", a.ouviu("e que dia é hoje?", ts=time.time()) is not None)
r = a.ouviu("repete", ts=time.time())
check("repete a última resposta", r == "Hoje é terça-feira, dez de novembro.", f"-> {r!r}")
r = a.ouviu("tchau", ts=time.time())
check("tchau encerra a conversa", r == "Tá bom. Até mais!" and a.follow_until == 0)
check("depois do tchau, conversa dos outros é ignorada", a.ouviu("que horas são", ts=time.time()) is None)
a.follow_until = 0
p1, p2 = a.ouviu("Willbert, conta uma piada"), a.ouviu("Willbert, outra piada")
check("piadas locais e diferentes", p1 in a.PIADAS and p2 in a.PIADAS and p1 != p2, f"-> {p1!r}")
check("frases fixas usam a voz do cache", "De nada!" in getattr(sp, "fixas", []) and p1 in sp.fixas)
check("respostas que mudam não vão para o cache", "São duas e meia da tarde." not in sp.fixas)
t0 = time.time()
for _ in range(200):
    a.follow_until = 0
    a.ouviu("Willbert, quem saiu hoje?")
ms = (time.time() - t0) / 200 * 1000
check(f"resposta pronta em menos de 5 ms ({ms:.2f} ms)", ms < 5)

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
r = a.ouviu("Willbert, qual a capital da Itália?", ts=time.time())
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
h = {"arch": "x86_64", "cpu": "x", "cores": 6, "ram_gb": 8, "gpus": [], "vram_gb": 0, "avx2": True}
check("PC sem placa, 6 núcleos -> cpu", hardware.choose(h) == "cpu")
check("i5 antigo (4 núcleos, sem AVX2) -> cpu-basico",
      hardware.choose(dict(h, cores=4, avx2=False)) == "cpu-basico")
check("perfil cpu-basico usa Whisper base", hardware.PROFILES["cpu-basico"]["stt"]["model"] == "base")
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
# LLM lento: avisa que está pensando e depois responde
class SpAviso:
    playing, last_play_end = False, 0
    def __init__(self): self.falas = []
    def speak_now(self, t): self.falas.append(t)
class LLMLento:
    enabled = True
    def chat(self, *a, **k): time.sleep(0.5); return "Brasília é a capital."
cfg_l = load("/nonexistent.toml", detect_hardware=False); cfg_l.assistant["thinking_s"] = 0.2
sp_a = SpAviso()
al = Assistente(cfg_l, sp_a, lambda: {"agora": datetime.now(), "membros": [], "na_camera": [], "pessoa_falando": None,
                                      "presentes": [], "hoje": [], "ultima_vez": {}}, LLMLento())
al.ouviu("Willbert, qual a capital do Brasil?")
check(f"LLM lento: aviso antes da resposta {sp_a.falas}", len(sp_a.falas) == 2 and sp_a.falas[0] in cfg_l.assistant.thinking_phrases)
from visao.llm import cortar_frase_incompleta
check("resposta cortada pelo limite fica só com frases completas",
      cortar_frase_incompleta("A capital do Brasil é Brasília. Também é o") == "A capital do Brasil é Brasília.")
check("resposta sem frase completa fica como está", cortar_frase_incompleta("Sim") == "Sim")
print("TUDO OK" if ok else "HÁ FALHAS")
