# Willbert no lab2

O lab2 (i5-3330, 4 núcleos sem AVX2, GTX 750 Ti) divide a máquina com a caixa de areia
(SARndbox + projetor, repositório [Projetor-LAOB](https://github.com/VituColombo0/Projetor-LAOB)).
Por isso o Willbert não liga sozinho: só quando pedido.

| Script | Rodar | O que faz |
|---|---|---|
| `configurar.sh` | `sudo bash configurar.sh` | Câmera (IP, conta da câmera), senha da conta Tapo (voz) e usuário/senha do site. Senhas digitadas escondidas, gravadas só no `/opt/visao/config.toml` (root:visao 640). Guarda o anterior em `config.toml.antes-configurar`. Não liga o Willbert. |
| `otimizar.sh` | `sudo bash otimizar.sh` | Processador em desempenho, prioridade menor para Willbert/Ollama, Ollama só na CPU e desligado no boot, impressão e modem parados, swappiness 10, driver NVIDIA fora das atualizações automáticas; instala o `willbert-controle` e a regra do sudo. Estado anterior em `/etc/willbert-otimizacao`. |
| `desfazer.sh` | `sudo bash desfazer.sh` | Volta tudo o que o `otimizar.sh` mudou. |
| `willbert` | `willbert ligar` / `desligar` / `estado` / `testar-camera` | Atalho do `willbert-controle` para o laboratório; instalado em `~/.local/bin` pelo `instalar-atalhos.sh`, que também cria a pasta "6 - Willbert" com o manual (`area-de-trabalho/6-willbert.txt`) na Área de Trabalho. Sem sudo. |
| `willbert-controle` | `sudo -n /usr/local/sbin/willbert-controle <ação>` | Liberado sem senha para o usuário laob (`/etc/sudoers.d/willbert`). Ações: `ligar`, `desligar`, `estado` (sem mostrar senhas), `testar-camera` (uma tentativa de login na Tapo), `foto` (uma foto da câmera para o Projetor-LAOB), `importar` (legado do mini servidor, sem uso). |

Ordem numa máquina nova: `sudo bash ../install-pc.sh`, depois `otimizar.sh`, depois `configurar.sh`.

Cuidados:
- A Tapo bloqueia o login depois de algumas falhas: teste a câmera **uma vez** (`testar-camera`).
- Ao mudar o `willbert-controle` aqui, ele só vale no PC depois de rodar o `otimizar.sh` de novo
  (é ele que instala a cópia em `/usr/local/sbin`, de dono root).
