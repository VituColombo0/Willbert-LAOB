# Willbert no lab2

O lab2 (i5-3330, 4 núcleos sem AVX2, GTX 750 Ti) divide a máquina com a caixa de areia
(SARndbox + projetor, repositório [Projetor-LAOB](https://github.com/VituColombo0/Projetor-LAOB)).
Por isso o Willbert não liga sozinho: só quando pedido.

| Script | Rodar | O que faz |
|---|---|---|
| `configurar.sh` | `sudo bash configurar.sh` | Câmera (IP, conta da câmera), senha da conta Tapo (voz) e usuário/senha do site. Senhas digitadas escondidas, gravadas só no `/opt/visao/config.toml` (root:visao 640). Guarda o anterior em `config.toml.antes-configurar`. Não liga o Willbert. |
| `otimizar.sh` | `sudo bash otimizar.sh` | Processador em desempenho, prioridade menor para Willbert/Ollama, Ollama só na CPU e desligado no boot, impressão, modem, relatórios de erro (kerneloops, apport), avahi e switcheroo parados, swappiness 10, driver NVIDIA fora das atualizações automáticas, sem janelas de aviso (atualização, relatório, alarmes, backup) por cima do projetor; instala o `willbert-controle` e a regra do sudo. Estado anterior em `/etc/willbert-otimizacao` (guardado só na primeira vez: pode rodar de novo). |
| `desfazer.sh` | `sudo bash desfazer.sh` | Volta tudo o que o `otimizar.sh` mudou. |
| `atualizar-app.sh` | `sudo bash atualizar-app.sh` | Depois de mudar o código: copia o app para `/opt/visao`, os modelos que faltarem, o `willbert-controle`, os comandos `willbert`/`willbert-janela` e o manual da pasta 6; reinicia o detector se estiver ligado. |
| `kernel-com-driver.sh` | `sudo bash kernel-com-driver.sh` (`--desfazer`) | Faz o PC iniciar no kernel mais novo que tem o driver NVIDIA (o `otimizar.sh` já chama). Em 2026-11-11 o kernel 6.8 chegou pelas atualizações automáticas sem o driver (que está fora delas): a areia caiu para 1 quadro/s e o projetor sumiu. Não instala nem remove pacotes. |
| `willbert` | `willbert ligar` / `desligar` / `estado` / `testar-camera` | Atalho do `willbert-controle` para o laboratório; instalado em `~/.local/bin` pelo `instalar-atalhos.sh`, que também cria a pasta "6 - Willbert" com o manual (`area-de-trabalho/6-willbert.txt`) na Área de Trabalho. Sem sudo. |
| `willbert-controle` | `sudo -n /usr/local/sbin/willbert-controle <ação>` | Liberado sem senha para o usuário laob (`/etc/sudoers.d/willbert`). Ações: `ligar`, `desligar`, `estado` (sem mostrar senhas), `testar-camera` (uma tentativa de login na Tapo), `foto` (uma foto da câmera para o Projetor-LAOB), `importar` (legado do mini servidor, sem uso). |

Ordem numa máquina nova: `sudo bash ../install-pc.sh`, depois `otimizar.sh`, depois `configurar.sh`.

Cuidados:
- A Tapo bloqueia o login depois de algumas falhas: teste a câmera **uma vez** (`testar-camera`).
- Ao mudar o `willbert-controle` aqui, ele só vale no PC depois de rodar o `otimizar.sh` de novo
  (é ele que instala a cópia em `/usr/local/sbin`, de dono root).
