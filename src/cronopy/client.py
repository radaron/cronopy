"""Minimal unofficial client for the Cronometer web API."""

from __future__ import annotations

import datetime as dt
import logging
import re
from collections.abc import Callable
from typing import Any

import httpx

from cronopy import gwt
from cronopy.gwt import BASE_URL, Boxed, GwtParam
from cronopy.models import CalorieSummary, Source
from cronopy.session import Session

# Cronometer stores the weight goal (``weightGoal`` preference) in lb/week and
# converts it to a daily energy adjustment as its web UI does:
GRAMS_PER_LB = 453.59237
KCAL_PER_GRAM_BODY_WEIGHT = 7.7  # the usual 7700 kcal/kg rule of thumb
KCAL_PER_LB_PER_WEEK = GRAMS_PER_LB * KCAL_PER_GRAM_BODY_WEIGHT / 7  # ~499 kcal/day per lb/week
log = logging.getLogger("cronopy.client")

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:155.0) Gecko/20100101 Firefox/155.0"


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
                "X-GWT-Module-Base": gwt.MODULE_BASE,
                "X-GWT-Permutation": permutation,
            },
        )
        return resp.text

    def _gwt_rpc(
        self, method: str, *params: GwtParam, hashes: tuple[str, str] | None = None
    ) -> str:
        """Invoke ``CronometerService.<method>(*params)`` and return the raw response body.

        ``hashes`` (permutation, policy) can be passed to reuse them across
        several calls; otherwise they are fetched.
        """
        permutation, policy_hash = hashes or self._gwt_hashes()
        payload = gwt.encode_request(policy_hash, method, *params)
        return self._gwt_call(payload, permutation)

    @staticmethod
    def _gwt_decode[T](decode: Callable[[str], T], body: str) -> T:
        """Run a ``cronopy.gwt`` decoder, mapping its errors onto client errors."""
        try:
            return decode(body)
        except gwt.GwtServerError as exc:
            raise CronometerError(f"GWT call failed: {exc}") from exc
        except gwt.GwtProtocolError as exc:
            raise NotAuthenticatedError("Session expired. Log in again.") from exc

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
        body = self._gwt_rpc("authenticate", Boxed(120))
        try:
            user_id = gwt.decode_int(body)
        except gwt.GwtError as exc:
            raise LoginError(f"GWT authenticate failed: {exc}") from exc

        self.session = self._snapshot(email, user_id)
        log.debug(
            "authenticated user_id=%s cookies=%s", self.session.user_id, list(self.session.cookies)
        )
        return self.session

    def logout(self) -> None:
        session = self._require_session()
        try:
            self._gwt_rpc("logout", self._sesnonce(session))
        finally:
            self._http.cookies.clear()
            self.session = None

    def _sesnonce(self, session: Session) -> str:
        return self._http.cookies.get("sesnonce") or session.cookies.get("sesnonce", "")

    def _gwt_get_preference(self, key: str, sesnonce: str, hashes: tuple[str, str]) -> str | None:
        """Call the ``getPreference`` GWT-RPC; ``None`` when the key is unset."""
        body = self._gwt_rpc("getPreference", sesnonce, key, hashes=hashes)
        return self._gwt_decode(gwt.decode_string, body)

    def get_preference(self, key: str) -> str | None:
        """Return a raw Cronometer user preference (e.g. ``weightGoal``), or ``None``."""
        session = self._require_session()
        return self._gwt_get_preference(key, self._sesnonce(session), self._gwt_hashes())

    def get_calories(self, day: dt.date | None = None) -> CalorieSummary:
        """Return the energy balance (consumed, burned, target, remaining) for ``day``.

        Defaults to today. Uses the ``getCaloriesConsumedAndBurned`` GWT-RPC
        the web diary uses (end day exclusive server-side) plus the
        ``weightGoal`` and ``targets.custom.energy.target`` preferences.
        """
        session = self._require_session()
        day = day or dt.date.today()
        sesnonce = self._sesnonce(session)
        hashes = self._gwt_hashes()

        weight_goal = self._gwt_get_preference("weightGoal", sesnonce, hashes)
        adjustment = round(float(weight_goal) * KCAL_PER_LB_PER_WEEK) if weight_goal else 0.0
        custom = self._gwt_get_preference("targets.custom.energy.target", sesnonce, hashes)
        custom_target = float(custom) if custom else None

        body = self._gwt_rpc(
            "getCaloriesConsumedAndBurned",
            sesnonce,
            session.user_id,
            day,
            day + dt.timedelta(days=1),
            hashes=hashes,
        )
        tokens = self._gwt_decode(gwt.decode_response, body)
        # Wire order is reversed: [..values.., 12, <type>, <rows>, <type>, [strings], 0, 7].
        # An empty diary day yields zero rows.
        rows = tokens[-5]
        if rows == 0:
            return CalorieSummary(
                day=day,
                consumed=0.0,
                bmr=0.0,
                activity=0.0,
                exercise=0.0,
                tef=0.0,
                weight_goal_adjustment=adjustment,
                custom_target=custom_target,
            )
        values = tokens[:12][::-1]
        if len(values) != 12:
            raise CronometerError(f"Unexpected calories response: {tokens!r}")
        return CalorieSummary(
            day=day,
            consumed=float(values[0]),
            exercise=abs(float(values[1])),
            bmr=float(values[3]),
            tef=float(values[4]),
            activity=float(values[9]) + float(values[10]),
            weight_goal_adjustment=adjustment,
            custom_target=custom_target,
        )

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
