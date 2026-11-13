// 對應後端 world-api 的 /world/* 回傳格式。
// 後端改格式時要同步更新這裡（後端：src/civsim/api/server.py、src/civsim/analysis/metrics.py）。

export type CitizenType = "producer_trader" | "producer_self" | "consumer" | "dependent";
export type Phase = "stable" | "disaster" | "evaluation";

export interface CitizenView {
  id: string;
  type: CitizenType;
  alive: boolean;
  food: number;
  money: number;
  hungry_days: number;
}

export interface EquilibriumBand {
  mean: number;
  std: number;
  low: number;
  high: number;
  k: number;
  samples: number;
}

export interface WorldState {
  tick: number;
  phase: Phase;
  disaster_id: number | null;
  market: {
    price: number;
    customary_price: number;
    price_cap: number | null;
    price_cap_until: number | null;
  };
  government: {
    budget: number;
    food_reserve: number;
  };
  band: EquilibriumBand | null;
  citizens: CitizenView[];
}

export interface TickMetrics {
  tick: number;
  day: number;
  disaster_id: number | null;
  phase: Phase;
  food_multiplier: number;
  price: number;
  traded: boolean;
  volume: number;
  demand: number;
  supply: number;
  price_capped: boolean;
  customary_price: number;
  alive: number;
  hungry: number;
  deaths: number;
  citizen_food: number;
  citizen_money: number;
  money_gini: number;
  gov_budget: number;
  gov_reserve: number;
  gov_accepted: number;
  gov_rejected: number;
  rejected_actions: number;
}

export interface SimEvent {
  tick: number;
  kind: string; // action:buy / world:disaster_start / gov:release_reserve ...
  actor: string | null;
  success: boolean;
  payload: Record<string, unknown>;
  message: string;
}

export const PHASE_LABEL: Record<Phase, string> = {
  stable: "平穩期",
  disaster: "天災期",
  evaluation: "評估期",
};