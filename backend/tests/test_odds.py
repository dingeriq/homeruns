"""Odds API integration tests — parsing, probability maths, identity, safety."""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone

import pytest

API_KEY_SENTINEL = "SECRET_ODDS_KEY_ce9881"


@pytest.fixture()
def sqlite_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{tmp_path/'odds.db'}")
    monkeypatch.setenv("ODDS_API_KEY", API_KEY_SENTINEL)
    import app.config as config_mod
    import app.database.session as session_mod

    config_mod.settings = config_mod.Settings()
    monkeypatch.setattr("app.services.odds_service.settings", config_mod.settings)
    session_mod._engine = None
    session_mod._session_factory = None
    from app.database import models  # noqa: F401

    session_mod.Base.metadata.create_all(bind=session_mod.get_engine())
    yield session_mod
    session_mod._engine = None
    session_mod._session_factory = None


# ---------------------------------------------------------------------------
# Probability conversion
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "price,expected",
    [
        (100, 0.5),
        (-100, 0.5),
        (150, 0.4),
        (-150, 0.6),
        (300, 0.25),
        (-400, 0.8),
    ],
)
def test_american_to_implied_probability(price, expected):
    from app.services.odds_service import american_to_implied_probability

    assert american_to_implied_probability(price) == pytest.approx(expected, abs=1e-6)


@pytest.mark.parametrize("bad", [None, 0, 50, -50, "abc", ""])
def test_american_conversion_rejects_nonsense(bad):
    from app.services.odds_service import american_to_implied_probability

    assert american_to_implied_probability(bad) is None


def test_decimal_and_devig():
    from app.services.odds_service import decimal_to_implied_probability, devig_two_way

    assert decimal_to_implied_probability(2.5) == pytest.approx(0.4)
    assert decimal_to_implied_probability(0.9) is None
    # +300 / -400 -> 0.25 and 0.8, no-vig yes = 0.25/1.05
    assert devig_two_way(0.25, 0.8) == pytest.approx(0.238095, abs=1e-5)
    assert devig_two_way(0.25, None) is None


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _event_payload(event_id="evt1", with_props=True, with_game=True):
    books = []
    if with_game:
        books.append(
            {
                "key": "fanduel",
                "title": "FanDuel",
                "last_update": "2026-08-11T12:00:00Z",
                "markets": [
                    {
                        "key": "h2h",
                        "last_update": "2026-08-11T12:00:00Z",
                        "outcomes": [
                            {"name": "Detroit Tigers", "price": -130},
                            {"name": "Cleveland Guardians", "price": 110},
                        ],
                    },
                    {
                        "key": "totals",
                        "last_update": "2026-08-11T12:00:00Z",
                        "outcomes": [
                            {"name": "Over", "price": -105, "point": 8.5},
                            {"name": "Under", "price": -115, "point": 8.5},
                        ],
                    },
                ],
            }
        )
    if with_props:
        for book, price in (("fanduel", 320), ("draftkings", 360)):
            books.append(
                {
                    "key": book,
                    "title": book.title(),
                    "last_update": "2026-08-11T13:00:00Z",
                    "markets": [
                        {
                            "key": "batter_home_runs",
                            "last_update": "2026-08-11T13:00:00Z",
                            "outcomes": [
                                {
                                    "name": "Over",
                                    "description": "Riley Greene",
                                    "price": price,
                                    "point": 0.5,
                                },
                                {
                                    "name": "Under",
                                    "description": "Riley Greene",
                                    "price": -450,
                                    "point": 0.5,
                                },
                                {
                                    "name": "Over",
                                    "description": "Unknown Book Guy",
                                    "price": 500,
                                    "point": 0.5,
                                },
                            ],
                        }
                    ],
                }
            )
    return {
        "id": event_id,
        "sport_key": "baseball_mlb",
        "commence_time": "2026-08-11T22:41:00Z",
        "home_team": "Detroit Tigers",
        "away_team": "Cleveland Guardians",
        "bookmakers": books,
    }


