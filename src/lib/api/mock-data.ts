import type {
  BacktestDto,
  FeatureImportanceDto,
  GameDto,
  ModelPerformanceDto,
  PlayerDto,
  RankingDto,
  SlateSummaryDto,
} from "./queries";

const teams = ["NYY", "LAD", "HOU", "ATL", "TOR", "BAL", "TEX", "PHI", "SD", "SEA"];
const parks = ["Yankee Stadium", "Dodger Stadium", "Minute Maid", "Truist Park", "Rogers Centre"];
const pitchers = ["G. Cole", "T. Skubal", "Z. Wheeler", "L. Webb", "S. Alcantara"];
const names = [
  "Aaron Judge", "Shohei Ohtani", "Juan Soto", "Yordan Alvarez", "Kyle Tucker",
  "Bryce Harper", "Mookie Betts", "Fernando Tatis Jr.", "Ronald Acuña Jr.", "Vlad Guerrero Jr.",
  "José Ramírez", "Matt Olson", "Rafael Devers", "Freddie Freeman", "Corey Seager",
  "Adley Rutschman", "Bobby Witt Jr.", "Elly De La Cruz", "Julio Rodríguez", "Gunnar Henderson",
  "Manny Machado", "Pete Alonso", "Marcell Ozuna", "Teoscar Hernández", "Cody Bellinger",
];

function seeded(i: number, offset = 0): number {
  const x = Math.sin(i * 9301 + offset * 49297) * 233280;
  return x - Math.floor(x);
}

function makeRanking(i: number): RankingDto {
  const p = 0.22 - i * 0.006 + seeded(i) * 0.02;
  const conf = 0.9 - i * 0.015 + seeded(i, 1) * 0.05;
  return {
    rank: i + 1,
    player_id: `p${1000 + i}`,
    player: names[i % names.length],
    team: teams[i % teams.length],
    opp: teams[(i + 3) % teams.length],
    pitcher: pitchers[i % pitchers.length],
    park: parks[i % parks.length],
    p_hr: Math.max(0.03, p),
    confidence: Math.max(0.4, Math.min(0.98, conf)),
    drivers: [
      { name: "Barrel% 14d", value: 0.05 + seeded(i, 2) * 0.04 },
      { name: "Park HR factor", value: 0.03 + seeded(i, 3) * 0.03 },
      { name: "Pitcher HR/9", value: 0.02 + seeded(i, 4) * 0.03 },
    ],
    negatives: [
      { name: "Wind in from CF", value: -0.02 - seeded(i, 5) * 0.02 },
      { name: "Cold temp", value: -0.01 - seeded(i, 6) * 0.02 },
    ],
  };
}

const mockRankings: RankingDto[] = Array.from({ length: 25 }, (_, i) => makeRanking(i));

export function mockTopCandidates(limit = 25): RankingDto[] {
  return mockRankings.slice(0, limit);
}

export function mockSlateSummary(): SlateSummaryDto {
  return {
    games: 12,
    hitters_scored: 216,
    top_prob: mockRankings[0].p_hr,
    top_prob_player: mockRankings[0].player,
    avg_confidence: 0.72,
  };
}

export function mockPlayer(playerId: string): PlayerDto {
  const idx = Math.max(
    0,
    mockRankings.findIndex((r) => r.player_id === playerId),
  );
  const base = mockRankings[idx === -1 ? 0 : idx];
  return {
    ...base,
    stats: {
      barrel_14: 0.14,
      barrel_30: 0.12,
      hard_hit: 0.48,
      exit_velo: 92.4,
      fly_ball: 0.38,
      pull: 0.44,
      iso: 0.245,
      hr_per_pa: 0.062,
      x_slg: 0.532,
      x_woba: 0.388,
    },
    last_30: Array.from({ length: 30 }, (_, d) => ({
      day: d + 1,
      p_hr: 0.08 + seeded(d, 10) * 0.12,
      actual: seeded(d, 20) > 0.9 ? 1 : 0,
    })),
  };
}

export function mockFeaturedMatchup(): GameDto {
  const b = mockRankings[0];
  return {
    game_id: "g-demo-1",
    home: b.team,
    away: b.opp,
    park: b.park,
    first_pitch: "7:05 PM ET",
    weather: { temp_f: 78, humidity: 55, wind_mph: 8, wind_dir: "out to RF", conditions: "Clear" },
    vegas: { total: 9.5, home_total: 4.9, away_total: 4.6 },
    batter: { ...b, hand: "R", order: 2, vs_hand_woba: 0.395, vs_hand_iso: 0.268 },
    pitcher: {
      name: b.pitcher,
      hand: "R",
      hr_per_9: 1.42,
      barrel_pct_allowed: 0.089,
      fb_pct_allowed: 0.36,
      season_x_era: 3.68,
    },
    park_factors: { hr_factor_r: 1.12, hr_factor_l: 1.05 },
    pitch_types: [
      { pitch: "4-seam", batter_x_slg: 0.58, league_x_slg: 0.44 },
      { pitch: "Slider", batter_x_slg: 0.41, league_x_slg: 0.36 },
      { pitch: "Curveball", batter_x_slg: 0.32, league_x_slg: 0.34 },
      { pitch: "Changeup", batter_x_slg: 0.49, league_x_slg: 0.39 },
      { pitch: "Sinker", batter_x_slg: 0.51, league_x_slg: 0.42 },
    ],
  };
}

