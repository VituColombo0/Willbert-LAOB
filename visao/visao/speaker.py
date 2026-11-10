"""Cumprimento falado: gera a frase com espeak-ng (offline) e toca no alto-falante.

Saídas (greeting.output):
  local  - saída de som da própria TV Box (conector AV de 3,5 mm ou HDMI), via aplay;
  camera - alto-falante da Tapo, pelo go2rtc (precisa da senha da conta Tapo);
  auto   - tenta a câmera e, se falhar, toca na saída local.
O detector só enfileira; a fala roda numa thread separada.
"""
import hashlib
import json
import logging
import os
import queue
import random
import re
import signal
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import struct
import tempfile
import wave
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .fala import saudacao  # noqa: F401  (usado aqui e importado de speaker por outros módulos)

import numpy as np

log = logging.getLogger("visao.speaker")


_SEG_END = np.array([0x1F, 0x3F, 0x7F, 0xFF, 0x1FF, 0x3FF, 0x7FF, 0xFFF])


def to_alaw(pcm16):
    """PCM 16 bits -> G.711 A-law (o formato que o alto-falante da C200 recebe)."""
    v = pcm16.astype(np.int32) >> 3
    mask = np.where(v >= 0, 0xD5, 0x55)
    v = np.where(v >= 0, v, -v - 1)
    seg = np.searchsorted(_SEG_END, v)
    aval = np.where(seg < 2, (v >> 1) & 0xF, (v >> np.minimum(seg, 7)) & 0xF) | (np.minimum(seg, 7) << 4)
    aval = np.where(seg >= 8, 0x7F, aval)
    return (aval ^ mask).astype(np.uint8)


def resample(x, sr, out_sr=8000):
    """Reamostra (com filtro passa-baixa) para 8 kHz, só com numpy."""
    if sr == out_sr:
        return x
    spec = np.fft.rfft(x)
    freqs = np.fft.rfftfreq(len(x), 1.0 / sr)
    spec[freqs > out_sr * 0.475] = 0
    y = np.fft.irfft(spec, len(x))
    n = int(len(x) * out_sr / sr)
    return np.interp(np.arange(n) * (sr / out_sr), np.arange(len(x)), y)


def alaw_wav(data):
    """Cabeçalho WAV para A-law 8 kHz mono (formato 6, como o ffmpeg grava)."""
    fmt = struct.pack("<HHIIHHH", 6, 1, 8000, 8000, 1, 8, 0)
    return (b"RIFF" + struct.pack("<I", 4 + 8 + len(fmt) + 8 + len(data)) + b"WAVE"
            + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(data)) + data)


class _AudioServer:
    """Servidor HTTP só em 127.0.0.1 que entrega a fala ao go2rtc no ritmo real.
    Assim o go2rtc não precisa abrir o ffmpeg (que leva ~4 s só para iniciar na TV Box)."""

    def __init__(self):
        clips = self.clips = {}

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                item = clips.get(self.path.strip("/"))
                if item is None:
                    self.send_error(404)
                    return
                data = item["data"]
                item["first"] = time.monotonic()
                self.send_response(200)
                self.send_header("Content-Type", "audio/wav")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                head, body = data[:46], data[46:]
                self.wfile.write(head)
                t0, sent, chunk = time.monotonic(), 0, 160  # 20 ms por pacote
                burst = 1600  # 0,2 s adiantado para o go2rtc não ficar sem áudio
                while sent < len(body):
                    self.wfile.write(body[sent:sent + chunk])
                    sent += chunk
                    wait = t0 + (sent - burst) / 8000.0 - time.monotonic()
                    if wait > 0:
                        time.sleep(wait)
                item["done"].set()

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True, name="audio-http").start()




