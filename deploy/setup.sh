#!/usr/bin/env bash
# Roda NO SERVIDOR (chamado pelo deploy.sh): instala dependências e (re)inicia o serviço.
# Uso: bash deploy/setup.sh <usuario> <diretorio-do-app>
set -euo pipefail
DEPLOY_USER="$1"
DEPLOY_DIR="$2"
cd "$DEPLOY_DIR"

missing=()
command -v ffmpeg >/dev/null || missing+=(ffmpeg)
python3 -c 'import venv, ensurepip' 2>/dev/null || missing+=(python3-venv)
if (( ${#missing[@]} )); then
    echo "--> Instalando pacotes do sistema: ${missing[*]}"
    sudo apt-get update
    sudo apt-get install -y "${missing[@]}"
fi

[[ -d venv ]] || python3 -m venv venv
venv/bin/pip install --upgrade pip -q
venv/bin/pip install -r requirements.txt -q

sed -e "s|__USER__|$DEPLOY_USER|g" -e "s|__APP_DIR__|$DEPLOY_DIR|g" \
    deploy/cam-app.service | sudo tee /etc/systemd/system/cam-app.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable cam-app >/dev/null 2>&1
sudo systemctl restart cam-app
sleep 2
sudo systemctl --no-pager --lines=5 status cam-app || true
