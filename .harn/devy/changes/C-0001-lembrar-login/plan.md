# Implementation Plan: Login persistente ("Lembrar de mim")

## Overview

Fazer o login do cam-app sobreviver ao fechamento do navegador no celular e aos restarts do serviço. São duas correções independentes no `app.py`:

1. **Chave de sessão estável:** sem `SECRET_KEY` no ambiente, gerar uma vez e persistir em `.secret_key` (0600), protegido do git e do `rsync --delete`.
2. **"Lembrar de mim":** checkbox no login, desmarcado por padrão. Marcado, a sessão fica permanente por 30 dias, deslizante.

Antes das duas, montar um harness de testes com pytest e testes de caracterização do login atual, para provar que nada regrediu. Spec: `spec.md` nesta pasta.

## Architecture Decisions

- **Usar só os mecanismos nativos do Flask** (`permanent_session_lifetime`, `session.permanent`, `SESSION_REFRESH_EACH_REQUEST` padrão). Nenhum código próprio de cookie, nenhuma sessão em servidor. O Flask já valida o `max_age` na assinatura, então o cookie expira também do lado do servidor.
- **Precedência da chave:** `SECRET_KEY` do ambiente > `.secret_key` > gerar e gravar. Assim quem já tem `SECRET_KEY` no `.env` não percebe mudança nenhuma.
- **`.secret_key` mora no diretório do app** (ao lado de `app.py`), que é o `WorkingDirectory` do systemd e é gravável pelo usuário do serviço. Falha ao gravar só gera um aviso e cai na chave efêmera de hoje, sem derrubar o app.
- **Rsync exclude é obrigatório.** O `deploy.sh` roda `rsync --delete` a partir do diretório local, onde não existe `.secret_key` do servidor. Sem o exclude, cada deploy apagaria a chave e deslogaria todo mundo, e nada avisaria.
- **Harness de teste:** `pytest.ini` com `pythonpath = .` e `testpaths = tests` (arquivo novo, pequeno, não listado na spec) para `pytest` funcionar com ou sem `python -m`. O `conftest.py` define `AUTH_USER`/`AUTH_PASSWORD`/`SECRET_KEY` no ambiente **antes** de importar `app`. O `load_dotenv()` não sobrescreve variáveis existentes, então o `.env` local não interfere e o import não cria `.secret_key` no repo. O import de `app.py` não abre rede, e `/` só renderiza template, então é seguro para o test client.
- **`requirements-dev.txt` separado** para o `deploy/setup.sh` (que instala só `requirements.txt`) não levar pytest para o servidor.

## Dependency Graph

```
T-001 harness pytest + caracterização do login atual
   │
   └── T-002 chave de sessão estável (.secret_key, .gitignore, rsync exclude)
          │        [edge por arquivo: app.py, tests/test_auth.py]
          └── T-003 "Lembrar de mim" (checkbox + sessão permanente 30d deslizante)
                 │
                 └── T-004 documentação (README, .env.example)
```

T-002 e T-003 são logicamente independentes, mas ambos editam `app.py` e `tests/test_auth.py`. Por isso têm edge e rodam em série. T-004 depende dos dois porque documenta o comportamento final.

## Phases

### Phase 1: Fundação — T-001

Harness de teste e testes que fixam o comportamento atual: senha errada, logout e cookie sem `Expires`. Vem primeiro porque T-002 e T-003 precisam de rede de segurança, e porque o import de `app.py` sob pytest (dotenv, cv2, chave) é o ponto mais provável de surpresa.

**Checkpoint:** `venv/bin/python -m pytest -q` verde. Nenhum `.secret_key` criado no repo (`git status` limpo fora dos arquivos novos).

### Phase 2: Correções — T-002, depois T-003

T-002 primeiro, por risco: grava arquivo persistente no servidor, e a falha (rsync apagando a chave) seria silenciosa. T-003 entrega o valor principal para o usuário.

**Checkpoint (humano, local):**
- [ ] `venv/bin/python -m pytest -q` verde.
- [ ] Matar instâncias antigas do app (`kill <pid>` do `python app.py`; o `pkill -f` às vezes não pega e a porta 5000 fica presa servindo código velho), subir `venv/bin/python app.py`.
- [ ] Logar com "Lembrar de mim" marcado. Nas devtools, cookie `session` com expiração em ~30 dias, `HttpOnly` e `SameSite=Lax`.
- [ ] Reiniciar o app. Recarregar a página e continuar logado (com `SECRET_KEY` removida temporariamente do ambiente, para exercitar o `.secret_key`).
- [ ] Logar numa aba anônima **sem** marcar. O cookie aparece como "Sessão".

### Phase 3: Documentação e entrega — T-004

**Checkpoint final (humano, servidor + celular). Não é tarefa do backlog: é deploy e teste manual.**
- [ ] `DEPLOY_HOST=<ip> ./deploy.sh`
- [ ] No celular: logar com "Lembrar de mim", fechar e matar o navegador, reabrir e cair direto no app.
- [ ] `sudo systemctl restart cam-app` no servidor, recarregar no celular e continuar logado.
- [ ] Rodar `./deploy.sh` de novo e confirmar que `.secret_key` continua no servidor (`ls -l ~/cam-app/.secret_key`, modo `-rw-------`) e que continua logado.

## Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| `rsync --delete` apaga `.secret_key` do servidor a cada deploy, deslogando todos em silêncio | Alto | `--exclude '.secret_key'` dentro de T-002, com verificação por grep. Deploy duplo no checkpoint final. |
| Import de `app.py` sob pytest lê o `.env` local ou cria `.secret_key` no repo | Médio | T-001 define o env antes do import. O critério de aceite checa que nenhum `.secret_key` aparece no repo. |
| `.secret_key` commitado por engano | Alto | Entra no `.gitignore` em T-002. Verificação com `git check-ignore`. |
| Cookie de 30 dias em HTTP pode ser copiado na LAN | Médio | Aceito na spec (risco parecido com a senha em claro de hoje). Documentado em T-004, junto com como derrubar todas as sessões. HTTPS fica fora do escopo. |
| `SESSION_COOKIE_SECURE` ligado por engano quebraria o login em HTTP | Alto | Proibido na spec (Never). Teste de T-003 faz login completo via test client em HTTP. |

## Open Questions

- **Open redirect pré-existente:** `next` começando com `/` aceita `//outro-site.com`. Ainda sem decisão: corrigir nesta change (viraria T-005, depois de T-003, mexendo em `app.py` e `tests/test_auth.py`) ou numa change separada?