def test_parse_event_flattens_markets_and_stores_both_price_and_probability():
    from app.services.odds_service import parse_event_payload

    rows = parse_event_payload(_event_payload())
    markets = {r["market"] for r in rows}
    assert markets == {"h2h", "totals", "batter_home_runs"}
    assert {r["bookmaker"] for r in rows} == {"fanduel", "draftkings"}

    hr = next(
        r
        for r in rows
        if r["market"] == "batter_home_runs"
        and r["bookmaker"] == "draftkings"
        and r["outcome_name"] == "Over"
        and r["player_name"] == "Riley Greene"
    )
    assert hr["price"] == 360  # raw price preserved
    assert hr["implied_probability"] == pytest.approx(0.217391, abs=1e-5)
    assert hr["odds_format"] == "american"
    assert hr["outcome_point"] == 0.5
    assert hr["captured_at"] is not None


def test_parse_handles_missing_markets_and_empty_bookmakers():
    from app.services.odds_service import parse_event_payload

    assert parse_event_payload({"id": "e", "bookmakers": []}) == []
    assert parse_event_payload({"bookmakers": [{"key": "x", "markets": []}]}) == []
    # A market present with no outcomes yields nothing, never a placeholder row.
    payload = {"id": "e", "bookmakers": [{"key": "x", "markets": [{"key": "h2h"}]}]}
    assert parse_event_payload(payload) == []


def test_snapshot_uid_is_stable_for_identical_quotes_and_changes_with_price():
    from app.services.odds_service import parse_event_payload

    a = parse_event_payload(_event_payload())
    b = parse_event_payload(_event_payload())
    assert {r["snapshot_uid"] for r in a} == {r["snapshot_uid"] for r in b}

    moved = _event_payload()
    moved["bookmakers"][1]["markets"][0]["outcomes"][0]["price"] = 330
    c = parse_event_payload(moved)
    assert {r["snapshot_uid"] for r in c} != {r["snapshot_uid"] for r in a}


# ---------------------------------------------------------------------------
# Sync (fake client)
# ---------------------------------------------------------------------------

class FakeOddsClient:
    def __init__(self, game_payloads=None, event_payloads=None, fail=None):
        self._game = game_payloads if game_payloads is not None else []
        self._event = event_payloads or {}
        self._fail = fail
        self.quota = {"remaining": 480, "used": 20, "last_cost": 1}
        self.calls = []

    async def game_odds(self, markets):
        self.calls.append("game_odds")
        if self._fail == "game":
            from app.services.odds_service import OddsApiError

            raise OddsApiError(500, "upstream boom")
        return self._game

    async def events(self):
        self.calls.append("events")
        return [{"id": p["id"]} for p in self._game] or [{"id": k} for k in self._event]

    async def event_odds(self, event_id, markets):
        self.calls.append(f"event_odds:{event_id}")
        if self._fail == "event":
            from app.services.odds_service import OddsApiError

            raise OddsApiError(None, "ReadTimeout: timed out")
        return self._event.get(event_id, {})


def _seed_baseball(session_mod):
    from app.database import models

    with session_mod.session_scope() as s:
        s.add(models.Team(id=116, abbreviation="DET", name="Detroit Tigers", league="AL", division="ALC"))
        s.add(models.Team(id=114, abbreviation="CLE", name="Cleveland Guardians", league="AL", division="ALC"))
        s.add(models.Player(id=682985, full_name="Riley Greene", team_abbreviation="DET", bats="L"))
        s.add(
            models.Game(
                game_id=778001,
                game_date=date(2026, 8, 11),
                game_datetime=datetime(2026, 8, 11, 22, 41, tzinfo=timezone.utc),
                home_team="DET",
                away_team="CLE",
                venue="Comerica Park",
                status="Scheduled",
            )
        )


