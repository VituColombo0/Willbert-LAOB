"""Testes da conversa (sem câmera, sem Whisper, sem Ollama de verdade):
    python -m tests.test_conversa
"""
import json
import os
import numpy as np
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
check("sem LLM diz o que dá para pedir", r == a.NAO_ENTENDI, f"-> {r!r}")


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
# --- caixa de areia por voz: brincadeiras e cores pelo painel (Projetor-LAOB)
mandados = []
estado_areia = {"tesouro": "escondido", "tesouros_achados": 2, "fossil": "nenhum", "labirinto": "correndo",
                "labirinto_rodadas": 1}
def acao_areia(caminho, dados=None):
    if dados is None:
        return estado_areia
    mandados.append((caminho, dados))
    return True
aa = Assistente(cfg, sp, fatos, LLM(cfg.llm), acoes={"areia": acao_areia, "areia_rosto": lambda n: True})
casos_areia = [
    ("Willbert, coloca o tema vulcão", ("/tema", {"tema": "vulcao"}), "vulcão"),
    ("Willbert, transforma a areia em neve", ("/tema", {"tema": "inverno"}), "inverno"),
    ("Willbert, muda as cores para arco-íris", ("/tema", {"tema": "arco-iris"}), "arco-íris"),
    ("Willbert, liga a caça ao tesouro", ("/brincar", {"acao": "tesouro"}), "tesouro"),
    ("Willbert, solta os peixes para a gente", ("/brincar", {"acao": "peixes"}), "peixes"),
    ("Willbert, coloca os carrinhos", ("/brincar", {"acao": "carros"}), "carrinhos"),
    ("Willbert, solta os peixes e os carrinhos", ("/brincar", {"acao": "todos"}), "Peixes no lago"),
    ("Willbert, solta os bichos", ("/brincar", {"acao": "todos"}), "Peixes no lago"),
    ("Willbert, vamos brincar de labirinto de água", ("/brincar", {"acao": "labirinto"}), "Labirinto de água ligado"),
    ("Willbert, outro labirinto", ("/brincar", {"acao": "labirintoNovo"}), "Rodada nova"),
    ("Willbert, enterra um triceratops", ("/brincar", {"acao": "triceratops"}), "Triceratops"),
    ("Willbert, quero escavar um dinossauro", ("/brincar", {"acao": "trex"}), "Tiranossauro"),
    ("Willbert, para as brincadeiras", ("/brincar", {"acao": "parar"}), "parei"),
    ("Willbert, tira os peixes", ("/brincar", {"acao": "parar"}), "parei"),
    ("Willbert, volta para a topografia", ("/modo", {"modo": "original"}), "topografia"),
    ("Willbert, volta a areia ao normal", ("/modo", {"modo": "original"}), "topografia"),
]
for frase, esperado, trecho in casos_areia:
    aa.follow_until = 0
    n0 = len(mandados)
    t0 = time.perf_counter()
    r = aa.ouviu(frase)
    ms = (time.perf_counter() - t0) * 1000
    check(f"{frase!r} -> {esperado[1]}", mandados[n0:] == [esperado] and trecho in (r or ""), f"-> {r!r} {mandados[n0:]}")
check("comando da areia responde na hora (sem LLM)", ms < 50, f"{ms:.1f} ms")
for frase, trecho in (("Willbert, quantos tesouros vocês acharam?", "acharam dois tesouros"),
                      ("Willbert, a água chegou no labirinto?", "ainda não chegou ao alvo. Vocês já venceram uma rodada."),
                      ("Willbert, quantos fósseis acharam?", "Não há fóssil")):
    aa.follow_until = 0
    n0 = len(mandados)
    r = aa.ouviu(frase)
    check(f"placar {frase!r}", trecho in (r or "") and len(mandados) == n0, f"-> {r!r}")
for frase in ("Willbert, grave o meu rosto", "Willbert, me coloca na areia"):
    aa.follow_until = 0
    n0 = len(mandados)
    r = aa.ouviu(frase)
    check(f"{frase!r} continua sendo a foto", r.startswith("Sorria") and len(mandados) == n0, f"-> {r!r}")
aa.follow_until = 0
check("frase sem pedido sobre a areia não vira comando",
      aa._areia_comando(norm("o mar estava bonito ontem"), fatos()) is None)
