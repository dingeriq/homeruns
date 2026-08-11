"""The Odds API integration — sportsbook market data as an independent benchmark.

Hard rules:

* Odds NEVER feed the DingerIQ HR probability. They are stored and served as a
  market benchmark only; the model stays driven by baseball/Statcast/park/
  weather features.
* Snapshots are append-only. A repeated identical quote is deduped, a changed
  price writes a new row, so line movement survives for later analysis.
* The raw sportsbook price is always stored; implied probability is stored
  *alongside* it, never in place of it.
* Players are resolved to canonical MLB person ids through a separate mapping
  layer (``odds_player_map``). Canonical ``players`` rows are never modified,
  and an unresolved prop is recorded as unresolved rather than guessed.
* The API key is read from the environment and never logged, returned or
  included in any error message or metric label.
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
import unicodedata
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

import httpx
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.database import models
from app.database.session import session_scope
from app.monitoring import record_odds_request, record_odds_sync

logger = logging.getLogger("dingeriq.odds")

_TIMEOUT = httpx.Timeout(20.0, connect=8.0)
SOURCE = "the-odds-api/v4"

# Markets we care about, in priority order.
HR_MARKETS = ("batter_home_runs", "batter_first_home_run")
GAME_MARKETS = ("h2h", "totals", "team_totals")

# Outcomes that mean "hits a home run".
HR_YES_OUTCOMES = {"over", "yes"}
HR_NO_OUTCOMES = {"under", "no"}


class OddsApiNotConfigured(RuntimeError):
    """Raised when no ODDS_API_KEY is present in the environment."""


# ---------------------------------------------------------------------------
# Probability conversion
# ---------------------------------------------------------------------------

def american_to_implied_probability(price: Optional[float]) -> Optional[float]:
    """Convert American odds to raw (vig-inclusive) implied probability.

    +150 -> 0.4, -150 -> 0.6. Returns None for missing or nonsensical input;
    never raises, never guesses.
    """
    if price is None:
        return None
    try:
        value = float(price)
    except (TypeError, ValueError):
        return None
    if value == 0 or -100 < value < 100:
        return None
    if value > 0:
        prob = 100.0 / (value + 100.0)
    else:
        prob = -value / (-value + 100.0)
    return round(prob, 6)


def decimal_to_implied_probability(price: Optional[float]) -> Optional[float]:
    if price is None:
        return None
    try:
        value = float(price)
    except (TypeError, ValueError):
        return None
    if value <= 1.0:
        return None
    return round(1.0 / value, 6)


def implied_probability(price: Optional[float], odds_format: str = "american") -> Optional[float]:
    if (odds_format or "american").lower().startswith("dec"):
        return decimal_to_implied_probability(price)
    return american_to_implied_probability(price)


def devig_two_way(
    prob_yes: Optional[float], prob_no: Optional[float]
) -> Optional[float]:
    """No-vig probability for a two-way market, when both sides are priced."""
    if prob_yes is None or prob_no is None:
        return None
    total = prob_yes + prob_no
    if total <= 0:
        return None
    return round(prob_yes / total, 6)


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

def _sanitize(text: str) -> str:
    """Strip anything key-shaped out of a string before it is logged/returned."""
    key = settings.odds_api_key
    cleaned = re.sub(r"(?i)(apikey|api_key)=[^&\s\"']+", r"\1=***", text)
    if key:
        cleaned = cleaned.replace(key, "***")
    return cleaned


class OddsApiClient:
    """Thin async client. The key travels in the query string only."""

    def __init__(self, api_key: Optional[str] = None) -> None:
        self.api_key = api_key or settings.odds_api_key
        if not self.api_key:
            raise OddsApiNotConfigured(
                "ODDS_API_KEY is not set in the environment; odds ingestion is disabled."
            )
        self.base = settings.odds_api_base.rstrip("/")
        self.sport = settings.odds_sport_key
        self.quota: Dict[str, Optional[int]] = {"remaining": None, "used": None, "last_cost": None}

    async def _get(self, path: str, params: Dict[str, Any], endpoint_label: str) -> Any:
        url = f"{self.base}{path}"
        query = {**params, "apiKey": self.api_key}
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(url, params=query)
            duration = time.perf_counter() - started
            if resp.status_code >= 400:
                record_odds_request(
                    endpoint_label, duration, error=RuntimeError(f"http_{resp.status_code}")
                )
                detail = _sanitize(resp.text[:200])
                raise OddsApiError(resp.status_code, detail)
            for header, key in (
                ("x-requests-remaining", "remaining"),
                ("x-requests-used", "used"),
                ("x-requests-last", "last_cost"),
            ):
                raw = resp.headers.get(header)
                if raw is not None:
                    try:
                        self.quota[key] = int(float(raw))
                    except ValueError:
                        pass
            record_odds_request(endpoint_label, duration)
            return resp.json()
        except OddsApiError:
            raise
        except Exception as exc:
            record_odds_request(endpoint_label, time.perf_counter() - started, error=exc)
            # Never let a URL containing the key reach the logs.
            raise OddsApiError(None, f"{type(exc).__name__}: {_sanitize(str(exc))}") from None

    async def sports(self) -> Any:
        return await self._get("/sports/", {}, "sports")

    async def events(self) -> List[Dict[str, Any]]:
        data = await self._get(f"/sports/{self.sport}/events", {}, "events")
        return data if isinstance(data, list) else []

    async def game_odds(self, markets: Iterable[str]) -> List[Dict[str, Any]]:
        data = await self._get(
            f"/sports/{self.sport}/odds",
            {
                "regions": settings.odds_regions,
                "markets": ",".join(markets),
                "oddsFormat": settings.odds_format,
            },
            "odds",
        )
        return data if isinstance(data, list) else []

    async def event_odds(self, event_id: str, markets: Iterable[str]) -> Dict[str, Any]:
        data = await self._get(
            f"/sports/{self.sport}/events/{event_id}/odds",
            {
                "regions": settings.odds_regions,
                "markets": ",".join(markets),
                "oddsFormat": settings.odds_format,
            },
            "event_odds",
        )
        return data if isinstance(data, dict) else {}


class OddsApiError(RuntimeError):
    def __init__(self, status: Optional[int], detail: str) -> None:
        self.status = status
        self.detail = _sanitize(detail)
        super().__init__(f"OddsAPI error{f' {status}' if status else ''}: {self.detail}")


# ---------------------------------------------------------------------------
# Identity resolution
# ---------------------------------------------------------------------------

def normalize_name(name: Optional[str]) -> str:
    if not name:
        return ""
    text = unicodedata.normalize("NFKD", str(name))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().replace(".", " ").replace("'", "").replace("-", " ")
    text = re.sub(r"\b(jr|sr|ii|iii|iv)\b", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _player_index(session) -> Dict[str, List[Tuple[int, Optional[str]]]]:
    rows = session.execute(
        select(models.Player.id, models.Player.full_name, models.Player.team_abbreviation)
    ).all()
    index: Dict[str, List[Tuple[int, Optional[str]]]] = {}
    for pid, full_name, team in rows:
        index.setdefault(normalize_name(full_name), []).append((pid, team))
    return index


def resolve_player(
    index: Dict[str, List[Tuple[int, Optional[str]]]],
    name: Optional[str],
    teams: Optional[Iterable[str]] = None,
) -> Tuple[Optional[int], str]:
    """Resolve a sportsbook player string to an MLB person id.

    Returns ``(player_id, match_method)``. An ambiguous name is only resolved
    when one candidate plays for a team in this event; otherwise the prop is
    left unresolved rather than mis-assigned.
    """
    key = normalize_name(name)
    if not key:
        return None, "unresolved"
    candidates = index.get(key)
    if not candidates:
        return None, "unresolved"
    if len(candidates) == 1:
        return candidates[0][0], "exact_name"
    team_set = {t for t in (teams or []) if t}
    narrowed = [c for c in candidates if c[1] and c[1] in team_set]
    if len(narrowed) == 1:
        return narrowed[0][0], "exact_name_team"
    return None, "ambiguous_name"


def _team_lookup(session) -> Dict[str, str]:
    """Full team name -> abbreviation, for tying an odds event to a gamePk."""
    rows = session.execute(select(models.Team.name, models.Team.abbreviation)).all()
    return {normalize_name(name): abbr for name, abbr in rows if name and abbr}


def _match_event_to_game(
    session, teams: Dict[str, str], home: Optional[str], away: Optional[str], commence: Optional[datetime]
) -> Tuple[Optional[int], Optional[date], str]:
    home_abbr = teams.get(normalize_name(home))
    away_abbr = teams.get(normalize_name(away))
    if not (home_abbr and away_abbr and commence):
        return None, None, "unmatched"
    window_start = commence - timedelta(hours=18)
    window_end = commence + timedelta(hours=18)
    row = session.execute(
        select(models.Game.game_id, models.Game.game_date)
        .where(
            models.Game.home_team == home_abbr,
            models.Game.away_team == away_abbr,
            models.Game.game_datetime >= window_start,
            models.Game.game_datetime <= window_end,
        )
        .order_by(models.Game.game_datetime)
    ).first()
    if row is None:
        return None, commence.date(), "unmatched"
    return int(row[0]), row[1], "team_names+date"


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _snapshot_uid(parts: Iterable[Any]) -> str:
    raw = "|".join("" if p is None else str(p) for p in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:40]


def parse_event_payload(
    payload: Dict[str, Any],
    *,
    captured_at: Optional[datetime] = None,
    odds_format: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Flatten one Odds API event into snapshot rows (no DB access).

    Bookmakers or markets that are absent simply produce no rows — a missing
    market is never backfilled with a placeholder price.
    """
    captured_at = captured_at or datetime.now(timezone.utc)
    fmt = (odds_format or settings.odds_format or "american").lower()
    event_id = payload.get("id")
    if not event_id:
        return []
    commence = _parse_dt(payload.get("commence_time"))
    home = payload.get("home_team")
    away = payload.get("away_team")

    rows: List[Dict[str, Any]] = []
    for book in payload.get("bookmakers") or []:
        book_key = book.get("key")
        if not book_key:
            continue
        book_title = book.get("title")
        book_update = _parse_dt(book.get("last_update"))
        for market in book.get("markets") or []:
            market_key = market.get("key")
            if not market_key:
                continue
            market_update = _parse_dt(market.get("last_update")) or book_update
            for outcome in market.get("outcomes") or []:
                price = outcome.get("price")
                point = outcome.get("point")
                name = outcome.get("name")
                description = outcome.get("description")
                rows.append(
                    {
                        "snapshot_uid": _snapshot_uid(
                            [
                                event_id,
                                book_key,
                                market_key,
                                name,
                                description,
                                point,
                                price,
                                market_update.isoformat() if market_update else None,
                            ]
                        ),
                        "event_id": event_id,
                        "commence_time": commence,
                        "home_team": home,
                        "away_team": away,
                        "bookmaker": book_key,
                        "bookmaker_title": book_title,
                        "market": market_key,
                        "outcome_name": name,
                        "outcome_description": description,
                        "outcome_point": point,
                        "player_name": description if market_key.startswith("batter_") else None,
                        "price": price,
                        "odds_format": fmt,
                        "implied_probability": implied_probability(price, fmt),
                        "book_last_update": market_update,
                        "captured_at": captured_at,
                        "source": SOURCE,
                    }
                )
    return rows


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _store_snapshots(session, rows: List[Dict[str, Any]]) -> int:
    """Append-only insert; identical repeated quotes are skipped."""
    if not rows:
        return 0
    uids = [r["snapshot_uid"] for r in rows]
    existing = set()
    for chunk_start in range(0, len(uids), 500):
        chunk = uids[chunk_start : chunk_start + 500]
        existing.update(
            uid
            for (uid,) in session.execute(
                select(models.OddsSnapshot.snapshot_uid).where(
                    models.OddsSnapshot.snapshot_uid.in_(chunk)
                )
            ).all()
        )
    stored = 0
    seen: set[str] = set()
    for row in rows:
        uid = row["snapshot_uid"]
        if uid in existing or uid in seen:
            continue
        seen.add(uid)
        session.add(models.OddsSnapshot(**row))
        stored += 1
    try:
        session.flush()
    except IntegrityError:  # pragma: no cover - concurrent writer
        session.rollback()
        return 0
    return stored


