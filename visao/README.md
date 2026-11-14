# Visão — identificação por reconhecimento facial

Módulo do Mini-Server que usa uma câmera **Tapo C200** para registrar quem foi identificado no
laboratório e quando (sem medir permanência), e guardar foto e vídeo de quem não é cadastrado. Tudo roda na própria TV Box
(RK322x, ARMv7, 1 GB de RAM), sem nuvem.

## Como funciona

```
Tapo C200 ──RTSP 720p──▶ ffmpeg (decodifica ~4 qps) ──▶ detector ──▶ SQLite ──▶ web :8080
          ──RTSP 1080p─▶ ffmpeg (copia, sem recodificar) ─▶ segmentos na RAM ─▶ clipes de eventos
          ──ONVIF──────▶ eventos de movimento + posição do pan/tilt
```

1. **Gatilho barato:** o detector só analisa continuamente quando a câmera avisa movimento
   (ONVIF), quando o pan/tilt está girando (a C200 segue as pessoas), quando há movimento na
   imagem ou quando há rostos sendo acompanhados. Parado, faz uma varredura a cada 20 s.
2. **Detecção:** YuNet (OpenCV) no quadro inteiro reduzido para 576×324. Rosto novo ainda sem
   nome é analisado sem pausa nos primeiros 15 s, mesmo com a pessoa parada.
3. **Qualidade:** só tenta reconhecer rostos com tamanho e nitidez suficientes. Rosto de lado ou
   de perfil também é reconhecido, mas com regra mais rígida (similaridade ≥ 0,33, vantagem
   dobrada sobre o 2º mais parecido e sempre duas leituras).
4. **Reconhecimento:** SFace (OpenCV) gera um vetor de 128 números por rosto, comparado com as
   amostras de cada membro. Rostos acompanhados entre ciclos não são reprocessados.
5. **Identificação:** com certeza alta (≥ 0,50) basta uma leitura; abaixo disso, duas leituras
   precisam concordar. Cada membro gera no máximo uma identificação a cada `session_gap_min`
   (30 min), para não repetir o registro enquanto a pessoa continua na sala.
   **Várias pessoas:** um membro nunca fica em dois rostos ao mesmo tempo (vence a leitura mais
   certa); duas leituras fracas em rostos diferentes que estão na cena juntos não se confirmam
   entre si; e se um rosto some e outro aparece no mesmo lugar (alguém sentou onde o outro
   estava), a identidade é conferida de novo antes de continuar valendo.
6. **Desconhecidos:** várias leituras ruins e parecidas entre si viram um alerta com foto (e
   Telegram, se configurado). O alerta diz se aquela pessoa **já foi vista antes** (e quando foi
   a primeira vez); a mesma pessoa só gera um aviso no Telegram por dia. Rostos que não deu
   para decidir vão para a página **Revisão**.
7. **Aprendizado contínuo:** quando a certeza é alta, o sistema guarda novas amostras do membro
   até 50 amostras, **espalhadas no tempo**: as 8 primeiras logo (para reconhecer rápido); depois,
   no máximo 1 a 3 por dia (quantidade sorteada), em momentos sorteados entre 07h e 23h, com pelo
   menos 2 h entre uma e outra — assim entram roupas, luz, cabelo e óculos de dias diferentes.
   Com 50, não para: troca 1 por dia a amostra aprendida mais repetida entre as mais antigas (as
   fotos do cadastro nunca saem). Corrigir uma identificação errada (página Hoje → "corrigir") desfaz o que foi
   aprendido naquela identificação e ensina o rosto à pessoa certa. Membro com poucas amostras
   (menos de 8) aprende mais rápido, até formar uma base boa; a página Membros avisa quem
   precisa de "Capturar pela câmera". Quem só tem a foto de celular do cadastro também é
   aprendido sozinho: 3 leituras do mesmo rosto apontando para ele, com folga sobre os outros,
   bastam para identificar e guardar a amostra da câmera.
   **Revisão automática:** a cada 30 min, os rostos pendentes são conferidos de novo com a
   galeria atual; os que agora batem claramente com um membro são resolvidos sozinhos.
8. **Cumprimento:** sozinho na cena, fala logo após identificar. Com outros rostos próximos
   ainda sem nome, espera até 5 s para juntar: dois membros → os dois nomes; com visitante ou
   3+ pessoas → "pessoal". Cada pessoa é cumprimentada **uma vez por período**: de manhã
   (5–12 h, "bom dia"), à tarde (12–18 h, "boa tarde") e à noite (18–5 h, "boa noite"); a
   madrugada conta como a mesma noite. Quem já foi cumprimentado no período não entra na conta
   (`once_per = "dia"` volta a um cumprimento por dia). As frases de cada membro, do grupo e das duplas
   ficam prontas com antecedência na voz natural (Piper, voz brasileira **Faber**, escolhida
   ouvindo também Jeff e Cadu na câmera). Vozes do Piper 1.5 como Jeff e Cadu também
   funcionam: usam ditongos como um fonema só, que o Piper instalado não entende, então o
   Visão converte o texto em fonemas pelo espeak-ng e entrega pronto. Ao trocar de voz,
   `piper_fallback` faz os cumprimentos usarem a voz anterior até a nova gerar as frases.

Desempenho medido na TV Box (motor clássico do OpenCV 5, ~30% mais rápido que o novo e com
resultados idênticos): detecção ~0,35–0,48 s por quadro, reconhecimento ~0,63 s por rosto,
decodificação do 720p ~70% de um núcleo, gravação do 1080p ~20% de um núcleo.

