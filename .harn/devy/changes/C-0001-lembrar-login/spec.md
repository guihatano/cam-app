# Spec: Login persistente ("Lembrar de mim")

## Objective

No celular, toda vez que o navegador é fechado (ou o sistema o encerra em segundo plano), o cam-app pede usuário e senha de novo.

**Causa raiz** (verificada no código):

1. `app.py` faz `session["auth"] = True` no login, mas nunca define `session.permanent = True`. O Flask então emite o cookie `session` **sem `Expires`/`Max-Age`**, um cookie de sessão do navegador que some quando o navegador fecha.
2. `app.py:25` usa `os.getenv("SECRET_KEY") or secrets.token_hex(32)`. Sem `SECRET_KEY` no `.env`, cada restart do serviço (deploy, `Restart=always`, reboot) gera uma chave nova e invalida todos os cookies já emitidos.

**O que vamos construir:**

- Um checkbox **"Lembrar de mim"** na tela de login, **desmarcado por padrão**.
  - Marcado: sessão persistente de **30 dias, deslizante**. Cada requisição autenticada renova o prazo, então o app só pede login de novo depois de 30 dias sem uso.
  - Desmarcado: comportamento atual, sessão que termina quando o navegador fecha.
- Uma **chave de sessão estável entre restarts**: se `SECRET_KEY` não estiver no ambiente, gerar uma vez e persistir num arquivo `.secret_key` (permissão `0600`), reaproveitado nos próximos starts.
- O botão "Sair" continua encerrando a sessão na hora.

**Usuário:** o dono da casa (usuário único `admin`), acessando pelo celular e pelo PC na LAN.

**Sucesso:** logar no celular com "Lembrar de mim" marcado, fechar/matar o navegador, reabrir no dia seguinte (ou depois de um restart/deploy do serviço) e cair direto no "Ao vivo", sem tela de login.

### Premissas

1. Usuário único, uso doméstico na LAN.
2. Continua em HTTP puro. HTTPS fica fora desta mudança.
3. Sessão continua sendo o cookie assinado padrão do Flask (`SecureCookieSessionInterface`), sem sessão em servidor nem banco.
4. Única dependência nova é `pytest`, só para desenvolvimento (não vai para o servidor).

## Tech Stack

- Python 3, Flask 3.1.x (Werkzeug 3.1.x), python-dotenv. Versões instaladas hoje: Flask 3.1.3, Werkzeug 3.1.8.
- Novo (dev only): `pytest>=8` em `requirements-dev.txt`.

Mecanismos do Flask usados (sem código próprio de cookie):

- `app.permanent_session_lifetime = timedelta(days=30)` define `Expires` do cookie e o `max_age` validado na assinatura.
- `session.permanent = True/False` por login.
- `SESSION_REFRESH_EACH_REQUEST = True` (padrão do Flask) reemite o cookie de sessões permanentes a cada requisição, o que dá a expiração deslizante.
- `SESSION_COOKIE_HTTPONLY = True` (padrão) e `SESSION_COOKIE_SAMESITE = "Lax"` (explícito).

## Commands

```bash
# Dev: rodar o app local (porta 5000)
venv/bin/python app.py

# Instalar dependências de dev (inclui as de produção)
venv/bin/pip install -r requirements-dev.txt

# Testes
venv/bin/python -m pytest -q

# Deploy para o servidor (Armbian)
DEPLOY_HOST=<ip-do-servidor> ./deploy.sh
```

## Project Structure

Arquivos tocados por esta mudança:

```
app.py                     → lifetime de 30 dias, session.permanent no login,
                             SameSite=Lax, load_or_create_secret_key()
templates/login.html       → checkbox "Lembrar de mim" (name="remember", desmarcado)
.gitignore                 → + .secret_key
deploy.sh                  → rsync: + --exclude '.secret_key' (o --delete apagaria a
                             chave criada no servidor a cada deploy)
README.md                  → documentar "Lembrar de mim", .secret_key e como derrubar
                             todas as sessões (apagar .secret_key / trocar SECRET_KEY)
.env.example               → comentário sobre SECRET_KEY (opcional; auto-gerada se ausente)
requirements-dev.txt       → novo: -r requirements.txt + pytest
tests/conftest.py          → novo: env de teste antes de importar app; fixture client
tests/test_auth.py         → novo: testes de sessão/login/logout/chave
.harn/devy/changes/C-0001-lembrar-login/spec.md → esta spec
```

## Code Style

Seguir o estilo atual do `app.py`: Flask simples, constantes de módulo lidas de `os.getenv`, funções curtas, comentários em português, sem abstrações extras.

```python
SECRET_KEY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".secret_key")
SESSION_DAYS = 30


def load_or_create_secret_key(path):
    """Lê a chave de sessão do arquivo; se não existir, gera e grava com 0600.

    Mantém os logins válidos entre restarts quando SECRET_KEY não está no .env.
    """
    try:
        with open(path) as f:
            key = f.read().strip()
        if key:
            return key
    except FileNotFoundError:
        pass
    key = secrets.token_hex(32)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(key)
    except OSError as e:
        print(f"[auth] não foi possível gravar {path} ({e}); sessões não sobrevivem a restart")
    return key


app.secret_key = os.getenv("SECRET_KEY") or load_or_create_secret_key(SECRET_KEY_FILE)
app.permanent_session_lifetime = timedelta(days=SESSION_DAYS)
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
```