check("sem a caixa de areia ligada, avisa",
      a.ouviu("Willbert, liga a caça ao tesouro") == "A caixa de areia não está ligada a mim agora.")
check("frases da areia ficam com a voz em cache", all(t in aa._fixas for t in aa.AREIA_FIXAS.values()))
# o detector conversa com o painel por HTTP (painel falso)
from visao.detector import painel_areia
class PainelFalso(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b'{"labirinto": "nenhum"}')
    def do_POST(self):
        dados = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        ruim = dados.get("tema") == "xyz"
        self.send_response(400 if ruim else 200); self.end_headers()
        self.wfile.write(json.dumps({"ok": not ruim, "msg": "tema inválido" if ruim else "feito"}).encode())
srv = ThreadingHTTPServer(("127.0.0.1", 0), PainelFalso)
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_address[1]}"
check("painel: GET do estado", painel_areia(base + "/estado") == {"labirinto": "nenhum"})
check("painel: POST aceito", painel_areia(base + "/tema", {"tema": "vulcao"}) == {"ok": True, "msg": "feito"})
check("painel: recusa vira ok False com o motivo", painel_areia(base + "/tema", {"tema": "xyz"}) ==
      {"ok": False, "msg": "tema inválido"})
srv.shutdown()
check("painel desligado: None", painel_areia("http://127.0.0.1:9/estado", timeout=1) is None)
# --- ffmpeg do Ubuntu 22.04 (4.4) x novos: opções certas para cada versão
from visao.camera import ffmpeg_versao_do_texto, opcoes_quadros_sem_repetir, opcoes_rtsp_timeout
check("versão do ffmpeg 4.4 do Ubuntu", ffmpeg_versao_do_texto("ffmpeg version 4.4.2-0ubuntu0.22.04.1 Copyright") == (4, 4))
check("versão do ffmpeg 6.1 e compilação do git", ffmpeg_versao_do_texto("ffmpeg version n6.1.1") == (6, 1)
      and ffmpeg_versao_do_texto("ffmpeg version N-112233-gabc") == (99, 0))
check("ffmpeg 4.4: -stimeout e -vsync 0", opcoes_rtsp_timeout(5, (4, 4)) == ["-stimeout", "5"]
      and opcoes_quadros_sem_repetir((4, 4)) == ["-vsync", "0"])
check("ffmpeg 6: -timeout e -fps_mode", opcoes_rtsp_timeout(5, (6, 1)) == ["-timeout", "5"]
      and opcoes_quadros_sem_repetir((6, 1)) == ["-fps_mode", "passthrough"])
import subprocess as _sp
from visao.camera import ErrosFfmpeg
_p = _sp.Popen(["sh", "-c", "echo 'rtsp://usuario:segredo@192.168.0.166:554/stream1: 401 Unauthorized' >&2"],
               stderr=_sp.PIPE, stdout=_sp.DEVNULL)
_e = ErrosFfmpeg(_p); _p.wait(); time.sleep(0.2)
check("erro do ffmpeg vai para o log sem a senha", "401" in _e.texto() and "segredo" not in _e.texto()
      and "usuario" not in _e.texto(), _e.texto())
import io as _io
from visao.ouvido import le_exato
class CanoQuebrado(_io.RawIOBase):
    """Entrega no máximo 7 bytes por leitura, como um cano real em pedaços."""
    def __init__(self, dados): self.d = dados
    def readable(self): return True
    def read(self, n=-1):
        b, self.d = self.d[:min(n, 7)], self.d[min(n, 7):]
        return b
c = CanoQuebrado(bytes(range(50)))
check("áudio em pedaços quebrados é juntado", le_exato(c, 20) == bytes(range(20)) and le_exato(c, 30) == bytes(range(20, 50)))
check("fim de verdade do áudio é percebido", le_exato(c, 10) is None)
# --- sem LLM: só comandos fixos, tolerantes a erro de escuta (casos reais do lab2, 2026-11-11)
from visao.assistente import falacao
fotos_sem = []
sem = Assistente(cfg, sp, fatos, None, acoes={"areia_rosto": lambda n: fotos_sem.append(n) or True,
                                              "areia": acao_areia})
