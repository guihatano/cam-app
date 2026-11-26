import os

# Definidos ANTES de importar o app: o load_dotenv() não sobrescreve variáveis
# existentes, então o .env local não interfere nos testes. SECRET_KEY fixa evita
# que o import crie arquivos de chave no repositório.
os.environ["AUTH_USER"] = "admin"
os.environ["AUTH_PASSWORD"] = "test-pass"
os.environ["SECRET_KEY"] = "test-secret"

import pytest  # noqa: E402

from app import app as flask_app  # noqa: E402


@pytest.fixture
def app():
    flask_app.config.update(TESTING=True)
    return flask_app


@pytest.fixture
def client(app):
    return app.test_client()
