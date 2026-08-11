"""Tests for the historical player identity backfill.

Everything runs against an in-memory SQLite database with a fake MLB client —
no network, no Postgres required.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.database import models
from app.database.session import Base
from app.services import player_backfill as pb


# ---------------------------------------------------------------- fixtures


@pytest.fixture()
def session() -> Session:
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(
        bind=engine,
        tables=[models.Team.__table__, models.Player.__table__],
    )
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE statcast_pitches (
                    pitch_uid TEXT PRIMARY KEY,
                    game_date DATE,
                    batter_id INTEGER,
                    pitcher_id INTEGER
                )
                """
            )
        )
    with Session(engine, future=True) as s:
        yield s


def add_pitch(session: Session, uid: str, game_date: str, batter: int, pitcher: int) -> None:
    session.execute(
        text(
            "INSERT INTO statcast_pitches (pitch_uid, game_date, batter_id, pitcher_id)"
            " VALUES (:u, :d, :b, :p)"
        ),
        {"u": uid, "d": game_date, "b": batter, "p": pitcher},
    )
    session.commit()


class FakeClient:
    """Stands in for MLBStatsClient.people — records the exact ids requested."""

    def __init__(self, people: Dict[int, Dict[str, Any]], fail_batches: set[int] | None = None):
        self.people_by_id = people
        self.fail_batches = fail_batches or set()
        self.calls: List[List[int]] = []

    async def people(self, person_ids) -> Dict[str, Any]:
        ids = [int(p) for p in person_ids]
        self.calls.append(ids)
        if (len(self.calls) - 1) in self.fail_batches:
            raise RuntimeError("simulated MLB API failure")
        return {"people": [self.people_by_id[i] for i in ids if i in self.people_by_id]}


ACTIVE_PERSON = {
    "id": 592450,
    "fullName": "Aaron Judge",
    "active": True,
    "currentTeam": {"id": 147, "abbreviation": "NYY"},
    "primaryPosition": {"abbreviation": "RF"},
    "batSide": {"code": "R"},
    "pitchHand": {"code": "R"},
}
RETIRED_PERSON = {
    "id": 453568,
    "fullName": "Charlie Blackmon",
    "active": False,
    "primaryPosition": {"abbreviation": "CF"},
    "batSide": {"code": "L"},
    "pitchHand": {"code": "L"},
}


# ------------------------------------------------------------- discovery


def test_discovers_missing_historical_ids(session: Session) -> None:
    session.add(models.Player(id=592450, full_name="Aaron Judge"))
    session.commit()
    add_pitch(session, "a", "2024-06-26", 592450, 700001)
    add_pitch(session, "b", "2024-06-26", 453568, 700001)

    missing = pb.discover_missing_ids(session)

    # 592450 already exists, so only the two unknown ids surface.
    assert set(missing) == {453568, 700001}
    assert missing[453568] == 2024  # season observed, not invented
    assert pb.count_statcast_person_ids(session) == 3


def test_last_seen_season_is_the_latest_observed(session: Session) -> None:
    add_pitch(session, "a", "2024-06-26", 453568, 700001)
    add_pitch(session, "b", "2026-08-09", 453568, 700001)
    assert pb.discover_missing_ids(session)[453568] == 2026


# ------------------------------------------------------------ resolution


def test_resolution_batches_by_id_only() -> None:
    ids = list(range(1, 121))
    client = FakeClient({i: {"id": i, "fullName": f"P{i}"} for i in ids})
    resolved, errors = asyncio.run(pb.resolve_people(client, ids, batch_size=50, pause_seconds=0))

    assert errors == []
    assert len(resolved) == 120
    assert [len(c) for c in client.calls] == [50, 50, 20]
    # No name ever leaves the client: requests carry ids exclusively.
    assert all(isinstance(i, int) for call in client.calls for i in call)


def test_failed_batch_is_reported_and_others_continue() -> None:
    ids = list(range(1, 101))
    client = FakeClient({i: {"id": i, "fullName": f"P{i}"} for i in ids}, fail_batches={0})
    resolved, errors = asyncio.run(pb.resolve_people(client, ids, batch_size=50, pause_seconds=0))

    assert len(resolved) == 50  # second batch still processed
    assert len(errors) == 1
    assert errors[0]["error"] == "RuntimeError"
    assert errors[0]["ids"] == 50


def test_retired_player_keeps_null_team_fields() -> None:
    row = pb.person_to_row(RETIRED_PERSON, 2024)
    assert row["team_id"] is None
    assert row["team_abbreviation"] is None
    assert row["is_active"] is False
    assert row["full_name"] == "Charlie Blackmon"
    assert row["bats"] == "L"
    assert row["last_seen_season"] == 2024


