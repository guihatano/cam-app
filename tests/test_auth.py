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
