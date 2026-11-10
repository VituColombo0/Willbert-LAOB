# Willbert

Assistente de visão, voz e IA do laboratório, rodando num PC de verdade. É o sucessor do
**Visão**, que nasceu no [Mini-Server](https://github.com/VituColombo0/Mini-Server) (TV Box
RK322x): reconhece os membros pela câmera Tapo C200, cumprimenta pelo alto-falante da
câmera e registra quem passou pelo laboratório.

## Ponto de partida

`visao/` é o código do Visão como estava no Mini-Server (commit `2cd512a`): detecção
YuNet + reconhecimento SFace, acompanhamento de várias pessoas, aprendizado contínuo,
cumprimento por período na voz Piper (Faber), áudio para a câmera via go2rtc sem ffmpeg,
página web, revisão automática, retenção de 7 dias. Ele já roda num PC Linux; o plano é
evoluir daqui.

## Plano

| Etapa | O quê |
|---|---|
| 1. Migração | Visão rodando no PC (Linux ou WSL2), câmera e voz funcionando, mesmo banco de dados e cadastros |
| 2. Tempo real | Análise a 15–30 qps, modelos melhores (InsightFace/ArcFace, RetinaFace), GPU se houver; fala em ~1–2 s |
| 3. Voz | TTS neural gerado na hora (frases livres), mantendo a voz escolhida |
| 4. LLM local | Modelo de linguagem local (Ollama) para cumprimentos e respostas com contexto ("faz 2 dias que não te via") |
| 5. Conversa | Microfone da câmera + reconhecimento de fala (Whisper) + LLM + voz |
| 6. Relatórios | Resumos em linguagem natural (dia/semana) |

## Papel do Mini-Server

A TV Box continua ligada como **reserva e apoio**: assume com o Visão atual se o PC cair,
grava os clipes da câmera, vigia o PC, guarda backup do banco e dos cadastros e segue sendo
a porta de entrada pelo Tailscale.

## Segredos

Senhas (câmera, conta Tapo, login web) ficam só no `config.toml` da máquina, fora do git.
