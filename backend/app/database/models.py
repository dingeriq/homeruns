"""ORM models for cached MLB reference data."""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database.session import Base


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # MLB team id
    abbreviation: Mapped[str] = mapped_column(String(8), index=True)
    name: Mapped[str] = mapped_column(String(128))
    league: Mapped[str] = mapped_column(String(8))
    division: Mapped[str] = mapped_column(String(32))
    venue: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)


class Player(Base):
    __tablename__ = "players"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # MLB person id
    full_name: Mapped[str] = mapped_column(String(128), index=True)
    team_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("teams.id", ondelete="SET NULL"), nullable=True, index=True
    )
    team_abbreviation: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    position: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    bats: Mapped[Optional[str]] = mapped_column(String(2), nullable=True)
    throws: Mapped[Optional[str]] = mapped_column(String(2), nullable=True)


class Game(Base):
    __tablename__ = "games"

    game_id: Mapped[int] = mapped_column(Integer, primary_key=True)  # MLB gamePk
    game_date: Mapped[date] = mapped_column(Date, index=True)
    game_datetime: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    home_team: Mapped[str] = mapped_column(String(8))
    away_team: Mapped[str] = mapped_column(String(8))
    home_team_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    away_team_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    venue: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32))
    home_probable_pitcher: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    away_probable_pitcher: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    # MLB person ids — the reliable join key to players / statcast_pitches.
    home_probable_pitcher_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    away_probable_pitcher_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    venue_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)


class StatcastPitch(Base):
    """Raw pitch-level Statcast data from Baseball Savant (no derived features)."""

    __tablename__ = "statcast_pitches"

    pitch_uid: Mapped[str] = mapped_column(String(48), primary_key=True)
    game_id: Mapped[int] = mapped_column(Integer, index=True)
    game_date: Mapped[date] = mapped_column(Date, index=True)
    at_bat_number: Mapped[int] = mapped_column(Integer)
    pitch_number: Mapped[int] = mapped_column(Integer)

    pitcher_id: Mapped[int] = mapped_column(Integer, index=True)
    batter_id: Mapped[int] = mapped_column(Integer, index=True)

    # Pitcher characteristics
    pitch_type: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    pitch_name: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    velocity: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    spin_rate: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    horizontal_break: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    vertical_break: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    release_pos_x: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    release_pos_y: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    release_pos_z: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    extension: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    plate_x: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    plate_z: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Batted-ball / contact
    exit_velocity: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    launch_angle: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    hit_distance: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    spray_angle: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    spray_angle_y: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    barrel_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    hard_hit_flag: Mapped[bool] = mapped_column(Boolean, default=False)

    # Expected metrics
    estimated_ba: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    estimated_woba: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    woba_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Context
    bb_type: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    events: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(String(48), nullable=True)
    stand: Mapped[Optional[str]] = mapped_column(String(2), nullable=True)
    p_throws: Mapped[Optional[str]] = mapped_column(String(2), nullable=True)
    home_team: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    away_team: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)

    __table_args__ = (
        Index("ix_statcast_batter_date", "batter_id", "game_date"),
        Index("ix_statcast_pitcher_date", "pitcher_id", "game_date"),
    )


class Venue(Base):
    """MLB venue with the coordinates weather lookups are keyed on."""

    __tablename__ = "venues"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # MLB venue id
    name: Mapped[str] = mapped_column(String(128), index=True)
    city: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    state: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    country: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    latitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    longitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    timezone: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    roof_type: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    # Bearing (deg) from home plate to centre field — lets wind direction be
    # resolved into an out-to-centre component.
    azimuth_angle: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    elevation_ft: Mapped[Optional[float]] = mapped_column(Float, nullable=True)


class GameWeather(Base):
    """OpenWeather observation/forecast resolved for a game's first pitch."""

    __tablename__ = "game_weather"

    game_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    venue_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    venue_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    game_datetime: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    forecast_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    forecast_offset_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    is_forecast: Mapped[bool] = mapped_column(Boolean, default=True)

    temperature_f: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    feels_like_f: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    humidity_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    pressure_hpa: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    wind_speed_mph: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    wind_gust_mph: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    wind_deg: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    wind_direction: Mapped[Optional[str]] = mapped_column(String(4), nullable=True)
    # Positive = blowing out to centre field, negative = blowing in.
    wind_out_mph: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    cloud_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    precipitation_prob: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    conditions: Mapped[Optional[str]] = mapped_column(String(48), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(String(96), nullable=True)
    roof_status: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)

    source: Mapped[Optional[str]] = mapped_column(String(48), nullable=True)
    fetched_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class ParkFactor(Base):
    """Venue HR factor for a season, overall and split by batter handedness."""

    __tablename__ = "park_factors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    venue_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    venue_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    season: Mapped[int] = mapped_column(Integer, index=True)
    # 'ALL' | 'L' | 'R' | 'U' (unknown batter hand)
    batter_hand: Mapped[str] = mapped_column(String(4), index=True)

    batted_balls: Mapped[int] = mapped_column(Integer, default=0)
    home_runs: Mapped[int] = mapped_column(Integer, default=0)
    hr_rate: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    league_hr_rate: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # Null when the sample is below threshold — never estimated.
    hr_factor: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    sample_note: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    source: Mapped[Optional[str]] = mapped_column(String(48), nullable=True, index=True)
    computed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_park_factor_key", "season", "venue_id", "batter_hand"),
    )