def _record_player_map(session, entries: Dict[Tuple[str, Optional[str]], Dict[str, Any]]) -> None:
    now = datetime.now(timezone.utc)
    for (norm, team), info in entries.items():
        row = session.execute(
            select(models.OddsPlayerMap).where(
                models.OddsPlayerMap.normalized_name == norm,
                models.OddsPlayerMap.team_abbreviation == team,
            )
        ).scalars().first()
        if row is None:
            session.add(
                models.OddsPlayerMap(
                    normalized_name=norm,
                    source_name=info.get("source_name"),
                    team_abbreviation=team,
                    player_id=info.get("player_id"),
                    match_method=info.get("match_method", "unresolved"),
                    is_manual=False,
                    first_seen_at=now,
                    last_seen_at=now,
                )
            )
            continue
        row.last_seen_at = now
        if row.is_manual:
            continue  # a human pin always wins
        if info.get("player_id") is not None:
            row.player_id = info["player_id"]
            row.match_method = info.get("match_method", row.match_method)


def _manual_overrides(session) -> Dict[Tuple[str, Optional[str]], int]:
    rows = session.execute(
        select(
            models.OddsPlayerMap.normalized_name,
            models.OddsPlayerMap.team_abbreviation,
            models.OddsPlayerMap.player_id,
        ).where(models.OddsPlayerMap.is_manual.is_(True))
    ).all()
    return {(n, t): pid for n, t, pid in rows if pid is not None}


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------

