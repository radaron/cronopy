"""Minimal unofficial client for the Cronometer web API."""

from __future__ import annotations

import logging
import re
from enum import StrEnum
from typing import Any

import httpx

from cronopy.session import Session

BASE_URL = "https://cronometer.com"
GWT_MODULE_BASE = f"{BASE_URL}/cronometer/"
GWT_SERVICE = "com.cronometer.shared.rpc.CronometerService"
log = logging.getLogger("cronopy.client")

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:155.0) Gecko/20100101 Firefox/155.0"


class Source(StrEnum):
    """Food source filter accepted by the search endpoint."""

    ALL = "All"


class CronometerError(Exception):
    """Base error for the client."""


class LoginError(CronometerError):
    """Raised when authentication fails."""


class NotAuthenticatedError(CronometerError):
    """Raised when an action requires a session but none is available."""


class CronometerClient:
    """Client for the Cronometer web API.

    Authenticate either with a previously saved ``session`` or with
    ``email``/``password``. With credentials, login happens lazily on the
    first call that needs a session. An expired session raises
    :class:`NotAuthenticatedError`; the caller decides whether to ``login()``
    again.
    """

    def __init__(
        self,
        session: Session | None = None,
        *,
        email: str | None = None,
        password: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        if (email is None) != (password is None):
            raise ValueError("email and password must be given together")
        self._email = email
        self._password = password
        self._http = httpx.Client(
            base_url=BASE_URL,
            timeout=timeout,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
            event_hooks={"request": [self._log_request], "response": [self._log_response]},
        )
        self.session: Session | None = None
        if session is not None:
            self._restore(session)

    @staticmethod
    def _log_request(request: httpx.Request) -> None:
        if not log.isEnabledFor(logging.DEBUG):
            return
        log.debug("--> %s %s", request.method, request.url)
        for k, v in request.headers.items():
            if k.lower() == "cookie":
                v = f"<{len(v)} bytes>"
            log.debug("    %s: %s", k, v)
        if request.content:
            body = request.content.decode("utf-8", "replace")
            body = re.sub(r"(password=)[^&]*", r"\1***", body)
            log.debug("    body: %s", body[:1000])

    @staticmethod
    def _log_response(response: httpx.Response) -> None:
        if not log.isEnabledFor(logging.DEBUG):
            return
        log.debug(
            "<-- %s %s (%s)",
            response.status_code,
            response.url,
            response.headers.get("content-type", ""),
        )
        for k, v in response.headers.multi_items():
            if k.lower() == "set-cookie":
                log.debug("    set-cookie: %s", v.split(";", 1)[0])
        if not response.is_stream_consumed:
            response.read()
        text = response.text
        if "javascript" in response.headers.get("content-type", ""):
            log.debug("    body: <%d bytes of js>", len(text))
        else:
            log.debug("    body: %s", text[:1000])

    def __enter__(self) -> CronometerClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def _restore(self, session: Session) -> None:
        self.session = session
        log.debug("restoring session user_id=%s cookies=%s", session.user_id, list(session.cookies))
        for name, value in session.cookies.items():
            self._http.cookies.set(name, value, domain="cronometer.com", path="/")

    def _snapshot(self, email: str | None, user_id: int) -> Session:
        return Session(
            user_id=user_id,
            email=email,
            cookies={name: value for name, value in self._http.cookies.items()},
        )

    @property
    def is_authenticated(self) -> bool:
        return self.session is not None

    def refresh_session(self) -> Session | None:
        """Sync the live cookie jar back into ``self.session``.

        Returns the updated session if any cookie changed (e.g. the AWS ALB
        stickiness cookie is re-issued on every response), otherwise ``None``.
        """
        if self.session is None:
            return None
        current = {name: value for name, value in self._http.cookies.items()}
        if current == self.session.cookies:
            return None
        changed = sorted(
            k
            for k in set(current) | set(self.session.cookies)
            if current.get(k) != self.session.cookies.get(k)
        )
        log.debug("session cookies changed: %s", changed)
        self.session = Session(
            user_id=self.session.user_id, email=self.session.email, cookies=current
        )
        return self.session

    @property
    def has_credentials(self) -> bool:
        return self._email is not None and self._password is not None

    def _require_session(self) -> Session:
        if self.session is None and self.has_credentials:
            return self.login()
        if self.session is None:
            raise NotAuthenticatedError(
                "Not logged in. Pass a session or email/password, or run `crono login`."
            )
        return self.session

    def _gwt_hashes(self) -> tuple[str, str]:
        """Fetch the current GWT permutation and RPC policy hash.

        These change with every frontend deploy, so they are never persisted.
        """
        nocache = self._http.get("/cronometer/cronometer.nocache.js").text
        m = re.search(r"'([0-9A-F]{32})'", nocache)
        if not m:
            raise CronometerError("Could not find GWT permutation hash")
        permutation = m.group(1)
        cache_js = self._http.get(f"/cronometer/{permutation}.cache.js").text
        m = re.search(r"'app','([0-9A-F]{32})'", cache_js)
        if not m:
            raise CronometerError("Could not find GWT policy hash")
        log.debug("gwt permutation=%s policy_hash=%s", permutation, m.group(1))
        return permutation, m.group(1)

    def _gwt_call(self, payload: str, permutation: str) -> str:
        resp = self._http.post(
            "/cronometer/app",
            content=payload,
            headers={
                "Content-Type": "text/x-gwt-rpc; charset=UTF-8",
                "X-GWT-Module-Base": GWT_MODULE_BASE,
                "X-GWT-Permutation": permutation,
            },
        )
        return resp.text

    def login(self) -> Session:
        """Authenticate with the constructor credentials and return the new session."""
        email, password = self._email, self._password
        if email is None or password is None:
            raise LoginError("No credentials: construct the client with email and password")
        self._http.cookies.clear()
        self._http.get("/login/")

        # Double-submit cookie CSRF: the token is the name of a 32-char cookie.
        csrf_token = next(
            (name for name in self._http.cookies if len(name) == 32 and name.islower()),
            None,
        )
        log.debug("csrf token cookie: %s", csrf_token)
        if csrf_token is None:
            raise LoginError("Could not find anti-CSRF cookie on login page")

        resp = self._http.post(
            "/login",
            data={
                "anticsrf": csrf_token,
                "password": password,
                "username": email,
                "userCode": "",
            },
            headers={
                "Referer": f"{BASE_URL}/login/",
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        if resp.status_code >= 400:
            raise LoginError(f"Login request failed with HTTP {resp.status_code}")
        if "sesnonce" not in self._http.cookies:
            raise LoginError("Login failed: no session cookie returned (bad credentials?)")

        self._http.get("/")
        permutation, policy_hash = self._gwt_hashes()
        auth_rpc = (
            f"7|0|5|{GWT_MODULE_BASE}|{policy_hash}|{GWT_SERVICE}|authenticate|"
            f"java.lang.Integer/3438268394|1|2|3|4|1|5|5|120|"
        )
        body = self._gwt_call(auth_rpc, permutation)
        m = re.search(r"//OK\[(\d+),", body)
        if not m:
            raise LoginError(f"GWT authenticate failed: {body[:200]}")

        self.session = self._snapshot(email, int(m.group(1)))
        log.debug(
            "authenticated user_id=%s cookies=%s", self.session.user_id, list(self.session.cookies)
        )
        return self.session

    def logout(self) -> None:
        session = self._require_session()
        sesnonce = self._http.cookies.get("sesnonce") or session.cookies.get("sesnonce", "")
        try:
            permutation, policy_hash = self._gwt_hashes()
            payload = (
                f"7|0|6|{GWT_MODULE_BASE}|{policy_hash}|{GWT_SERVICE}|logout|"
                f"java.lang.String/2004016611|{sesnonce}|1|2|3|4|1|5|6|"
            )
            self._gwt_call(payload, permutation)
        finally:
            self._http.cookies.clear()
            self.session = None

    def search(
        self,
        query: str,
        max_results: int = 50,
        sources: Source = Source.ALL,
    ) -> Any:
        session = self._require_session()
        resp = self._http.get(
            f"/api/v3/user/{session.user_id}/food-search/string",
            params={
                "query": query,
                "maxResults": max_results,
                "sources": Source(sources).value,
                "categoryId": 0,
                "selectedTab": "ALL",
                "type": "All",
            },
        )
        if resp.status_code in (401, 403):
            raise NotAuthenticatedError("Session expired. Log in again.")
        resp.raise_for_status()
        return resp.json()
