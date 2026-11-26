# Todo: C-0001 Login persistente ("Lembrar de mim")

- [x] T-001: Criar harness pytest com testes de caracterização do login atual
    Adiciona infraestrutura de testes ao projeto (hoje não existe nenhuma) e fixa o comportamento atual do login antes de qualquer mudança. Criar `requirements-dev.txt` com `-r requirements.txt` e `pytest>=8`, e `pytest.ini` com `[pytest]`, `testpaths = tests` e `pythonpath = .`. Em `tests/conftest.py`, definir no `os.environ` `AUTH_USER=admin`, `AUTH_PASSWORD=test-pass` e `SECRET_KEY=test-secret` ANTES de importar `app`. O `load_dotenv()` do `app.py` não sobrescreve variáveis existentes, então o `.env` local não interfere. Expor uma fixture `client` com `app.test_client()`. Em `tests/test_auth.py`, testar o comportamento ATUAL: (a) POST `/login` com senha errada devolve 200 com "Usuário ou senha inválidos."; (b) GET `/` com `Accept: text/html` sem login redireciona (302) para `/login`; (c) login correto redireciona para `/` e GET `/` em seguida devolve 200; (d) o `Set-Cookie` do login correto NÃO tem `Expires` nem `Max-Age`; (e) `/logout` e depois GET `/` com `Accept: text/html` redireciona para `/login`. Não alterar `app.py` nesta tarefa. O import de `app.py` não abre rede (OnvifPTZ e captura são lazy) e `/` só renderiza template.
    Acceptance: `venv/bin/python -m pytest -q` passa com os 5 testes; `venv/bin/pytest -q` (sem `-m`) também passa; nenhum arquivo `.secret_key` é criado na raiz do repo.
    Verification: venv/bin/pip install -r requirements-dev.txt -q && venv/bin/python -m pytest -q && venv/bin/pytest -q && test ! -e .secret_key
    Deps: none
    Files: requirements-dev.txt, pytest.ini, tests/conftest.py, tests/test_auth.py
    Size: M
    Risk: L

- [x] T-002: Persistir a chave de sessão em .secret_key quando SECRET_KEY não estiver no ambiente
    Hoje `app.py` faz `app.secret_key = os.getenv("SECRET_KEY") or secrets.token_hex(32)`, então sem `SECRET_KEY` no `.env` cada restart do serviço invalida todos os logins. Criar em `app.py` a função `load_or_create_secret_key(path)`: lê o arquivo e, se existir com conteúdo (após `strip`), devolve esse conteúdo. Se o arquivo não existir ou estiver vazio, gera `secrets.token_hex(32)` e grava com `os.open(path, O_WRONLY|O_CREAT|O_TRUNC, 0o600)`. Se a gravação falhar com `OSError`, imprime um aviso em português SEM o valor da chave e devolve a chave gerada (efêmera). Definir a constante `SECRET_KEY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".secret_key")` e trocar a atribuição por `app.secret_key = os.getenv("SECRET_KEY") or load_or_create_secret_key(SECRET_KEY_FILE)`. Precedência: env > arquivo > gerar. Adicionar `.secret_key` ao `.gitignore`, na seção de segredos. Em `deploy.sh`, adicionar `--exclude '.secret_key'` ao `rsync -az --delete`. Sem isso, cada deploy apaga a chave criada no servidor. Testes em `tests/test_auth.py` usando `tmp_path`, chamando a função diretamente: cria arquivo com modo 0600 (`stat.S_IMODE(os.stat(p).st_mode) == 0o600`) e chave não vazia; segunda chamada devolve a mesma chave; arquivo pré-existente com conteúdo é respeitado; arquivo vazio é regenerado; diretório inexistente/não gravável devolve chave não vazia sem lançar exceção. Nunca imprimir nem logar o valor da chave.
    Acceptance: testes novos e os de T-001 passam; `git check-ignore -q .secret_key` sucede; `deploy.sh` contém `--exclude '.secret_key'` e passa em `bash -n`.
    Verification: venv/bin/python -m pytest -q && git check-ignore -q .secret_key && grep -q "exclude '.secret_key'" deploy.sh && bash -n deploy.sh
    Deps: T-001
    Files: app.py, .gitignore, deploy.sh, tests/test_auth.py
    Size: M
    Risk: M