async def sync_odds(
    on: Optional[date] = None,
    client: Optional[OddsApiClient] = None,
    include_player_props: bool = True,
) -> Dict[str, Any]:
    """Pull game markets plus player HR props and append snapshots.

    Request budget: one bulk call for game markets, then at most
    ``ODDS_MAX_EVENT_REQUESTS`` per-event calls for player props.
    """
    started = time.perf_counter()
    client = client or OddsApiClient()
    slate = on or date.today()
    errors: List[str] = []

    payloads: List[Dict[str, Any]] = []
    markets_seen: set[str] = set()
    books_seen: set[str] = set()

    try:
        payloads = await client.game_odds(settings.odds_game_markets)
    except OddsApiError as exc:
        errors.append(f"game_markets: {exc.detail}")
        logger.warning("Odds game-market fetch failed: %s", exc.detail)

    event_ids = [p.get("id") for p in payloads if p.get("id")]
    if not event_ids:
        try:
            event_ids = [e["id"] for e in await client.events() if e.get("id")]
        except OddsApiError as exc:
            errors.append(f"events: {exc.detail}")

    prop_payloads: List[Dict[str, Any]] = []
    requested_events = 0
    if include_player_props and settings.odds_event_markets:
        for event_id in event_ids[: max(0, settings.odds_max_event_requests)]:
            try:
                payload = await client.event_odds(event_id, settings.odds_event_markets)
                requested_events += 1
                if payload:
                    prop_payloads.append(payload)
            except OddsApiError as exc:
                errors.append(f"event {event_id[:8]}: {exc.detail}")
                logger.warning("Odds event fetch failed for %s: %s", event_id[:8], exc.detail)

    captured_at = datetime.now(timezone.utc)
    all_rows: List[Dict[str, Any]] = []
    event_meta: Dict[str, Dict[str, Any]] = {}
    for payload in [*payloads, *prop_payloads]:
        rows = parse_event_payload(payload, captured_at=captured_at)
        all_rows.extend(rows)
        if payload.get("id"):
            event_meta[payload["id"]] = payload
        for row in rows:
            markets_seen.add(row["market"])
            books_seen.add(row["bookmaker"])

    hr_rows = [r for r in all_rows if r["market"] in HR_MARKETS]
    matched_players = 0
    unresolved_names: set[str] = set()

    with session_scope() as session:
        teams = _team_lookup(session)
        index = _player_index(session)
        overrides = _manual_overrides(session)

        # Event -> gamePk mapping.
        game_by_event: Dict[str, Tuple[Optional[int], Optional[date], str]] = {}
        for event_id, payload in event_meta.items():
            commence = _parse_dt(payload.get("commence_time"))
            gid, gdate, method = _match_event_to_game(
                session, teams, payload.get("home_team"), payload.get("away_team"), commence
            )
            game_by_event[event_id] = (gid, gdate, method)
            existing = session.get(models.OddsEvent, event_id)
            values = {
                "sport_key": payload.get("sport_key") or settings.odds_sport_key,
                "commence_time": commence,
                "home_team": payload.get("home_team"),
                "away_team": payload.get("away_team"),
                "game_id": gid,
                "game_date": gdate,
                "match_method": method,
                "source": SOURCE,
                "last_seen_at": captured_at,
            }
            if existing is None:
                session.add(models.OddsEvent(event_id=event_id, **values))
            else:
                for key, value in values.items():
                    setattr(existing, key, value)

        map_entries: Dict[Tuple[str, Optional[str]], Dict[str, Any]] = {}
        for row in all_rows:
            gid, gdate, _ = game_by_event.get(row["event_id"], (None, None, "unmatched"))
            row["game_id"] = gid
            row["game_date"] = gdate
            if row["market"] not in HR_MARKETS or not row.get("player_name"):
                continue
            event_teams = {
                teams.get(normalize_name(row.get("home_team"))),
                teams.get(normalize_name(row.get("away_team"))),
            }
            norm = normalize_name(row["player_name"])
            pid = overrides.get((norm, None))
            method = "manual" if pid is not None else None
            if pid is None:
                for team in event_teams:
                    if (norm, team) in overrides:
                        pid, method = overrides[(norm, team)], "manual"
                        break
            if pid is None:
                pid, method = resolve_player(index, row["player_name"], event_teams)
            row["player_id"] = pid
            row["player_match_method"] = method
            if pid is None:
                unresolved_names.add(row["player_name"])
            map_entries[(norm, None)] = {
                "source_name": row["player_name"],
                "player_id": pid,
                "match_method": method,
            }

        matched_players = len(
            {r["player_id"] for r in hr_rows if r.get("player_id") is not None}
        )
        _record_player_map(session, map_entries)
        stored = _store_snapshots(session, all_rows)

    duration = time.perf_counter() - started
    result: Dict[str, Any] = {
        "date": slate.isoformat(),
        "events_retrieved": len(event_meta),
        "event_prop_requests": requested_events,
        "bookmakers": sorted(books_seen),
        "bookmaker_count": len(books_seen),
        "markets": sorted(markets_seen),
        "outcomes_parsed": len(all_rows),
        "snapshots_stored": stored,
        "duplicates_skipped": len(all_rows) - stored,
        "hr_prop_outcomes": len(hr_rows),
        "hr_players_matched": matched_players,
        "hr_players_unresolved": sorted(unresolved_names),
        "errors": errors,
        "duration_seconds": round(duration, 3),
        "quota_remaining": client.quota.get("remaining"),
        "quota_used": client.quota.get("used"),
        "source": SOURCE,
    }
    record_odds_sync(result, success=not errors or bool(stored))
    logger.info(
        "Odds sync: %d events, %d outcomes, %d stored, %d HR props (%d matched)",
        result["events_retrieved"],
        result["outcomes_parsed"],
        stored,
        len(hr_rows),
        matched_players,
    )
    return result


