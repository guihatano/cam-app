# cam-app

Interface web leve para câmera IP **iCSee / XM (X6E-WEQ)** — visualização ao vivo, controle da câmera (PTZ), snapshot e acesso às gravações do cartão SD, tudo pelo navegador e **sem as propagandas** do app oficial.

## Funcionalidades

- **Ao vivo** — stream MJPEG a partir do RTSP da câmera
- **Snapshot** — captura um quadro em JPEG
- **PTZ** — mover a câmera (pan/tilt) e zoom via ONVIF, com "segurar para mover"
- **Gravações** — lista as gravações do SD por data (protocolo XMEye) e reproduz no navegador (download DVRIP → demux XM → transcode para H.264/MP4, com cache local)
- **Login** — autenticação por sessão protegendo todas as rotas

## Requisitos

- Python 3.9+
- **ffmpeg** instalado e no `PATH` (necessário para o playback das gravações)
- Uma câmera iCSee/XM na mesma rede, com RTSP, ONVIF (porta 8899) e XMEye (porta 34567) acessíveis

Instalar o ffmpeg (Ubuntu/Debian):

```bash
sudo apt install ffmpeg
```

## Como rodar localmente

```bash
# 1. Clonar / entrar no diretório
cd cam-app

# 2. Criar e ativar um ambiente virtual
python3 -m venv venv
source venv/bin/activate

# 3. Instalar as dependências
pip install -r requirements.txt

# 4. Configurar o ambiente
cp .env.example .env
# edite o .env com os dados da sua câmera e a senha de login

# 5. Subir o app
python app.py
```

Acesse **http://localhost:5000** (ou `http://<ip-da-maquina>:5000` de outro dispositivo na LAN). Faça login com o usuário/senha definidos no `.env`.

## Deploy em servidor (ex: Armbian)

O `deploy.sh` envia o app via `rsync` (incluindo o `.env` local), instala `ffmpeg`/`python3-venv` se faltarem, cria o `venv`, instala as dependências e registra o serviço systemd `cam-app` (sobe no boot e reinicia se cair).

```bash
DEPLOY_HOST=192.168.1.50 ./deploy.sh
# opcionais: DEPLOY_USER (padrão: usuário local), DEPLOY_DIR (padrão: /home/$DEPLOY_USER/cam-app)
```

Requer acesso SSH ao servidor e `rsync` instalado nos dois lados. O `sudo` pode pedir a senha do servidor. O `venv/` e o cache `recordings/` do servidor são preservados entre deploys.

No servidor:

```bash
sudo systemctl status cam-app     # estado
journalctl -u cam-app -f          # logs
```

> Em Armbian **64 bits** (`uname -m` → `aarch64`) o `opencv-python-headless` instala via wheel pronto. Em 32 bits (`armv7l`) o pip tentaria compilar — nesse caso use `sudo apt install python3-opencv` e crie o venv com `--system-site-packages`.

## Configuração (`.env`)

| Variável        | Descrição                                                              | Padrão                  |
|-----------------|------------------------------------------------------------------------|-------------------------|
| `RTSP_URL`      | URL RTSP da câmera, com usuário e senha (ex: `rtsp://admin:senha@192.168.1.100:554/`) | — |
| `LIVE_RTSP_URL` | Stream do "Ao vivo"; recomendado o substream (ex: `rtsp://192.168.1.100:554/user=admin&password=senha&channel=1&stream=1.sdp`). O snapshot segue usando o `RTSP_URL` | `RTSP_URL` |
| `AUTH_USER`     | Usuário de login do app                                                | `admin`                 |
| `AUTH_PASSWORD` | Senha de login do app (obrigatória)                                    | — (vazia = login negado)|
| `SECRET_KEY`    | Chave secreta das sessões Flask (gere uma aleatória)                   | aleatória a cada boot   |
| `PORT`          | Porta em que o app sobe                                                | `5000`                  |
| `JPEG_QUALITY`  | Qualidade do JPEG do stream/snapshot (0–100)                           | `80`                    |
| `ONVIF_PORT`    | Porta ONVIF da câmera (usada pelo PTZ)                                 | `8899`                  |
| `PTZ_SPEED`     | Velocidade do movimento PTZ (0–1)                                      | `0.5`                   |

> O usuário e a senha da **câmera** são lidos automaticamente do `RTSP_URL` e reaproveitados para ONVIF e XMEye.

## Estrutura

| Arquivo               | Responsabilidade                                                         |
|-----------------------|--------------------------------------------------------------------------|
| `app.py`              | Servidor Flask — stream, snapshot, PTZ, API de gravações, playback, login |
| `xmeye.py`            | Cliente do protocolo XMEye (porta 34567) — busca e download de gravações |
| `onvif_ptz.py`        | Cliente ONVIF (porta 8899) — controle de pan/tilt/zoom                    |
| `templates/index.html`| UI com abas "Ao vivo" e "Gravações"                                       |
| `templates/login.html`| Tela de login                                                            |
| `deploy.sh`           | Deploy para servidor remoto via rsync + systemd                          |
| `deploy/`             | Template do serviço systemd e script de setup executado no servidor      |
| `recordings/`         | Cache dos MP4 já transcodados (pode ser apagado a qualquer momento)      |

## Notas

- O app roda em **HTTP puro** — adequado para uso na LAN. As credenciais trafegam em claro; se for expor fora de casa, use um proxy reverso com HTTPS.
- A **primeira reprodução** de uma gravação leva ~50s (download + transcode); reproduções seguintes são instantâneas por vir do cache em `recordings/`.
- Cada gravação em cache ocupa ~27 MB. Apague `recordings/` quando quiser liberar espaço.
