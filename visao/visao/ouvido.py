"""Ouve o microfone da câmera e transcreve as falas (faster-whisper), em segundo plano.

Áudio: go2rtc (já conectado à Tapo para o alto-falante) -> RTSP local -> ffmpeg -> PCM 16 kHz.
Detecção de voz (webrtcvad) recorta cada frase: começa com ~0,3 s de fala e termina com
~0,7 s de silêncio. Enquanto a própria câmera está falando (e logo depois), o microfone é
ignorado, para o assistente não responder ao próprio eco.
"""
import collections
import logging
import subprocess
import threading
import time

import numpy as np

from .camera import ErrosFfmpeg

PROMPT_COMANDOS = ("Willbert, grave o meu rosto. Que horas são? Quem está aqui? Coloca o tema vulcão. "
                   "Liga a caça ao tesouro. Solta os peixes e os carrinhos. Labirinto de água. "
                   "Volta para a topografia.")

log = logging.getLogger("visao.ouvido")


def le_exato(arquivo, n):
    """Lê exatamente n bytes de um cano. Um cano sem buffer pode entregar menos que o pedido
    sem ter acabado (no laob o áudio chega em pedaços quebrados): junta até completar.
    None só quando o ffmpeg fechou de verdade."""
    partes, falta = [], n
    while falta > 0:
        b = arquivo.read(falta)
        if not b:
            return None
        partes.append(b)
        falta -= len(b)
    return partes[0] if len(partes) == 1 else b"".join(partes)

SR = 16000
FRAME_MS = 30
FRAME_BYTES = SR * FRAME_MS // 1000 * 2


