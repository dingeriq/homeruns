import { queryOptions } from "@tanstack/react-query";
import {
  mockBacktest,
  mockFeatureImportance,
  mockModelPerformance,
} from "./mock-data";
import { withFallback } from "./fallback";
import { logApi } from "./log";
import {
  adaptPredictions,
  getGamesToday,
  getPlayers,
  getPredictionsToday,
} from "./services";

// Response types mirror the Phase 10 FastAPI service.
export type RankingDto = {
  rank: number;
  player_id: string;
  player: string;
  team: string;
  opp: string;
  pitcher: string;
  park: string;
  p_hr: number;
  confidence: number;
  drivers: { name: string; value: number }[];
  negatives: { name: string; value: number }[];
};

export type PlayerDto = RankingDto & {
  stats: {
    barrel_14: number;
    barrel_30: number;
    hard_hit: number;
    exit_velo: number;
    fly_ball: number;
    pull: number;
    iso: number;
    hr_per_pa: number;
    x_slg: number;
    x_woba: number;
  };
  last_30: { day: number; p_hr: number; actual: number }[];
};

export type GameDto = {
  game_id: string;
  home: string;
  away: string;
  park: string;
  first_pitch: string;
  weather: { temp_f: number; humidity: number; wind_mph: number; wind_dir: string; conditions: string };
  vegas: { total: number; home_total: number; away_total: number };
  batter: RankingDto & { hand: "L" | "R"; order: number; vs_hand_woba: number; vs_hand_iso: number };
  pitcher: { name: string; hand: "L" | "R"; hr_per_9: number; barrel_pct_allowed: number; fb_pct_allowed: number; season_x_era: number };
  park_factors: { hr_factor_r: number; hr_factor_l: number };
  pitch_types: { pitch: string; batter_x_slg: number; league_x_slg: number }[];
};

export type ModelPerformanceDto = {
  models: { name: string; log_loss: number; brier: number; roc_auc: number; pr_auc: number; ece: number }[];
  calibration: { predicted: number; observed: number }[];
  rolling_auc: { week: string; auc: number; log_loss: number }[];
  headline: { roc_auc: number; pr_auc: number; log_loss: number; brier: number; ece: number; champion: string };
};

export type FeatureImportanceDto = {
  feature: string;
  group: string;
  gain: number;
  permutation: number;
  shap: number;
}[];

export type BacktestDto = {
  months: { label: string; month: string; year: number; hit_rate: number; roi: number; bets: number }[];
  summary: { total_bets: number; hit_rate: number; roi: number; sharpe: number; max_drawdown: number };
};

export type SlateSummaryDto = {
  games: number;
  hitters_scored: number;
  top_prob: number;
  top_prob_player: string;
  avg_confidence: number;
};

const emptyStats = {
  barrel_14: 0,
  barrel_30: 0,
  hard_hit: 0,
  exit_velo: 0,
  fly_ball: 0,
  pull: 0,
  iso: 0,
  hr_per_pa: 0,
  x_slg: 0,
  x_woba: 0,
};

// ---- Query option factories (live PostgreSQL-backed endpoints) ----

export const topCandidatesQuery = (limit = 25) =>
  queryOptions({
    queryKey: ["predictions", "today", { limit }],
    queryFn: () =>
      withFallback<RankingDto[]>(
        "GET /predictions/today",
        async () => {
          const [preds, games] = await Promise.all([getPredictionsToday(), getGamesToday()]);
          return adaptPredictions(preds, games).slice(0, limit);
        },
        () => [],
      ),
    staleTime: 60_000,
  });

export const slateSummaryQuery = () =>
  queryOptions({
    queryKey: ["predictions", "slate-summary"],
    queryFn: () =>
      withFallback<SlateSummaryDto>(
        "derived: slate summary (/games/today + /predictions/today)",
        async () => {
          const [games, preds] = await Promise.all([getGamesToday(), getPredictionsToday()]);
          const rows = adaptPredictions(preds, games);
          const top = rows[0];
          const avg = rows.length ? rows.reduce((a, r) => a + r.confidence, 0) / rows.length : 0;
          return {
            games: games.length,
            hitters_scored: rows.length,
            top_prob: top?.p_hr ?? 0,
            top_prob_player: top?.player ?? "—",
            avg_confidence: avg,
          };
        },
        () => ({ games: 0, hitters_scored: 0, top_prob: 0, top_prob_player: "—", avg_confidence: 0 }),
      ),
    staleTime: 60_000,
  });