**Tempo até falar:** a fala vai para a câmera já convertida (A-law 8 kHz, feita em numpy e
guardada junto da frase), servida ao go2rtc por um HTTP local no ritmo real. Antes o go2rtc
abria o ffmpeg a cada frase, e o ffmpeg leva ~4 s só para iniciar na TV Box (carrega 210
bibliotecas); agora a câmera começa a receber o áudio em ~0,1–0,5 s.

## Proteções e manutenção

- **Watchdog de hardware:** se a TV Box travar (sistema parado por mais de 30 s), o próprio
  hardware a reinicia; um kernel panic reinicia em 10 s (`/etc/systemd/system.conf.d/watchdog.conf`
  e `/etc/sysctl.d/90-visao-panic.conf`). Os serviços sobem sozinhos no boot.

- **Proteção térmica:** acima de 78 °C a análise fica mais espaçada; acima de 85 °C, bem mais.
- **Ritmo adaptativo:** quando todos os rostos visíveis já estão identificados, analisa a cada 1,5 s;
  sem movimento, a cada 20 s.
- **Clipes em fila única:** nunca há dois ffmpeg montando vídeo ao mesmo tempo.
- **Manutenção noturna (após 03:00):** backup do banco em `data/backup/` (guarda 3 dias) e otimização.
- **Cumprimento falado:** desliga sozinho após 3 falhas seguidas, para não bloquear a conta Tapo.
- **Modo segurança (opcional, `[security]`):** movimento no horário vigiado vira alerta com foto e vídeo.

## Instalação

```bash
sudo bash visao/deploy/install.sh
sudo nano /opt/visao/config.toml      # IP e conta da câmera (app Tapo → Conta da câmera)
sudo systemctl restart visao-detector visao-web
sudo cat /root/visao-senha-inicial.txt
```

A interface fica em `http://<ip-do-servidor>:8080` (pela rede local ou pelo Tailscale).

## Cumprimento falado pela câmera

Na primeira identificação do dia, o Visão diz "Bom dia / Boa tarde / Boa noite, Nome!" pelo
alto-falante da Tapo (ou pela saída de som da TV Box, como reserva). Para a C200 aceitar o áudio:

1. No app Tapo: **Eu → Tapo Lab → Compatibilidade com terceiros → Ligado** (sem isso a câmera
   responde 401 mesmo com a senha certa).
2. Em `/opt/visao/config.toml`, seção `[greeting]`: `tapo_cloud_password` = senha da conta
   TP-Link do app (não a "Conta da câmera"), `tapo_auth = "sha256"`, `output = "auto"`.
3. `visao-cli go2rtc-config && systemctl restart visao-go2rtc visao-detector`
4. `visao-cli testar-camera` (uma tentativa só) e `visao-cli falar "Olá!"`.

A Tapo bloqueia o login depois de várias falhas seguidas; por isso o Visão para de tentar a câmera
após 3 falhas e o comando de teste faz uma única tentativa.

## Uso

- **Hoje:** quem foi identificado (primeira e última vez), eventos com foto/vídeo e correção.
- **Ao vivo:** imagem da câmera com os rostos marcados e o estado do sistema.
- **Relatório:** por período, por membro (dias em que foi visto, identificações, última vez),
  horário da primeira identificação de cada dia e lista completa, com exportação para CSV.
- **Membros:** cadastro, envio de fotos e **captura pela câmera** (mais precisa: a pessoa fica
  sozinha na frente da câmera por até 90 s e o sistema guarda 12 amostras variadas).
- **Revisão:** rostos não identificados; atribuir a um membro vira nova amostra.

Linha de comando no servidor:

```bash
visao-cli status
visao-cli membros
visao-cli adicionar "Nome da Pessoa" foto1.jpg foto2.jpg
visao-cli relatorio --dias 7
visao-cli senha
```

Logs: `journalctl -u visao-detector -f`.

Teste dos cenários com várias pessoas (sem câmera, roda em segundos):
`cd visao && /opt/visao/venv/bin/python -m tests.sim_multi`.

## Arquivos

| Caminho | Conteúdo |
| :--- | :--- |
| `/opt/visao/config.toml` | configuração (tem a senha da câmera; fora do git) |
| `/opt/visao/data/visao.db` | membros, amostras, sessões e eventos (SQLite) |
| `/opt/visao/data/members/` | fotos e recortes de rosto dos membros |
| `/opt/visao/data/events/`, `clips/` | fotos e vídeos de eventos (apagados após `retention_days`, padrão 7) |
| `/dev/shm/visao/` | imagem ao vivo, status e segmentos de vídeo (RAM) |

## Privacidade (LGPD)

Rosto é dado biométrico, ou seja, dado pessoal sensível (art. 11). Use só com membros que
deram consentimento específico para o cadastro facial. O que já existe para isso:

- os dados ficam só no servidor e a interface pede senha;
- excluir um membro apaga as fotos, as amostras e o histórico dele;
- todo o histórico (presenças, eventos, fotos, vídeos e rostos não identificados) é apagado após `retention_days` (7 dias); só as amostras de cadastro dos membros ficam.

## Limitações conhecidas

- Rostos a mais de ~5 m ficam pequenos demais no 720p.
- A C200 só segue uma pessoa por vez; com várias pessoas, ela segue o movimento.