- [x] T-003: Adicionar checkbox "Lembrar de mim" com sessão permanente de 30 dias deslizante
    Causa raiz do problema no celular: o login faz `session["auth"] = True` sem `session.permanent`, e o cookie sai sem `Expires` e some quando o navegador fecha. Em `app.py`: constante `SESSION_DAYS = 30`; `app.permanent_session_lifetime = timedelta(days=SESSION_DAYS)` (importar `timedelta` de `datetime`); `app.config["SESSION_COOKIE_SAMESITE"] = "Lax"`. NÃO ligar `SESSION_COOKIE_SECURE`, porque o app roda em HTTP e isso quebraria o login. No login bem-sucedido: `session.clear()`, `session["auth"] = True`, `session.permanent = request.form.get("remember") == "on"`. A expiração deslizante vem do padrão do Flask `SESSION_REFRESH_EACH_REQUEST=True`: não reimplementar. Em `templates/login.html`: checkbox `name="remember"` desmarcado por padrão, com label "Lembrar de mim" clicável (input dentro do `<label>` ou `for`/`id`), entre o campo de senha e o botão, no estilo escuro atual (cor de destaque `#2e7d32` via `accent-color`) e com alvo de toque confortável no celular. Testes em `tests/test_auth.py`: login com `remember=on` gera `Set-Cookie` de `session` com `Expires` entre agora+29d e agora+31d, `HttpOnly` e `SameSite=Lax` (usar `client.get_cookie("session").expires` e/ou o header); login sem `remember` continua sem `Expires`/`Max-Age` (o teste (d) de T-001 segue válido); com sessão permanente, um GET `/` seguinte reemite o cookie de `session` com `Expires` (deslizante); o valor do cookie levado para um client novo (`set_cookie`) ainda autentica GET `/` (cookie stateless, depende só da chave). Ajustar os testes de T-001 só se mudar algo que eles cobrem.
    Acceptance: todos os testes passam; `login.html` renderiza o checkbox desmarcado (GET `/login` contém `name="remember"` e não contém `checked`); login com e sem o checkbox gera cookies com e sem `Expires`, respectivamente.
    Verification: venv/bin/python -m pytest -q
    Deps: T-001, T-002
    Files: app.py, templates/login.html, tests/test_auth.py
    Size: M
    Risk: M

- [x] T-004: Documentar "Lembrar de mim", .secret_key e como derrubar todas as sessões
    Atualizar `README.md`. Na tabela de variáveis, mudar a linha de `SECRET_KEY`: o padrão deixa de ser "aleatória a cada boot" e passa a ser "gerada uma vez e salva em `.secret_key`". Na seção de segurança/autenticação, explicar: o checkbox "Lembrar de mim" (30 dias sem uso até pedir login de novo, desmarcado por padrão; sem marcar, a sessão termina ao fechar o navegador); o arquivo `.secret_key` (0600, fora do git e do rsync); como derrubar todas as sessões (apagar `.secret_key` no servidor ou trocar `SECRET_KEY` e reiniciar o serviço com `sudo systemctl restart cam-app`); e o aviso de que, em HTTP puro, o cookie de 30 dias pode ser copiado por quem capturar tráfego na LAN. Em `.env.example`, adicionar `SECRET_KEY` comentada, explicando que é opcional e é auto-gerada em `.secret_key` se ausente. Manter o tom e o idioma (português) atuais dos dois arquivos.
    Acceptance: README descreve checkbox, prazo, `.secret_key` e como invalidar sessões; a linha de `SECRET_KEY` na tabela não diz mais "aleatória a cada boot"; `.env.example` menciona `SECRET_KEY` como opcional.
    Verification: grep -q "Lembrar de mim" README.md && grep -q ".secret_key" README.md && ! grep -q "aleatória a cada boot" README.md && grep -q "SECRET_KEY" .env.example
    Deps: T-002, T-003
    Files: README.md, .env.example
    Size: S
    Risk: L
