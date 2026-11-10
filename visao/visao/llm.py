"""Cliente do modelo de linguagem local (Ollama), sem dependências extras.

As respostas vão ser FALADAS pelo alto-falante da câmera: o prompt pede frases curtas, em
português do Brasil, sem listas nem markdown, usando só os fatos fornecidos (quem está no
laboratório, quem chegou etc.) para não inventar.
"""
import json
import logging
import time
import urllib.error
import urllib.request

log = logging.getLogger("visao.llm")

SISTEMA = """Você é {nome}, o assistente do laboratório. Você fala pelo alto-falante de uma câmera,
então suas respostas são OUVIDAS, não lidas.

Regras:
- Responda em português do Brasil, de forma simpática e natural, como uma pessoa falando.
- No máximo duas frases curtas (até 30 palavras no total). Nada de listas, emojis, markdown ou símbolos.
- Use só os FATOS abaixo sobre pessoas, horários e presença. Se não souber, diga que não sabe.
- Não invente nomes nem horários. Não repita a pergunta.
- Quem está falando com você aparece em "pessoa_falando" quando a câmera a reconheceu.

FATOS:
{fatos}"""


def cortar_frase_incompleta(texto):
    """A resposta passou do limite de tokens e parou no meio: fica só até a última frase
    completa (melhor "A capital é Brasília." do que "A capital é Brasília. Também é o")."""
    import re
    fim = max((m.end() for m in re.finditer(r"[.!?](?=\s|$)", texto)), default=0)
    return texto[:fim].strip() if fim >= 12 else texto


class LLM:
    def __init__(self, cfg, nome="Willbert"):
        self.cfg, self.nome = cfg, nome
        self.ok, self.last_error, self.last_ms = None, None, None

    @property
    def enabled(self):
        return bool(self.cfg.model)

    def _post(self, path, payload, timeout):
        req = urllib.request.Request(self.cfg.url.rstrip("/") + path, data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())

    def chat(self, pergunta, fatos, historico=(), timeout=None, num_predict=None):
        """Resposta curta ou None se o LLM não estiver disponível / demorar demais."""
        if not self.enabled:
            return None
        msgs = [{"role": "system", "content": SISTEMA.format(nome=self.nome, fatos=fatos)}]
        for q, a in list(historico)[-3:]:
            msgs += [{"role": "user", "content": q}, {"role": "assistant", "content": a}]
        msgs.append({"role": "user", "content": pergunta})
        payload = {
            "model": self.cfg.model, "messages": msgs, "stream": False,
            "think": False,  # Qwen3 e afins: sem "pensar em voz alta" (mais rápido)
            "keep_alive": self.cfg.keep_alive,
            "options": {"temperature": float(self.cfg.temperature),
                        "num_predict": int(num_predict or self.cfg.num_predict)},
        }
        payload["options"].update(self._opcoes_carga())
        t0 = time.time()
        try:
            try:
                r = self._post("/api/chat", payload, timeout or float(self.cfg.timeout_s))
            except urllib.error.HTTPError as e:
                if e.code != 400:
                    raise
                payload.pop("think")  # Ollama antigo não conhece "think"
                r = self._post("/api/chat", payload, timeout or float(self.cfg.timeout_s))
            self.ok, self.last_error = True, None
            self.last_ms = int((time.time() - t0) * 1000)
            texto = (r.get("message") or {}).get("content", "").strip()
            if r.get("done_reason") == "length":
                texto = cortar_frase_incompleta(texto)
            return texto or None
        except Exception as e:
            self.ok, self.last_error = False, f"{type(e).__name__}: {e}"
            log.warning("LLM indisponível (%s)", self.last_error)
            return None

    def _opcoes_carga(self):
        """Opções que mudam como o modelo é carregado: iguais no aquecimento e nas perguntas
        (senão o Ollama recarrega o modelo na primeira pergunta)."""
        o = {}
        if int(self.cfg.get("num_thread") or 0) > 0:  # usar todos os núcleos trava (disputa com o resto)
            o["num_thread"] = int(self.cfg.num_thread)
        if int(self.cfg.get("num_gpu", -1)) >= 0:
            o["num_gpu"] = int(self.cfg.num_gpu)
        return o

    def warmup(self):
        """Carrega o modelo na memória (a 1ª resposta não precisa esperar o carregamento)."""
        if not self.enabled:
            return False
        try:
            self._post("/api/generate", {"model": self.cfg.model, "prompt": "", "keep_alive": self.cfg.keep_alive,
                                         "options": self._opcoes_carga()}, timeout=180)
            # deixa as regras (parte fixa do prompt) já processadas no cache do Ollama:
            # a primeira pergunta de verdade não paga esse tempo
            self.chat("oi", "(nenhum)", timeout=120, num_predict=1)
            self.ok = True
            return True
        except Exception as e:
            self.ok, self.last_error = False, f"{type(e).__name__}: {e}"
            log.warning("não foi possível carregar o modelo %s (%s)", self.cfg.model, self.last_error)
            return False