for ouvido_errado in ("Não. É que está ficando, né? Willbert, grave o meu gosto.",
                      "Willbert, grado o meu rosto. Brate meu rosto.", "Willbert, grado o meu rosto.",
                      "Wilbert, grava meu rosto"):
    sem.follow_until = 0
    t0 = time.perf_counter()
    r = sem.ouviu(ouvido_errado)
    ms = (time.perf_counter() - t0) * 1000
    check(f"ouvido errado {ouvido_errado!r} -> foto na areia ({ms:.0f} ms)", (r or "").startswith("Sorria") and ms < 100, f"-> {r!r}")
check("as 4 viraram foto", len(fotos_sem) == 4)
sem.follow_until = 0
n0 = len(mandados)
r = sem.ouviu("Willbert, coloca o tema vucão")
check("'tema vucão' -> vulcão", mandados[n0:] == [("/tema", {"tema": "vulcao"})], f"-> {r!r}")
sem.follow_until = 0
r = sem.ouviu("Willbert, qual é a capital da França?")
check("pergunta fora dos comandos: explica o que dá para pedir, na hora", r == sem.NAO_ENTENDI, f"-> {r!r}")
check("e essa resposta tem voz em cache", sem.NAO_ENTENDI in sem._fixas)
sem.follow_until = time.time() + 30   # continuação sem dizer o nome
check("conversa dos outros na continuação: silêncio", sem.ouviu("ontem o jogo foi muito bom") is None)
check("pedido na continuação sem o nome ainda vale", bool(sem.ouviu("que horas são")))
babel = "Você é um ex-venginho de ele, " + "o que ele tem, " * 30
check("falação repetida do Whisper é ignorada", falacao(norm(babel)) and sem.ouviu("Willbert, " + babel) is None)
check("frase normal não é falação", not falacao(norm("Willbert, liga a caça ao tesouro")))
check("frase sem nada a ver não vira comando", sem.frase_parecida(norm("o mar estava bonito ontem")) is None)
check("ajuda sem 'eu penso um pouco'", "penso" not in sem._ajuda("ajuda", None))
# o detector roda como "python -m visao.detector": tudo o que ele usa tem que estar definido ANTES
# do main() (funções depois do if __name__ nunca existem no serviço; foi o erro do multipart_foto)
import ast as _ast, inspect as _inspect, visao.detector as _det
_arv = _ast.parse(_inspect.getsource(_det))
_main_i = next(i for i, n in enumerate(_arv.body) if isinstance(n, _ast.If) and "__main__" in _ast.unparse(n.test))
check("nada definido depois do 'if __name__' no detector", _main_i == len(_arv.body) - 1)
# aprendizado automático só depois de 3 fotos cadastradas por gente (engano não vira amostra)
import sqlite3 as _sq
from visao import db as _db
from visao.config import Section as _Sec
_con = _db.connect(":memory:")
_con.execute("INSERT INTO members(id, name, created_at) VALUES(1, 'Lucas', 0)")
_d = _det.Detector.__new__(_det.Detector)
_d.con, _d.cfg = _con, load("/nonexistent.toml", detect_hardware=False)
check("padrão: aprende desde a 1ª foto cadastrada", int(_d.cfg.detection.min_manual) == 1)
_d.cfg.detection["min_manual"] = 3   # modo cauteloso
def _amostra(fonte):
    _con.execute("INSERT INTO samples(member_id, source, status, created_at) VALUES(1, ?, 'ok', 0)", (fonte,))
_amostra("foto")
for _ in range(9):
    _amostra("auto")
