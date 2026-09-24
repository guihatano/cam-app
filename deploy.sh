#!/usr/bin/env bash
# Envia o app para o servidor (ex: Armbian) e (re)inicia o serviço systemd.
#
# Uso:
#   DEPLOY_HOST=192.168.1.50 ./deploy.sh
#
# Variáveis:
#   DEPLOY_HOST  IP/hostname do servidor (obrigatório)
#   DEPLOY_USER  usuário SSH no servidor          (padrão: usuário local)
#   DEPLOY_DIR   diretório do app no servidor     (padrão: /home/$DEPLOY_USER/cam-app)
set -euo pipefail

: "${DEPLOY_HOST:?defina DEPLOY_HOST (ex: DEPLOY_HOST=192.168.1.50 ./deploy.sh)}"
DEPLOY_USER="${DEPLOY_USER:-$USER}"
DEPLOY_DIR="${DEPLOY_DIR:-/home/$DEPLOY_USER/cam-app}"
TARGET="$DEPLOY_USER@$DEPLOY_HOST"

cd "$(dirname "$0")"

if [[ ! -f .env ]]; then
    echo "Erro: .env não encontrado (copie de .env.example e preencha)." >&2
    exit 1
fi

echo "==> Sincronizando arquivos para $TARGET:$DEPLOY_DIR"
ssh "$TARGET" "mkdir -p '$DEPLOY_DIR'"
rsync -az --delete \
    --exclude '.git/' \
    --exclude '.claude/' \
    --exclude 'venv/' \
    --exclude '__pycache__/' \
    --exclude '*.pyc' \
    --exclude 'recordings/' \
    ./ "$TARGET:$DEPLOY_DIR/"

echo "==> Instalando dependências e configurando o serviço"
# -t para o sudo poder pedir senha
ssh -t "$TARGET" "bash '$DEPLOY_DIR/deploy/setup.sh' '$DEPLOY_USER' '$DEPLOY_DIR'"

PORT="$(grep -E '^PORT=' .env | cut -d= -f2 || true)"
echo "==> Pronto: http://$DEPLOY_HOST:${PORT:-5000}"
