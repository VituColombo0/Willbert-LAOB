"""Texto para ser FALADO: tudo por extenso e curto, para sair claro no alto-falante da câmera.

A voz (Piper) lê "14:30" ou "3" de jeitos estranhos e não sabe o que fazer com markdown,
emoji ou listas. Aqui o texto vira frase falável em português do Brasil.
"""
import re
import unicodedata
from datetime import datetime

UNIDADES = ["zero", "um", "dois", "três", "quatro", "cinco", "seis", "sete", "oito", "nove", "dez",
            "onze", "doze", "treze", "catorze", "quinze", "dezesseis", "dezessete", "dezoito", "dezenove"]
DEZENAS = ["", "", "vinte", "trinta", "quarenta", "cinquenta", "sessenta", "setenta", "oitenta", "noventa"]
CENTENAS = ["", "cento", "duzentos", "trezentos", "quatrocentos", "quinhentos", "seiscentos",
            "setecentos", "oitocentos", "novecentos"]
FEM = {"um": "uma", "dois": "duas", "duzentos": "duzentas", "trezentos": "trezentas",
       "quatrocentos": "quatrocentas", "quinhentos": "quinhentas", "seiscentos": "seiscentas",
       "setecentos": "setecentas", "oitocentos": "oitocentas", "novecentos": "novecentas"}
DIAS = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"]
MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro",
         "outubro", "novembro", "dezembro"]


def saudacao(hour=None):
    h = datetime.now().hour if hour is None else hour
    if 5 <= h < 12:
        return "Bom dia"
    if 12 <= h < 18:
        return "Boa tarde"
    return "Boa noite"


def _ate_mil(n):
    if n < 20:
        return UNIDADES[n]
    if n < 100:
        d, u = divmod(n, 10)
        return DEZENAS[d] + (f" e {UNIDADES[u]}" if u else "")
    if n == 100:
        return "cem"
    c, r = divmod(n, 100)
    return CENTENAS[c] + (f" e {_ate_mil(r)}" if r else "")


def numero(n, feminino=False):
    """Inteiro por extenso (até bilhões), em português do Brasil."""
    n = int(n)
    if n < 0:
        return "menos " + numero(-n, feminino)
    partes = []
    for valor, sing, plur in ((10 ** 9, "bilhão", "bilhões"), (10 ** 6, "milhão", "milhões")):
        q, n = divmod(n, valor)
        if q:
            partes.append(f"{numero(q)} {sing if q == 1 else plur}")
    q, n = divmod(n, 1000)
    if q:
        partes.append("mil" if q == 1 else f"{_ate_mil(q)} mil")
    if n or not partes:
        partes.append(_ate_mil(n))
    txt = " e ".join(partes) if len(partes) == 2 and (n < 100 or n % 100 == 0) else " ".join(partes)
    if feminino:
        txt = " ".join(FEM.get(w, w) for w in txt.split())
    return txt


def hora(dt=None):
    """'duas e meia da tarde', 'meio-dia e quinze', 'nove horas da manhã'."""
    dt = dt or datetime.now()
    h, m = dt.hour, dt.minute
    if h == 0:
        base, periodo = "meia-noite", ""
    elif h == 12:
        base, periodo = "meio-dia", ""
    else:
        h12 = h if h < 13 else h - 12
        base = numero(h12, feminino=True) + ("" if m else (" hora" if h12 == 1 else " horas"))
        periodo = " da manhã" if h < 12 else " da tarde" if h < 18 else " da noite"
    if m == 0:
        mins = ""
    elif m == 30:
        mins = " e meia"
    else:
        mins = f" e {numero(m)}"
    return f"{base}{mins}{periodo}".strip()


def data(dt=None):
    dt = dt or datetime.now()
    dia = "primeiro" if dt.day == 1 else numero(dt.day)
    return f"{DIAS[dt.weekday()]}, {dia} de {MESES[dt.month - 1]}"


def ha_quanto(segundos):
    """'agora há pouco', 'há vinte minutos', 'há duas horas', 'ontem', 'há três dias'."""
    s = max(0, int(segundos))
    if s < 120:
        return "agora há pouco"
    if s < 3600:
        return f"há {numero(s // 60)} minutos"
    if s < 2 * 3600:
        return "há uma hora"
    if s < 86400:
        return f"há {numero(s // 3600, feminino=True)} horas"
    d = s // 86400
    return "ontem" if d == 1 else f"há {numero(d)} dias"


def lista(nomes):
    nomes = [n for n in nomes if n]
    if not nomes:
        return ""
    if len(nomes) == 1:
        return nomes[0]
    return ", ".join(nomes[:-1]) + " e " + nomes[-1]


_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️‍]")


def _num_match(m):
    txt = m.group(0)
    if ":" in txt:  # 14:30 -> hora falada
        h, mi = txt.split(":")
        return hora(datetime(2000, 1, 1, int(h) % 24, int(mi) % 60))
    if "," in txt:
        a, b = txt.split(",")
        return f"{numero(int(a))} vírgula {' '.join(UNIDADES[int(c)] for c in b)}"
    return numero(int(txt.replace(".", "")))


def para_fala(texto, limite=240):
    """Limpa e encurta o texto para a voz: sem markdown/emoji, números por extenso, no
    máximo `limite` caracteres terminando numa frase completa."""
    t = unicodedata.normalize("NFC", texto or "")
    t = re.sub(r"<think>.*?</think>", " ", t, flags=re.S)       # raciocínio do modelo, se vier
    t = _EMOJI.sub("", t)
    t = re.sub(r"[*_#`>|~\[\]{}]", " ", t)                      # markdown
    t = re.sub(r"^\s*[-•]\s*", "", t, flags=re.M)                # itens de lista
    t = re.sub(r"\s*\n+\s*", ". ", t)
    t = re.sub(r"\b(\d{1,2}):(\d{2})\b|\b\d{1,3}(?:\.\d{3})+\b|\b\d+,\d+\b|\b\d+\b", _num_match, t)
    t = t.replace("%", " por cento").replace("&", " e ").replace("°C", " graus").replace("°", " graus")
    t = re.sub(r"\s+([,.!?])", r"\1", re.sub(r"\s+", " ", t)).strip(" .")
    t = re.sub(r"([.!?])\1+", r"\1", t)
    if len(t) > limite:
        frases = re.split(r"(?<=[.!?])\s+", t)
        out = ""
        for f in frases:
            if len(out) + len(f) + 1 > limite:
                break
            out = (out + " " + f).strip()
        t = out or t[:limite].rsplit(" ", 1)[0]
    if t and t[-1] not in ".!?":
        t += "."
    return t
