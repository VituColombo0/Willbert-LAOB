# Willbert

Assistente de visão, voz e IA do laboratório, rodando num PC. É o sucessor do **Visão**, que
nasceu no [Mini-Server](https://github.com/VituColombo0/Mini-Server) (TV Box RK322x):
reconhece os membros pela câmera Tapo C200, cumprimenta pelo alto-falante da câmera,
registra quem passou pelo laboratório e agora **conversa**: chame "Willbert" perto da câmera.

## O que ele faz

- **Reconhece** os membros (YuNet + SFace), com várias pessoas ao mesmo tempo, aprendizado
  contínuo e revisão automática (tudo herdado do Visão).
- **Cumprimenta** uma vez por período (bom dia / boa tarde / boa noite), pelo nome, em dupla ou
  o grupo. Com placa de vídeo, o cumprimento vem do LLM com contexto ("faz três dias que não te
  via, Mario!"); se o LLM demorar mais de 1,5 s, sai a frase padrão.
- **Conversa** pela câmera: microfone da Tapo -> detecção de voz -> Whisper -> resposta -> voz.
  - Perguntas comuns têm resposta **pronta e exata**, instantânea e sem inventar: que horas são,
    que dia é hoje, quem está aqui, quem chegou hoje, o Mario veio hoje?, cadê o Lucas?, quem sou eu?
  - O resto vai para o **LLM local** (Ollama), com os fatos do laboratório e a pedido de respostas
    de no máximo duas frases.
  - Depois de responder, escuta a continuação por 10 s sem precisar chamar pelo nome.
  - Enquanto a câmera fala (e 0,6 s depois), o microfone é ignorado: ele não responde ao próprio eco.
- **Fala clara**: toda resposta passa por `fala.para_fala()`: números, horas e datas por extenso
  ("duas e meia da tarde"), sem markdown/emoji, curta e terminando em frase completa.

## Hardware: escolhido sozinho

`visao/visao/hardware.py` detecta CPU, RAM e placa NVIDIA e escolhe o perfil
(`visao-cli hardware` mostra; `[hardware] profile` no config.toml força outro):

| Perfil | Quando | Reconhecimento | Fala (Whisper) | LLM (Ollama) | Cumprimento |
|---|---|---|---|---|---|
| `gpu-forte` | NVIDIA 10+ GB | 1280 px, todo quadro | large-v3-turbo (GPU) | qwen3:8b | com contexto (LLM) |
| `gpu` | NVIDIA 6–10 GB | 1280 px, todo quadro | large-v3-turbo (GPU, int8) | qwen3:4b | com contexto (LLM) |
| `cpu-forte` | sem NVIDIA, 8+ núcleos, 16+ GB | 1280 px, todo quadro | small (CPU, int8) | qwen3:4b | frase pronta, voz na hora |
| `cpu` | PC sem placa, até 7 núcleos | 960 px, 1 a cada 2 | small (CPU, int8) | qwen3:1.7b | frase pronta, voz na hora |
| `cpu-basico` | PC antigo (sem AVX2 ou 4 núcleos) | 960 px, 1 a cada 2 | base (CPU, int8) | qwen3:1.7b (3 threads) | frase pronta, voz na hora |
| `tvbox` | ARM com 1 GB | 576 px, 1 a cada 4 | — | — | frases geradas antes |

Placas NVIDIA antigas (compute capability abaixo de 7.0, como a GTX 750 Ti) não contam como
GPU: o faster-whisper atual não roda nelas e o Ollama só as usa pelo Vulkan, bem mais devagar
que a CPU. O LLM e o Whisper usam um núcleo a menos que a máquina tem (com todos, a disputa
com outros programas deixa o LLM quase parado). Se o LLM passar de 1,8 s, ele fala
"Só um instante." para a espera não parecer defeito.

Medido no PC do laboratório (i5-3330, 4 núcleos sem AVX2, GTX 750 Ti, junto com o SARndbox):

| Etapa | Tempo |
|---|---|
| Detecção de rosto (YuNet 960 px, 2 threads) | ~50 ms por quadro |
| Identificação (SFace) | ~35 ms por rosto |
| Fala -> texto (Whisper base) | ~2,5 s por frase (small: ~9 s, inviável) |
| Resposta pronta (hora, quem chegou...) | instantânea |
| Resposta do LLM (qwen3:1.7b) | 4–10 s |
| Voz (Piper Faber, frase curta) | ~1,4 s |

Qwen3 foi escolhido por ser o melhor modelo local pequeno em português do Brasil
([comparativo](https://www.promptquorum.com/local-llms/best-local-llms-portuguese-language-2026));
é chamado sem "pensar" (`think: false`) para responder rápido.

## Instalação no PC (Ubuntu/Debian x86_64)

```bash
git clone https://github.com/VituColombo0/Willbert.git && cd Willbert
sudo bash visao/deploy/install-pc.sh
```

O instalador: instala o driver NVIDIA se houver placa sem driver (pede reiniciar e rodar de
novo), o Ollama com o modelo do perfil, o Whisper, o Piper (voz Faber), o go2rtc e os serviços
`visao-go2rtc`, `visao-detector` e `visao-web` (:8080). Pode rodar de novo para atualizar.

Depois, para trazer cadastros, histórico e a configuração da câmera do mini servidor (de uma
máquina com SSH para os dois):

```bash
bash visao/deploy/migrar-do-miniserver.sh <ssh-do-pc> miniserver-root
```

## Mini-Server como reserva

`visao/deploy/reserva/` instala na TV Box um verificador a cada 30 s: se o PC não responder em
`/saude` 3 vezes seguidas (~90 s), a TV Box liga o Visão e assume a câmera; quando o PC volta,
ela desliga. Nunca os dois ao mesmo tempo (a câmera falaria duas vezes).

```bash
scp -r visao/deploy/reserva miniserver-root:/tmp/ && ssh miniserver-root 'sh /tmp/reserva/instalar.sh http://<ip-do-pc>:8080'
```

## Testes (sem câmera)

```bash
cd visao
python -m tests.test_conversa   # nome do assistente, respostas prontas, LLM simulado, perfis, fala
python -m tests.sim_multi       # várias pessoas, cumprimentos por período, aprendizado
visao-cli conversar --mudo      # no PC instalado: digite perguntas e veja as respostas
```

## Próximos passos (depois de ver o PC funcionando)

- Medir o tempo real e trocar o SFace por ArcFace/InsightFace se houver GPU (mais preciso de lado e de longe).
- Relatórios em linguagem natural ("resumo da semana").
- Backup diário do banco do PC na TV Box.

## Segredos

Senhas (câmera, conta Tapo, login web) ficam só no `config.toml` da máquina, fora do git.