# ---------------------------------------------------------------------------
# Reads (latest snapshot per book/outcome, history preserved underneath)
# ---------------------------------------------------------------------------

def _latest_rows(session, *, on: Optional[date] = None, player_id: Optional[int] = None,
                 market: Optional[str] = None, markets: Optional[Iterable[str]] = None):
    S = models.OddsSnapshot
    stmt = select(S)
    if on is not None:
        stmt = stmt.where(S.game_date == on)
    if player_id is not None:
        stmt = stmt.where(S.player_id == player_id)
    if market:
        stmt = stmt.where(S.market == market)
    elif markets:
        stmt = stmt.where(S.market.in_(list(markets)))
    rows = session.execute(stmt.order_by(S.captured_at)).scalars().all()
    latest: Dict[Tuple[Any, ...], models.OddsSnapshot] = {}
    for row in rows:
        key = (
            row.event_id,
            row.bookmaker,
            row.market,
            row.outcome_name,
            row.outcome_description,
            row.outcome_point,
        )
        latest[key] = row  # ordered by captured_at, so the last write wins
    return list(latest.values())


def _row_dict(row: models.OddsSnapshot) -> Dict[str, Any]:
    return {
        "event_id": row.event_id,
        "game_id": row.game_id,
        "game_date": row.game_date.isoformat() if row.game_date else None,
        "commence_time": row.commence_time.isoformat() if row.commence_time else None,
        "home_team": row.home_team,
        "away_team": row.away_team,
        "bookmaker": row.bookmaker,
        "bookmaker_title": row.bookmaker_title,
        "market": row.market,
        "outcome": row.outcome_name,
        "player_id": row.player_id,
        "player_name": row.player_name,
        "player_match_method": row.player_match_method,
        "point": row.outcome_point,
        "price": row.price,
        "odds_format": row.odds_format,
        "implied_probability": row.implied_probability,
        "book_last_update": row.book_last_update.isoformat() if row.book_last_update else None,
        "captured_at": row.captured_at.isoformat() if row.captured_at else None,
        "source": row.source,
    }


