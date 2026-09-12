"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import {
  AnthropicReconciliation,
  MetricsAccessError,
  UsageEvent,
  UsageSummary,
  WorkflowSummary,
  formatCount,
  formatLatency,
  formatPct,
  formatUsd,
  getAnthropicReconciliation,
  getRecentUsageEvents,
  getUsageSummary,
  workflowLabel,
} from "@/lib/metrics";

const WINDOW_OPTIONS = [1, 7, 30] as const;
type WindowDays = (typeof WINDOW_OPTIONS)[number];

function SectionCard({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-2xl border border-stone-200 bg-white p-5 shadow-sm">
      <div className="mb-4">
        <h2 className="text-base font-semibold text-stone-900">{title}</h2>
        {description ? (
          <p className="mt-1 text-sm text-stone-500">{description}</p>
        ) : null}
      </div>
      {children}
    </section>
  );
}

function KpiCard({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint?: string;
}) {
  return (
    <div className="rounded-2xl border border-stone-200 bg-white p-4 shadow-sm">
      <p className="text-xs font-medium uppercase tracking-wide text-stone-500">
        {label}
      </p>
      <p className="mt-2 text-2xl font-semibold text-stone-900">{value}</p>
      {hint ? <p className="mt-1 text-xs text-stone-500">{hint}</p> : null}
    </div>
  );
}

const th =
  "px-3 py-2 text-left text-xs font-semibold uppercase tracking-wide text-stone-500";
const td = "px-3 py-2 text-sm text-stone-800 whitespace-nowrap";
const tdNum = `${td} text-right tabular-nums`;

