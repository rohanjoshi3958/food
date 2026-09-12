import { apiFetch, parseError } from "@/lib/api";

export type TokenTotals = {
  uncached_input: number;
  cache_write_5m: number;
  cache_write_1h: number;
  cache_read: number;
  output: number;
  total_input: number;
  total: number;
};

export type VisionTotals = {
  image_count: number;
  document_count: number;
  calls_with_visual: number;
  approx_visual_tokens: number;
  approx_text_tokens: number;
  vision_share_pct: number | null;
};

export type Bucket = {
  calls: number;
  ok_calls: number;
  error_calls: number;
  error_rate_pct: number | null;
  estimated_cost_usd: number;
  tokens: TokenTotals;
  cache_read_pct: number | null;
  avg_latency_ms: number | null;
  unpriced_calls: number;
  vision: VisionTotals;
};

export type WorkflowSummary = Bucket & {
  workflow: string;
  runs: number;
  successful_runs: number;
  failed_runs: number;
  running_runs: number;
  run_success_rate_pct: number | null;
  cost_in_successful_runs_usd: number;
  cost_per_successful_run_usd: number | null;
  input_tokens_per_successful_run: number | null;
  output_tokens_per_successful_run: number | null;
  cost_outside_runs_usd: number;
  calls_per_run: number | null;
  retry_rate_pct: number | null;
  escalation_rate_pct: number | null;
  steps: (Bucket & { step: string })[];
  models: (Bucket & { model: string })[];
  routes: (Bucket & { route: string; share_of_calls_pct: number | null })[];
};

export type ModelSummary = Bucket & {
  model: string;
  share_of_cost_pct: number | null;
  share_of_calls_pct: number | null;
};

export type DailyPoint = {
  date: string;
  calls: number;
  estimated_cost_usd: number;
  by_workflow: Record<string, number>;
};

export type UsageSummary = {
  window: { days: number; since: string; until: string };
  pricing_version: string;
  totals: Bucket & { runs: number; successful_runs: number };
  workflows: WorkflowSummary[];
  models: ModelSummary[];
  daily: DailyPoint[];
};

export type UsageEvent = {
  id: string;
  created_at: string;
  workflow: string;
  step: string;
  run_id: string | null;
  attempt: number;
  model: string;
  provider: string;
  route: string | null;
  confidence: number | null;
  service_tier: string | null;
  status: "ok" | "error";
  error_type: string | null;
  stop_reason: string | null;
  latency_ms: number;
  max_tokens: number | null;
  uncached_input_tokens: number;
  cache_write_5m_tokens: number;
  cache_write_1h_tokens: number;
  cache_read_tokens: number;
  output_tokens: number;
  total_input_tokens: number;
  image_count: number;
  document_count: number;
  approx_visual_tokens: number | null;
  estimated_cost_usd: number;
  pricing_known: boolean;
  user_id: string | null;
  receipt_id: string | null;
  meal_id: string | null;
  anthropic_request_id: string | null;
};

export type AnthropicReconciliation =
  | { configured: false; message: string }
  | { configured: true; error: string }
  | {
      configured: true;
      window: { since: string; until: string; days: number };
      fetched_at: string;
      usage: {
        tokens: Record<string, number>;
        cache_read_pct: number | null;
        rows: {
          model: string;
          service_tier: string;
          workspace_id: string;
          uncached_input_tokens: number;
          cache_write_5m_tokens: number;
          cache_write_1h_tokens: number;
          cache_read_tokens: number;
          output_tokens: number;
        }[];
      };
      cost: {
        total_usd: number;
        by_model: Record<string, number>;
        by_workspace: Record<string, number>;
        by_token_type: Record<string, number>;
      };
    };

export class MetricsAccessError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function getJson<T>(path: string, fallback: string): Promise<T> {
  const response = await apiFetch(path);
  if (response.status === 401 || response.status === 403) {
    throw new MetricsAccessError(
      response.status,
      await parseError(response, fallback),
    );
  }
  if (!response.ok) {
    throw new Error(await parseError(response, fallback));
  }
  return (await response.json()) as T;
}

export function getUsageSummary(days: number) {
  return getJson<UsageSummary>(
    `/api/metrics/llm/summary?days=${days}`,
    "Unable to load usage summary.",
  );
}

export function getRecentUsageEvents(limit = 50) {
  return getJson<{ events: UsageEvent[]; workflows: string[] }>(
    `/api/metrics/llm/events?limit=${limit}`,
    "Unable to load recent Claude calls.",
  );
}

export function getAnthropicReconciliation(days: number) {
  return getJson<AnthropicReconciliation>(
    `/api/metrics/llm/anthropic?days=${Math.min(days, 31)}`,
    "Unable to load Anthropic billing data.",
  );
}

export function formatUsd(value: number | null | undefined, digits = 4) {
  if (value === null || value === undefined) {
    return "—";
  }
  return `$${value.toFixed(digits)}`;
}

export function formatPct(value: number | null | undefined) {
  if (value === null || value === undefined) {
    return "—";
  }
  return `${value.toFixed(1)}%`;
}

export function formatCount(value: number | null | undefined) {
  if (value === null || value === undefined) {
    return "—";
  }
  return new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 }).format(
    value,
  );
}

export function formatLatency(value: number | null | undefined) {
  if (value === null || value === undefined) {
    return "—";
  }
  if (value >= 1000) {
    return `${(value / 1000).toFixed(1)}s`;
  }
  return `${value}ms`;
}

export const WORKFLOW_LABELS: Record<string, string> = {
  receipt_parse: "Receipt parse",
  ingredient_normalize: "Ingredient normalize",
  meal_gen: "Meal generation",
  unattributed: "Unattributed",
};

export function workflowLabel(workflow: string) {
  return WORKFLOW_LABELS[workflow] ?? workflow;
}
