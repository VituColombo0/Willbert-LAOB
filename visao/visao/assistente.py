"""Conversa pela câmera: decide o que responder ao que foi ouvido.

Fluxo: ouvido.py transcreve a fala -> aqui procuramos o nome do assistente ("Willbert") ->
perguntas comuns têm resposta pronta e exata (hora, data, quem está, quem chegou, quando
fulano veio) -> o resto vai para o LLM local com os fatos do laboratório -> fala.para_fala()
deixa a resposta curta e falável -> alto-falante da câmera.

As respostas prontas não dependem do LLM: são instantâneas, nunca inventam e funcionam
mesmo num PC fraco ou com o Ollama fora do ar.
"""
import logging
import re
import threading
import time
import unicodedata
from collections import deque
from datetime import datetime
from difflib import SequenceMatcher

from . import fala
from .fala import saudacao

log = logging.getLogger("visao.assistente")


def norm(texto):
    """minúsculas, sem acento e sem pontuação (para comparar)."""
    t = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", t)).strip()


def acha_nome(texto_norm, palavras, limiar=0.84):
    """Procura o nome do assistente no texto, aceitando as grafias que o Whisper costuma usar
    (lista exata) e pequenas variações de uma palavra só (comparação aproximada).
    Retorna (achou, resto_do_texto_sem_o_nome)."""
    toks = texto_norm.split()
    exatos = {tuple(norm(p).split()) for p in palavras}
    # aproximação só contra o nome "de verdade" (as outras grafias valem só se forem exatas:
    # "gilbert" exato é o Whisper errando o nome, mas "Gilberto" é outra pessoa)
    base = [norm(p) for p in palavras[:2]]
    for i in range(len(toks)):
        for n in (2, 1):  # nomes escritos separados ("will bert") primeiro
            if tuple(toks[i:i + n]) in exatos:
                return True, " ".join(toks[:i] + toks[i + n:]).strip()
    for i, tok in enumerate(toks):
        if 5 <= len(tok) <= 10 and any(SequenceMatcher(None, tok, b_).ratio() >= limiar
                                       and abs(len(tok) - len(b_)) <= 1 for b_ in base):
            return True, " ".join(toks[:i] + toks[i + 1:]).strip()
    return False, texto_norm