class Ouvido:
    def __init__(self, acfg, scfg, speaker, on_text):
        self.acfg, self.scfg, self.speaker, self.on_text = acfg, scfg, speaker, on_text
        self.model = None
        self.state, self.last_error, self.last_text, self.heard = "parado", None, None, 0
        self._stop = threading.Event()
        self._q = collections.deque(maxlen=4)  # frases esperando transcrição
        self._q_ev = threading.Event()
        self.proc = None
        # nomes + frases dos comandos: o Whisper tende a escrever o que já "viu" no prompt
        self.prompt = (str(scfg.get("prompt") or "") + " " + PROMPT_COMANDOS).strip()

    def start(self):
        threading.Thread(target=self._load_and_transcribe, daemon=True, name="stt").start()
        threading.Thread(target=self._capture, daemon=True, name="mic").start()

    def stop(self):
        self._stop.set()
        p = self.proc
        if p and p.poll() is None:
            p.kill()

    # ---------------------------------------------------------- reconhecimento
    def _load_and_transcribe(self):
        try:
            from faster_whisper import WhisperModel
            t0 = time.time()
            self.model = WhisperModel(self.scfg.model, device=self.scfg.device,
                                      compute_type=self.scfg.compute_type,
                                      download_root=self.scfg.download_root,
                                      cpu_threads=int(self.scfg.get("cpu_threads") or 0))
            log.info("Whisper '%s' (%s/%s) carregado em %.0f s", self.scfg.model, self.scfg.device,
                     self.scfg.compute_type, time.time() - t0)
        except Exception as e:
            self.state, self.last_error = "erro", f"Whisper: {type(e).__name__}: {e}"
            log.error("não foi possível carregar o reconhecimento de fala: %s", e)
            return
        while not self._stop.is_set():
            if not self._q:
                self._q_ev.wait(0.5)
                self._q_ev.clear()
                continue
            audio, ts = self._q.popleft()
            try:
                t0 = time.time()
                segs, _ = self.model.transcribe(
                    audio, language="pt", beam_size=int(self.scfg.beam_size), vad_filter=False,
                    condition_on_previous_text=False, initial_prompt=self.prompt,
                    without_timestamps=True,
                    no_speech_threshold=0.6, temperature=0.0)
                texto = " ".join(s.text.strip() for s in segs
                                 if s.no_speech_prob < 0.6 and s.avg_logprob > -1.2).strip()
                if texto and not self._alucinacao(texto):
                    self.last_text, self.heard = texto, self.heard + 1
                    log.info("ouviu (%.1f s de áudio, %.2f s para transcrever): %s",
                             len(audio) / SR, time.time() - t0, texto)
                    self.on_text(texto, ts)
            except Exception:
                log.exception("erro ao transcrever")

    @staticmethod
    def _alucinacao(t):
        """O Whisper às vezes 'ouve' frases prontas no ruído."""
        tl = t.lower()
        return any(x in tl for x in ("legendas", "inscreva-se", "obrigado por assistir", "amara.org",
                                     "transcrição", "subtítulos")) or len(tl) < 2

    # ------------------------------------------------------------------ áudio
    def _capture(self):
        try:
            import webrtcvad
        except ImportError:
            self.state, self.last_error = "erro", "falta o pacote webrtcvad-wheels"
            log.error(self.last_error)
            return
        vad = webrtcvad.Vad(int(self.acfg.vad_aggressiveness))
        backoff = 2
        while not self._stop.is_set():
            cmd = ["ffmpeg", "-nostdin", "-loglevel", "error", "-rtsp_transport", "tcp",
                   "-fflags", "nobuffer", "-i", self.acfg.audio_url, "-vn", "-ac", "1", "-ar", str(SR),
                   "-f", "s16le", "pipe:1"]
            erros = None
            try:
                self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                             stdin=subprocess.DEVNULL, bufsize=0)
                erros = ErrosFfmpeg(self.proc)
                self.state = "ouvindo"
                self._loop(vad, self.proc)
                backoff = 2
            except Exception as e:
                self.last_error = f"{type(e).__name__}: {e}"
                if erros is not None:
                    try:
                        self.proc.wait(timeout=1)
                    except Exception:
                        pass
                    self.last_error += f" (ffmpeg: {erros.texto()})"
                log.warning("microfone da câmera: %s", self.last_error)
            finally:
                if self.proc and self.proc.poll() is None:
                    self.proc.kill()
            if not self._stop.is_set():
                self.state = "reconectando"
                self._stop.wait(backoff)
                backoff = min(backoff * 2, 30)

    def _muted(self):
        sp = self.speaker
        return sp.playing or time.time() - sp.last_play_end < float(self.acfg.mute_after_speech_s)

    def _loop(self, vad, proc):
        pre = collections.deque(maxlen=10)  # 0,3 s antes da fala (não corta a 1ª sílaba)
        voiced, buf, silence_ms, speech_ms, start_ts = False, [], 0, 0, 0.0
        need_start = max(1, int(self.acfg.min_speech_ms) // FRAME_MS)
        end_ms, max_ms = int(self.acfg.end_silence_ms), int(float(self.acfg.max_utterance_s) * 1000)
        recent = collections.deque(maxlen=need_start + 2)
        while not self._stop.is_set():
            frame = le_exato(proc.stdout, FRAME_BYTES)
            if frame is None:
                raise RuntimeError("áudio da câmera encerrado")
            if self._muted():
                pre.clear()
                recent.clear()
                voiced, buf = False, []
                continue
            is_speech = vad.is_speech(frame, SR)
            if not voiced:
                pre.append(frame)
                recent.append(is_speech)
                if sum(recent) >= need_start:
                    voiced, buf, silence_ms, speech_ms = True, list(pre), 0, len(pre) * FRAME_MS
                    start_ts = time.time()
                continue
            buf.append(frame)
            speech_ms += FRAME_MS
            silence_ms = 0 if is_speech else silence_ms + FRAME_MS
            if silence_ms >= end_ms or speech_ms >= max_ms:
                audio = np.frombuffer(b"".join(buf), np.int16).astype(np.float32) / 32768.0
                if speech_ms - silence_ms >= int(self.acfg.min_speech_ms):
                    self._q.append((audio, start_ts))
                    self._q_ev.set()
                voiced, buf = False, []
                pre.clear()
                recent.clear()

    def status(self):
        return {"state": self.state, "error": self.last_error, "last_text": self.last_text,
                "heard": self.heard, "model": self.scfg.model, "device": self.scfg.device}