def test_sync_stores_snapshots_matches_players_and_event(sqlite_db):
    from app.database import models
    from app.services.odds_service import sync_odds

    _seed_baseball(sqlite_db)
    client = FakeOddsClient(
        game_payloads=[_event_payload(with_props=False)],
        event_payloads={"evt1": _event_payload(with_game=False)},
    )
    result = asyncio.run(sync_odds(on=date(2026, 8, 11), client=client))

    assert result["events_retrieved"] == 1
    assert result["bookmaker_count"] == 2
    assert set(result["markets"]) == {"h2h", "totals", "batter_home_runs"}
    assert result["snapshots_stored"] == result["outcomes_parsed"] > 0
    assert result["hr_players_matched"] == 1
    assert result["hr_players_unresolved"] == ["Unknown Book Guy"]
    assert result["errors"] == []
    assert result["quota_remaining"] == 480

    with sqlia := sqlite_db.session_scope() as s:
        event = s.get(models.OddsEvent, "evt1")
        assert event.game_id == 778001
        assert event.match_method == "team_names+date"
        matched = (
            s.query(models.OddsSnapshot)
            .filter(models.OddsSnapshot.player_id == 682985)
            .all()
        )
        assert matched and all(r.game_id == 778001 for r in matched)
        assert all(r.player_match_method == "exact_name" for r in matched)
        # Unmatched props are still stored, flagged, never assigned an id.
        unresolved = (
            s.query(models.OddsSnapshot)
            .filter(models.OddsSnapshot.player_name == "Unknown Book Guy")
            .all()
        )
        assert unresolved and all(r.player_id is None for r in unresolved)
        # Safe mapping layer records the gap without touching canonical players.
        gaps = s.query(models.OddsPlayerMap).filter(
            models.OddsPlayerMap.player_id.is_(None)
        ).all()
        assert [g.source_name for g in gaps] == ["Unknown Book Guy"]
        assert s.query(models.Player).count() == 1


def test_duplicate_sync_does_not_duplicate_snapshots_but_movement_appends(sqlite_db):
    from app.database import models
    from app.services.odds_service import sync_odds

    _seed_baseball(sqlite_db)
    payloads = {"evt1": _event_payload(with_game=False)}
    first = asyncio.run(
        sync_odds(on=date(2026, 8, 11), client=FakeOddsClient(event_payloads=payloads))
    )
    second = asyncio.run(
        sync_odds(on=date(2026, 8, 11), client=FakeOddsClient(event_payloads=payloads))
    )
    assert second["snapshots_stored"] == 0
    assert second["duplicates_skipped"] == second["outcomes_parsed"]

    with sqlite_db.session_scope() as s:
        after_two = s.query(models.OddsSnapshot).count()
    assert after_two == first["snapshots_stored"]

    moved = _event_payload(with_game=False)
    moved["bookmakers"][0]["markets"][0]["outcomes"][0]["price"] = 275
    moved["bookmakers"][0]["markets"][0]["last_update"] = "2026-08-11T15:00:00Z"
    third = asyncio.run(
        sync_odds(on=date(2026, 8, 11), client=FakeOddsClient(event_payloads={"evt1": moved}))
    )
    assert third["snapshots_stored"] > 0
    with sqlite_db.session_scope() as s:
        assert s.query(models.OddsSnapshot).count() > after_two


def test_sync_survives_api_failure_and_reports_it_safely(sqlite_db):
    from app.services.odds_service import sync_odds

    _seed_baseball(sqlite_db)
    result = asyncio.run(
        sync_odds(
            on=date(2026, 8, 11),
            client=FakeOddsClient(event_payloads={"evt1": {}}, fail="game"),
        )
    )
    assert result["errors"]
    assert result["snapshots_stored"] == 0
    assert API_KEY_SENTINEL not in str(result)


def test_event_timeout_is_isolated_per_event(sqlite_db):
    from app.services.odds_service import sync_odds

    _seed_baseball(sqlite_db)
    result = asyncio.run(
        sync_odds(
            on=date(2026, 8, 11),
            client=FakeOddsClient(
                game_payloads=[_event_payload(with_props=False)], fail="event"
            ),
        )
    )
    # Game markets still landed even though the props call timed out.
    assert result["snapshots_stored"] > 0
    assert any("ReadTimeout" in e for e in result["errors"])