check("1 foto (+9 aprendidas sozinho): não aprende mais", not _d.pode_aprender(1))
_amostra("camera"); _amostra("foto")
check("3 fotos cadastradas: volta a aprender", _d.pode_aprender(1))
# --- gesto: mãos abertas ao lado do rosto tiram a foto da areia (casos medidos no lab2)
from visao.gesto import Gesto, ancoras, maos_ao_lado
from visao.faces import Face
check("detector de palmas: 2016 âncoras", ancoras().shape == (2016, 2))
rosto = (950, 450, 160, 200)   # x, y, largura, altura (quadro 1920x1080, medido às 18:35)
pal = lambda cx, cy: (cx - 60, cy - 60, cx + 60, cy + 60, 0.95)
check("palma de cada lado, na altura do rosto: gesto", maos_ao_lado(rosto, [pal(730, 580), pal(1340, 600)]))
check("uma palma só: não é gesto", not maos_ao_lado(rosto, [pal(730, 580)]))
check("as duas do mesmo lado: não é gesto", not maos_ao_lado(rosto, [pal(730, 580), pal(800, 560)]))
check("mãos lá embaixo (na mesa): não é gesto", not maos_ao_lado(rosto, [pal(730, 900), pal(1340, 900)]))
check("mãos longe do rosto (outra pessoa): não é gesto", not maos_ao_lado(rosto, [pal(100, 580), pal(1800, 600)]))
g = Gesto(segurar_s=1.5, pausa_s=20)
seq = [g.leitura(True, 100 + 0.5 * i) for i in range(4)]
check("segurando 1,5 s dispara uma vez", seq == [False, False, False, True], f"{seq}")
check("continuar segurando não dispara de novo", not any(g.leitura(True, 102 + 0.5 * i) for i in range(10)))
g2 = Gesto(segurar_s=1.5)
g2.leitura(True, 0); g2.leitura(True, 0.5); g2.leitura(False, 1.0)
check("uma leitura falhada no meio não zera", g2.leitura(True, 1.2) is False and g2.leitura(True, 1.6) is True)
g3 = Gesto(segurar_s=1.5)
g3.leitura(True, 0); g3.leitura(False, 2.0)
check("mão abaixada por mais tempo zera", g3.leitura(True, 2.1) is False and g3.leitura(True, 3.0) is False)
# detector: o gesto chama a foto da areia e fala a contagem
_g = _det.Detector.__new__(_det.Detector)
_g.cfg = load("/nonexistent.toml", detect_hardware=False)
_g.gesto, _g.last_palm = Gesto(segurar_s=1.0, pausa_s=20), 0.0
_g.palmas = type("P", (), {"detecta": lambda self, f: [pal(730, 580), pal(1340, 600)]})()
from visao.tracker import Track
_row = np.array([950, 450, 160, 200] + [0] * 10 + [0.9], np.float32)
_g.tracker = type("T", (), {"tracks": {1: Track(1, Face(_row), 0, 0, member_id=4)}})()
_g.gallery = type("G", (), {"names": {4: "Lucas"}})()
_falas, _fotos = [], []
_g.speaker = type("S", (), {"speak_fixed": lambda self, t: _falas.append(t)})()
_g.foto_para_areia = lambda nome, tid=None: _fotos.append(nome) or True
for i in range(6):
    _g.tracker.tracks[1].last_ts = 10 + 0.3 * i
    _g.check_gesture(None, 10 + 0.3 * i)
check("gesto do Lucas: fala a contagem e manda a foto dele", _fotos == ["Lucas"] and _falas == [_g.cfg.gesture.phrase],
      f"{_fotos} {_falas}")
_g.tracker.tracks[1].face = Face(np.array([950, 450, 30, 36] + [0] * 10 + [0.9], np.float32))
_g.gesto = Gesto(segurar_s=1.0); _fotos.clear()
for i in range(6):
    _g.check_gesture(None, 30 + 0.3 * i)
check("rosto pequeno (longe da câmera): não procura o gesto", _fotos == [])
# --- foto da areia: 1080p do gravador e imagem melhorada
import shutil as _sh, subprocess as _sp2, tempfile as _tf
from visao.detector import melhora_foto
_peq = (np.random.default_rng(1).integers(0, 255, (368, 311, 3))).astype(np.uint8)
_m = melhora_foto(_peq)
check("foto pequena é ampliada para 720 px de altura", _m.shape[0] == 720 and abs(_m.shape[1] / 720 - 311 / 368) < 0.01, f"{_m.shape}")
check("foto grande não é ampliada", melhora_foto(np.zeros((900, 700, 3), np.uint8)).shape[:2] == (900, 700))
if _sh.which("ffmpeg"):
    _dir = _tf.mkdtemp()
    _sp2.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=1920x1080:rate=10", "-t", "2",
              "-c:v", "libx264", "-pix_fmt", "yuv420p", os.path.join(_dir, "seg_20261111-180000.ts")], check=True)
    _q = _det.Detector.__new__(_det.Detector)
    _q.rec = type("R", (), {"dir": _dir})()
    _img = _q.quadro_alta()
    check("quadro 1080p tirado do pedaço gravado", _img is not None and _img.shape[:2] == (1080, 1920))
    _q.rec = None
    check("sem gravador: sem quadro 1080p (usa o vídeo de análise)", _q.quadro_alta() is None)
    _sh.rmtree(_dir)
print("TUDO OK" if ok else "HÁ FALHAS")