class Assistente:
    def __init__(self, cfg, speaker, fatos_fn, llm=None):
        self.cfg, self.acfg = cfg, cfg.assistant
        self.speaker, self.fatos_fn, self.llm = speaker, fatos_fn, llm
        self.nome = self.acfg.name
        self.follow_until = 0.0
        self.historico = deque(maxlen=6)
        self.last_heard = self.last_answer = None
        self.last_ts, self.last_ms, self.respostas = None, None, 0
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ entrada
    def ouviu(self, texto, ts=None):
        """Chamado pelo ouvido com cada frase transcrita. Retorna a resposta falada (ou None)."""
        ts = ts or time.time()
        t = norm(texto)
        if not t:
            return None
        chamado, resto = acha_nome(t, self.acfg.wake_words)
        if not chamado and ts > self.follow_until:
            return None  # conversa dos outros: não é com o assistente
        with self._lock:
            t0 = time.time()
            self.last_heard = texto
            fatos = self.fatos_fn()
            resposta = self.responder(resto if chamado else t, fatos, so_nome=chamado and not resto)
            resposta = fala.para_fala(resposta, int(self.acfg.max_answer_chars))
            self.last_answer, self.last_ts = resposta, ts
            self.last_ms = int((time.time() - t0) * 1000)
            self.respostas += 1
            self.historico.append((texto, resposta))
            log.info("ouviu: %r -> respondeu (%d ms): %s", texto, self.last_ms, resposta)
            self.speaker.speak_now(resposta)
            # escuta a continuação sem precisar chamar de novo (conta a partir do fim da fala)
            self.follow_until = time.time() + float(self.acfg.follow_up_s) + len(resposta) / 12.0
            return resposta

    # --------------------------------------------------------------- respostas
    def responder(self, pedido, fatos, so_nome=False):
        quem = fatos.get("pessoa_falando")
        if so_nome:
            return f"Oi, {quem}! Pode falar." if quem else "Oi! Pode falar."
        p = norm(pedido)
        for regra in (self._hora, self._data, self._quem_sou, self._quem_esta, self._quem_chegou,
                      self._sobre_membro, self._ajuda, self._obrigado):
            r = regra(p, fatos)
            if r:
                return r
        if self.llm is not None and self.llm.enabled:
            r = self.llm.chat(pedido, self.fatos_texto(fatos), self.historico)
            if r:
                return r
            return "Desculpe, não consegui pensar numa resposta agora. Pode perguntar de novo?"
        return "Desculpe, ainda não sei responder isso."

    def _hora(self, p, f):
        if re.search(r"\b(que horas|que hora|horas sao|hora e agora|me diz a hora)\b", p):
            return f"São {fala.hora(f['agora'])}." if f["agora"].hour not in (0, 1, 12, 13) \
                else f"É {fala.hora(f['agora'])}."

    def _data(self, p, f):
        if re.search(r"\b(que dia|qual (e )?a data|dia e hoje|dia de hoje|que data)\b", p):
            return f"Hoje é {fala.data(f['agora'])}."

    def _quem_sou(self, p, f):
        if re.search(r"\b(quem sou eu|quem e que sou eu|voce me (conhece|reconhece|ve)|sabe quem eu sou)\b", p):
            quem = f.get("pessoa_falando")
            return f"Você é o {quem}!" if quem else "Ainda não consegui te reconhecer. Olha para a câmera."

    def _quem_esta(self, p, f):
        if re.search(r"\bquem (esta|ta|tem|ficou|se encontra)\b|\b(alguem|tem gente) (aqui|ai|no lab)", p):
            nomes = [n for n, _ in f["presentes"]]
            if not nomes:
                return "Não vi nenhum membro por aqui na última meia hora."
            if len(nomes) == 1:
                return f"Só o {nomes[0]} está por aqui agora."
            return f"Estão por aqui: {fala.lista(nomes)}."

    def _quem_chegou(self, p, f):
        if re.search(r"\bquem (chegou|veio|passou|apareceu|esteve)\b", p):
            hoje = f["hoje"]
            if not hoje:
                return "Hoje ainda não vi nenhum membro."
            partes = [f"{n} às {fala.hora(datetime.fromtimestamp(ts))}" for n, ts in hoje[:5]]
            return f"Hoje vi {fala.lista(partes)}."

    def _sobre_membro(self, p, f):
        alvo = next((n for n in f["membros"] if re.search(rf"\b{re.escape(norm(n))}\b", p)), None)
        if not alvo or not re.search(r"\b(chegou|veio|esta|ta|viu|cade|onde|passou|apareceu|hoje)\b", p):
            return None
        presentes = dict(f["presentes"])
        if alvo in presentes:
            return f"O {alvo} está por aqui. Vi ele {fala.ha_quanto(f['agora'].timestamp() - presentes[alvo])}."
        hoje = dict(f["hoje"])
        if alvo in hoje:
            return f"O {alvo} chegou hoje às {fala.hora(datetime.fromtimestamp(hoje[alvo]))}, mas não está aqui agora."
        ult = f["ultima_vez"].get(alvo)
        if ult:
            return f"Hoje não vi o {alvo}. A última vez foi {fala.ha_quanto(f['agora'].timestamp() - ult)}."
        return f"Ainda não vi o {alvo} por aqui."

    def _ajuda(self, p, f):
        if re.search(r"\bo que (voce|vc) (faz|sabe|pode)|\b(me ajuda|ajuda)\b", p):
            return ("Eu reconheço os membros, cumprimento quem chega e respondo coisas como que horas são, "
                    "quem está no laboratório ou quando alguém chegou.")

    def _obrigado(self, p, f):
        if re.fullmatch(r"(muito )?(obrigad[oa]|valeu|brigad[oa])( (willbert|wilbert))?", p):
            return "De nada!"

    # ------------------------------------------------------------------- fatos
    @staticmethod
    def fatos_texto(f):
        agora = f["agora"]
        linhas = [f"agora: {fala.data(agora)}, {fala.hora(agora)} ({saudacao(agora.hour).lower()})",
                  f"membros: {', '.join(f['membros']) or 'nenhum'}",
                  f"pessoa_falando: {f.get('pessoa_falando') or 'não reconhecida'}",
                  f"na frente da câmera agora: {', '.join(f['na_camera']) or 'ninguém reconhecido'}",
                  "presentes (vistos na última meia hora): " + (", ".join(
                      f"{n} (visto {fala.ha_quanto(agora.timestamp() - ts)})" for n, ts in f["presentes"]) or "ninguém"),
                  "chegaram hoje: " + (", ".join(
                      f"{n} às {datetime.fromtimestamp(ts).strftime('%H:%M')}" for n, ts in f["hoje"]) or "ninguém")]
        fora = [f"{n} ({fala.ha_quanto(agora.timestamp() - ts)})" for n, ts in f["ultima_vez"].items()
                if n not in dict(f["hoje"])]
        if fora:
            linhas.append("última vez vistos (não vieram hoje): " + ", ".join(fora))
        return "\n".join(linhas)

    # ---------------------------------------------------------- cumprimento LLM
    def cumprimento(self, nomes, pessoas, fatos, timeout):
        """Cumprimento com contexto (ex.: 'faz três dias que não te via'), gerado pelo LLM.
        None se o LLM demorar mais que `timeout` (aí vale a frase padrão)."""
        if self.llm is None or not self.llm.enabled or not nomes:
            return None
        quem = fala.lista(nomes) if pessoas <= 2 else "o grupo"
        pedido = (f"Cumprimente {quem}, que acabou de chegar, com {saudacao(fatos['agora'].hour).lower()}. "
                  "Uma frase só, curta e calorosa. Se fizer dias que não vinha, mencione.")
        r = self.llm.chat(pedido, self.fatos_texto(fatos), timeout=timeout, num_predict=40)
        if not r:
            return None
        r = fala.para_fala(r, 140)
        # segurança: o cumprimento tem que citar quem chegou (ou o grupo)
        if pessoas <= 2 and not all(norm(n) in norm(r) for n in nomes):
            return None
        return r

    def status(self):
        return {"enabled": True, "name": self.nome, "last_heard": self.last_heard,
                "last_answer": self.last_answer, "last_ts": self.last_ts, "last_ms": self.last_ms,
                "answers": self.respostas,
                "llm": (self.llm.cfg.model if self.llm and self.llm.enabled else None),
                "llm_ok": (self.llm.ok if self.llm else None),
                "listening_follow_up": time.time() < self.follow_until}