def odds_for_date(on: Optional[date] = None) -> Dict[str, Any]:
    slate = on or date.today()
    with session_scope() as session:
        rows = _latest_rows(session, on=slate)
        payload = [_row_dict(r) for r in rows]
    hr = [r for r in payload if r["market"] in HR_MARKETS]
    return {
        "date": slate.isoformat(),
        "status": "ok" if payload else "unavailable",
        "reason": None
        if payload
        else "No odds snapshots stored for this slate yet. Run POST /admin/odds-sync.",
        "count": len(payload),
        "events": len({r["event_id"] for r in payload}),
        "bookmakers": sorted({r["bookmaker"] for r in payload}),
        "markets": sorted({r["market"] for r in payload}),
        "hr_prop_count": len(hr),
        "hr_players_matched": len({r["player_id"] for r in hr if r["player_id"]}),
        "odds": payload,
    }


def odds_for_player(player_id: int, on: Optional[date] = None) -> Dict[str, Any]:
    with session_scope() as session:
        rows = _latest_rows(session, on=on, player_id=player_id)
        payload = [_row_dict(r) for r in rows]
        history = session.execute(
            select(func.count()).select_from(models.OddsSnapshot).where(
                models.OddsSnapshot.player_id == player_id
            )
        ).scalar_one()
    best = best_hr_market(payload)
    return {
        "player_id": player_id,
        "status": "ok" if payload else "unavailable",
        "reason": None if payload else "No sportsbook HR market stored for this player.",
        "count": len(payload),
        "historical_snapshots": int(history or 0),
        "best_hr_market": best,
        "odds": payload,
    }


