"""Carrega a configuração (TOML) e completa com valores padrão."""
import os
try:
    import tomllib
except ImportError:  # Python 3.10 (Ubuntu 22.04)
    import tomli as tomllib
from urllib.parse import quote

DEFAULTS = {
    "camera": {
        "host": "",
        "user": "",
        "password": "",
        "rtsp_port": 554,
        "onvif_port": 2020,
        "main_stream": "stream1",  # 1080p: usado só para gravar clipes
        "sub_stream": "stream2",   # 720p: usado para a análise
    },
    "detection": {
        "det_width": 576,          # largura usada pelo detector (menor = mais rápido, alcance menor)
        "det_score_min": 0.6,      # confiança mínima para considerar um rosto
        "rec_det_score": 0.7,      # confiança mínima para tentar reconhecer
        "min_face_px": 36,         # largura mínima do rosto (px no quadro 720p)
        "yaw_max": 0.42,           # até aqui o rosto conta como "de frente" (0 = de frente)
        "yaw_side_max": 0.80,      # de lado/perfil até aqui: reconhece, mas pede accept_side
        "accept_side": 0.33,       # similaridade mínima para rosto de lado (sempre com 2 leituras)
        "enrich_until": 25,        # com tantas amostras o membro está aprendido: para de capturar
        "boot_readings": 3,        # poucas amostras: N leituras do mesmo rosto concordando identificam
        "boot_min": 0.27,          # ...cada uma com pelo menos essa similaridade
        "boot_mean": 0.30,         # ...e média pelo menos essa
        "boot_margin": 0.08,       # ...e folga sobre o 2º mais parecido
        "quick_accept": 0.42,      # 1 leitura basta se passar disso com folga >= quick_margin
        "quick_margin": 0.15,
        "learn_min_px": 48,        # amostra aprendida sozinha: rosto com pelo menos N px
        "learn_sharp": 70.0,       # ...e nitidez mínima (evita guardar rosto borrado)
        "few_samples": 8,          # membro com menos amostras que isso aprende mais rápido sozinho
        "same_visitor": 0.42,      # similaridade para dizer que um desconhecido já foi visto antes
        "retry_s": 6,              # rosto sem nome que esgotou as tentativas: tenta de novo a cada N s
        "sharp_min": 25.0,         # nitidez mínima do recorte
        "accept": 0.32,            # similaridade mínima (com 2 leituras concordando abaixo de 0,50)
        "margin": 0.04,            # vantagem mínima sobre o 2º membro mais parecido
        "unknown": 0.26,           # abaixo disso a leitura conta como "desconhecido"
        "same_person": 0.36,       # para agrupar leituras do mesmo desconhecido
        "unknown_attempts": 3,     # leituras ruins parecidas antes de alertar
        "unknown_cooldown_s": 600, # não repetir alerta da mesma pessoa nesse intervalo
        "max_attempts": 8,         # tentativas por rosto acompanhado
        "reverify_s": 45,          # reconfirma a identidade de um rosto acompanhado
        "max_rec_per_cycle": 2,    # reconhecimentos por ciclo (cada um custa ~1 s)
        "track_ttl_s": 6.0,
        "heartbeat_s": 20,         # varredura periódica mesmo sem movimento
        "idle_track_interval_s": 3.0,
        "stable_interval_s": 1.5,  # há movimento, mas todos os rostos visíveis já identificados
        "temp_hot": 78.0,          # °C: acima disso a análise fica mais espaçada
        "temp_critical": 85.0,     # °C: acima disso, bem mais espaçada (protege a TV Box)
        "frame_every": 4,          # usa 1 a cada N quadros do stream (15 fps -> ~4 fps)
        "threads": 4,
        "classic_engine": True,    # motor clássico do OpenCV: ~30% mais rápido, mesmos resultados
        "auto_enrich": True,       # aprende novas amostras quando a certeza é alta
        "auto_enrich_score": 0.45,
        "hard_learn": True,        # aprende o ângulo difícil quando o mesmo rosto é confirmado depois
        "auto_enrich_max": 30,     # ao encher, a mais antiga é trocada pela nova
        "strong_accept": 0.50,     # acima disso basta uma leitura; abaixo, precisa de duas
    },
    "presence": {
        "session_gap_min": 30,     # sem ver a pessoa por esse tempo = saiu
    },
    "storage": {
        "data_dir": "/opt/visao/data",
        "runtime_dir": "/dev/shm/visao",
        "models_dir": "/opt/visao/models",
        "retention_days": 7,
        "min_free_mb": 800,
        "record_clips": True,
        "clip_before_s": 10,
        "clip_after_s": 20,
    },
    "web": {
        "host": "0.0.0.0",
        "port": 8080,
    },
    "greeting": {
        "enabled": True,
        # frases (sorteadas para não ficar repetitivo); {saudacao} = Bom dia / Boa tarde / Boa noite
        "messages_one": ["{saudacao}, {nome}! Que bom te ver.",
                         "Olá, {nome}! {saudacao}.",
                         "{saudacao}, {nome}! Bom trabalho hoje."],
        "messages_two": ["{saudacao}, {nome1} e {nome2}!",
                         "Olá, {nome1} e {nome2}! {saudacao}."],
        "messages_group": ["{saudacao}, pessoal! Que bom ver vocês.",
                           "{saudacao} a todos!"],
        "group_window_s": 5,                 # máx. de espera por outros rostos para cumprimentar juntos
        "arrival_window_min": 15,            # só cumprimenta quem chegou há no máx. N minutos
        "live_synth": False,                 # PC: gera a frase na hora se não estiver pronta (Piper é rápido)
        "llm": False,                        # PC com placa de vídeo: cumprimento com contexto pelo LLM
        "llm_timeout_s": 1.5,                # se o LLM demorar mais que isso, usa a frase padrão
        "group_min_s": 0.3,                  # sozinho na cena: fala praticamente na hora
        "engine": "piper",                   # piper (voz neural, natural) ou espeak (robótica, leve)
        "piper_bin": "/opt/visao/piper/piper",
        "piper_model": "/opt/visao/voices/pt_BR-faber-medium.onnx",  # escolhida ouvindo Faber, Jeff e Cadu
        "piper_length_scale": 1.2,           # >1 fala mais devagar; 1.2 = "Faber mais calma" (Victor, 2026-11-10)
        # vozes anteriores [modelo, length_scale]: usadas enquanto uma voz nova gera as frases
        "piper_fallback": [["/opt/visao/voices/pt_BR-faber-medium.onnx", 1.05]],
        "once_per": "periodo",               # periodo = 1x de manhã, 1x à tarde, 1x à noite; "dia" = 1x por dia
        "voice": "mb-br3",                   # espeak-ng: mb-br1, mb-br3, mb-br4 ou pt-br
        "speed": 145,
        "volume": 180,
        "output": "auto",                    # local (TV Box) | camera (Tapo) | auto (câmera, senão local)
        "local_device": "plughw:CARD=analog,DEV=0",  # conector AV; HDMI: plughw:CARD=HDMI,DEV=0
        "lead_silence_s": 2.0,               # silêncio antes da frase (a câmera demora a ativar o som)
        "camera_lead_s": 0.8,                # silêncio antes da fala na câmera (tempo de ativar o alto-falante)
        "camera_fast": True,                 # envia a fala já convertida, sem abrir o ffmpeg (~4 s mais rápido)
        "loudness": 1.5,                     # volume da fala: 1.0 = original, 1.5 = +50%
        "go2rtc_url": "http://127.0.0.1:1984",
        "stream": "camera",
        # O alto-falante da Tapo só aceita áudio pelo protocolo próprio da TP-Link, que pede a
        # senha da CONTA TAPO (a do app/nuvem), não a "Conta da câmera" usada no RTSP.
        "tapo_cloud_password": "",
        "tapo_auth": "sha256",               # sha256 (firmwares novos da C200) | md5 | plain
    },
    "security": {
        "enabled": False,          # modo segurança: movimento no horário abaixo vira alerta
        "start": "23:00",
        "end": "06:00",
        "cooldown_min": 5,         # no máximo um alerta a cada N minutos
    },
    "hardware": {
        "profile": "auto",         # auto | tvbox | cpu | cpu-forte | gpu | gpu-forte (ver hardware.py)
    },
    # Conversa pela câmera: microfone da Tapo -> reconhecimento de fala -> resposta -> alto-falante
    "assistant": {
        "enabled": False,          # o perfil liga no PC
        "name": "Willbert",
        # "Willbert, grave o meu rosto": a foto vai para o painel da caixa de areia (Projetor-LAOB)
        "areia_url": "http://127.0.0.1:8090/foto",
        # como o Whisper costuma escrever o nome (comparação aproximada também é feita)
        "wake_words": ["willbert", "wilbert", "uilbert", "wilber", "uilber", "wilbur", "gilbert",
                       "vilbert", "will bert", "uil bert"],
        "usar_llm": False,         # conversa livre pelo Ollama: só vale num PC com placa de vídeo boa
                                   # (no lab2 ficava 12-25 s e respondia mal; Victor tirou em 2026-11-11)
        "follow_up_s": 10,         # depois de responder, escuta mais N s sem precisar do nome
        "audio_url": "rtsp://127.0.0.1:8554/camera",  # áudio da câmera pelo go2rtc
        "vad_aggressiveness": 2,   # 0-3: quanto maior, mais rigoroso para considerar "fala"
        "min_speech_ms": 300,
        "end_silence_ms": 700,     # silêncio que encerra a frase
        "max_utterance_s": 12,
        "mute_after_speech_s": 0.6,  # ignora o microfone logo depois de falar (eco do alto-falante)
        "max_answer_chars": 240,   # resposta curta = fala clara e rápida
        # se o LLM passar desse tempo, fala um aviso curto enquanto pensa (0 = nunca)
        "thinking_s": 1.8,
        "thinking_phrases": ["Só um instante.", "Hum, deixa eu pensar.", "Um momento."],
    },
    "llm": {
        "url": "http://127.0.0.1:11434",  # Ollama local
        "model": "",               # o perfil escolhe (qwen3:1.7b / 4b / 8b)
        "num_predict": 80,
        "temperature": 0.5,
        "timeout_s": 25,
        "keep_alive": "24h",       # mantém o modelo carregado: sem espera na primeira pergunta
        "num_thread": 0,           # 0 = o Ollama decide; o perfil usa núcleos - 1
        "num_gpu": -1,             # -1 = o Ollama decide; 0 = só CPU (perfis sem placa boa)
    },
    "stt": {
        "model": "",               # faster-whisper: small / large-v3-turbo (o perfil escolhe)
        "device": "cpu",
        "compute_type": "int8",
        "beam_size": 1,
        "cpu_threads": 0,          # 0 = padrão do faster-whisper; o perfil usa núcleos - 1
        "download_root": "/opt/visao/models/whisper",
        # nomes que o Whisper deve reconhecer (ajuda a escrever certo)
        # (as frases dos comandos são acrescentadas pelo ouvido: ajudam a ouvir "grave o meu rosto")
        "prompt": "Willbert, Victor, Gazziro, Mario, Lucas, laboratório.",
    },
    "telegram": {
        "token": "",
        "chat_id": "",
        "notify_unknown": True,
        "notify_entries": False,
    },
}