No login:

```python
if ok_user and ok_pw:
    session.clear()
    session["auth"] = True
    session.permanent = request.form.get("remember") == "on"
```

Convenções:

- Nomes em inglês no código e mensagens/comentários em português, como no resto do projeto.
- Nunca imprimir nem logar o valor da chave.
- Comparação de credenciais continua com `hmac.compare_digest`.

## Testing Strategy

- **Framework:** pytest + `app.test_client()` do Flask.
- **Local:** `tests/`. `conftest.py` define `AUTH_USER`, `AUTH_PASSWORD` e `SECRET_KEY` no ambiente **antes** de importar `app`. O `load_dotenv()` não sobrescreve variáveis já definidas, então o `.env` local não interfere, e o import não cria `.secret_key` no repositório. O import de `app.py` não abre rede (OnvifPTZ e captura são lazy).
- **Unitário:** `load_or_create_secret_key(tmp_path / ".secret_key")`:
  - cria o arquivo com modo `0600` e chave não vazia;
  - segunda chamada devolve a mesma chave;
  - arquivo existente com conteúdo é respeitado.
- **Integração (test client):**
  - login com `remember=on` → `Set-Cookie: session=...` contém `Expires` ≈ agora + 30 dias (tolerância de minutos), `HttpOnly` e `SameSite=Lax`;
  - login sem `remember` → `Set-Cookie` sem `Expires`/`Max-Age`;
  - requisição autenticada seguinte com sessão permanente → cookie reemitido com novo `Expires` (deslizante);
  - senha errada → continua na tela com "Usuário ou senha inválidos." e sem `auth` na sessão;
  - `/logout` → requisição seguinte a `/` com `Accept: text/html` redireciona para `/login`;
  - cookie assinado por uma instância continua válido para outra instância com a mesma chave (simula restart).
- **Manual (celular, depois do deploy):**
  1. Logar com "Lembrar de mim" marcado.
  2. Fechar o navegador e encerrá-lo pelo gerenciador de apps.
  3. Reabrir a URL e conferir que vai direto para o app.
  4. `sudo systemctl restart cam-app` no servidor, recarregar e conferir que continua logado.
  5. Logar em outra aba anônima **sem** marcar, fechar e reabrir: pede login.
- Rodar `venv/bin/python -m pytest -q` antes de cada commit.

## Boundaries

- **Always:**
  - Rodar os testes antes de commitar.
  - Arquivo `.secret_key` criado com `0600`, no `.gitignore` e excluído do rsync.
  - Precedência da chave: `SECRET_KEY` do ambiente > `.secret_key` > gerar e gravar.
  - Manter `hmac.compare_digest` e o `before_request` que protege todas as rotas.
  - Commits sem linha `Co-Authored-By`.
- **Ask first:**
  - Qualquer dependência além de `pytest`.
  - Mudar `deploy/setup.sh` ou o serviço systemd.
  - Alterar o prazo de 30 dias ou o padrão desmarcado do checkbox.
  - Ativar HTTPS / `SESSION_COOKIE_SECURE`.
- **Never:**
  - Commitar `.env` ou `.secret_key`.
  - Imprimir ou logar o valor da chave ou da senha.
  - Ligar `SESSION_COOKIE_SECURE=True` enquanto o app roda em HTTP, porque isso quebra o login.
  - Remover a validação de assinatura/expiração do cookie do Flask.

## Success Criteria

1. Com "Lembrar de mim" marcado, o cookie `session` sai com `Expires` de ~30 dias, `HttpOnly` e `SameSite=Lax`.
2. Sem o checkbox, o cookie sai sem `Expires` (comportamento atual preservado).
3. Cada acesso autenticado com sessão permanente renova o `Expires` (deslizante).
4. Sem `SECRET_KEY` no ambiente, o primeiro start cria `.secret_key` (0600) e os seguintes reutilizam a mesma chave. Um cookie emitido antes do restart continua válido depois.
5. Com `SECRET_KEY` no ambiente, `.secret_key` não é lido nem criado.
6. `.secret_key` está no `.gitignore` e no `--exclude` do rsync do `deploy.sh`.
7. Checkbox visível no `login.html`, desmarcado por padrão, com o tema escuro atual e usável no celular (alvo de toque confortável e label clicável).
8. `venv/bin/python -m pytest -q` passa.
9. Teste manual no celular (seção Testing Strategy) passa.

## Riscos

- **Cookie de longa duração em HTTP:** quem estiver capturando tráfego na LAN pode copiar o cookie e usá-lo por até 30 dias. Hoje a senha já trafega em claro na mesma rede, então o risco é parecido. Mitigação futura: HTTPS + `Secure`.
- **Derrubar todas as sessões** (ex.: celular perdido): apagar `.secret_key` no servidor (ou trocar `SECRET_KEY`) e reiniciar o serviço. Documentar no README.

## Open Questions

1. **Fora de escopo, mas encontrado:** o redirect pós-login aceita `next` começando com `/`, o que inclui `//outro-site.com` (open redirect). Corrigir junto nesta mudança ou em outra separada?