def best_hr_market(rows: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Best (longest) available 'hits a HR' price across books.

    Purely descriptive: it reports the market, it never becomes a DingerIQ
    probability.
    """
    yes_rows = [
        r
        for r in rows
        if r["market"] in HR_MARKETS
        and (r.get("outcome") or "").strip().lower() in HR_YES_OUTCOMES
        and r.get("price") is not None
    ]
    if not yes_rows:
        return None
    best = max(yes_rows, key=lambda r: float(r["price"]))
    no_by_book = {
        r["bookmaker"]: r
        for r in rows
        if r["market"] in HR_MARKETS
        and (r.get("outcome") or "").strip().lower() in HR_NO_OUTCOMES
    }
    counter = no_by_book.get(best["bookmaker"])
    probs = [r["implied_probability"] for r in yes_rows if r["implied_probability"] is not None]
    return {
        "market": best["market"],
        "bookmaker": best["bookmaker"],
        "bookmaker_title": best["bookmaker_title"],
        "price": best["price"],
        "odds_format": best["odds_format"],
        "implied_probability": best["implied_probability"],
        "no_vig_probability": devig_two_way(
            best["implied_probability"], counter["implied_probability"] if counter else None
        ),
        "under_no_price": counter["price"] if counter else None,
        "consensus_implied_probability": round(sum(probs) / len(probs), 6) if probs else None,
        "sportsbook_count": len({r["bookmaker"] for r in yes_rows}),
        "last_updated": max(
            (r["captured_at"] for r in yes_rows if r["captured_at"]), default=None
        ),
    }


def market_for_player(session, player_id: int, game_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """Latest HR market summary for the prediction-detail payload."""
    S = models.OddsSnapshot
    stmt = select(S).where(S.player_id == player_id, S.market.in_(HR_MARKETS))
    if game_id is not None:
        stmt = stmt.where(S.game_id == game_id)
    rows = session.execute(stmt.order_by(S.captured_at)).scalars().all()
    if not rows:
        return None
    latest: Dict[Tuple[Any, ...], models.OddsSnapshot] = {}
    for row in rows:
        latest[(row.bookmaker, row.market, row.outcome_name, row.outcome_point)] = row
    return best_hr_market([_row_dict(r) for r in latest.values()])