def test_ambiguous_name_is_not_guessed(sqlite_db):
    from app.database import models
    from app.services.odds_service import resolve_player, _player_index

    with sqlite_db.session_scope() as s:
        s.add(models.Player(id=1, full_name="Will Smith", team_abbreviation="LAD"))
        s.add(models.Player(id=2, full_name="Will Smith", team_abbreviation="KC"))
    with sqlite_db.session_scope() as s:
        index = _player_index(s)
    assert resolve_player(index, "Will Smith") == (None, "ambiguous_name")
    assert resolve_player(index, "Will Smith", {"LAD", "SF"}) == (1, "exact_name_team")
    assert resolve_player(index, "Nobody Here") == (None, "unresolved")


def test_accented_and_suffixed_names_normalize(sqlite_db):
    from app.database import models
    from app.services.odds_service import _player_index, resolve_player

    with sqlite_db.session_scope() as s:
        s.add(models.Player(id=9, full_name="Eury Pérez", team_abbreviation="MIA"))
        s.add(models.Player(id=10, full_name="Ronald Acuña Jr.", team_abbreviation="ATL"))
    with sqlite_db.session_scope() as s:
        index = _player_index(s)
    assert resolve_player(index, "Eury Perez")[0] == 9
    assert resolve_player(index, "Ronald Acuna Jr")[0] == 10


def test_manual_mapping_override_wins(sqlite_db):
    from app.database import models
    from app.services.odds_service import normalize_name, sync_odds

    _seed_baseball(sqlite_db)
    with sqlite_db.session_scope() as s:
        s.add(
            models.OddsPlayerMap(
                normalized_name=normalize_name("Unknown Book Guy"),
                source_name="Unknown Book Guy",
                player_id=682985,
                match_method="manual",
                is_manual=True,
            )
        )
    asyncio.run(
        sync_odds(
            on=date(2026, 8, 11),
            client=FakeOddsClient(event_payloads={"evt1": _event_payload(with_game=False)}),
        )
    )
    with sqlite_db.session_scope() as s:
        row = (
            s.query(models.OddsSnapshot)
            .filter(models.OddsSnapshot.player_name == "Unknown Book Guy")
            .first()
        )
        assert row.player_id == 682985 and row.player_match_method == "manual"


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

def test_odds_for_date_and_best_market_across_books(sqlite_db):
    from app.services.odds_service import odds_for_date, odds_for_player, sync_odds

    _seed_baseball(sqlite_db)
    asyncio.run(
        sync_odds(
            on=date(2026, 8, 11),
            client=FakeOddsClient(
                game_payloads=[_event_payload(with_props=False)],
                event_payloads={"evt1": _event_payload(with_game=False)},
            ),
        )
    )
    payload = odds_for_date(date(2026, 8, 11))
    assert payload["status"] == "ok"
    assert payload["events"] == 1
    assert sorted(payload["bookmakers"]) == ["draftkings", "fanduel"]
    assert payload["hr_players_matched"] == 1

    player = odds_for_player(682985)
    best = player["best_hr_market"]
    assert best["price"] == 360 and best["bookmaker"] == "draftkings"
    assert best["sportsbook_count"] == 2
    assert best["implied_probability"] == pytest.approx(0.217391, abs=1e-5)
    assert best["no_vig_probability"] is not None
    assert best["last_updated"] is not None


def test_odds_for_date_empty_is_unavailable_not_fabricated(sqlite_db):
    from app.services.odds_service import odds_for_date

    payload = odds_for_date(date(2026, 8, 11))
    assert payload["status"] == "unavailable" and payload["odds"] == []


