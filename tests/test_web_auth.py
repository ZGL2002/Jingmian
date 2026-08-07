from fastapi import FastAPI
from fastapi.testclient import TestClient
from interview_agent.web.auth import TokenAuthMiddleware, AUTH_COOKIE, token_matches


def make_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(TokenAuthMiddleware, token="secret")

    @app.get("/")
    def root():
        return {"ok": True}

    @app.get("/api/data")
    def data():
        return {"ok": True}

    @app.get("/login")
    def login():
        return "login page"

    return app


def test_anonymous_redirected_and_401():
    c = TestClient(make_app())
    r = c.get("/", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/login"
    assert c.get("/api/data").status_code == 401


def test_login_page_public_and_cookie_allows():
    c = TestClient(make_app())
    assert c.get("/login").status_code == 200
    c.cookies.set(AUTH_COOKIE, "secret")
    assert c.get("/api/data").status_code == 200


def test_wrong_cookie_rejected():
    c = TestClient(make_app())
    c.cookies.set(AUTH_COOKIE, "wrong")
    assert c.get("/api/data").status_code == 401


def test_token_matches_constant_time():
    assert token_matches("a", "a") is True
    assert token_matches("a", "b") is False
