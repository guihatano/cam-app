import os
import stat

from app import load_or_create_secret_key

HTML = {"Accept": "text/html"}


def session_set_cookie(resp):
    """Header Set-Cookie do cookie de sessão do Flask (ou None)."""
    for header in resp.headers.getlist("Set-Cookie"):
        if header.startswith("session="):
            return header
    return None


def login(client, password="test-pass", **extra):
    data = {"user": "admin", "password": password, **extra}
    return client.post("/login", data=data)


def test_wrong_password_shows_error_and_stays_logged_out(client):
    resp = login(client, password="errada")

    assert resp.status_code == 200
    assert "Usuário ou senha inválidos." in resp.get_data(as_text=True)
    assert client.get("/", headers=HTML).status_code == 302


def test_anonymous_html_request_redirects_to_login(client):
    resp = client.get("/", headers=HTML)

    assert resp.status_code == 302
    assert resp.headers["Location"].startswith("/login")


def test_correct_login_redirects_to_index_and_grants_access(client):
    resp = login(client)

    assert resp.status_code == 302
    assert resp.headers["Location"] == "/"
    assert client.get("/", headers=HTML).status_code == 200


def test_login_without_remember_sets_browser_session_cookie(client):
    cookie = session_set_cookie(login(client))

    assert cookie is not None
    assert "Expires=" not in cookie
    assert "Max-Age=" not in cookie


def test_logout_ends_session(client):
    login(client)

    client.get("/logout")

    resp = client.get("/", headers=HTML)
    assert resp.status_code == 302
    assert resp.headers["Location"].startswith("/login")


# --- Chave de sessão persistente (.secret_key) ---


def test_secret_key_file_is_created_with_owner_only_permissions(tmp_path):
    path = tmp_path / ".secret_key"

    key = load_or_create_secret_key(str(path))

    assert key
    assert path.read_text() == key
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_secret_key_is_reused_across_calls(tmp_path):
    path = str(tmp_path / ".secret_key")

    first = load_or_create_secret_key(path)
    second = load_or_create_secret_key(path)

    assert first == second


def test_existing_secret_key_file_is_respected(tmp_path):
    path = tmp_path / ".secret_key"
    path.write_text("chave-existente\n")

    assert load_or_create_secret_key(str(path)) == "chave-existente"


def test_empty_secret_key_file_is_regenerated(tmp_path):
    path = tmp_path / ".secret_key"
    path.write_text("")
    path.chmod(0o644)

    key = load_or_create_secret_key(str(path))

    assert key
    assert path.read_text() == key
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_unwritable_location_returns_ephemeral_key_without_raising(tmp_path, capsys):
    path = str(tmp_path / "nao-existe" / ".secret_key")

    key = load_or_create_secret_key(path)

    assert key
    assert key not in capsys.readouterr().out