def test_odds_today_endpoint(sqlite_db):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.services.odds_service import sync_odds

    _seed_baseball(sqlite_db)
    asyncio.run(
        sync_odds(
            on=date(2026, 8, 11),
            client=FakeOddsClient(event_payloads={"evt1": _event_payload(with_game=False)}),
        )
    )
    with TestClient(app) as client:
        resp = client.get("/odds/today", params={"on": "2026-08-11"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok" and body["count"] > 0
        assert "batter_home_runs" in body["markets"]

        filtered = client.get(
            "/odds/today", params={"on": "2026-08-11", "market": "batter_home_runs"}
        ).json()
        assert {r["market"] for r in filtered["odds"]} == {"batter_home_runs"}

        player = client.get("/odds/player/682985").json()
        assert player["status"] == "ok"
        assert player["best_hr_market"]["price"] == 360

        empty = client.get("/odds/player/999999").json()
        assert empty["status"] == "unavailable" and empty["best_hr_market"] is None


# ---------------------------------------------------------------------------
# Prediction detail + credential safety
# ---------------------------------------------------------------------------

def test_prediction_detail_market_section_without_fabricating_probability(sqlite_db):
    from app.services.odds_service import sync_odds
    from app.services.prediction_detail import build_prediction_detail

    _seed_baseball(sqlite_db)
    asyncio.run(
        sync_odds(
            on=date(2026, 8, 11),
            client=FakeOddsClient(event_payloads={"evt1": _event_payload(with_game=False)}),
        )
    )
    with sqlite_db.session_scope() as s:
        detail = build_prediction_detail(s, 682985)
    market = detail.market_odds
    assert market.status == "available"
    assert market.best_price == 360 and market.sportsbook == "draftkings"
    assert market.implied_probability == pytest.approx(0.217391, abs=1e-5)
    assert market.sportsbook_count == 2
    assert market.last_updated is not None
    # Market data must never become the model output.
    assert market.model_vs_market_edge is None
    assert detail.prediction.status == "unavailable"
    assert detail.prediction.hr_probability is None
    assert "market_odds" in detail.data_availability.available


def test_prediction_detail_reports_missing_market(sqlite_db):
    from app.database import models
    from app.services.prediction_detail import build_prediction_detail

    _seed_baseball(sqlite_db)
    with sqlite_db.session_scope() as s:
        detail = build_prediction_detail(s, 682985)
    assert detail.market_odds.status == "unavailable"
    assert detail.market_odds.implied_probability is None
    assert "market_odds" in detail.data_availability.unavailable
    assert detail.prediction.hr_probability is None


def test_api_key_never_leaks_into_errors_logs_or_responses(sqlite_db, caplog):
    import logging

    from fastapi.testclient import TestClient
    from app.main import app
    from app.services.odds_service import OddsApiError, _sanitize

    caplog.set_level(logging.DEBUG)
    err = OddsApiError(
        401,
        f"https://api.the-odds-api.com/v4/sports?apiKey={API_KEY_SENTINEL} unauthorized",
    )
    assert API_KEY_SENTINEL not in str(err)
    assert "apiKey=***" in str(err)
    assert API_KEY_SENTINEL not in _sanitize(f"boom {API_KEY_SENTINEL}")

    with TestClient(app) as client:
        for path in ("/odds/today", "/ready", "/metrics", "/metrics/summary", "/health"):
            resp = client.get(path)
            assert API_KEY_SENTINEL not in resp.text, path
            assert "ODDS_API_KEY" not in resp.text or "ce9881" not in resp.text
    assert API_KEY_SENTINEL not in caplog.text


def test_odds_metrics_recorded_without_key(sqlite_db):
    from app import monitoring
    from app.services.odds_service import sync_odds

    _seed_baseball(sqlite_db)
    asyncio.run(
        sync_odds(
            on=date(2026, 8, 11),
            client=FakeOddsClient(event_payloads={"evt1": _event_payload(with_game=False)}),
        )
    )
    summary = monitoring.summary()["odds_api"]
    assert summary["last_sync"]["counts"]["events"] == 1
    assert summary["last_success_at"] is not None
    body = monitoring.render_prometheus()[0].decode()
    assert "dingeriq_odds_last_import_count" in body
    assert API_KEY_SENTINEL not in body


def test_client_requires_key(monkeypatch):
    import app.config as config_mod
    from app.services import odds_service

    monkeypatch.delenv("ODDS_API_KEY", raising=False)
    monkeypatch.setattr(odds_service, "settings", config_mod.Settings())
    with pytest.raises(odds_service.OddsApiNotConfigured):
        odds_service.OddsApiClient()