def test_no_fabricated_values_for_sparse_payload() -> None:
    row = pb.person_to_row({"id": 12345}, None)
    assert row == {
        "id": 12345,
        "full_name": None,
        "team_id": None,
        "team_abbreviation": None,
        "position": None,
        "bats": None,
        "throws": None,
        "is_active": None,
        "last_seen_season": None,
    }


def test_payload_without_id_is_skipped() -> None:
    assert pb.person_to_row({"fullName": "Nobody"}, 2024) is None


# ---------------------------------------------------------------- upsert


def test_upsert_inserts_and_is_idempotent(session: Session) -> None:
    rows = [pb.person_to_row(RETIRED_PERSON, 2024)]
    inserted, updated = pb.upsert_players(session, rows)
    session.commit()
    assert (inserted, updated) == (1, 0)

    inserted, updated = pb.upsert_players(session, rows)
    session.commit()
    assert (inserted, updated) == (0, 1)

    assert session.query(models.Player).count() == 1
    stored = session.get(models.Player, 453568)
    assert stored.full_name == "Charlie Blackmon"
    assert stored.team_id is None


def test_existing_values_are_never_overwritten_with_null(session: Session) -> None:
    session.add(
        models.Player(
            id=453568,
            full_name="Charlie Blackmon",
            team_id=115,
            team_abbreviation="COL",
            position="RF",
            bats="L",
            throws="L",
        )
    )
    session.commit()

    # Payload with no currentTeam and a different position must not null the team.
    pb.upsert_players(session, [pb.person_to_row(RETIRED_PERSON, 2024)])
    session.commit()

    stored = session.get(models.Player, 453568)
    assert stored.id == 453568  # canonical id untouched
    assert stored.team_id == 115
    assert stored.team_abbreviation == "COL"
    assert stored.position == "CF"  # non-null incoming value does update
    assert stored.last_seen_season == 2024
    assert session.query(models.Player).count() == 1


def test_upsert_fills_only_missing_columns(session: Session) -> None:
    session.add(models.Player(id=592450, full_name="Aaron Judge"))
    session.commit()

    pb.upsert_players(session, [pb.person_to_row(ACTIVE_PERSON, 2026)])
    session.commit()

    stored = session.get(models.Player, 592450)
    assert stored.team_abbreviation == "NYY"
    assert stored.is_active is True
    assert stored.bats == "R"


def test_upsert_ignores_empty_input(session: Session) -> None:
    assert pb.upsert_players(session, []) == (0, 0)


# ------------------------------------------------------------ end-to-end


def test_backfill_end_to_end_and_idempotent(session: Session, monkeypatch) -> None:
    from contextlib import contextmanager

    @contextmanager
    def fake_scope():
        yield session
        session.commit()

    monkeypatch.setattr(pb, "session_scope", fake_scope)

    session.add(models.Player(id=592450, full_name="Aaron Judge"))
    session.commit()
    add_pitch(session, "a", "2024-06-26", 592450, 453568)

    client = FakeClient({453568: RETIRED_PERSON})
    result = asyncio.run(pb.backfill_players(client=client, pause_seconds=0))

    assert result["discovered_missing_ids"] == 1
    assert result["resolved_ids"] == 1
    assert result["unresolved_ids"] == 0
    assert result["players_inserted"] == 1
    assert result["players_updated"] == 0
    assert result["already_present"] == 1
    assert result["errors"] == []
    assert result["duration_seconds"] >= 0

    # Second run: nothing left to discover, nothing changes.
    again = asyncio.run(pb.backfill_players(client=client, pause_seconds=0))
    assert again["discovered_missing_ids"] == 0
    assert again["players_inserted"] == 0
    assert session.query(models.Player).count() == 2


def test_backfill_reports_unresolved_ids(session: Session, monkeypatch) -> None:
    from contextlib import contextmanager

    @contextmanager
    def fake_scope():
        yield session
        session.commit()

    monkeypatch.setattr(pb, "session_scope", fake_scope)
    add_pitch(session, "a", "2024-06-26", 999999, 999999)

    result = asyncio.run(pb.backfill_players(client=FakeClient({}), pause_seconds=0))

    assert result["discovered_missing_ids"] == 1
    assert result["resolved_ids"] == 0
    assert result["unresolved_ids"] == 1
    assert result["unresolved_sample"] == [999999]
    assert result["players_inserted"] == 0