class Speaker:
    def __init__(self, gcfg, cache_dir):
        self.cfg = gcfg
        self.output = str(gcfg.output).lower()
        # câmera só entra no jogo se houver senha da conta Tapo
        self.camera_ok = self.output in ("camera", "auto") and bool(gcfg.tapo_cloud_password)
        self.local_ok = self.output in ("local", "auto")
        self.enabled = bool(gcfg.enabled and (self.camera_ok or self.local_ok))
        self.cache = cache_dir
        os.makedirs(cache_dir, exist_ok=True)
        self.q = queue.Queue(maxsize=5)
        self.results = queue.Queue()  # (member_id, ok, ts) para o detector registrar
        self.last_ok, self.last_error, self.spoken, self.cam_fails = None, None, 0, 0
        self.last_route = None
        self._piper_lock = threading.Lock()
        self._gen_lock = threading.Lock()
        self._gen_list, self._gen_queued, self._gen_running = [], set(), False
        self._piper_broken, self._piper_fails = False, 0
        self._piper_proc, self._paused = None, False
        self._audio = None
        self.playing, self.last_play_end = False, 0.0  # o ouvido fica surdo enquanto a câmera fala
        base = "/dev/shm" if os.path.isdir("/dev/shm") else None
        self._tmp = tempfile.mkdtemp(prefix="visao-fala-", dir=base)
        self.last_connect_s = None
        if gcfg.enabled and not self.enabled:
            self.last_error = "saída 'camera' sem a senha da conta Tapo (greeting.tapo_cloud_password)"

    def start(self):
        if self.enabled:
            threading.Thread(target=self._run, daemon=True, name="speaker").start()

    def _templates(self, names, people):
        names = list(names)
        people = max(people or 0, len(names))
        if people <= 1 and len(names) == 1:
            return "one", self.cfg.messages_one, {"nome": names[0]}
        if people == 2 and len(names) == 2:
            a, b = sorted(names)  # ordem fixa: a frase pronta serve para a dupla nos dois casos
            return "two", self.cfg.messages_two, {"nome1": a, "nome2": b}
        return "group", self.cfg.messages_group, {}

    def phrase(self, names, people=None, hour=None, pick=None):
        """Monta o cumprimento: individual, para duas pessoas ou para o grupo."""
        _, options, fmt = self._templates(names, people)
        tpl = options[pick % len(options)] if pick is not None else random.choice(options)
        return tpl.format(saudacao=saudacao(hour), **fmt)

    def candidates(self, names, people=None):
        """Frases possíveis, em ordem de preferência (variações sorteadas; dupla cai para grupo)."""
        kind, options, fmt = self._templates(names, people)
        sau = saudacao()
        out = [t.format(saudacao=sau, **fmt) for t in random.sample(options, len(options))]
        if kind == "two":
            out += [t.format(saudacao=sau) for t in random.sample(self.cfg.messages_group,
                                                                   len(self.cfg.messages_group))]
        return out

    def greet(self, names, member_ids, people=None):
        """Fala uma frase já pronta na voz natural; se nenhuma estiver pronta, usa o espeak agora
        e deixa a voz natural sendo gerada para a próxima vez (nunca atrasa o cumprimento)."""
        options = self.candidates(names, people)
        if self.engine() == "piper":
            # 1º a voz escolhida; enquanto ela gera as frases, a voz anterior (se tiver a frase)
            for model, ls in [(self.cfg.piper_model, self.cfg.piper_length_scale)] + \
                    [(m, 1.0) if isinstance(m, str) else tuple(m) for m in self.cfg.piper_fallback]:
                for t in options:
                    path = self._path(t, "piper", model, ls)
                    if os.path.exists(path):
                        self.say(t, list(member_ids), "piper", path)
                        if model != self.cfg.piper_model:
                            self.generate_later([t])
                            return t + " (voz anterior; a nova está sendo gerada)"
                        return t
            self.generate_later([options[0]])
            if self.cfg.live_synth:  # PC: o Piper gera em ~0,3 s, então fala já na voz natural
                self.say(options[0], list(member_ids), "piper")
                return options[0]
            self.say(options[0], list(member_ids), "espeak")
            return options[0] + " (voz reserva; a natural está sendo gerada)"
        self.say(options[0], list(member_ids), "espeak")
        return options[0]

    def speak_now(self, text):
        """Fala uma resposta da conversa: gerada na hora e descartada depois (não vai pro cache)."""
        self.say(text, None, "piper" if self.engine() == "piper" else "espeak", temp=True)

    def speak_fixed(self, text):
        """Resposta que se repete (de nada, ajuda, piadas...): a voz fica no cache em disco,
        então da segunda vez em diante sai na hora, sem gerar de novo."""
        engine = "piper" if self.engine() == "piper" else "espeak"
        path = self._path(text, engine)
        self.say(text, None, engine, path=path if os.path.exists(path) else None)

    def say(self, text, member_id=None, engine=None, path=None, temp=False):
        if not self.enabled:
            return
        try:
            self.q.put_nowait((text, member_id, engine, path, temp))
        except queue.Full:
            log.warning("fila de fala cheia; descartando: %s", text)
            self.results.put((member_id, False, time.time()))

    def prewarm(self, names):
        """Gera na voz natural, em segundo plano, todas as frases dos membros (período atual
        primeiro). Fica salvo em disco: só é refeito se mudar a voz, a frase ou o nome."""
        if not self.enabled or self.engine() != "piper":
            return
        now = datetime.now().hour
        hours = [now] + [h for h in (9, 15, 20) if saudacao(h) != saudacao(now)]
        texts = []
        names = sorted(names)
        pairs = ([(a, b) for i, a in enumerate(names) for b in names[i + 1:]]
                 if len(names) <= 8 else [])  # duplas: cresce rápido com muitos membros
        for h in hours:
            texts += [self.phrase([n], 1, h, i) for n in names for i in range(len(self.cfg.messages_one))]
            texts += [self.phrase([], 3, h, i) for i in range(len(self.cfg.messages_group))]
        for h in hours:  # depois das individuais e de grupo, as duplas
            texts += [self.phrase([a, b], 2, h, i) for a, b in pairs
                      for i in range(len(self.cfg.messages_two))]
        self.prepare_cached(texts)
        self.generate_later(texts)

    def generate_later(self, texts):
        with self._gen_lock:
            todo = [t for t in texts if t not in self._gen_queued
                    and not os.path.exists(self._path(t, "piper"))]
            self._gen_queued.update(todo)
            self._gen_list.extend(todo)
            if todo and not self._gen_running:
                self._gen_running = True
                threading.Thread(target=self._gen_worker, daemon=True, name="tts-piper").start()

    def _gen_worker(self):
        while True:
            with self._gen_lock:
                if not self._gen_list or self.engine() != "piper":
                    self._gen_running = False
                    return
                text = self._gen_list.pop(0)
            try:
                t0 = time.time()
                path = self.synth(text, "piper")
                self.prepare(path)
                log.info("voz natural pronta (%.0f s): %s", time.time() - t0, text)
            except Exception as e:
                self._piper_fails += 1
                log.warning("Piper falhou (%s) em '%s'", e, text)
                if self._piper_fails >= 3:
                    self._piper_broken = True
                    log.error("Piper desligado após 3 falhas; usando a voz do espeak")
            finally:
                with self._gen_lock:
                    self._gen_queued.discard(text)

    def set_busy(self, busy):
        """O detector avisa quando há gente chegando: a geração da voz natural (pesada) é
        congelada na hora (SIGSTOP) e volta de onde parou quando a cena acalma, para o
        reconhecimento e o cumprimento não ficarem lentos."""
        if busy == self._paused:
            return
        self._paused = busy
        p = self._piper_proc
        if p is not None and p.poll() is None:
            try:
                os.kill(p.pid, signal.SIGSTOP if busy else signal.SIGCONT)
            except ProcessLookupError:
                pass

    def prepare(self, path):
        """Deixa pronta a versão final que vai para a câmera (volume + A-law 8 kHz), para a
        hora do cumprimento não gastar nem um segundo convertendo."""
        try:
            out = self.louder(path)
            if self.camera_ok and self.cfg.camera_fast:
                self.camera_wav(out)
        except Exception as e:
            log.warning("não foi possível preparar %s: %s", path, e)

    def prepare_cached(self, texts):
        """Frases já geradas antes desta versão: prepara a versão final em segundo plano."""
        def run():
            for t in texts:
                p = self._path(t, "piper")
                if os.path.exists(p):
                    self.prepare(p)
                    time.sleep(0.2)
        threading.Thread(target=run, daemon=True, name="tts-prepare").start()

    def pending(self):
        with self._gen_lock:
            return len(self._gen_list)

    def _path(self, text, engine, model=None, length_scale=None):
        pad = float(self.cfg.lead_silence_s)
        model = model or self.cfg.piper_model
        ls = self.cfg.piper_length_scale if length_scale is None else length_scale
        voice = (f"piper|{model}|{ls}" if engine == "piper"
                 else f"espeak|{self.cfg.voice}|{self.cfg.speed}|{self.cfg.volume}")
        key = hashlib.sha1(f"{voice}|{pad}|{text}".encode()).hexdigest()[:16]
        return os.path.join(self.cache, f"{key}.wav")

    def synth(self, text, engine=None, interactive=False, out_dir=None):
        """Gera (ou reaproveita) o WAV da frase com o motor pedido. interactive=True: alguém
        está esperando (cumprimento/resposta): não pausa nem espera a fila de fundo."""
        engine = engine or self.engine()
        path = self._path(text, engine)
        if out_dir:
            path = os.path.join(out_dir, os.path.basename(path))
        if os.path.exists(path):
            return path
        tmp = f"{path}.{threading.get_ident()}.tmp.wav"
        if engine == "piper" and interactive:
            self._piper(text, tmp, interactive=True)
        elif engine == "piper":
            with self._piper_lock:  # um Piper por vez (pesado para a TV Box)
                self._piper(text, tmp)
        else:
            subprocess.run(["espeak-ng", "-v", self.cfg.voice, "-s", str(self.cfg.speed),
                            "-a", str(self.cfg.volume), "-w", tmp, text],
                           check=True, capture_output=True, timeout=30)
        # silêncio no começo: a câmera leva ~1-2 s para ativar o alto-falante e
        # "come" o início do áudio; sem isso, frases curtas somem inteiras
        pad = float(self.cfg.lead_silence_s)
        with wave.open(tmp, "rb") as src:
            params, frames = src.getparams(), src.readframes(src.getnframes())
        silence = b"\x00" * int(params.framerate * pad) * params.sampwidth * params.nchannels
        with wave.open(tmp + ".pad", "wb") as dst:
            dst.setparams(params)
            dst.writeframes(silence + frames + silence[: len(silence) // 2])
        os.remove(tmp)
        os.replace(tmp + ".pad", path)
        return path

    def engine(self):
        if (str(self.cfg.engine).lower() == "piper" and not self._piper_broken
                and os.path.exists(self.cfg.piper_bin) and os.path.exists(self.cfg.piper_model)):
            return "piper"
        return "espeak"

    def _piper(self, text, out, interactive=False):
        """Voz neural (Piper), offline. Lenta na TV Box (~30 s por frase): roda com prioridade
        mínima para não atrapalhar o reconhecimento, e o resultado fica guardado."""
        env = dict(os.environ, LD_LIBRARY_PATH=os.path.dirname(self.cfg.piper_bin),
                   ESPEAK_DATA_PATH=os.path.join(os.path.dirname(self.cfg.piper_bin), "espeak-ng-data"))
        extra, text = self._compat(text)
        if interactive:  # alguém esperando: prioridade normal, sem pausa
            subprocess.run([self.cfg.piper_bin, "--model", self.cfg.piper_model,
                            "--length_scale", str(self.cfg.piper_length_scale),
                            "--sentence_silence", "0.2", "--output_file", out] + extra,
                           input=text.encode(), check=True, capture_output=True, timeout=120, env=env)
            return
        while self._paused:  # não começa uma frase nova enquanto há gente chegando
            time.sleep(0.5)
        p = subprocess.Popen(["nice", "-n", "19", self.cfg.piper_bin, "--model", self.cfg.piper_model,
                              "--length_scale", str(self.cfg.piper_length_scale),
                              "--sentence_silence", "0.25", "--output_file", out] + extra,
                             stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                             env=env, start_new_session=True)
        self._piper_proc = p
        if self._paused:  # pausou entre a checagem e o início
            os.kill(p.pid, signal.SIGSTOP)
        try:
            p.stdin.write(text.encode())
            p.stdin.close()
            run_s, last = 0.0, time.time()
            while p.poll() is None:  # o tempo congelado não conta para o limite
                time.sleep(0.2)
                now = time.time()
                if not self._paused:
                    run_s += now - last
                last = now
                if run_s > 600:
                    raise subprocess.TimeoutExpired(p.args, 600)
            if p.returncode != 0:
                raise subprocess.CalledProcessError(p.returncode, p.args, stderr=p.stderr.read())
        finally:
            self._piper_proc = None
            if p.poll() is None:
                os.kill(p.pid, signal.SIGCONT)
                p.kill()
                p.wait()

    def _compat(self, text):
        """Vozes treinadas no Piper 1.5 (ex.: Jeff, Cadu) usam ditongos ("oɪ", "aɪ"...) como um
        fonema só, o que o Piper instalado não entende. Para elas o Visão mesmo converte o texto
        em fonemas (espeak-ng), troca cada ditongo por um símbolo de 1 caractere e manda ao Piper
        no modo "texto = fonemas", com uma cópia ajustada da configuração da voz."""
        src = self.cfg.piper_model + ".json"
        try:
            with open(src) as f:
                cfg = json.load(f)
        except OSError:
            return [], text
        idmap = cfg.get("phoneme_id_map", {})
        if all(len(k) == 1 for k in idmap):
            return [], text  # voz antiga: o Piper resolve sozinho
        clusters = ["".join(c) for c in cfg.get("vowel_clusters", []) if "".join(c) in idmap]
        priv = {c: chr(0xE000 + i) for i, c in enumerate(clusters)}
        comp = os.path.join(self.cache, os.path.basename(self.cfg.piper_model) + ".compat.json")
        if not os.path.exists(comp) or os.path.getmtime(comp) < os.path.getmtime(src):
            m = {k: v for k, v in idmap.items() if len(k) == 1}
            for c, ch in priv.items():
                m[ch] = idmap[c]
            cfg.update(phoneme_id_map=m, phoneme_type="text", phoneme_map={})
            with open(comp + ".tmp", "w") as f:
                json.dump(cfg, f, ensure_ascii=False)
            os.replace(comp + ".tmp", comp)
        parts = re.findall(r"([^,.!?;:]+)([,.!?;:]*)", text)
        ipa = subprocess.run(["espeak-ng", "-q", "-v", cfg.get("espeak", {}).get("voice", "pt-br"), "--ipa"],
                             input="\n".join(p[0] for p in parts), capture_output=True, text=True,
                             check=True, timeout=30).stdout.replace("\u200d", "").strip().split("\n")
        phon = " ".join(line.strip() + p[1] for line, p in zip(ipa, parts))
        for c, ch in priv.items():
            phon = phon.replace(c, ch)
        return ["-c", comp], phon

    def camera_wav(self, path):
        """Converte a frase (já com volume ajustado) para A-law 8 kHz e guarda ao lado."""
        lead = min(float(self.cfg.camera_lead_s), float(self.cfg.lead_silence_s))
        out = path[:-4] + f".alaw{int(round(lead * 100))}.wav"
        if os.path.exists(out):
            with open(out, "rb") as f:
                return f.read()
        with wave.open(path, "rb") as w:
            sr, n, ch, sw = w.getframerate(), w.getnframes(), w.getnchannels(), w.getsampwidth()
            raw = w.readframes(n)
        if sw != 2:
            raise ValueError("WAV precisa ser 16 bits")
        x = np.frombuffer(raw, np.int16).astype(np.float64)
        if ch > 1:
            x = x.reshape(-1, ch).mean(axis=1)
        # a frase guardada tem lead_silence_s de silêncio no começo; para a câmera basta
        # camera_lead_s (o resto era espera do ffmpeg). Corta o excesso sem gerar a voz de novo.
        cut = int((float(self.cfg.lead_silence_s) - lead) * sr)
        if cut > 0 and not np.any(np.abs(x[:cut]) > 64):
            x = x[cut:]
        y = np.clip(np.round(resample(x, sr)), -32768, 32767).astype(np.int16)
        data = alaw_wav(to_alaw(y).tobytes())
        tmp = f"{out}.{threading.get_ident()}.tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, out)
        return data

    def play_camera(self, path):
        if self.cfg.camera_fast:
            try:
                return self._play_camera_fast(path)
            except urllib.error.HTTPError:
                raise
            except Exception as e:  # conversão própria falhou: usa o caminho antigo (ffmpeg)
                log.warning("envio rápido falhou (%s); usando o ffmpeg", e)
        self._play_camera_ffmpeg(path)

    def _play_camera_fast(self, path):
        if self._audio is None:
            self._audio = _AudioServer()
        key = hashlib.sha1(path.encode()).hexdigest()[:12] + ".wav"
        data = self.camera_wav(path)
        item = self._audio.clips[key] = {"data": data, "first": None, "done": threading.Event()}
        t0 = time.monotonic()
        try:
            src = f"http://127.0.0.1:{self._audio.port}/{key}"
            url = (f"{self.cfg.go2rtc_url}/api/streams?dst={urllib.parse.quote(self.cfg.stream)}"
                   f"&src={urllib.parse.quote(src, safe='')}")
            with urllib.request.urlopen(urllib.request.Request(url, method="POST"), timeout=30) as r:
                r.read()
            # o go2rtc responde antes de terminar de tocar: espera o áudio sair todo
            item["done"].wait(len(data) / 8000.0 + 5)
            if item["first"] is not None:
                self.last_connect_s = item["first"] - t0
                log.info("câmera começou a receber a fala %.2f s após o pedido", self.last_connect_s)
        finally:
            self._audio.clips.pop(key, None)

    def _play_camera_ffmpeg(self, path):
        src = f"ffmpeg:{path}#audio=pcma/8000#input=file"  # G.711 A-law 8 kHz mono, o que a C200 aceita
        url = (f"{self.cfg.go2rtc_url}/api/streams?dst={urllib.parse.quote(self.cfg.stream)}"
               f"&src={urllib.parse.quote(src, safe='')}")
        req = urllib.request.Request(url, method="POST")
        with urllib.request.urlopen(req, timeout=15) as r:
            r.read()

    def play_local(self, path):
        subprocess.run(["aplay", "-q", "-D", self.cfg.local_device, path],
                       check=True, capture_output=True, timeout=30)

    def louder(self, path):
        """Versão mais alta da frase (greeting.loudness: 1.25 = +25% de volume percebido).
        Os picos da voz já batem no máximo, então um limitador suave segura só os picos em
        vez de estourar o som. Fica guardada ao lado do original."""
        target = float(self.cfg.loudness)
        if abs(target - 1.0) < 0.01:
            return path
        out = f"{path[:-4]}.v{int(round(target * 100))}.wav"
        if os.path.exists(out):
            return out
        with wave.open(path, "rb") as src:
            params, raw = src.getparams(), src.readframes(src.getnframes())
        if params.sampwidth != 2:
            return path
        x = np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0
        knee = 0.6

        def limit(v):
            a = np.abs(v)
            over = a > knee
            a[over] = knee + (1 - knee) * np.tanh((a[over] - knee) / (1 - knee))
            return np.sign(v) * a * 0.97

        base = float(np.sqrt(np.mean(x * x))) or 1.0
        lo, hi = 1.0, 4.0
        for _ in range(14):  # acha o ganho que dá o volume pedido depois do limitador
            g = (lo + hi) / 2
            if float(np.sqrt(np.mean(limit(x * g) ** 2))) / base < target:
                lo = g
            else:
                hi = g
        y = (limit(x * lo) * 32767).astype(np.int16)
        tmp = f"{out}.{threading.get_ident()}.tmp"
        with wave.open(tmp, "wb") as dst:
            dst.setparams(params)
            dst.writeframes(y.tobytes())
        os.replace(tmp, out)
        return out

    def play(self, path):
        """Toca pela saída configurada. Retorna o nome da saída usada."""
        self.playing = True
        try:
            return self._play(path)
        finally:
            self.playing, self.last_play_end = False, time.time()

    def _play(self, path):
        path = self.louder(path)
        if self.camera_ok:
            try:
                self.play_camera(path)
                self.cam_fails = 0
                return "câmera"
            except Exception as e:
                self.cam_fails += 1
                log.warning("câmera não tocou o áudio (%s: %s)", type(e).__name__, e)
                if self.cam_fails >= 3:
                    # a Tapo bloqueia a conta depois de muitas falhas de login: para de tentar
                    self.camera_ok = False
                    log.error("saída pela câmera desligada após 3 falhas seguidas "
                              "(confira a conta Tapo e reinicie o visao-detector)")
                if not self.local_ok:
                    raise
        if self.local_ok:
            self.play_local(path)
            return "TV Box"
        raise RuntimeError("nenhuma saída de som disponível")

    def _run(self):
        while True:
            text, member_id, engine, path, temp = self.q.get()
            ok = False
            try:
                if path is None:
                    try:
                        path = self.synth(text, engine, interactive=True, out_dir=self._tmp if temp else None)
                    except Exception as e:  # Piper falhou: não deixa a pessoa sem resposta
                        if engine != "piper":
                            raise
                        log.warning("Piper falhou (%s); falando com o espeak", e)
                        path = self.synth(text, "espeak", out_dir=self._tmp if temp else None)
                self.last_route = self.play(path)
                self.last_ok, self.last_error, ok = time.time(), None, True
                self.spoken += 1
                log.info("falou (%s): %s", self.last_route, text)
            except Exception as e:
                self.last_error = f"{type(e).__name__}: {e}"
                log.warning("não foi possível falar '%s': %s", text, self.last_error)
            if not (self.camera_ok or self.local_ok):
                self.enabled = False
            if temp:  # resposta de conversa: apaga o áudio e as versões derivadas
                for f in os.listdir(self._tmp):
                    try:
                        os.remove(os.path.join(self._tmp, f))
                    except OSError:
                        pass
            if member_id is not None:
                self.results.put((member_id, ok, time.time()))
            if not self.enabled:
                while not self.q.empty():
                    _, mid, _, _, _ = self.q.get_nowait()
                    self.results.put((mid, False, time.time()))
                return
            time.sleep(0.3)
