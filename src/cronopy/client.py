from __future__ import annotations

import datetime as dt
import logging
import re
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from cronopy.enums import DiaryGroup, Source
from cronopy.models import (
    METRIC_BODY_FAT,
    METRIC_WEIGHT,
    UNIT_KG,
    UNIT_PERCENT,
    Activity,
    BiometricEntry,
    BiometricPoint,
    CalorieSummary,
    DayDiary,
    DiaryEntry,
    ExerciseEntry,
    FoodInfo,
    Metric,
    Session,
    WeightGoal,
)
from cronopy.util import format_day, parse_day, parse_time, totp_code

log = logging.getLogger("cronopy.client")

BASE_URL = "https://mobile.cronometer.com"
USER_AGENT = "Dart/3.9 (dart:io)"

APP_BUILD = "2807"
APP_VERSION = "4.48.2"

_APP_AUTH = {"api": 3, "os": "Android", "build": APP_BUILD, "flavour": "free"}


class CronometerError(Exception):
    """Base error for the client."""


class LoginError(CronometerError):
    """Raised when authentication fails."""


class NotAuthenticatedError(CronometerError):
    """Raised when an action requires a session but none is available or it expired."""


class CronometerClient:
    """Client for the Cronometer mobile API.

    Authenticate either with a previously saved ``session`` or with
    ``email``/``password`` (plus ``totp_secret`` for accounts with 2FA). With
    credentials, login happens lazily on the first call that needs a session.
    An expired session raises :class:`NotAuthenticatedError`; the caller
    decides whether to ``login()`` again. Cronometer rate-limits logins, so
    reuse sessions whenever possible.
    """

    def __init__(
        self,
        session: Session | None = None,
        *,
        email: str | None = None,
        password: str | None = None,
        totp_secret: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        if (email is None) != (password is None):
            raise ValueError("email and password must be given together")
        self._email = email
        self._password = password
        self._totp_secret = totp_secret
        self.session: Session | None = session
        self._http = httpx.Client(
            base_url=BASE_URL,
            timeout=timeout,
            headers={
                "user-agent": USER_AGENT,
                "content-type": "text/plain; charset=utf-8",
                "accept-encoding": "gzip",
            },
            event_hooks={"request": [self._log_request], "response": [self._log_response]},
        )

    @staticmethod
    def _log_request(request: httpx.Request) -> None:
        if not log.isEnabledFor(logging.DEBUG):
            return
        log.debug("--> %s %s", request.method, request.url)
        if request.content:
            body = request.content.decode("utf-8", "replace")
            body = re.sub(r'("(?:password|token)":\s*")[^"]*', r"\1***", body)
            log.debug("    body: %s", body[:1000])

    @staticmethod
    def _log_response(response: httpx.Response) -> None:
        if not log.isEnabledFor(logging.DEBUG):
            return
        log.debug("<-- %s %s", response.status_code, response.url)
        if not response.is_stream_consumed:
            response.read()
        log.debug("    body: %s", response.text[:1000])

    def __enter__(self) -> CronometerClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    @property
    def is_authenticated(self) -> bool:
        return self.session is not None

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

    def login(self) -> Session:
        """Authenticate with the constructor credentials and return the new session."""
        email, password = self._email, self._password
        if email is None or password is None:
            raise LoginError("No credentials: construct the client with email and password")
        payload = {
            "email": email,
            "password": password,
            # Must stay null: a non-null timezone *overwrites* the account setting.
            "timezone": None,
            "userCode": totp_code(self._totp_secret) if self._totp_secret else None,
            "build": f"{APP_VERSION} b{APP_BUILD}-a",
            "device": "Android 14 (SDK 34), Google Pixel 6 Pro",
            "firebaseToken": "",
            "features": {
                "food_search_config": '{"newSearch": true, "newSpellcheck": true}',
                "use_gpt_autofill": "true",
            },
            "auth": {"userId": None, "token": None, **_APP_AUTH},
            "lastSeen": 0,
            "config": {"call_version": 2},
        }
        resp = self._http.post("/api/v2/login", json=payload)
        if resp.status_code >= 400:
            raise LoginError(f"Login request failed with HTTP {resp.status_code}")
        try:
            data = resp.json()
        except ValueError as exc:
            raise LoginError(f"Login returned non-JSON response: {resp.text[:200]}") from exc
        if data.get("result") != "SUCCESS" and "sessionKey" not in data:
            if data.get("error") == "TOTP_CODE_REQUIRED":
                raise LoginError(
                    "Login failed: the account has two-factor authentication enabled; "
                    "pass the base32 TOTP secret shown when 2FA was set up"
                )
            raise LoginError(f"Login failed: {data.get('error') or data}")
        timezone = data.get("timezone")
        if isinstance(timezone, str) and timezone:
            try:
                ZoneInfo(timezone)
            except (ZoneInfoNotFoundError, ValueError):
                log.warning("Cronometer reported unknown timezone %r; ignoring", timezone)
                timezone = None
        else:
            timezone = None
        self.session = Session(
            user_id=int(data["id"]), token=str(data["sessionKey"]), email=email, timezone=timezone
        )
        log.debug("authenticated user_id=%s tz=%s", self.session.user_id, timezone)
        return self.session

    def logout(self) -> None:
        """Forget the session. The mobile API has no logout endpoint; tokens expire server-side."""
        self.session = None

    def tzinfo(self) -> dt.tzinfo | None:
        """The account's timezone from the session, or ``None`` to use local time."""
        timezone = self.session.timezone if self.session else None
        if not timezone:
            return None
        try:
            return ZoneInfo(timezone)
        except (ZoneInfoNotFoundError, ValueError):
            return None

    def now(self) -> dt.datetime:
        """Current wall-clock time in the account's timezone (local time if unknown)."""
        return dt.datetime.now(self.tzinfo())

    def today(self) -> dt.date:
        return self.now().date()

    @staticmethod
    def _expired() -> NotAuthenticatedError:
        return NotAuthenticatedError("Session expired. Log in again.")

    def _post(self, endpoint: str, payload: dict[str, Any]) -> Any:
        """Send a v2 POST with the JSON auth block and return the decoded body."""
        session = self._require_session()
        body = {
            **payload,
            "auth": {"userId": session.user_id, "token": session.token, **_APP_AUTH},
        }
        body.setdefault("lastSeen", 0)
        resp = self._http.post(endpoint, json=body)
        if resp.status_code in (401, 403):
            raise self._expired()
        if resp.status_code >= 400:
            raise CronometerError(f"{endpoint} failed with HTTP {resp.status_code}")
        try:
            data = resp.json()
        except ValueError as exc:
            raise CronometerError(f"{endpoint} returned non-JSON: {resp.text[:200]}") from exc
        if isinstance(data, dict) and data.get("result") in ("FAIL", "FAILURE"):
            error = str(data.get("error") or "")
            if "auth" in error.lower() or "session" in error.lower() or "token" in error.lower():
                raise self._expired()
            raise CronometerError(f"{endpoint} failed: {error or data}")
        return data

    def _v3(
        self, method: str, path: str, *, json_body: dict[str, Any] | None = None
    ) -> httpx.Response:
        """Send a v3 REST request (header auth) under ``/api/v3/user/{id}``."""
        session = self._require_session()
        resp = self._http.request(
            method,
            f"/api/v3/user/{session.user_id}{path}",
            json=json_body,
            headers={
                "x-crono-session": session.token,
                "x-crono-app-os": "android",
                "x-crono-app-build-number": APP_BUILD,
                "x-crono-app-version": APP_VERSION,
                "content-type": "application/json; charset=utf-8",
            },
        )
        if resp.status_code in (401, 403):
            raise self._expired()
        return resp

    def search(
        self, query: str, max_results: int = 50, sources: Source = Source.ALL
    ) -> list[dict[str, Any]]:
        """Search foods, recipes and meals; returns the raw result objects.

        Each has ``id``, ``name``, ``measureId``, ``measureDisplayName``,
        ``source``, ``translationId`` and scoring fields.
        """
        data = self._post(
            "/api/v2/find_food",
            {
                "query": query,
                "tab": "ALL",
                "sources": [Source(sources).value],
                "config": {"newSearch": True, "newSpellcheck": True, "call_version": 1},
            },
        )
        foods = data.get("foods", []) if isinstance(data, dict) else []
        return foods[:max_results]

    def get_foods(self, food_ids: list[int]) -> dict[int, FoodInfo]:
        """Fetch several foods (measures and per-100 g nutrients) in one call."""
        unique = list(dict.fromkeys(food_ids))
        if not unique:
            return {}
        data = self._post("/api/v2/get_foods", {"ids": unique, "config": {"call_version": 1}})
        foods = data.get("foods", []) if isinstance(data, dict) else []
        infos = (FoodInfo.from_api(f) for f in foods if isinstance(f, dict) and "id" in f)
        return {info.id: info for info in infos}

    def get_food(self, food_id: int) -> FoodInfo:
        data = self._post("/api/v2/get_food", {"id": food_id, "config": {"call_version": 1}})
        if not isinstance(data, dict) or data.get("id") is None:
            raise CronometerError(f"Food {food_id} not found")
        return FoodInfo.from_api(data)

    def get_diary_raw(self, day: dt.date | None = None) -> dict[str, Any]:
        """Return the full ``get_diary`` response (entries plus ``summary``)."""
        day = day or self.today()
        data = self._post(
            "/api/v2/get_diary", {"day": format_day(day), "config": {"call_version": 1}}
        )
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _servings(diary: dict[str, Any]) -> list[DiaryEntry]:
        entries = [
            DiaryEntry.from_api(e)
            for e in diary.get("diary") or []
            if isinstance(e, dict) and e.get("type") == "Serving" and "servingId" in e
        ]
        return sorted(entries, key=lambda e: (e.time, e.group, e.order))

    def get_diary(self, day: dt.date | None = None) -> list[DiaryEntry]:
        """Return the food servings logged on ``day`` (default today), oldest first."""
        return self._servings(self.get_diary_raw(day))

    @staticmethod
    def _entries_of_type(diary: dict[str, Any], kind: str, id_key: str) -> list[dict[str, Any]]:
        return [
            e
            for e in diary.get("diary") or []
            if isinstance(e, dict) and e.get("type") == kind and e.get(id_key) is not None
        ]

    def get_day(self, day: dt.date | None = None) -> DayDiary:
        """Return servings, exercises, biometrics and calories for ``day`` in one request."""
        day = day or self.today()
        raw = self.get_diary_raw(day)
        summary = raw.get("summary") or {}
        try:
            calories: CalorieSummary | None = CalorieSummary.from_summary(day, summary)
        except ValueError:
            calories = None
        return DayDiary(
            day=day,
            servings=self._servings(raw),
            exercises=sorted(
                (
                    ExerciseEntry.from_api(e)
                    for e in self._entries_of_type(raw, "Exercise", "exerciseId")
                ),
                key=lambda e: e.time,
            ),
            biometrics=sorted(
                (
                    BiometricEntry.from_api(e)
                    for e in self._entries_of_type(raw, "Biometric", "biometricId")
                ),
                key=lambda e: e.time,
            ),
            calories=calories,
        )

    def get_calories(self, day: dt.date | None = None) -> CalorieSummary:
        """Return consumed, burned and target kcal for ``day`` (default today)."""
        day = day or self.today()
        summary = self.get_diary_raw(day).get("summary") or {}
        try:
            return CalorieSummary.from_summary(day, summary)
        except ValueError as exc:
            raise CronometerError(str(exc)) from exc

    def _entry_stamp(self, day: dt.date | None, time: dt.time | None) -> tuple[str, str]:
        now = self.now()
        day = day or now.date()
        time = (time or now.time()).replace(microsecond=0)
        return format_day(day), f"{time.hour}:{time.minute}:{time.second}"

    def _created_id(self, endpoint: str, data: Any, *keys: str) -> int:
        if isinstance(data, dict):
            for key in (*keys, "id"):
                if data.get(key) is not None:
                    return int(data[key])
        raise CronometerError(f"{endpoint} returned no entry id: {data!r}")

    @staticmethod
    def _deletable(entry: dict[str, Any]) -> dict[str, Any]:
        """Shape a ``get_diary`` entry for the v3 delete endpoint.

        The deserializer rejects the ``meta`` object attached to biometric and
        exercise entries, and it identifies biometrics by ``id`` rather than
        ``biometricId`` (a 204 without ``id`` deletes nothing).
        """
        body = {k: v for k, v in entry.items() if k != "meta"}
        if entry.get("type") == "Biometric" and "biometricId" in entry:
            body["id"] = entry["biometricId"]
        return body

    def _delete_entries(self, entries: list[dict[str, Any]]) -> None:
        """Delete diary entries of any type via the v3 endpoint."""
        body = {"diaryEntries": [self._deletable(e) for e in entries]}
        resp = self._v3("DELETE", "/diary-entries", json_body=body)
        if resp.status_code not in (200, 204):
            raise CronometerError(f"Delete failed with HTTP {resp.status_code}: {resp.text[:300]}")

    def remove_entry(
        self, entry_id: int, day: dt.date | None = None
    ) -> DiaryEntry | BiometricEntry | ExerciseEntry:
        """Delete the food, biometric or exercise entry ``entry_id`` on ``day`` (default today)."""
        day = day or self.today()
        diary = self.get_day(day)
        found: list[DiaryEntry | BiometricEntry | ExerciseEntry] = [
            *diary.servings,
            *diary.exercises,
            *diary.biometrics,
        ]
        match = next((e for e in found if e.id == entry_id), None)
        if match is None:
            raise CronometerError(f"No diary entry {entry_id} on {day.isoformat()}")
        self._delete_entries([match.raw])
        return match

    def add_food(
        self,
        food_id: int,
        measure_id: int,
        amount: float,
        *,
        group: DiaryGroup = DiaryGroup.UNCATEGORIZED,
        day: dt.date | None = None,
        time: dt.time | None = None,
        translation_id: int = 0,
    ) -> DiaryEntry:
        """Log ``amount`` units of ``measure_id`` of food ``food_id`` and return the entry.

        ``UNCATEGORIZED`` picks the meal group from the time of day, as the
        app does. The measure's weight is looked up to convert ``amount`` to
        grams.
        """
        session = self._require_session()
        food = self.get_food(food_id)
        measure = food.measure(measure_id)
        if measure is None:
            raise CronometerError(
                f"Food {food_id} has no measure {measure_id}; "
                f"available: {', '.join(f'{m.id} ({m.name})' for m in food.measures)}"
            )
        now = self.now()
        time = (time or now.time()).replace(microsecond=0)
        if group == DiaryGroup.UNCATEGORIZED:
            group = DiaryGroup.for_hour(time.hour)
        day_str, time_str = self._entry_stamp(day, time)
        serving = {
            "order": (int(group) << 16) | 1,
            "day": day_str,
            "time": time_str,
            "offset": None,
            "source": None,
            "userId": session.user_id,
            "servingId": None,
            "type": "Serving",
            "foodId": food_id,
            "measureId": measure_id,
            "grams": measure.grams * amount,
            "translationId": translation_id,
        }
        data = self._post(
            "/api/v2/add_serving", {"serving": serving, "config": {"call_version": 2}}
        )
        if not isinstance(data, dict):
            raise CronometerError(f"add_serving returned unexpected response: {data!r}")
        created = data.get("serving") if isinstance(data.get("serving"), dict) else data
        if created.get("servingId") is None and created.get("id") is not None:
            created = {**created, "servingId": created["id"]}
        if created.get("servingId") is None:
            raise CronometerError(f"add_serving returned no entry id: {data!r}")
        return DiaryEntry.from_api({**serving, **created})

    def remove_food(self, entry_id: int, day: dt.date | None = None) -> DiaryEntry:
        """Delete the food serving ``entry_id`` logged on ``day`` (default today)."""
        day = day or self.today()
        entries = [e for e in self.get_diary(day) if e.id == entry_id]
        if not entries:
            raise CronometerError(f"No diary entry {entry_id} on {day.isoformat()}")
        self._delete_entries([entries[0].raw])
        return entries[0]

    def get_metrics(self) -> list[Metric]:
        """Return the catalog of trackable biometrics and their units."""
        data = self._post("/api/v2/get_metrics", {"config": {"call_version": 1}})
        metrics = data.get("metrics", []) if isinstance(data, dict) else []
        return [Metric.from_api(m) for m in metrics if isinstance(m, dict) and "id" in m]

    def get_biometrics(
        self,
        metric_id: int,
        unit_id: int,
        start: dt.date | None = None,
        end: dt.date | None = None,
    ) -> list[BiometricPoint]:
        """Return the ``metric_id`` series in ``unit_id`` between ``start`` and ``end``.

        ``end`` defaults to today and ``start`` to 30 days before ``end``.
        """
        end = end or self.today()
        start = start or end - dt.timedelta(days=30)
        data = self._post(
            "/api/v2/get_biometrics",
            {
                "metricId": metric_id,
                "unitId": unit_id,
                "start": format_day(start),
                "end": format_day(end),
                "config": {"call_version": 1},
            },
        )
        points = data.get("data", []) if isinstance(data, dict) else []
        return [
            BiometricPoint(
                day=parse_day(p["day"]),
                value=float(p["value"]),
                time=parse_time(p["time"]) if p.get("time") else None,
            )
            for p in points
            if isinstance(p, dict) and "day" in p and "value" in p
        ]

    def add_biometric(
        self,
        metric_id: int,
        unit_id: int,
        amount: float,
        *,
        day: dt.date | None = None,
        time: dt.time | None = None,
    ) -> BiometricEntry:
        """Log a biometric reading (``POST /api/v2/add_biometric``) and return the entry."""
        session = self._require_session()
        day_str, time_str = self._entry_stamp(day, time)
        biometric = {
            "type": "Biometric",
            "metricId": metric_id,
            "unitId": unit_id,
            "amount": amount,
            "day": day_str,
            "time": time_str,
            "order": 1,
            "userId": session.user_id,
            "biometricId": None,
            "source": None,
            "externalId": None,
            "offset": None,
            "samplesVersion": 0,
            "meta": {},
        }
        data = self._post(
            "/api/v2/add_biometric", {"biometric": biometric, "config": {"call_version": 1}}
        )
        entry_id = self._created_id("add_biometric", data, "biometricId")
        return BiometricEntry.from_api({**biometric, "biometricId": entry_id})

    def add_weight(
        self, kg: float, *, day: dt.date | None = None, time: dt.time | None = None
    ) -> BiometricEntry:
        """Log body weight in kilograms."""
        return self.add_biometric(METRIC_WEIGHT, UNIT_KG, kg, day=day, time=time)

    def add_body_fat(
        self, percent: float, *, day: dt.date | None = None, time: dt.time | None = None
    ) -> BiometricEntry:
        """Log body fat percentage."""
        return self.add_biometric(METRIC_BODY_FAT, UNIT_PERCENT, percent, day=day, time=time)

    def find_activity(self, query: str) -> list[Activity]:
        """Search Cronometer's exercise activity catalog."""
        data = self._post("/api/v2/find_activity", {"query": query, "config": {"call_version": 1}})
        activities = data.get("activities", []) if isinstance(data, dict) else []
        return [Activity.from_api(a) for a in activities if isinstance(a, dict) and "id" in a]

    def add_exercise(
        self,
        name: str,
        minutes: float,
        kcal_burned: float,
        *,
        activity_id: int = 0,
        day: dt.date | None = None,
        time: dt.time | None = None,
    ) -> ExerciseEntry:
        """Log an exercise (``POST /api/v2/add_exercise``) burning ``kcal_burned`` kcal.

        The calorie value is sent as an override, so Cronometer uses it as-is.
        """
        session = self._require_session()
        day_str, time_str = self._entry_stamp(day, time)
        exercise = {
            "type": "Exercise",
            "name": name,
            "minutes": minutes,
            "calories": -abs(kcal_burned),
            "calorieOverride": True,
            "activityId": activity_id,
            "activitySpecId": 0,
            "weight": 0,
            "exerciseId": None,
            "day": day_str,
            "time": time_str,
            "order": 1,
            "userId": session.user_id,
            "source": None,
            "externalId": None,
        }
        data = self._post(
            "/api/v2/add_exercise", {"exercise": exercise, "config": {"call_version": 1}}
        )
        entry_id = self._created_id("add_exercise", data, "exerciseId")
        return ExerciseEntry.from_api({**exercise, "exerciseId": entry_id})

    def get_profile(self) -> dict[str, Any]:
        """Return the raw ``get_profile`` response (weight, height, prefs, history...)."""
        data = self._post("/api/v2/get_profile", {"config": {"call_version": 1}})
        return data if isinstance(data, dict) else {}

    def get_preferences(self) -> dict[str, Any]:
        """Return profile ``prefs`` flattened into one dict."""
        prefs: dict[str, Any] = {}
        for item in self.get_profile().get("prefs") or []:
            if isinstance(item, dict):
                prefs.update(item)
        return prefs

    def get_weight_goal(self) -> WeightGoal:
        """Return the weight goal: weekly rate, target weight and latest logged weight."""
        profile = self.get_profile()
        prefs: dict[str, Any] = {}
        for item in profile.get("prefs") or []:
            if isinstance(item, dict):
                prefs.update(item)
        rate = prefs.get("weightGoal")
        target = prefs.get("wgkg")
        weight = profile.get("weight")
        weight_date = profile.get("weightDate")
        return WeightGoal(
            rate_lb_per_week=float(rate) if rate not in (None, "") else 0.0,
            target_kg=float(target) if target not in (None, "") else None,
            current_kg=float(weight) if weight is not None else None,
            weight_date=parse_day(weight_date) if weight_date else None,
        )