class Section(dict):
    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(key) from None


def load(path=None, detect_hardware=True):
    """Padrões < perfil do hardware < config.toml do usuário."""
    path = path or os.environ.get("VISAO_CONFIG", "/opt/visao/config.toml")
    user = {}
    if os.path.exists(path):
        with open(path, "rb") as f:
            user = tomllib.load(f)
    profile_vals, profile, hw = {}, None, None
    if detect_hardware:
        from . import hardware
        profile = str(user.get("hardware", {}).get("profile", "auto"))
        if profile == "auto":
            hw = hardware.detect()
            profile = hardware.choose(hw)
        profile_vals = hardware.PROFILES.get(profile, {})
    cfg = Section()
    for sec, vals in DEFAULTS.items():
        merged = Section(vals)
        merged.update(profile_vals.get(sec, {}))
        merged.update(user.get(sec, {}))
        cfg[sec] = merged
    n = os.cpu_count() or 4
    for sec, key in (("detection", "threads"), ("llm", "num_thread"), ("stt", "cpu_threads")):
        v = cfg[sec].get(key)
        if v == "cores":
            cfg[sec][key] = min(n, 8)
        elif v == "cores-1":
            cfg[sec][key] = max(1, min(n - 1, 8))
    cfg.hardware["active"] = profile or "padrão"
    cfg.hardware["info"] = hw
    return cfg


def rtsp_url(cam, which="sub"):
    stream = cam.sub_stream if which == "sub" else cam.main_stream
    cred = f"{quote(cam.user, safe='')}:{quote(cam.password, safe='')}@" if cam.user else ""
    return f"rtsp://{cred}{cam.host}:{cam.rtsp_port}/{stream}"
