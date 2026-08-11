"""Response contract for the Prediction Detail endpoint.

Every section is optional/nullable by design: the endpoint reports what the
database can currently support and explicitly lists what is missing. No
probability, weight or score is fabricated — those fields stay ``None`` until
the real DingerIQ model is plugged in.
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class DataAvailability(BaseModel):
    """Per-section availability so clients can render gaps honestly."""

    available: List[str] = Field(default_factory=list)
    unavailable: List[str] = Field(default_factory=list)
    notes: Dict[str, str] = Field(default_factory=dict)


class PlayerInfo(BaseModel):
    id: int
    full_name: str
    team_id: Optional[int] = None
    team_abbreviation: Optional[str] = None
    position: Optional[str] = None
    bats: Optional[str] = None
    throws: Optional[str] = None


class GameInfo(BaseModel):
    game_id: int
    game_date: datetime
    home_team: str
    away_team: str
    venue: Optional[str] = None
    status: Optional[str] = None
    home_away: Optional[str] = Field(None, description="'home' or 'away' for this hitter")
    opponent_team: Optional[str] = None


class PitcherInfo(BaseModel):
    id: Optional[int] = None
    full_name: Optional[str] = None
    team_abbreviation: Optional[str] = None
    throws: Optional[str] = None
    source: Optional[str] = Field(None, description="How the probable starter was resolved")


class MatchupInfo(BaseModel):
    batter_stands: Optional[str] = None
    pitcher_throws: Optional[str] = None
    platoon_split: Optional[str] = Field(None, description="e.g. 'L vs R' when both are known")
    head_to_head_pitches: Optional[int] = None
    head_to_head_home_runs: Optional[int] = None
    head_to_head_batted_balls: Optional[int] = None


class HitterMetrics(BaseModel):
    """Descriptive Statcast aggregates — no modelling, no weights."""

    window_days: Optional[int] = None
    batted_balls: Optional[int] = None
    avg_exit_velocity: Optional[float] = None
    max_exit_velocity: Optional[float] = None
    avg_launch_angle: Optional[float] = None
    barrel_rate: Optional[float] = None
    hard_hit_rate: Optional[float] = None
    avg_estimated_woba: Optional[float] = None
    home_runs: Optional[int] = None


class PitcherMetrics(BaseModel):
    window_days: Optional[int] = None
    pitches: Optional[int] = None
    batted_balls_allowed: Optional[int] = None
    avg_velocity: Optional[float] = None
    avg_spin_rate: Optional[float] = None
    avg_exit_velocity_allowed: Optional[float] = None
    barrel_rate_allowed: Optional[float] = None
    hard_hit_rate_allowed: Optional[float] = None
    home_runs_allowed: Optional[int] = None


class PitchTypeUsage(BaseModel):
    pitch_type: Optional[str] = None
    pitch_name: Optional[str] = None
    count: int
    usage_rate: Optional[float] = None
    avg_velocity: Optional[float] = None
    avg_spin_rate: Optional[float] = None


class PitchMix(BaseModel):
    window_days: Optional[int] = None
    total_pitches: Optional[int] = None
    pitches: List[PitchTypeUsage] = Field(default_factory=list)


class StatcastMetrics(BaseModel):
    """Raw Statcast coverage diagnostics for this matchup."""

    batter_pitches_tracked: Optional[int] = None
    pitcher_pitches_tracked: Optional[int] = None
    first_tracked_date: Optional[str] = None
    last_tracked_date: Optional[str] = None


class RecentPerformanceGame(BaseModel):
    game_date: str
    batted_balls: int
    home_runs: int
    max_exit_velocity: Optional[float] = None
    barrels: int = 0


class RecentPerformance(BaseModel):
    window_days: Optional[int] = None
    games: List[RecentPerformanceGame] = Field(default_factory=list)


class ParkFactors(BaseModel):
    venue: Optional[str] = None
    hr_factor: Optional[float] = None
    hr_factor_lhb: Optional[float] = None
    hr_factor_rhb: Optional[float] = None
    source: Optional[str] = None


class WeatherFactors(BaseModel):
    temperature_f: Optional[float] = None
    wind_speed_mph: Optional[float] = None
    wind_direction: Optional[str] = None
    humidity_pct: Optional[float] = None
    conditions: Optional[str] = None
    roof_status: Optional[str] = None
    source: Optional[str] = None


class ModelFeatures(BaseModel):
    """Named feature slots the model will consume. Values are null until the
    feature pipeline is materialised."""

    feature_set_version: Optional[str] = None
    computed_at: Optional[datetime] = None
    values: Dict[str, Optional[float]] = Field(default_factory=dict)
    missing: List[str] = Field(default_factory=list)


class PredictionResult(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    hr_probability: Optional[float] = None
    confidence: Optional[float] = None
    model_version: Optional[str] = None
    generated_at: Optional[datetime] = None
    status: str = Field("unavailable", description="'ok' | 'unavailable'")
    reason: Optional[str] = None


class ExplanationFactor(BaseModel):
    name: str
    value: Optional[float] = None
    contribution: Optional[float] = None
    direction: Optional[str] = None
    description: Optional[str] = None


class Explanation(BaseModel):
    status: str = Field("unavailable", description="'ok' | 'unavailable'")
    method: Optional[str] = Field(None, description="e.g. 'shap' once the model ships")
    summary: Optional[str] = None
    factors: List[ExplanationFactor] = Field(default_factory=list)


class PredictionDetail(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    player: PlayerInfo
    game: Optional[GameInfo] = None
    pitcher: Optional[PitcherInfo] = None
    matchup: Optional[MatchupInfo] = None
    hitter_metrics: Optional[HitterMetrics] = None
    pitcher_metrics: Optional[PitcherMetrics] = None
    pitch_mix: Optional[PitchMix] = None
    statcast_metrics: Optional[StatcastMetrics] = None
    recent_performance: Optional[RecentPerformance] = None
    park_factors: Optional[ParkFactors] = None
    weather: Optional[WeatherFactors] = None
    model_features: ModelFeatures
    prediction: PredictionResult
    explanation: Explanation
    data_availability: DataAvailability


__all__ = [
    "DataAvailability",
    "PlayerInfo",
    "GameInfo",
    "PitcherInfo",
    "MatchupInfo",
    "HitterMetrics",
    "PitcherMetrics",
    "PitchTypeUsage",
    "PitchMix",
    "StatcastMetrics",
    "RecentPerformance",
    "RecentPerformanceGame",
    "ParkFactors",
    "WeatherFactors",
    "ModelFeatures",
    "PredictionResult",
    "ExplanationFactor",
    "Explanation",
    "PredictionDetail",
]