export function mockModelPerformance(): ModelPerformanceDto {
  return {
    models: [
      { name: "XGBoost", log_loss: 0.148, brier: 0.041, roc_auc: 0.742, pr_auc: 0.198, ece: 0.014 },
      { name: "LightGBM", log_loss: 0.150, brier: 0.042, roc_auc: 0.738, pr_auc: 0.192, ece: 0.017 },
      { name: "CatBoost", log_loss: 0.151, brier: 0.042, roc_auc: 0.736, pr_auc: 0.190, ece: 0.019 },
      { name: "Random Forest", log_loss: 0.158, brier: 0.045, roc_auc: 0.712, pr_auc: 0.171, ece: 0.028 },
      { name: "Logistic Regression", log_loss: 0.162, brier: 0.047, roc_auc: 0.694, pr_auc: 0.158, ece: 0.033 },
    ],
    calibration: Array.from({ length: 10 }, (_, i) => ({
      predicted: (i + 0.5) / 10,
      observed: Math.min(1, Math.max(0, (i + 0.5) / 10 + (seeded(i, 30) - 0.5) * 0.04)),
    })),
    rolling_auc: Array.from({ length: 16 }, (_, i) => ({
      week: `W${i + 1}`,
      auc: 0.72 + seeded(i, 40) * 0.04,
      log_loss: 0.15 + seeded(i, 41) * 0.01,
    })),
    headline: { roc_auc: 0.742, pr_auc: 0.198, log_loss: 0.148, brier: 0.041, ece: 0.014, champion: "XGBoost" },
  };
}

export function mockFeatureImportance(): FeatureImportanceDto {
  const feats: Array<[string, string]> = [
    ["Barrel% 14d", "Hitter"],
    ["xISO 30d", "Hitter"],
    ["Hard Hit% 14d", "Hitter"],
    ["Pull FB% 30d", "Hitter"],
    ["xwOBA 30d", "Hitter"],
    ["HR/9 pitcher", "Pitcher"],
    ["Barrel% allowed", "Pitcher"],
    ["FB% allowed", "Pitcher"],
    ["xSLG allowed", "Pitcher"],
    ["Park HR factor (hand)", "Ballpark"],
    ["Altitude", "Ballpark"],
    ["Air density", "Weather"],
    ["Wind to CF (mph)", "Weather"],
    ["Temp (°F)", "Weather"],
    ["vs Hand wOBA split", "Matchup"],
    ["Pitch-type xSLG", "Matchup"],
    ["Vegas team total", "Vegas"],
    ["Implied run total", "Vegas"],
    ["Batting order", "Opportunity"],
    ["Expected PA", "Opportunity"],
  ];
  return feats.map(([feature, group], i) => ({
    feature,
    group,
    gain: 0.14 - i * 0.005 + seeded(i, 50) * 0.01,
    permutation: 0.11 - i * 0.004 + seeded(i, 51) * 0.01,
    shap: 0.18 - i * 0.006 + seeded(i, 52) * 0.01,
  }));
}

export function mockBacktest(): BacktestDto {
  const months = Array.from({ length: 6 }, (_, i) => {
    const roi = (seeded(i, 60) - 0.4) * 20;
    return {
      label: ["Apr", "May", "Jun", "Jul", "Aug", "Sep"][i],
      month: `2025-${String(i + 4).padStart(2, "0")}`,
      year: 2025,
      hit_rate: 0.16 + seeded(i, 61) * 0.05,
      roi,
      bets: 700 + Math.floor(seeded(i, 62) * 60),
    };
  });
  const totalRoi = months.reduce((a, m) => a + m.roi, 0);
  const totalBets = months.reduce((a, m) => a + m.bets, 0);
  const hitAvg = months.reduce((a, m) => a + m.hit_rate, 0) / months.length;
  return {
    months,
    summary: {
      total_bets: totalBets,
      hit_rate: hitAvg,
      roi: totalRoi,
      sharpe: 1.32,
      max_drawdown: -8.4,
    },
  };
}
