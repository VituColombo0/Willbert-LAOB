"""Descobre o hardware da máquina e escolhe um perfil (modelos e ritmo de análise).

O mesmo código roda na TV Box (armv7, 1 GB) e num PC com ou sem placa NVIDIA. Tudo que
estiver como "auto" na configuração é preenchido pelo perfil; o que o usuário escrever no
config.toml sempre vence.

Perfis:
  tvbox      ARM fraco: o Visão como era no Mini-Server (frases prontas, sem conversa)
  cpu-basico PC antigo (sem AVX2 ou até 4 núcleos, ex.: i5 de 3ª geração): Whisper base, LLM 1.7B
             usando um núcleo a menos (sobra para o resto da máquina)
  cpu        PC sem placa de vídeo, poucos núcleos/RAM: conversa com modelos pequenos
  cpu-forte  PC sem placa de vídeo, 8+ núcleos e 16+ GB: modelos médios
  gpu        NVIDIA com 6–10 GB: Whisper turbo e LLM 4B na placa
  gpu-forte  NVIDIA com 10+ GB: LLM 8B, tudo em tempo real

Placas NVIDIA antigas (compute capability < 7.0, ex.: GTX 750 Ti / 1060) não contam como GPU:
o faster-whisper atual não roda nelas (medido numa GTX 750 Ti: "invalid device ordinal").
"""
import os
import platform
import shutil
import subprocess


def _meminfo_gb():
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) / 1024 / 1024
    except OSError:
        pass
    return 0.0


def _cpu_model():
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.lower().startswith(("model name", "hardware")):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or "?"


def _cpu_flags():
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.lower().startswith(("flags", "features")):
                    return set(line.split(":", 1)[1].split())
    except OSError:
        pass
    return set()


def _nvidia():
    """[(nome, vram_gb, compute_capability)] das placas NVIDIA com driver funcionando."""
    if not shutil.which("nvidia-smi"):
        return []
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total,compute_cap", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    gpus = []
    for line in out.strip().splitlines():
        try:
            name, mem, cc = [x.strip() for x in line.rsplit(",", 2)]
            gpus.append((name, float(mem) / 1024, float(cc)))
        except ValueError:
            continue
    return gpus


