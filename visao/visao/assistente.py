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
from datetime import datetime, timedelta
from difflib import SequenceMatcher

from . import fala
from .fala import saudacao

log = logging.getLogger("visao.assistente")
DIAS_SEMANA = ("segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo")
MESES = ("janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro",
         "outubro", "novembro", "dezembro")


def norm(texto):
    """minúsculas, sem acento e sem pontuação (para comparar)."""
    t = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode().lower()
    for op, palavra in (("+", "mais"), ("*", "vezes"), ("/", "dividido por"), ("-", "menos")):
        t = re.sub(rf"(\d)\s*{re.escape(op)}\s*(\d)", rf"\1 {palavra} \2", t)
    t = re.sub(r"(\d)\s*x\s*(\d)", r"\1 vezes \2", t)
    t = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", t)).strip()
    # erros comuns do Whisper pequeno: "que aora são", "que oras são"
    return re.sub(r"\b(?:a|h)?oras?\b", lambda m: "horas" if m.group(0).endswith("s") else "hora", t)


def as_hora(dt):
    """'às três da tarde', mas 'ao meio-dia', 'à meia-noite' e 'à uma da tarde'."""
    h = fala.hora(dt)
    if h.startswith("meio-dia"):
        return "ao " + h
    if h.startswith(("meia-noite", "uma")):
        return "à " + h
    return "às " + h


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
    def __init__(self, cfg, speaker, fatos_fn, llm=None, acoes=None):
        self.cfg, self.acfg = cfg, cfg.assistant
        self.acoes = acoes or {}  # ex.: {"areia_rosto": fn(nome) -> bool}, ligadas pelo detector
        self.speaker, self.fatos_fn, self.llm = speaker, fatos_fn, llm
        self.nome = self.acfg.name
        self.follow_until = 0.0
        self.historico = deque(maxlen=6)
        self.last_heard = self.last_answer = None
        self.last_ts, self.last_ms, self.respostas = None, None, 0
        self._lock = threading.Lock()
        self._encerra = False
        self._fixas = set(self.frases_fixas())

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
            self._fala(resposta)
            # escuta a continuação sem precisar chamar de novo (conta a partir do fim da fala)
            self.follow_until = 0.0 if self._encerra else \
                time.time() + float(self.acfg.follow_up_s) + len(resposta) / 12.0
            self._encerra = False
            return resposta

    # --------------------------------------------------------------- respostas
    # Ordem importa: as mais específicas primeiro. Tudo aqui é local e instantâneo (regex sobre o
    # texto já sem acento); só o que não casar com nenhuma regra vai para o LLM.
    REGRAS = ("_encerrar", "_areia_rosto", "_repete", "_saudacao", "_tudo_bem", "_quem_e_voce", "_conta",
              "_dia_semana", "_mes_ano", "_hora", "_data", "_quem_sou", "_primeiro_ultimo", "_ontem",
              "_quem_saiu", "_quem_esta", "_quantos", "_quem_chegou", "_visitantes", "_sobre_membro",
              "_piada", "_ajuda", "_obrigado")

    PIADAS = (
        "Por que o computador foi ao médico? Porque estava com vírus.",
        "O que o zero disse para o oito? Que cinto bonito!",
        "Por que o livro de matemática estava triste? Porque tinha muitos problemas.",
        "Qual é o café mais perigoso do mundo? O ex-presso.",
        "O que a impressora falou para a outra? Essa folha é sua ou é impressão minha?",
        "Por que a planta não responde? Porque ela é muda.",
        "Qual é o contrário de volátil? Vem cá, sobrinho.",
        "O que o pato disse para a pata? Vem quá.",
    )
    FIXAS = ("Oi! Pode falar.", "De nada!", "Tudo ótimo por aqui! E com você?",
             "Eu sou o Willbert, o assistente do laboratório. Reconheço quem chega e respondo perguntas.",
             "Tá bom. Até mais!", "Ainda não falei nada.", "Hoje ainda não vi nenhum membro.",
             "Hoje ainda não vi ninguém sair.", "Não vi nenhum membro por aqui na última meia hora.",
             "Ainda não consegui te reconhecer. Olha para a câmera.",
             "Hoje não apareceu ninguém de fora.", "Ontem não vi nenhum membro.")

    def responder(self, pedido, fatos, so_nome=False):
        quem = fatos.get("pessoa_falando")
        if so_nome:
            return f"Oi, {quem}! Pode falar." if quem else "Oi! Pode falar."
        p = norm(pedido)
        for nome in self.REGRAS:
            r = getattr(self, nome)(p, fatos)
            if r:
                return r
        if self.llm is not None and self.llm.enabled:
            # PC lento: se o LLM demorar, avisa em voz que está pensando (silêncio longo parece defeito)
            espera = float(self.acfg.get("thinking_s", 0) or 0)
            aviso = None
            if espera > 0 and self.acfg.get("thinking_phrases"):
                frase = self.acfg.thinking_phrases[self.respostas % len(self.acfg.thinking_phrases)]
                aviso = threading.Timer(espera, self._fala, args=(frase,))
                aviso.daemon = True
                aviso.start()
            try:
                r = self.llm.chat(pedido, self.fatos_texto(fatos), self.historico)
            finally:
                if aviso:
                    aviso.cancel()
            if r:
                return r
            return "Desculpe, não consegui pensar numa resposta agora. Pode perguntar de novo?"
        return "Desculpe, ainda não sei responder isso."

    # --- conversa
    def _encerrar(self, p, f):
        if re.fullmatch(r"(tchau|ate (mais|logo|amanha)|pode parar|para|chega|silencio|cala a boca|"
                        r"so isso|era so isso|nada nao|esquece)( (willbert|wilbert|obrigad[oa]))?", p):
            self._encerra = True
            return "Tá bom. Até mais!"

    def _repete(self, p, f):
        if re.fullmatch(r"(repete|repita|fala de novo|pode repetir|o que voce (disse|falou)|como|"
                        r"nao entendi|hein)( (por favor|de novo))?", p):
            return self.last_answer or "Ainda não falei nada."

    def _saudacao(self, p, f):
        if re.fullmatch(r"(oi|ola|opa|e ai|fala|bom dia|boa tarde|boa noite|eai)( (tudo bem|tudo bom))?", p):
            quem = f.get("pessoa_falando")
            return f"{saudacao(f['agora'].hour)}{', ' + quem if quem else ''}!"

    def _tudo_bem(self, p, f):
        if re.fullmatch(r"(tudo (bem|bom|certo|joia)|como (voce|vc) (esta|ta|vai)|como vai|beleza|blz)", p):
            return "Tudo ótimo por aqui! E com você?"

    def _quem_e_voce(self, p, f):
        if re.search(r"\b(quem e (voce|vc)|qual (e )?(o )?seu nome|como (voce|vc) se chama|o que (voce|vc) e)\b", p):
            return "Eu sou o Willbert, o assistente do laboratório. Reconheço quem chega e respondo perguntas."

    def _conta(self, p, f):
        m = re.search(r"\b(\d+(?: \d{3})*)\s+(mais|menos|vezes|x|dividido por)\s+(\d+(?: \d{3})*)\b", p)
        if not m or not re.search(r"\b(quanto|quantos|calcula|conta)\b", p):
            return None
        a, b = int(m.group(1).replace(" ", "")), int(m.group(3).replace(" ", ""))
        op = m.group(2)
        if op == "dividido por":
            if b == 0:
                return "Não dá para dividir por zero."
            r = a / b
            r = int(r) if r == int(r) else round(r, 2)
        else:
            r = a + b if op == "mais" else a - b if op == "menos" else a * b
        return f"Dá {str(r).replace('.', ',')}."

    # --- ações
    def _areia_rosto(self, p, f):
        """'Willbert, grave o meu rosto' -> foto de quem está falando, projetada na caixa de areia."""
        if not re.search(r"\b(grav[ae]|grava|tira|tire|bate|bata|faz|faca|coloca|coloque|poe|ponha|mostra|mostre)\b"
                         r".*\b(meu rosto|minha cara|minha foto|uma foto|foto minha|na areia)\b", p):
            return None
        quem = f.get("pessoa_falando")
        acao = self.acoes.get("areia_rosto")
        if acao is None:
            return "A caixa de areia não está ligada a mim agora."
        if not f.get("na_camera"):
            return "Não estou te vendo. Fica de frente para a câmera e pede de novo."
        if not acao(quem):
            return "Não consegui mandar para a areia agora. Tenta de novo daqui a pouco."
        return f"Sorria{', ' + quem if quem else ''}! Em alguns segundos você aparece na areia."

    # --- tempo
    def _dia_semana(self, p, f):
        if re.search(r"\b(dia da semana|que dia (da semana )?e hoje|hoje e que dia)\b", p) and "semana" in p:
            return f"Hoje é {DIAS_SEMANA[f['agora'].weekday()]}."

    def _mes_ano(self, p, f):
        if re.search(r"\b(que|qual) (mes|ano)\b|\bem que (mes|ano)\b", p):
            return f"Estamos em {MESES[f['agora'].month - 1]} de {f['agora'].year}."

    def _hora(self, p, f):
        if re.search(r"\b(que horas|que hora|horas sao|hora e agora|me diz a hora)\b", p) and \
                not re.search(r"\b(chegou|saiu|veio|foi|chega|sai)\b", p):  # "que horas o Lucas chegou"
            return f"São {fala.hora(f['agora'])}." if f["agora"].hour not in (0, 1, 12, 13) \
                else f"É {fala.hora(f['agora'])}."

    def _data(self, p, f):
        desloc = 1 if re.search(r"\bamanha\b", p) else -1 if re.search(r"\bontem\b", p) else 0
        if re.search(r"\b(que dia|qual (e )?a data|dia e hoje|dia de hoje|que data)\b", p):
            d = f["agora"] + timedelta(days=desloc)
            quando = {0: "Hoje é", 1: "Amanhã é", -1: "Ontem foi"}[desloc]
            return f"{quando} {fala.data(d)}."

    # --- pessoas
    def _quem_sou(self, p, f):
        if re.search(r"\b(quem sou eu|quem e que sou eu|voce me (conhece|reconhece|ve)|sabe quem eu sou)\b", p):
            quem = f.get("pessoa_falando")
            return f"Você é o {quem}!" if quem else "Ainda não consegui te reconhecer. Olha para a câmera."

    def _primeiro_ultimo(self, p, f):
        primeiro = re.search(r"\b(chegou|veio|foi) primeiro\b|\bprimeir[oa] a (chegar|vir)\b", p)
        ultimo = re.search(r"\b(chegou|veio) por ultimo\b|\bultim[oa] a (chegar|vir)\b", p)
        if not (primeiro or ultimo):
            return None
        hoje = f["hoje"]
        if not hoje:
            return "Hoje ainda não vi nenhum membro."
        n, ts = hoje[0] if primeiro else hoje[-1]
        return f"{'O primeiro' if primeiro else 'O último'} a chegar foi o {n}, {as_hora(datetime.fromtimestamp(ts))}."

    def _ontem(self, p, f):
        if not re.search(r"\bquem (veio|esteve|chegou|apareceu|passou)\b.*\bontem\b", p):
            return None
        ontem = f.get("ontem") or []
        if not ontem:
            return "Ontem não vi nenhum membro."
        return f"Ontem vi {fala.lista([n for n, _ in ontem])}."

    def _quem_saiu(self, p, f):
        if not re.search(r"\bquem (saiu|foi embora|ja foi|ja saiu|ja foi embora)\b", p):
            return None
        saidas = f.get("saidas_hoje") or []
        if not saidas:
            return "Hoje ainda não vi ninguém sair."
        partes = [f"{n} {as_hora(datetime.fromtimestamp(ts))}" for n, ts in saidas[-5:]]
        return f"Saíram hoje: {fala.lista(partes)}."

    def _quem_esta(self, p, f):
        if re.search(r"\bquem (esta|ta|tem|ficou|se encontra)\b|\b(alguem|tem gente) (aqui|ai|no lab)", p):
            nomes = [n for n, _ in f["presentes"]]
            if not nomes:
                return "Não vi nenhum membro por aqui na última meia hora."
            if len(nomes) == 1:
                return f"Só o {nomes[0]} está por aqui agora."
            return f"Estão por aqui: {fala.lista(nomes)}."

    def _quantos(self, p, f):
        if not re.search(r"\bquant[oa]s (pessoas|membros|estao|tem|gente)\b", p):
            return None
        n = len(f["presentes"])
        if n == 0:
            return "Não vi nenhum membro por aqui na última meia hora."
        return f"{'Tem um membro' if n == 1 else f'Tem {fala.numero(n)} membros'} por aqui agora."

    def _quem_chegou(self, p, f):
        if re.search(r"\bquem (chegou|veio|passou|apareceu|esteve)\b", p):
            hoje = f["hoje"]
            if not hoje:
                return "Hoje ainda não vi nenhum membro."
            partes = [f"{n} {as_hora(datetime.fromtimestamp(ts))}" for n, ts in hoje[:5]]
            return f"Hoje vi {fala.lista(partes)}."

    def _visitantes(self, p, f):
        if not re.search(r"\b(visitante|visita|desconhecid[oa]|estranh[oa]|alguem de fora|nao cadastrad)", p):
            return None
        n = int(f.get("visitantes_hoje") or 0)
        if n == 0:
            return "Hoje não apareceu ninguém de fora."
        return f"Hoje a câmera viu alguém que não é membro {fala.numero(n, feminino=True) if n > 1 else 'uma'} {'vezes' if n > 1 else 'vez'}."

    def _sobre_membro(self, p, f):
        alvo = next((n for n in f["membros"] if re.search(rf"\b{re.escape(norm(n))}\b", p)), None)
        if not alvo:
            return None
        presentes = dict(f["presentes"])
        hoje = dict(f["hoje"])
        saidas = dict(f.get("saidas_hoje") or [])
        agora = f["agora"].timestamp()
        if re.search(r"\b(semana|quantas vezes|quantos dias)\b", p):
            n = (f.get("dias_semana") or {}).get(alvo, 0)
            if not n:
                return f"O {alvo} não veio nos últimos sete dias."
            return f"Nos últimos sete dias o {alvo} veio {'um dia' if n == 1 else f'{fala.numero(n)} dias'}."
        if re.search(r"\bontem\b", p):
            veio = dict(f.get("ontem") or [])
            return (f"Sim, o {alvo} veio ontem, chegou {as_hora(datetime.fromtimestamp(veio[alvo]))}."
                    if alvo in veio else f"Ontem não vi o {alvo}.")
        if re.search(r"\b(saiu|foi embora|ja foi|ja saiu|ainda (esta|ta))\b", p):
            if alvo in presentes:
                return f"O {alvo} ainda está por aqui."
            if alvo in saidas:
                return f"O {alvo} saiu {as_hora(datetime.fromtimestamp(saidas[alvo]))}."
            return f"Hoje não vi o {alvo}."
        if re.search(r"\bdesde que horas\b|\b(que horas|quando)\b.*\bchegou\b", p):
            if alvo in hoje:
                return f"O {alvo} chegou hoje {as_hora(datetime.fromtimestamp(hoje[alvo]))}."
            return f"Hoje não vi o {alvo}."
        if not re.search(r"\b(chegou|veio|esta|ta|viu|cade|onde|passou|apareceu|hoje)\b", p):
            return None
        if alvo in presentes:
            return f"O {alvo} está por aqui. Vi ele {fala.ha_quanto(agora - presentes[alvo])}."
        if alvo in hoje:
            sai = f", e saiu {as_hora(datetime.fromtimestamp(saidas[alvo]))}" if alvo in saidas else \
                ", mas não está aqui agora"
            return f"O {alvo} chegou hoje {as_hora(datetime.fromtimestamp(hoje[alvo]))}{sai}."
        ult = f["ultima_vez"].get(alvo)
        if ult:
            return f"Hoje não vi o {alvo}. A última vez foi {fala.ha_quanto(agora - ult)}."
        return f"Ainda não vi o {alvo} por aqui."

    # --- outros
    def _piada(self, p, f):
        if re.search(r"\b(piada|me faz rir|algo engracado|uma graca)\b", p):
            self._piadas = getattr(self, "_piadas", -1) + 1
            return self.PIADAS[self._piadas % len(self.PIADAS)]

    def _ajuda(self, p, f):
        if re.search(r"\bo que (voce|vc) (faz|sabe|pode)|\b(me ajuda|ajuda)\b", p):
            return ("Eu sei a hora e a data, quem está, quem chegou e quem saiu, quantas vezes alguém veio "
                    "na semana, faço contas, conto piadas e gravo o seu rosto na areia. Para o resto, eu penso um pouco.")

    def _obrigado(self, p, f):
        if re.fullmatch(r"(muito )?(obrigad[oa]|valeu|brigad[oa])( (willbert|wilbert))?", p):
            return "De nada!"

    def frases_fixas(self):
        """Respostas que não mudam: a voz delas fica em cache (da segunda vez em diante sai na hora)."""
        textos = list(self.FIXAS) + list(self.PIADAS) + [self._ajuda("ajuda", None)]
        textos += list(self.acfg.get("thinking_phrases") or [])
        return [fala.para_fala(t, int(self.acfg.max_answer_chars)) for t in textos]

    def _fala(self, texto):
        """Frase fixa: usa a voz do cache; o resto é gerado na hora."""
        if texto in self._fixas and hasattr(self.speaker, "speak_fixed"):
            self.speaker.speak_fixed(texto)
        else:
            self.speaker.speak_now(texto)

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
