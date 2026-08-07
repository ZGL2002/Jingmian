"""访问口令中间件：token → HttpOnly cookie，常量时间比较。"""
from __future__ import annotations
import hmac
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, RedirectResponse

AUTH_COOKIE = "jingmian_token"


def token_matches(token: str, expected: str) -> bool:
    return hmac.compare_digest(token.encode("utf-8"), expected.encode("utf-8"))


class TokenAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, token: str, login_path: str = "/login"):
        super().__init__(app)
        self.token = token
        self.login_path = login_path

    async def dispatch(self, request, call_next):
        path = request.url.path
        if path == self.login_path or path == "/api/login" or path.startswith("/static"):
            return await call_next(request)
        cookie = request.cookies.get(AUTH_COOKIE, "")
        if cookie and token_matches(cookie, self.token):
            return await call_next(request)
        if path.startswith("/api/"):
            return JSONResponse({"error": "未登录"}, status_code=401)
        return RedirectResponse(self.login_path, status_code=302)
