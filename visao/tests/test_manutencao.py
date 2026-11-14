"""Vigia do go2rtc, ponto de partida do vídeo gravado, ouvido só com gente perto e limpeza das
pastas de fala:
    python -m tests.test_manutencao"""
import collections
import os
import tempfile

from visao.detector import partida_do_video
from visao.ouvido import FRAME_BYTES, Ouvido
from visao.speaker import Speaker
from visao.vigia_go2rtc import VigiaGo2rtc, cpu_do_processo

ok = True


def check(label, cond, extra=""):
    global ok
    ok &= bool(cond)
    print(("OK   " if cond else "FALHA"), label, extra)


# --- vigia do go2rtc
gasto = {"v": 0.0}
pedidos = []
v = VigiaGo2rtc("http://x", cpu=lambda pid: gasto["v"], pid=lambda: 77, reinicia=lambda: pedidos.append(1))
t = 1000.0
check("primeira medição só anota", v.confere(t) is False)
for i in range(5):   # uso normal: 1%
    t += 60
    gasto["v"] += 0.6
    v.confere(t)
check("uso normal não reinicia", not pedidos and v.ultimo_uso < 0.05, f"{v.ultimo_uso:.2f}")
res = []
for i in range(3):   # preso: 100% por 3 medições
    t += 60
    gasto["v"] += 60
    res.append(v.confere(t))
check("preso em 100% por 3 medições: reinicia uma vez", res == [False, False, True] and len(pedidos) == 1, f"{res}")
t += 60
gasto["v"] += 0.5
check("depois do reinício volta a só medir", v.confere(t) is False and v.confere(t + 60) is False and len(pedidos) == 1)
v2 = VigiaGo2rtc("http://x", cpu=lambda pid: gasto["v"], pid=lambda: 77, reinicia=lambda: pedidos.append(2))
t = 5000.0
v2.confere(t)
for i in range(4):
    t += 60
    gasto["v"] += 60
    r = v2.confere(t, ocupado=True)
check("câmera falando: adia o reinício", r is False and 2 not in pedidos)
t += 60
gasto["v"] += 60
check("parou de falar: reinicia", v2.confere(t) is True and 2 in pedidos)
t += 60
gasto["v"] = 1.0   # contador voltou (processo novo)
v2.confere(t)
t += 60
gasto["v"] = 0.5
check("contador que volta não conta como uso alto", v2.confere(t) is False and v2.altas == 0)
v3 = VigiaGo2rtc("http://x", cpu=lambda pid: None, pid=lambda: None, reinicia=lambda: pedidos.append(3))
check("sem go2rtc rodando: nada a fazer", v3.confere(1.0) is False and v3.confere(100.0) is False and 3 not in pedidos)
check("lê a CPU de um processo de verdade", (cpu_do_processo(os.getpid()) or 0) > 0 and cpu_do_processo(2 ** 22 + 5) is None)

# --- ponto de partida no pedaço gravado
pac = [(1.4 + i / 15, i % 30 == 0) for i in range(91)]          # quadro-chave a cada 2 s; o último é o fim do arquivo
ini, fim, chave = partida_do_video(pac)
check("último quadro-chave no fim do arquivo: parte do anterior", abs(chave - (1.4 + 4.0)) < 1e-6 and abs(fim - 7.4) < 1e-6, f"{chave:.2f}")
pac = [(1.4 + i / 15, i % 30 == 0) for i in range(80)]          # 1,27 s depois do último quadro-chave
check("quadro-chave com folga: usa o último", abs(partida_do_video(pac)[2] - 5.4) < 1e-6)
check("só um quadro-chave, no fim: usa ele mesmo", partida_do_video([(3.0, False), (3.1, True)])[2] == 3.1)
check("sem quadro-chave: do início", partida_do_video([(3.0, False), (3.1, False)])[2] == 3.0)


# --- ouvido: sem ninguém por perto não junta áudio
class VadFalso:
    def is_speech(self, frame, sr):
        return True


class Cano:
    def __init__(self, n):
        self.n = n

    def read(self, k):
        if self.n <= 0:
            return b""
        self.n -= 1
        return b"\0" * k


def roda(ativo):
    acfg = type("A", (), dict(min_speech_ms=300, end_silence_ms=700, max_utterance_s=1.5, mute_after_speech_s=0.6,
                              vad_aggressiveness=2))()
    sp = type("S", (), dict(playing=False, last_play_end=0.0))()
    o = Ouvido.__new__(Ouvido)
    o.acfg, o.speaker, o.ativo = acfg, sp, ativo
    import threading
    o._stop, o._q, o._q_ev = threading.Event(), collections.deque(maxlen=4), threading.Event()
    try:
        o._loop(VadFalso(), type("P", (), {"stdout": Cano(120)})())
    except RuntimeError:
        pass
    return len(o._q)


check("com gente perto: a fala vai para o Whisper", roda(lambda: True) >= 1)
check("sem ninguém perto: nada vai para o Whisper", roda(lambda: False) == 0)
check("sem a regra (respostas completas): escuta sempre", roda(None) >= 1)

# --- pastas de fala que sobraram de processos mortos
base = tempfile.mkdtemp()
morta = os.path.join(base, "visao-fala-999999999-abc")
viva = os.path.join(base, f"visao-fala-{os.getpid()}-abc")
antiga = os.path.join(base, "visao-fala-xyz123")
recente = os.path.join(base, "visao-fala-qwe456")
outra = os.path.join(base, "outra-coisa")
for d in (morta, viva, antiga, recente, outra):
    os.mkdir(d)
os.utime(antiga, (1.0, 1.0))
Speaker._limpa_sobras(base)
sobrou = sorted(os.listdir(base))
check("limpa só as pastas de processos mortos e as antigas",
      sobrou == sorted(os.path.basename(d) for d in (viva, recente, outra)), f"{sobrou}")
print("TUDO OK" if ok else "HÁ FALHAS")
