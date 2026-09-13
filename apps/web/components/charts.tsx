"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { ScenarioEngineResult, SensitivityResult, SotpResult } from "@/lib/types";

const COLORS = {
  primary: "#0B1F3A",
  secondary: "#2563EB",
  success: "#16A34A",
  warning: "#F59E0B",
  danger: "#DC2626",
  border: "#E5E7EB",
  text: "#111827",
};

const tooltipStyle = {
  borderRadius: 10,
  border: `1px solid ${COLORS.border}`,
  fontSize: 12,
  boxShadow: "0 1px 3px rgba(0,0,0,0.08)",
};

function fmt(n: number): string {
  if (Math.abs(n) >= 1_000_000_000) return `${(n / 1_000_000_000).toFixed(1)}B`;
  if (Math.abs(n) >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (Math.abs(n) >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return n.toFixed(1);
}

// --- Segment valuation breakdown (bar chart) --------------------------------

export function SegmentValuationChart({
  data,
}: {
  data: { name: string; enterprise_value: number }[];
}) {
  if (data.length === 0) {
    return (
      <div className="flex h-64 items-center justify-center text-sm text-[#111827]/40">
        No segment valuation data available yet.
      </div>
    );
  }
  return (
    <ResponsiveContainer width="100%" height={280}>
      <BarChart data={data} margin={{ top: 8, right: 16, left: 8, bottom: 8 }}>
        <CartesianGrid strokeDasharray="3 3" stroke={COLORS.border} vertical={false} />
        <XAxis dataKey="name" tick={{ fontSize: 11, fill: COLORS.text }} />
        <YAxis tick={{ fontSize: 11, fill: COLORS.text }} tickFormatter={fmt} />
        <Tooltip contentStyle={tooltipStyle} formatter={(v: number) => fmt(v)} />
        <Bar dataKey="enterprise_value" name="Attributed EV" fill={COLORS.secondary} radius={[6, 6, 0, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
}

// --- SOTP bridge waterfall ---------------------------------------------------

export function SotpWaterfallChart({ result }: { result: SotpResult }) {
  const steps: { name: string; value: number; base: number; tone: string }[] = [];
  let running = 0;

  Object.entries(result.segment_attributed_evs).forEach(([name, ev]) => {
    steps.push({ name, value: ev, base: running, tone: COLORS.secondary });
    running += ev;
  });
  steps.push({
    name: "Non-op. assets",
    value: result.non_operating_assets,
    base: running,
    tone: COLORS.success,
  });
  running += result.non_operating_assets;

  const overheadDeduction = -Math.abs(result.capitalized_overhead_deduction);
  if (result.capitalized_overhead_deduction) {
    steps.push({
      name: "Overhead deduction",
      value: overheadDeduction,
      base: running + overheadDeduction,
      tone: COLORS.danger,
    });
    running += overheadDeduction;
  }

  const equity = result.equity_value;
  steps.push({ name: "Equity value", value: equity, base: 0, tone: COLORS.primary });

  const chartData = steps.map((s) => ({
    name: s.name,
    invisible: s.name === "Equity value" ? 0 : Math.min(s.base, s.base + s.value),
    visible: s.name === "Equity value" ? s.value : Math.abs(s.value),
    tone: s.tone,
  }));

  return (
    <ResponsiveContainer width="100%" height={320}>
      <BarChart data={chartData} margin={{ top: 8, right: 16, left: 8, bottom: 24 }}>
        <CartesianGrid strokeDasharray="3 3" stroke={COLORS.border} vertical={false} />
        <XAxis dataKey="name" tick={{ fontSize: 10, fill: COLORS.text }} angle={-20} textAnchor="end" height={60} />
        <YAxis tick={{ fontSize: 11, fill: COLORS.text }} tickFormatter={fmt} />
        <Tooltip contentStyle={tooltipStyle} formatter={(v: number) => fmt(v)} />
        <Bar dataKey="invisible" stackId="a" fill="transparent" />
        <Bar dataKey="visible" stackId="a" radius={[6, 6, 0, 0]}>
          {chartData.map((entry, i) => (
            <Cell key={i} fill={entry.tone} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

// --- scenario comparison ---------------------------------------------------

export function ScenarioComparisonChart({ result }: { result: ScenarioEngineResult }) {
  const data = [
    { name: "Bear", ev: result.bear.enterprise_value, price: result.bear.implied_price_per_share, tone: COLORS.danger },
    { name: "Base", ev: result.base.enterprise_value, price: result.base.implied_price_per_share, tone: COLORS.secondary },
    { name: "Bull", ev: result.bull.enterprise_value, price: result.bull.implied_price_per_share, tone: COLORS.success },
  ];
  return (
    <ResponsiveContainer width="100%" height={300}>
      <BarChart data={data} margin={{ top: 8, right: 16, left: 8, bottom: 8 }}>
        <CartesianGrid strokeDasharray="3 3" stroke={COLORS.border} vertical={false} />
        <XAxis dataKey="name" tick={{ fontSize: 12, fill: COLORS.text }} />
        <YAxis tick={{ fontSize: 11, fill: COLORS.text }} tickFormatter={fmt} />
        <Tooltip contentStyle={tooltipStyle} formatter={(v: number) => fmt(v)} />
        <Legend wrapperStyle={{ fontSize: 12 }} />
        <Bar dataKey="ev" name="Enterprise Value" radius={[6, 6, 0, 0]}>
          {data.map((d, i) => (
            <Cell key={i} fill={d.tone} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

// --- sensitivity heatmap -----------------------------------------------------

export function SensitivityHeatmap({ result }: { result: SensitivityResult }) {
  const flat = result.matrix.flat();
  const min = Math.min(...flat);
  const max = Math.max(...flat);
  const range = max - min || 1;

  function colorFor(v: number): string {
    const t = (v - min) / range; // 0..1
    // interpolate danger -> warning -> success
    if (t < 0.5) {
      return mix(COLORS.danger, COLORS.warning, t / 0.5);
    }
    return mix(COLORS.warning, COLORS.success, (t - 0.5) / 0.5);
  }

  return (
    <div className="overflow-x-auto">
      <table className="border-collapse text-xs">
        <thead>
          <tr>
            <th className="border border-[#E5E7EB] bg-[#F8FAFC] p-2 text-[10px] font-semibold text-[#111827]/50">
              {result.row_label} \ {result.col_label}
            </th>
            {result.col_values.map((c, i) => (
              <th key={i} className="border border-[#E5E7EB] bg-[#F8FAFC] p-2 text-[10px] font-semibold text-[#111827]/70">
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {result.matrix.map((row, i) => (
            <tr key={i}>
              <td className="border border-[#E5E7EB] bg-[#F8FAFC] p-2 text-[10px] font-semibold text-[#111827]/70">
                {result.row_values[i]}
              </td>
              {row.map((v, j) => (
                <td
                  key={j}
                  className="border border-[#E5E7EB] p-2 text-center tabular-nums text-[#0B1F3A]"
                  style={{ backgroundColor: colorFor(v) }}
                  title={`${result.row_label}=${result.row_values[i]}, ${result.col_label}=${result.col_values[j]}`}
                >
                  {fmt(v)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// --- historical revenue trend (bar, REPORTED-only) --------------------------

export function RevenueTrendChart({
  years,
}: {
  years: { fiscal_year: number; value?: number | null; data_status: string }[];
}) {
  const data = years.filter((y) => y.value != null);
  if (data.length === 0) {
    return (
      <div className="flex h-64 items-center justify-center text-sm text-[#111827]/40">
        No REPORTED revenue history available.
      </div>
    );
  }
  return (
    <ResponsiveContainer width="100%" height={280}>
      <BarChart data={data} margin={{ top: 8, right: 16, left: 8, bottom: 8 }}>
        <CartesianGrid strokeDasharray="3 3" stroke={COLORS.border} vertical={false} />
        <XAxis dataKey="fiscal_year" tick={{ fontSize: 11, fill: COLORS.text }} />
        <YAxis tick={{ fontSize: 11, fill: COLORS.text }} tickFormatter={fmt} />
        <Tooltip contentStyle={tooltipStyle} formatter={(v: number) => fmt(v)} />
        <Bar dataKey="value" name="Revenue (REPORTED)" fill={COLORS.secondary} radius={[6, 6, 0, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
}

// --- operating margin trend (line) -----------------------------------------

export function MarginTrendChart({
  years,
}: {
  years: { fiscal_year: number; value?: number | null }[];
}) {
  const data = years.filter((y) => y.value != null);
  if (data.length === 0) {
    return (
      <div className="flex h-56 items-center justify-center text-sm text-[#111827]/40">
        No margin trend available.
      </div>
    );
  }
  return (
    <ResponsiveContainer width="100%" height={240}>
      <LineChart data={data} margin={{ top: 8, right: 16, left: 8, bottom: 8 }}>
        <CartesianGrid strokeDasharray="3 3" stroke={COLORS.border} vertical={false} />
        <XAxis dataKey="fiscal_year" tick={{ fontSize: 11, fill: COLORS.text }} />
        <YAxis tick={{ fontSize: 11, fill: COLORS.text }} tickFormatter={(v: number) => `${v.toFixed(0)}%`} />
        <Tooltip contentStyle={tooltipStyle} formatter={(v: number) => `${v.toFixed(1)}%`} />
        <Line type="monotone" dataKey="value" name="Operating Margin" stroke={COLORS.primary} strokeWidth={2} dot={{ r: 3 }} />
      </LineChart>
    </ResponsiveContainer>
  );
}

// --- football field: SOTP / DCF / Comps triangulation ranges ---------------

export type FootballFieldRange = {
  name: string;
  low: number | null;
  high: number | null;
  point: number | null; // point estimate / midpoint marker within the range
  color: string;
};

export function FootballFieldChart({
  ranges,
  marketPrice,
  triangulatedPrice,
}: {
  ranges: FootballFieldRange[];
  marketPrice?: number | null;
  triangulatedPrice?: number | null;
}) {
  const usable = ranges.filter((r) => r.low != null || r.high != null || r.point != null);
  if (usable.length === 0) {
    return (
      <div className="flex h-40 items-center justify-center text-sm text-[#111827]/40">
        Enter at least one methodology's implied price/range to render the football field.
      </div>
    );
  }

  const allValues: number[] = [];
  usable.forEach((r) => {
    if (r.low != null) allValues.push(r.low);
    if (r.high != null) allValues.push(r.high);
    if (r.point != null) allValues.push(r.point);
  });
  if (marketPrice != null) allValues.push(marketPrice);
  if (triangulatedPrice != null) allValues.push(triangulatedPrice);

  const dataMin = Math.min(...allValues);
  const dataMax = Math.max(...allValues);
  const pad = (dataMax - dataMin) * 0.12 || Math.max(dataMax * 0.1, 1);
  const min = Math.max(0, dataMin - pad);
  const max = dataMax + pad;
  const span = max - min || 1;
  const pct = (v: number) => `${((v - min) / span) * 100}%`;

  return (
    <div className="space-y-4">
      <div className="relative">
        {/* market price vertical reference line, spans all rows */}
        {marketPrice != null && (
          <div
            className="pointer-events-none absolute top-0 z-10 h-full border-l-2 border-dashed border-[#111827]/40"
            style={{ left: pct(marketPrice) }}
          />
        )}
        {triangulatedPrice != null && (
          <div
            className="pointer-events-none absolute top-0 z-10 h-full border-l-2 border-[#0B1F3A]"
            style={{ left: pct(triangulatedPrice) }}
          />
        )}
        <div className="space-y-3">
          {usable.map((r) => {
            const low = r.low ?? r.point ?? r.high!;
            const high = r.high ?? r.point ?? r.low!;
            return (
              <div key={r.name} className="flex items-center gap-3">
                <div className="w-20 shrink-0 text-xs font-medium text-[#111827]/70">{r.name}</div>
                <div className="relative h-6 flex-1 rounded-[6px] bg-[#F8FAFC]">
                  <div
                    className="absolute top-1/2 h-3 -translate-y-1/2 rounded-full opacity-70"
                    style={{ left: pct(low), width: `calc(${pct(high)} - ${pct(low)})`, backgroundColor: r.color }}
                  />
                  {r.point != null && (
                    <div
                      className="absolute top-1/2 h-4 w-[2px] -translate-y-1/2"
                      style={{ left: pct(r.point), backgroundColor: r.color }}
                      title={`${r.name} point estimate: $${r.point.toFixed(2)}`}
                    />
                  )}
                </div>
                <div className="w-28 shrink-0 text-right text-xs tabular-nums text-[#111827]/60">
                  {r.low != null && r.high != null
                    ? `$${r.low.toFixed(0)} – $${r.high.toFixed(0)}`
                    : r.point != null
                    ? `$${r.point.toFixed(2)}`
                    : "—"}
                </div>
              </div>
            );
          })}
        </div>
      </div>
      <div className="flex flex-wrap gap-4 text-[11px] text-[#111827]/60">
        {marketPrice != null && (
          <span className="flex items-center gap-1.5">
            <span className="inline-block h-0 w-4 border-t-2 border-dashed border-[#111827]/40" />
            Current market price (${marketPrice.toFixed(2)})
          </span>
        )}
        {triangulatedPrice != null && (
          <span className="flex items-center gap-1.5">
            <span className="inline-block h-0 w-4 border-t-2 border-[#0B1F3A]" />
            Triangulated (weighted) fair value (${triangulatedPrice.toFixed(2)})
          </span>
        )}
      </div>
    </div>
  );
}

function mix(hexA: string, hexB: string, t: number): string {
  const a = hexToRgb(hexA);
  const b = hexToRgb(hexB);
  const r = Math.round(a.r + (b.r - a.r) * t);
  const g = Math.round(a.g + (b.g - a.g) * t);
  const bl = Math.round(a.b + (b.b - a.b) * t);
  return `rgba(${r}, ${g}, ${bl}, 0.22)`;
}

function hexToRgb(hex: string) {
  const v = hex.replace("#", "");
  return {
    r: parseInt(v.slice(0, 2), 16),
    g: parseInt(v.slice(2, 4), 16),
    b: parseInt(v.slice(4, 6), 16),
  };
}

// --- DCF FCFF projection line chart -----------------------------------------

export function DcfProjectionChart({
  projections,
}: {
  projections: { year_index: number; fcff: number; discounted_fcff: number }[];
}) {
  if (projections.length === 0) return null;
  return (
    <ResponsiveContainer width="100%" height={260}>
      <LineChart data={projections} margin={{ top: 8, right: 16, left: 8, bottom: 8 }}>
        <CartesianGrid strokeDasharray="3 3" stroke={COLORS.border} vertical={false} />
        <XAxis dataKey="year_index" tick={{ fontSize: 11, fill: COLORS.text }} label={{ value: "Forecast year", position: "insideBottom", offset: -4, fontSize: 11 }} />
        <YAxis tick={{ fontSize: 11, fill: COLORS.text }} tickFormatter={fmt} />
        <Tooltip contentStyle={tooltipStyle} formatter={(v: number) => fmt(v)} />
        <Legend wrapperStyle={{ fontSize: 12 }} />
        <Line type="monotone" dataKey="fcff" name="FCFF" stroke={COLORS.secondary} strokeWidth={2} dot={{ r: 3 }} />
        <Line type="monotone" dataKey="discounted_fcff" name="Discounted FCFF" stroke={COLORS.primary} strokeWidth={2} dot={{ r: 3 }} />
      </LineChart>
    </ResponsiveContainer>
  );
}
