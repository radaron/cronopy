"""Minimal unofficial client for the Cronometer web API."""

from __future__ import annotations

import datetime as dt
import logging
import re
from collections.abc import Callable
from typing import Any

import httpx

from cronopy import gwt
from cronopy.gwt import BASE_URL, Boxed, GwtObject, GwtParam, Long
from cronopy.models import CalorieSummary, DiaryEntry, DiaryGroup, FoodInfo, Measure, Source
from cronopy.session import Session

# Cronometer stores the weight goal (``weightGoal`` preference) in lb/week and
# converts it to a daily energy adjustment as its web UI does:
GRAMS_PER_LB = 453.59237
KCAL_PER_GRAM_BODY_WEIGHT = 7.7  # the usual 7700 kcal/kg rule of thumb
KCAL_PER_LB_PER_WEEK = GRAMS_PER_LB * KCAL_PER_GRAM_BODY_WEIGHT / 7  # ~499 kcal/day per lb/week
log = logging.getLogger("cronopy.client")

FOODS_PER_REQUEST = 25  # getAllFood rejects larger batches ("Too many food IDs requested")

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

    @staticmethod
    def _read_serving(reader: gwt.GwtReader) -> DiaryEntry:
        """Read one ``Serving`` whose type token has just been consumed."""
        reader.read()  # Day type
        d, m, y = reader.read(), reader.read(), reader.read()
        reader.read()  # boolean
        reader.read()  # boolean
        reader.skip_object_ref()  # nullable Short
        packed = reader.read()  # group << 16 | order
        reader.read()  # Time type
        hh, mm, ss = reader.read(), reader.read(), reader.read()
        user_id = reader.read()
        amount = float(reader.read())
        food_id = reader.read()
        entry_id = reader.read_long()
        measure_id = reader.read()
        reader.read()
        reader.read()
        return DiaryEntry(
            id=entry_id,
            day=dt.date(y, m, d),
            time=dt.time(hh, mm, ss),
            group=DiaryGroup(packed >> 16),
            order=packed & 0xFFFF,
            food_id=food_id,
            measure_id=measure_id,
            amount=amount,
            user_id=user_id,
        )

    @classmethod
    def _servings_in(cls, body: str) -> list[DiaryEntry]:
        """Extract every ``Serving`` object from a response, regardless of its container."""
        reader = cls._gwt_decode(gwt.GwtReader, body)
        serving = reader.type_index(gwt.TYPE_SERVING)
        day = reader.type_index(gwt.TYPE_DAY)
        if serving is None or day is None:
            return []
        entries = []
        while reader.pos < len(reader.tokens) - 1:
            if reader.tokens[reader.pos] == serving and reader.tokens[reader.pos + 1] == day:
                reader.read()
                entries.append(cls._read_serving(reader))
            else:
                reader.pos += 1
        return entries

    @staticmethod
    def _serving_object(entry: DiaryEntry) -> GwtObject:
        packed = (int(entry.group) << 16) | (entry.order & 0xFFFF)
        return GwtObject(
            gwt.TYPE_SERVING,
            (
                entry.day,
                True,
                True,
                None,
                packed,
                entry.time,
                entry.user_id,
                entry.amount,
                entry.food_id,
                Long(entry.id),
                entry.measure_id,
                0,
                0,
            ),
        )

    def get_diary(self, day: dt.date | None = None) -> list[DiaryEntry]:
        """Return the food servings logged on ``day`` (default today), oldest first."""
        session = self._require_session()
        day = day or dt.date.today()
        body = self._gwt_rpc("getDayInfo", self._sesnonce(session), day, session.user_id)
        return sorted(self._servings_in(body), key=lambda e: (e.time, e.order))

    def add_food(
        self,
        food_id: int,
        measure_id: int,
        amount: float,
        *,
        group: DiaryGroup = DiaryGroup.UNCATEGORIZED,
        day: dt.date | None = None,
        time: dt.time | None = None,
    ) -> DiaryEntry:
        """Log ``amount`` of ``measure_id`` of food ``food_id`` and return the created entry."""
        session = self._require_session()
        now = dt.datetime.now()
        day = day or now.date()
        siblings = [e.order for e in self.get_diary(day) if e.group == group]
        entry = DiaryEntry(
            id=0,
            day=day,
            time=(time or now.time()).replace(microsecond=0),
            group=group,
            order=max(siblings, default=0) + 1,
            food_id=food_id,
            measure_id=measure_id,
            amount=amount,
            user_id=0,
        )
        change = GwtObject(gwt.TYPE_ADD_ENTRY, (True, True, self._serving_object(entry)))
        body = self._gwt_rpc(
            "updateDiary", self._sesnonce(session), session.user_id, gwt.gwt_list([change])
        )
        created = self._servings_in(body)
        if not created:
            raise CronometerError(f"updateDiary returned no entry: {body[:200]}")
        return created[0]

    def remove_food(self, entry_id: int, day: dt.date | None = None) -> DiaryEntry:
        """Delete the diary entry ``entry_id`` logged on ``day`` (default today)."""
        session = self._require_session()
        entries = [e for e in self.get_diary(day) if e.id == entry_id]
        if not entries:
            raise CronometerError(
                f"No diary entry {entry_id} on {(day or dt.date.today()).isoformat()}"
            )
        change = GwtObject(gwt.TYPE_DELETE_ENTRY, (self._serving_object(entries[0]),))
        body = self._gwt_rpc(
            "updateDiary", self._sesnonce(session), session.user_id, gwt.gwt_list([change])
        )
        self._gwt_decode(gwt.decode_response, body)
        return entries[0]

    # -- foods ---------------------------------------------------------------

    @staticmethod
    def _parse_foods(reader: gwt.GwtReader) -> dict[int, FoodInfo]:
        """Pull name, per-100g energy and measures for each ``Food`` in a response.

        Walks the token stream looking for ``Measure`` and ``Nutrient`` objects
        (fixed layouts) instead of deserializing the whole ``Food`` graph.
        Measures carry their food id; a food's nutrient map follows its measures.
        """
        food_t = reader.type_index(gwt.TYPE_FOOD)
        measure_t = reader.type_index(gwt.TYPE_MEASURE)
        nutrient_t = reader.type_index(gwt.TYPE_NUTRIENT)
        toks = reader.tokens
        names: dict[int, str] = {}
        measures: dict[int, list[Measure]] = {}
        energy: dict[int, float] = {}
        current: int | None = None
        i = 0
        while i < len(toks) - 12:
            t = toks[i]
            if t == food_t:
                # Food: int, bool, list(type, size), int, name, ..., id. Only when the
                # list is empty do the name and id sit at fixed offsets +6 and +9.
                name, fid = toks[i + 6], toks[i + 9]
                if (
                    isinstance(name, int)
                    and isinstance(fid, int)
                    and 0 < name <= len(reader.strings)
                    and fid > 0
                ):
                    names[fid] = reader.strings[name - 1]
            elif t == measure_t and isinstance(toks[i + 1], float):
                # Measure: quantity, bool, food id, bool, id, Double|null (volume in ml),
                # name, HashMap (type, size), Measure$Type (type+ordinal or back-ref), grams
                quantity, food_id, mid = toks[i + 1], toks[i + 3], toks[i + 5]
                j = i + 6
                j += 2 if toks[j] > 0 else 1
                name_idx = toks[j]
                j += 1  # HashMap type
                if toks[j + 1] != 0:  # non-empty attribute map: layout unknown, skip measure
                    i += 1
                    continue
                j += 2
                j += 2 if toks[j] > 0 else 1
                grams = float(toks[j])
                current = food_id
                measures.setdefault(food_id, []).append(
                    Measure(mid, reader.strings[name_idx - 1], float(quantity), grams)
                )
                i = j + 1
                continue
            elif t == nutrient_t and toks[i + 2] == gwt.NUTRIENT_ENERGY and current is not None:
                energy[current] = float(toks[i + 1])
            i += 1
        return {
            fid: FoodInfo(fid, names.get(fid, ""), energy.get(fid), tuple(ms))
            for fid, ms in measures.items()
        }

    def get_foods(self, food_ids: list[int]) -> dict[int, FoodInfo]:
        """Fetch measures and energy for several foods via ``getAllFood``.

        The server rejects more than :data:`FOODS_PER_REQUEST` ids per call, so
        larger lists are split into several requests.
        """
        unique = list(dict.fromkeys(food_ids))
        if not unique:
            return {}
        session = self._require_session()
        hashes = self._gwt_hashes()
        sesnonce = self._sesnonce(session)
        foods: dict[int, FoodInfo] = {}
        for start in range(0, len(unique), FOODS_PER_REQUEST):
            chunk = unique[start : start + FOODS_PER_REQUEST]
            ids = GwtObject(gwt.TYPE_ARRAY_LIST, (len(chunk), *(Boxed(i) for i in chunk)))
            body = self._gwt_rpc("getAllFood", sesnonce, ids, hashes=hashes)
            foods.update(self._parse_foods(self._gwt_decode(gwt.GwtReader, body)))
        return foods

    def get_food(self, food_id: int) -> FoodInfo:
        foods = self.get_foods([food_id])
        if food_id not in foods:
            raise CronometerError(f"Food {food_id} not found")
        return foods[food_id]

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