export const playerQuery = (playerId: string) =>
  queryOptions({
    queryKey: ["player", playerId],
    queryFn: () =>
      withFallback<PlayerDto>(
        `GET /players (lookup ${playerId})`,
        async () => {
          const [players, preds, games] = await Promise.all([
            getPlayers(1000),
            getPredictionsToday(),
            getGamesToday(),
          ]);
          const p = players.find((x) => x.player_id === String(playerId));
          const ranked = adaptPredictions(preds, games).find((r) => r.player_id === String(playerId));
          if (!p && !ranked) throw new Error(`Player ${playerId} not found in database`);
          const base: RankingDto = ranked ?? {
            rank: 0,
            player_id: String(playerId),
            player: p?.name ?? String(playerId),
            team: p?.team ?? "",
            opp: "",
            pitcher: "",
            park: "",
            p_hr: 0,
            confidence: 0,
            drivers: [],
            negatives: [],
          };
          return { ...base, player: p?.name ?? base.player, team: p?.team ?? base.team, stats: emptyStats, last_30: [] };
        },
        () => ({
          rank: 0,
          player_id: String(playerId),
          player: String(playerId),
          team: "",
          opp: "",
          pitcher: "",
          park: "",
          p_hr: 0,
          confidence: 0,
          drivers: [],
          negatives: [],
          stats: emptyStats,
          last_30: [],
        }),
      ),
    staleTime: 60_000,
  });

export const featuredMatchupQuery = () =>
  queryOptions({
    queryKey: ["predictions", "featured-matchup"],
    queryFn: () =>
      withFallback<GameDto | null>(
        "derived: featured matchup (/games/today)",
        async () => {
          const [games, preds] = await Promise.all([getGamesToday(), getPredictionsToday()]);
          const g = games[0];
          if (!g) return null;
          const ranked = adaptPredictions(preds, games).find((r) => r.player_id) ?? null;
          const batter: GameDto["batter"] = {
            ...(ranked ?? {
              rank: 0,
              player_id: "",
              player: "—",
              team: g.home_team,
              opp: g.away_team,
              pitcher: g.away_probable_pitcher ?? "TBD",
              park: g.park,
              p_hr: 0,
              confidence: 0,
              drivers: [],
              negatives: [],
            }),
            hand: "R",
            order: 0,
            vs_hand_woba: 0,
            vs_hand_iso: 0,
          };
          return {
            game_id: g.game_id,
            home: g.home_team,
            away: g.away_team,
            park: g.park,
            first_pitch: g.first_pitch,
            weather: { temp_f: 0, humidity: 0, wind_mph: 0, wind_dir: "—", conditions: "—" },
            vegas: { total: 0, home_total: 0, away_total: 0 },
            batter,
            pitcher: {
              name: g.home_probable_pitcher ?? g.away_probable_pitcher ?? "TBD",
              hand: "R",
              hr_per_9: 0,
              barrel_pct_allowed: 0,
              fb_pct_allowed: 0,
              season_x_era: 0,
            },
            park_factors: { hr_factor_r: 0, hr_factor_l: 0 },
            pitch_types: [],
          };
        },
        () => null,
      ),
    staleTime: 60_000,
  });

// ---- Model analytics: no backend endpoint exists yet (ML phase pending). ----
// These intentionally serve static reference data and are logged as such.

export const modelPerformanceQuery = () =>
  queryOptions({
    queryKey: ["model", "performance"],
    queryFn: async () => {
      const data = mockModelPerformance();
      logApi("model performance", data.models.length, "mock", "no backend endpoint yet");
      return data;
    },
    staleTime: 5 * 60_000,
  });

export const featureImportanceQuery = () =>
  queryOptions({
    queryKey: ["model", "feature-importance"],
    queryFn: async () => {
      const data = mockFeatureImportance();
      logApi("model feature-importance", data.length, "mock", "no backend endpoint yet");
      return data;
    },
    staleTime: 10 * 60_000,
  });

export const backtestQuery = () =>
  queryOptions({
    queryKey: ["model", "backtest"],
    queryFn: async () => {
      const data = mockBacktest();
      logApi("model backtest", data.months.length, "mock", "no backend endpoint yet");
      return data;
    },
    staleTime: 10 * 60_000,
  });
