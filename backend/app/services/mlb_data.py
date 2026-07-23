"""In-memory placeholder data store.

This module returns static sample data so the frontend can validate the
API contract before real ETL and prediction logic is wired in.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List

from app.models.schemas import Game, Player, Team


_TEAMS: List[Team] = [
    Team(id=147, abbreviation="NYY", name="New York Yankees", league="AL", division="East"),
    Team(id=111, abbreviation="BOS", name="Boston Red Sox", league="AL", division="East"),
    Team(id=119, abbreviation="LAD", name="Los Angeles Dodgers", league="NL", division="West"),
    Team(id=137, abbreviation="SF", name="San Francisco Giants", league="NL", division="West"),
    Team(id=112, abbreviation="CHC", name="Chicago Cubs", league="NL", division="Central"),
    Team(id=117, abbreviation="HOU", name="Houston Astros", league="AL", division="West"),
]

_PLAYERS: List[Player] = [
    Player(id=592450, full_name="Aaron Judge", team_id=147, team_abbreviation="NYY", position="RF", bats="R", throws="R"),
    Player(id=660271, full_name="Shohei Ohtani", team_id=119, team_abbreviation="LAD", position="DH", bats="L", throws="R"),
    Player(id=665742, full_name="Juan Soto", team_id=147, team_abbreviation="NYY", position="RF", bats="L", throws="L"),
    Player(id=545361, full_name="Mike Trout", team_id=108, team_abbreviation="LAA", position="CF", bats="R", throws="R"),
    Player(id=671096, full_name="Kyle Tucker", team_id=112, team_abbreviation="CHC", position="RF", bats="L", throws="R"),
]


def list_teams() -> List[Team]:
    return list(_TEAMS)


def list_players() -> List[Player]:
    return list(_PLAYERS)


def list_games_today() -> List[Game]:
    now = datetime.now(timezone.utc)
    return [
        Game(
            game_id=776001,
            game_date=now,
            home_team="NYY",
            away_team="BOS",
            venue="Yankee Stadium",
            status="Scheduled",
        ),
        Game(
            game_id=776002,
            game_date=now,
            home_team="LAD",
            away_team="SF",
            venue="Dodger Stadium",
            status="Scheduled",
        ),
        Game(
            game_id=776003,
            game_date=now,
            home_team="CHC",
            away_team="HOU",
            venue="Wrigley Field",
            status="Scheduled",
        ),
    ]