def _nvidia_sem_driver():
    """Há placa NVIDIA no barramento mas o driver não está instalado."""
    if _nvidia() or not shutil.which("lspci"):
        return False
    try:
        out = subprocess.run(["lspci"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return any("nvidia" in l.lower() and ("vga" in l.lower() or "3d" in l.lower()) for l in out.splitlines())


def detect():
    gpus = _nvidia()
    uteis = [g for g in gpus if g[2] >= 7.0]  # placas que o Whisper/LLM aproveitam de verdade
    flags = _cpu_flags()
    return {
        "arch": platform.machine(),
        "cpu": _cpu_model(),
        "cores": os.cpu_count() or 1,
        "ram_gb": round(_meminfo_gb(), 1),
        "gpus": gpus,
        "vram_gb": round(max((g[1] for g in uteis), default=0.0), 1),
        "avx2": "avx2" in flags or not flags,
        "nvidia_sem_driver": _nvidia_sem_driver(),
    }


def choose(hw):
    """Escolhe o perfil pelo hardware detectado."""
    arch = hw["arch"].lower()
    if arch.startswith(("arm", "aarch64")) and hw["ram_gb"] < 3:
        return "tvbox"
    if hw["vram_gb"] >= 10:
        return "gpu-forte"
    if hw["vram_gb"] >= 5.5:
        return "gpu"
    if hw["cores"] >= 8 and hw["ram_gb"] >= 15:
        return "cpu-forte"
    if not hw.get("avx2", True) or hw["cores"] <= 4:
        return "cpu-basico"
    return "cpu"


# O que cada perfil usa. Modelos de LLM pelo nome do Ollama; Whisper pelo faster-whisper.
PROFILES = {
    "tvbox": {
        "detection": {"det_width": 576, "frame_every": 4, "max_rec_per_cycle": 2, "threads": 4},
        "assistant": {"enabled": False},
        "greeting": {"live_synth": False, "llm": False},
        "llm": {"model": ""},
        "stt": {"model": "", "device": "cpu", "compute_type": "int8"},
    },
    # medido num i5-3330 (4 núcleos, sem AVX2) dividindo a máquina com outro programa pesado:
    # Whisper small ~9 s por frase (inviável), base ~2,5 s; Qwen3 1.7B com 3 threads 12 tok/s
    # (~2 s por resposta curta) e com 4 threads quase parado (disputa com o resto da máquina).
# Placa antiga pelo Vulkan do Ollama: 1 tok/s (e ela é do programa que já usa a tela): num_gpu 0
    "cpu-basico": {
        "detection": {"det_width": 960, "frame_every": 2, "max_rec_per_cycle": 2, "threads": 2},
        "assistant": {"enabled": True},
        "greeting": {"live_synth": True, "llm": False},
        "llm": {"model": "qwen3:1.7b", "num_predict": 50, "num_thread": "cores-1", "num_gpu": 0},
        "stt": {"model": "base", "device": "cpu", "compute_type": "int8", "beam_size": 1,
                "cpu_threads": "cores-1"},
    },
    "cpu": {
        "detection": {"det_width": 960, "frame_every": 2, "max_rec_per_cycle": 3, "threads": "cores"},
        "assistant": {"enabled": True},
        "greeting": {"live_synth": True, "llm": False},
        "llm": {"model": "qwen3:1.7b", "num_predict": 70, "num_thread": "cores-1", "num_gpu": 0},
        "stt": {"model": "small", "device": "cpu", "compute_type": "int8", "beam_size": 1,
                "cpu_threads": "cores-1"},
    },
    "cpu-forte": {
        "detection": {"det_width": 1280, "frame_every": 1, "max_rec_per_cycle": 4, "threads": "cores"},
        "assistant": {"enabled": True},
        "greeting": {"live_synth": True, "llm": False},
        "llm": {"model": "qwen3:4b", "num_predict": 80, "num_thread": "cores-1", "num_gpu": 0},
        "stt": {"model": "small", "device": "cpu", "compute_type": "int8", "beam_size": 2,
                "cpu_threads": "cores-1"},
    },
    "gpu": {
        "detection": {"det_width": 1280, "frame_every": 1, "max_rec_per_cycle": 6, "threads": "cores"},
        "assistant": {"enabled": True},
        "greeting": {"live_synth": True, "llm": True},
        "llm": {"model": "qwen3:4b", "num_predict": 90},
        "stt": {"model": "large-v3-turbo", "device": "cuda", "compute_type": "int8_float16", "beam_size": 3},
    },
    "gpu-forte": {
        "detection": {"det_width": 1280, "frame_every": 1, "max_rec_per_cycle": 8, "threads": "cores"},
        "assistant": {"enabled": True},
        "greeting": {"live_synth": True, "llm": True},
        "llm": {"model": "qwen3:8b", "num_predict": 100},
        "stt": {"model": "large-v3-turbo", "device": "cuda", "compute_type": "float16", "beam_size": 5},
    },
}


def resumo(hw, profile):
    gpu = ", ".join(f"{g[0]} ({g[1]:.0f} GB" + (", antiga: não usada)" if g[2] < 7.0 else ")")
                    for g in hw["gpus"]) or (
        "NVIDIA sem driver" if hw.get("nvidia_sem_driver") else "nenhuma NVIDIA")
    return (f"{hw['cpu']} · {hw['cores']} núcleos{'' if hw.get('avx2', True) else ' (sem AVX2)'} · "
            f"{hw['ram_gb']:.0f} GB RAM · placa: {gpu} "
            f"→ perfil '{profile}'")


if __name__ == "__main__":
    h = detect()
    p = choose(h)
    print(resumo(h, p))
    for sec, vals in PROFILES[p].items():
        print(f"  [{sec}] " + ", ".join(f"{k}={v}" for k, v in vals.items()))