function WorkflowRow({ row }: { row: WorkflowSummary }) {
  const [expanded, setExpanded] = useState(false);
  const hasDetails = row.steps.length > 0 || row.models.length > 0;

  return (
    <>
      <tr className="border-t border-stone-100">
        <td className={td}>
          <button
            type="button"
            onClick={() => setExpanded((current) => !current)}
            disabled={!hasDetails}
            className="flex items-center gap-2 font-medium text-stone-900 disabled:cursor-default"
            aria-expanded={expanded}
          >
            <span
              className={`text-stone-400 transition ${expanded ? "rotate-90" : ""} ${hasDetails ? "" : "opacity-0"}`}
            >
              ▸
            </span>
            {workflowLabel(row.workflow)}
          </button>
        </td>
        <td className={tdNum}>
          {formatCount(row.successful_runs)}
          <span className="text-stone-400"> / {formatCount(row.runs)}</span>
        </td>
        <td className={`${tdNum} font-semibold`}>
          {formatUsd(row.cost_per_successful_run_usd)}
        </td>
        <td className={tdNum}>
          {formatCount(row.input_tokens_per_successful_run)}
          <span className="text-stone-400"> / </span>
          {formatCount(row.output_tokens_per_successful_run)}
        </td>
        <td className={tdNum}>{formatUsd(row.estimated_cost_usd)}</td>
        <td className={tdNum}>{formatCount(row.calls)}</td>
        <td className={tdNum}>{formatPct(row.cache_read_pct)}</td>
        <td className={tdNum}>{formatPct(row.vision.vision_share_pct)}</td>
        <td className={tdNum}>{formatPct(row.retry_rate_pct)}</td>
        <td className={tdNum}>{formatPct(row.escalation_rate_pct)}</td>
        <td className={tdNum}>{formatLatency(row.avg_latency_ms)}</td>
        <td className={tdNum}>
          {row.error_calls > 0 ? (
            <span className="text-red-600">{formatCount(row.error_calls)}</span>
          ) : (
            "0"
          )}
        </td>
      </tr>
      {expanded ? (
        <tr className="bg-stone-50">
          <td colSpan={12} className="px-3 py-3">
            <div className="grid gap-4 md:grid-cols-2">
              <div>
                <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-stone-500">
                  Steps
                </p>
                <table className="w-full">
                  <thead>
                    <tr>
                      <th className={th}>Step</th>
                      <th className={`${th} text-right`}>Calls</th>
                      <th className={`${th} text-right`}>Est. $</th>
                      <th className={`${th} text-right`}>In / Out tok</th>
                      <th className={`${th} text-right`}>Cache %</th>
                      <th className={`${th} text-right`}>Latency</th>
                    </tr>
                  </thead>
                  <tbody>
                    {row.steps.map((step) => (
                      <tr key={step.step} className="border-t border-stone-200/60">
                        <td className={td}>{step.step}</td>
                        <td className={tdNum}>{formatCount(step.calls)}</td>
                        <td className={tdNum}>{formatUsd(step.estimated_cost_usd)}</td>
                        <td className={tdNum}>
                          {formatCount(step.tokens.total_input)} /{" "}
                          {formatCount(step.tokens.output)}
                        </td>
                        <td className={tdNum}>{formatPct(step.cache_read_pct)}</td>
                        <td className={tdNum}>{formatLatency(step.avg_latency_ms)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {row.cost_outside_runs_usd > 0 ? (
                  <p className="mt-2 text-xs text-stone-500">
                    {formatUsd(row.cost_outside_runs_usd)} of this workflow&apos;s
                    spend came from auxiliary calls outside a run (live unit
                    checks, image prompts) and is excluded from $/successful run.
                  </p>
                ) : null}
              </div>
              <div>
                <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-stone-500">
                  Models
                </p>
                <table className="w-full">
                  <thead>
                    <tr>
                      <th className={th}>Model</th>
                      <th className={`${th} text-right`}>Calls</th>
                      <th className={`${th} text-right`}>Est. $</th>
                      <th className={`${th} text-right`}>In / Out tok</th>
                    </tr>
                  </thead>
                  <tbody>
                    {row.models.map((model) => (
                      <tr key={model.model} className="border-t border-stone-200/60">
                        <td className={td}>{model.model}</td>
                        <td className={tdNum}>{formatCount(model.calls)}</td>
                        <td className={tdNum}>{formatUsd(model.estimated_cost_usd)}</td>
                        <td className={tdNum}>
                          {formatCount(model.tokens.total_input)} /{" "}
                          {formatCount(model.tokens.output)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <p className="mt-2 text-xs text-stone-500">
                  Runs: {row.successful_runs} succeeded, {row.failed_runs} failed
                  {row.running_runs ? `, ${row.running_runs} running` : ""}.
                  Calls per run: {row.calls_per_run ?? "—"}.
                </p>
              </div>
            </div>
          </td>
        </tr>
      ) : null}
    </>
  );
}

function DailySpend({ summary }: { summary: UsageSummary }) {
  const max = Math.max(...summary.daily.map((d) => d.estimated_cost_usd), 0);
  if (max === 0) {
    return (
      <p className="text-sm text-stone-500">No Claude spend in this window.</p>
    );
  }
  const barAreaPx = 144;
  return (
    <div className="flex h-40 items-end gap-1">
      {summary.daily.map((day) => {
        const heightPx = Math.max(
          2,
          Math.round((day.estimated_cost_usd / max) * barAreaPx),
        );
        const breakdown = Object.entries(day.by_workflow)
          .map(([workflow, cost]) => `${workflowLabel(workflow)}: ${formatUsd(cost)}`)
          .join("\n");
        return (
          <div
            key={day.date}
            className="group flex flex-1 flex-col items-center justify-end"
            title={`${day.date}\n${formatUsd(day.estimated_cost_usd)} · ${day.calls} calls${breakdown ? `\n${breakdown}` : ""}`}
          >
            <div className="flex w-full items-end" style={{ height: barAreaPx }}>
              <div
                className="w-full rounded-t bg-orange-400 transition group-hover:bg-orange-500"
                style={{ height: heightPx }}
              />
            </div>
            <span className="mt-1 text-[10px] text-stone-400">
              {day.date.slice(5)}
            </span>
          </div>
        );
      })}
    </div>
  );
}

function Reconciliation({
  data,
  estimatedUsd,
}: {
  data: AnthropicReconciliation | null;
  estimatedUsd: number;
}) {
  if (!data) {
    return <p className="text-sm text-stone-500">Loading…</p>;
  }
  if (!data.configured) {
    return (
      <p className="text-sm text-stone-500">
        {data.message} Our estimates use list prices; the Admin API shows what
        Anthropic actually metered for the whole organization (all workspaces,
        including calls outside this app).
      </p>
    );
  }
  if ("error" in data) {
    return <p className="text-sm text-red-600">{data.error}</p>;
  }
  const drift = data.cost.total_usd - estimatedUsd;
  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-3">
        <KpiCard label="Anthropic billed" value={formatUsd(data.cost.total_usd, 2)} hint="Organization-wide, daily buckets" />
        <KpiCard label="Our estimate" value={formatUsd(estimatedUsd, 2)} hint="This app, list prices" />
        <KpiCard
          label="Difference"
          value={`${drift >= 0 ? "+" : "−"}${formatUsd(Math.abs(drift), 2)}`}
          hint="Positive: Anthropic reports more than we estimated"
        />
      </div>
      <div className="overflow-x-auto">
        <table className="w-full">
          <thead>
            <tr>
              <th className={th}>Model</th>
              <th className={th}>Service tier</th>
              <th className={th}>Workspace</th>
              <th className={`${th} text-right`}>Uncached in</th>
              <th className={`${th} text-right`}>Cache write 5m / 1h</th>
              <th className={`${th} text-right`}>Cache read</th>
              <th className={`${th} text-right`}>Output</th>
              <th className={`${th} text-right`}>Billed $</th>
            </tr>
          </thead>
          <tbody>
            {data.usage.rows.map((row) => (
              <tr key={`${row.model}-${row.service_tier}-${row.workspace_id}`} className="border-t border-stone-100">
                <td className={td}>{row.model}</td>
                <td className={td}>{row.service_tier}</td>
                <td className={td}>{row.workspace_id}</td>
                <td className={tdNum}>{formatCount(row.uncached_input_tokens)}</td>
                <td className={tdNum}>
                  {formatCount(row.cache_write_5m_tokens)} / {formatCount(row.cache_write_1h_tokens)}
                </td>
                <td className={tdNum}>{formatCount(row.cache_read_tokens)}</td>
                <td className={tdNum}>{formatCount(row.output_tokens)}</td>
                <td className={tdNum}>{formatUsd(data.cost.by_model[row.model], 2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-stone-500">
        Org cache read rate: {formatPct(data.usage.cache_read_pct)}. Fetched{" "}
        {new Date(data.fetched_at).toLocaleString()} (cached 5 minutes).
      </p>
    </div>
  );
}

function EventsTable({ events }: { events: UsageEvent[] }) {
  if (events.length === 0) {
    return <p className="text-sm text-stone-500">No Claude calls recorded yet.</p>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full">
        <thead>
          <tr>
            <th className={th}>Time</th>
            <th className={th}>Workflow / step</th>
            <th className={th}>Model</th>
            <th className={`${th} text-right`}>Uncached in</th>
            <th className={`${th} text-right`}>Cache w / r</th>
            <th className={`${th} text-right`}>Out</th>
            <th className={`${th} text-right`}>Img / vis tok</th>
            <th className={th}>Stop</th>
            <th className={`${th} text-right`}>Latency</th>
            <th className={`${th} text-right`}>Est. $</th>
            <th className={th}>Status</th>
          </tr>
        </thead>
        <tbody>
          {events.map((event) => (
            <tr key={event.id} className="border-t border-stone-100">
              <td className={td} title={event.created_at}>
                {new Date(event.created_at).toLocaleTimeString()}
              </td>
              <td className={td}>
                <span className="font-medium">{workflowLabel(event.workflow)}</span>
                <span className="text-stone-400"> / {event.step}</span>
                {event.attempt > 1 ? (
                  <span className="ml-1 rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-medium text-amber-800">
                    retry #{event.attempt}
                  </span>
                ) : null}
              </td>
              <td className={td}>
                {event.model}
                {event.route ? (
                  <span className="ml-1 rounded bg-stone-100 px-1.5 py-0.5 text-[10px] font-medium text-stone-600">
                    {event.route}
                    {event.confidence !== null ? ` · ${Math.round(event.confidence * 100)}%` : ""}
                  </span>
                ) : null}
              </td>
              <td className={tdNum}>{formatCount(event.uncached_input_tokens)}</td>
              <td className={tdNum}>
                {formatCount(event.cache_write_5m_tokens + event.cache_write_1h_tokens)} /{" "}
                {formatCount(event.cache_read_tokens)}
              </td>
              <td className={tdNum}>{formatCount(event.output_tokens)}</td>
              <td className={tdNum}>
                {event.image_count + event.document_count > 0
                  ? `${event.image_count + event.document_count} / ${event.approx_visual_tokens ?? "?"}`
                  : "—"}
              </td>
              <td className={td}>{event.stop_reason ?? "—"}</td>
              <td className={tdNum}>{formatLatency(event.latency_ms)}</td>
              <td className={tdNum}>
                {formatUsd(event.estimated_cost_usd, 5)}
                {!event.pricing_known ? (
                  <span className="ml-1 text-amber-600" title="No price for this model">
                    ?
                  </span>
                ) : null}
              </td>
              <td className={td}>
                {event.status === "ok" ? (
                  <span className="rounded bg-emerald-100 px-1.5 py-0.5 text-xs text-emerald-800">ok</span>
                ) : (
                  <span className="rounded bg-red-100 px-1.5 py-0.5 text-xs text-red-800" title={event.error_type ?? ""}>
                    {event.error_type ?? "error"}
                  </span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function LlmUsageDashboard() {
  const router = useRouter();
  const [days, setDays] = useState<WindowDays>(7);
  const [summary, setSummary] = useState<UsageSummary | null>(null);
  const [events, setEvents] = useState<UsageEvent[]>([]);
  const [reconciliation, setReconciliation] = useState<AnthropicReconciliation | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [forbidden, setForbidden] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);

  const refresh = useCallback(() => {
    setLoading(true);
    setError(null);
    setRefreshKey((current) => current + 1);
  }, []);

  const changeWindow = useCallback((next: WindowDays) => {
    setLoading(true);
    setError(null);
    setDays(next);
  }, []);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const [summaryData, eventsData] = await Promise.all([
          getUsageSummary(days),
          getRecentUsageEvents(50),
        ]);
        if (cancelled) {
          return;
        }
        setSummary(summaryData);
        setEvents(eventsData.events);
        getAnthropicReconciliation(days)
          .then((data) => {
            if (!cancelled) {
              setReconciliation(data);
            }
          })
          .catch(() => {
            if (!cancelled) {
              setReconciliation({ configured: true, error: "Unable to load Anthropic billing data." });
            }
          });
      } catch (err) {
        if (cancelled) {
          return;
        }
        if (err instanceof MetricsAccessError) {
          if (err.status === 401) {
            router.replace("/login");
            return;
          }
          setForbidden(true);
          return;
        }
        setError(err instanceof Error ? err.message : "Unable to load metrics.");
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }

    void load();

    return () => {
      cancelled = true;
    };
  }, [days, refreshKey, router]);

  if (forbidden) {
    return (
      <div className="flex flex-1 items-center justify-center bg-stone-50 p-6">
        <div className="max-w-md rounded-2xl border border-stone-200 bg-white p-6 text-center shadow-sm">
          <h1 className="text-lg font-semibold text-stone-900">Metrics are restricted</h1>
          <p className="mt-2 text-sm text-stone-500">
            This internal dashboard is limited to admins. Add your email to
            <code className="mx-1 rounded bg-stone-100 px-1">ADMIN_EMAILS</code>
            or use a <code className="rounded bg-stone-100 px-1">METRICS_API_TOKEN</code>.
          </p>
          <Link href="/" className="mt-4 inline-block text-sm font-medium text-orange-600 hover:underline">
            Back to the app
          </Link>
        </div>
      </div>
    );
  }

  const totals = summary?.totals;

  return (
    <div className="flex flex-1 flex-col bg-stone-50">
      <header className="border-b border-stone-200 bg-white">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-3 px-4 py-4">
          <div>
            <p className="text-xs font-medium uppercase tracking-wide text-orange-600">Internal · ops</p>
            <h1 className="text-lg font-semibold text-stone-900">Claude usage &amp; cost</h1>
            <p className="text-sm text-stone-500">
              Per-workflow tokens and estimated USD. Estimates use list prices
              {summary ? ` (${summary.pricing_version})` : ""}.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <div className="flex rounded-xl border border-stone-200 bg-stone-50 p-1">
              {WINDOW_OPTIONS.map((option) => (
                <button
                  key={option}
                  type="button"
                  onClick={() => changeWindow(option)}
                  className={`rounded-lg px-3 py-1.5 text-sm font-medium transition ${
                    days === option ? "bg-white text-stone-900 shadow-sm" : "text-stone-500 hover:text-stone-800"
                  }`}
                >
                  {option === 1 ? "24h" : `${option}d`}
                </button>
              ))}
            </div>
            <button
              type="button"
              onClick={refresh}
              className="rounded-xl border border-stone-200 px-3 py-1.5 text-sm font-medium text-stone-700 transition hover:bg-stone-50"
            >
              Refresh
            </button>
            <Link
              href="/"
              className="rounded-xl border border-stone-200 px-3 py-1.5 text-sm font-medium text-stone-700 transition hover:bg-stone-50"
            >
              Back to app
            </Link>
          </div>
        </div>
      </header>

      <main className="mx-auto w-full max-w-7xl flex-1 space-y-6 px-4 py-6">
        {error ? (
          <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>
        ) : null}

        {loading && !summary ? (
          <p className="text-sm text-stone-500">Loading metrics…</p>
        ) : null}

        {summary && totals ? (
          <>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
              <KpiCard label={`Est. spend (${days === 1 ? "24h" : `${days}d`})`} value={formatUsd(totals.estimated_cost_usd, 2)} hint={`${formatCount(totals.tokens.total)} tokens`} />
              <KpiCard label="Claude calls" value={formatCount(totals.calls)} hint={`${formatCount(totals.error_calls)} errors · ${formatLatency(totals.avg_latency_ms)} avg`} />
              <KpiCard label="Cache read rate" value={formatPct(totals.cache_read_pct)} hint={`${formatCount(totals.tokens.cache_read)} of ${formatCount(totals.tokens.total_input)} input tokens`} />
              <KpiCard label="Vision share of input" value={formatPct(totals.vision.vision_share_pct)} hint={`~${formatCount(totals.vision.approx_visual_tokens)} visual · ${formatCount(totals.vision.image_count + totals.vision.document_count)} images/docs`} />
              <KpiCard label="Successful runs" value={formatCount(totals.successful_runs)} hint={`${formatCount(totals.runs)} runs total`} />
            </div>

            <SectionCard
              title="Cost per workflow"
              description="$ and tokens per successful run answer “what does one receipt parse / ingredient normalize / meal plan cost”. Click a row for step and model breakdowns."
            >
              <div className="overflow-x-auto">
                <table className="w-full">
                  <thead>
                    <tr>
                      <th className={th}>Workflow</th>
                      <th className={`${th} text-right`}>Success / runs</th>
                      <th className={`${th} text-right`}>$ / success</th>
                      <th className={`${th} text-right`}>In / out tok per success</th>
                      <th className={`${th} text-right`}>Total est. $</th>
                      <th className={`${th} text-right`}>Calls</th>
                      <th className={`${th} text-right`}>Cache read</th>
                      <th className={`${th} text-right`}>Vision</th>
                      <th className={`${th} text-right`}>Retry</th>
                      <th className={`${th} text-right`}>Escalation</th>
                      <th className={`${th} text-right`}>Latency</th>
                      <th className={`${th} text-right`}>Errors</th>
                    </tr>
                  </thead>
                  <tbody>
                    {summary.workflows.map((row) => (
                      <WorkflowRow key={row.workflow} row={row} />
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="mt-3 text-xs text-stone-500">
                Retry = runs where a step was attempted more than once. Escalation = runs
                that used more than one model (0% until model tiering ships).
              </p>
            </SectionCard>

            <div className="grid gap-6 lg:grid-cols-2">
              <SectionCard title="Model mix" description="Share of calls and estimated spend by model.">
                {summary.models.length === 0 ? (
                  <p className="text-sm text-stone-500">No calls in this window.</p>
                ) : (
                  <table className="w-full">
                    <thead>
                      <tr>
                        <th className={th}>Model</th>
                        <th className={`${th} text-right`}>Calls</th>
                        <th className={`${th} text-right`}>% calls</th>
                        <th className={`${th} text-right`}>Est. $</th>
                        <th className={`${th} text-right`}>% spend</th>
                        <th className={`${th} text-right`}>In / out tok</th>
                      </tr>
                    </thead>
                    <tbody>
                      {summary.models.map((model) => (
                        <tr key={model.model} className="border-t border-stone-100">
                          <td className={td}>{model.model}</td>
                          <td className={tdNum}>{formatCount(model.calls)}</td>
                          <td className={tdNum}>{formatPct(model.share_of_calls_pct)}</td>
                          <td className={tdNum}>{formatUsd(model.estimated_cost_usd)}</td>
                          <td className={tdNum}>{formatPct(model.share_of_cost_pct)}</td>
                          <td className={tdNum}>
                            {formatCount(model.tokens.total_input)} / {formatCount(model.tokens.output)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </SectionCard>

              <SectionCard title="Daily estimated spend" description="Hover a bar for the per-workflow split.">
                <DailySpend summary={summary} />
              </SectionCard>
            </div>

            <SectionCard
              title="Anthropic billing reconciliation"
              description="Usage & Cost Admin API grouped by model, service tier and workspace."
            >
              <Reconciliation data={reconciliation} estimatedUsd={totals.estimated_cost_usd} />
            </SectionCard>

            <SectionCard title="Recent Claude calls" description="Latest 50 calls with the raw per-call fields.">
              <EventsTable events={events} />
            </SectionCard>
          </>
        ) : null}
      </main>
    </div>
  );
}
