import type { ReactNode } from "react";
import type { DataStatus, RiskLevel } from "@/lib/types";
import { ApiError } from "@/lib/api";

// -----------------------------------------------------------------------
// Design system primitives (see docs/implementation-plan.md Phase 9):
// background #FFFFFF, primary #0B1F3A, secondary #2563EB, success #16A34A,
// warning #F59E0B, danger #DC2626, text #111827, border #E5E7EB,
// card #F8FAFC. 10px radius, thin borders, subtle shadows, Inter font.
// -----------------------------------------------------------------------

export function Card({
  children,
  className = "",
  title,
  subtitle,
  action,
}: {
  children: ReactNode;
  className?: string;
  title?: string;
  subtitle?: string;
  action?: ReactNode;
}) {
  return (
    <div
      className={`rounded-[10px] border border-[#E5E7EB] bg-[#F8FAFC] shadow-sm ${className}`}
    >
      {(title || action) && (
        <div className="flex items-center justify-between px-5 pt-4 pb-2">
          <div>
            {title && (
              <h3 className="text-sm font-semibold tracking-wide text-[#111827]">
                {title}
              </h3>
            )}
            {subtitle && (
              <p className="mt-0.5 text-xs text-[#111827]/60">{subtitle}</p>
            )}
          </div>
          {action}
        </div>
      )}
      <div className={title ? "px-5 pb-5" : "p-5"}>{children}</div>
    </div>
  );
}

// --- data provenance badges -------------------------------------------------

const PROVENANCE_STYLE: Record<DataStatus, string> = {
  REPORTED: "bg-[#16A34A]/10 text-[#16A34A] border-[#16A34A]/30",
  DERIVED: "bg-[#2563EB]/10 text-[#2563EB] border-[#2563EB]/30",
  ESTIMATED: "bg-[#F59E0B]/10 text-[#B45309] border-[#F59E0B]/40",
  MISSING: "bg-[#111827]/5 text-[#111827]/50 border-[#E5E7EB]",
  CONFLICTING: "bg-[#DC2626]/10 text-[#DC2626] border-[#DC2626]/30",
};

export function ProvenanceBadge({ status }: { status: DataStatus | string }) {
  const key = (PROVENANCE_STYLE[status as DataStatus] && status) as DataStatus;
  const style = PROVENANCE_STYLE[key] ?? "bg-[#111827]/5 text-[#111827]/50 border-[#E5E7EB]";
  return (
    <span
      className={`inline-flex items-center rounded-[6px] border px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${style}`}
      title={`Data provenance: ${status}`}
    >
      {status}
    </span>
  );
}

// --- AI-origin badge ---------------------------------------------------

export function AIBadge({ label = "AI" }: { label?: string }) {
  return (
    <span
      className="inline-flex items-center gap-1 rounded-[6px] border border-[#2563EB]/40 bg-[#0B1F3A] px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-white"
      title="AI-generated content — recommendation only, not a deterministic calculation"
    >
      <svg width="9" height="9" viewBox="0 0 24 24" fill="none" className="shrink-0">
        <path
          d="M12 2l1.8 6.2L20 10l-6.2 1.8L12 18l-1.8-6.2L4 10l6.2-1.8L12 2z"
          fill="currentColor"
        />
      </svg>
      {label}
    </span>
  );
}

export function AIPanel({
  children,
  title,
}: {
  children: ReactNode;
  title?: string;
}) {
  return (
    <div className="rounded-[10px] border border-[#2563EB]/25 bg-[#2563EB]/[0.04] p-4">
      <div className="mb-2 flex items-center gap-2">
        <AIBadge />
        {title && (
          <span className="text-xs font-semibold text-[#0B1F3A]">{title}</span>
        )}
      </div>
      {children}
    </div>
  );
}

// --- risk level badge -------------------------------------------------

const RISK_STYLE: Record<RiskLevel, string> = {
  LOW: "bg-[#16A34A]/10 text-[#16A34A] border-[#16A34A]/30",
  MEDIUM: "bg-[#F59E0B]/10 text-[#B45309] border-[#F59E0B]/40",
  HIGH: "bg-[#DC2626]/10 text-[#DC2626] border-[#DC2626]/30",
};

export function RiskBadge({ level }: { level: RiskLevel | string }) {
  const style = RISK_STYLE[level as RiskLevel] ?? "bg-[#111827]/5 text-[#111827]/60 border-[#E5E7EB]";
  return (
    <span className={`inline-flex items-center rounded-[6px] border px-2 py-0.5 text-xs font-semibold uppercase tracking-wide ${style}`}>
      {level}
    </span>
  );
}

// --- classification badge (reverse valuation) -----------------------------

const CLASSIFICATION_STYLE: Record<string, string> = {
  CONSERVATIVE: "bg-[#2563EB]/10 text-[#2563EB] border-[#2563EB]/30",
  REASONABLE: "bg-[#16A34A]/10 text-[#16A34A] border-[#16A34A]/30",
  AGGRESSIVE: "bg-[#F59E0B]/10 text-[#B45309] border-[#F59E0B]/40",
  EXTREME: "bg-[#DC2626]/10 text-[#DC2626] border-[#DC2626]/30",
};

export function ClassificationBadge({ value }: { value: string }) {
  const style = CLASSIFICATION_STYLE[value] ?? "bg-[#111827]/5 text-[#111827]/60 border-[#E5E7EB]";
  return (
    <span className={`inline-flex items-center rounded-[10px] border px-2.5 py-1 text-xs font-semibold uppercase tracking-wide ${style}`}>
      {value}
    </span>
  );
}

// --- metric card -------------------------------------------------

export function MetricCard({
  label,
  value,
  sub,
  tone = "default",
  provenance,
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  tone?: "default" | "success" | "danger" | "warning";
  provenance?: DataStatus;
}) {
  const toneClass =
    tone === "success"
      ? "text-[#16A34A]"
      : tone === "danger"
      ? "text-[#DC2626]"
      : tone === "warning"
      ? "text-[#B45309]"
      : "text-[#0B1F3A]";
  return (
    <div className="rounded-[10px] border border-[#E5E7EB] bg-white p-4 shadow-sm">
      <div className="flex items-center justify-between">
        <p className="text-[11px] font-medium uppercase tracking-wide text-[#111827]/50">
          {label}
        </p>
        {provenance && <ProvenanceBadge status={provenance} />}
      </div>
      <p className={`mt-1.5 text-2xl font-semibold tabular-nums ${toneClass}`}>
        {value}
      </p>
      {sub && <p className="mt-1 text-xs text-[#111827]/50">{sub}</p>}
    </div>
  );
}

// --- loading skeletons -------------------------------------------------

export function Skeleton({ className = "" }: { className?: string }) {
  return (
    <div
      className={`animate-pulse rounded-[6px] bg-[#E5E7EB] ${className}`}
    />
  );
}

export function PageSkeleton() {
  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} className="h-24" />
        ))}
      </div>
      <Skeleton className="h-64 w-full" />
      <Skeleton className="h-48 w-full" />
    </div>
  );
}

// --- error state -------------------------------------------------

export function ErrorState({
  error,
  context,
}: {
  error: unknown;
  context?: string;
}) {
  const isApiError = error instanceof ApiError;
  const isUnreachable = isApiError && error.status === 0;
  const status = isApiError ? error.status : undefined;

  let title = "Something went wrong";
  let body =
    isApiError && error.detail
      ? error.detail
      : error instanceof Error
      ? error.message
      : "Unknown error.";

  if (isUnreachable) {
    title = "Backend unavailable";
    body =
      "Could not reach the SOTP Intelligence API. Confirm the backend is running and NEXT_PUBLIC_API_BASE_URL is set correctly.";
  } else if (status === 404) {
    title = "Not found";
  } else if (status === 501) {
    title = "Not yet implemented";
  } else if (status === 400 || status === 422) {
    title = "Insufficient data coverage for reliable valuation";
  } else if (status === 503) {
    title = "Upstream data source unavailable";
  }

  return (
    <div className="rounded-[10px] border border-[#DC2626]/25 bg-[#DC2626]/[0.04] p-6 text-center">
      <p className="text-sm font-semibold text-[#DC2626]">{title}</p>
      {context && <p className="mt-1 text-xs text-[#111827]/50">{context}</p>}
      <p className="mx-auto mt-2 max-w-md text-xs text-[#111827]/70">{body}</p>
    </div>
  );
}

export function EmptyState({ message }: { message: string }) {
  return (
    <div className="rounded-[10px] border border-dashed border-[#E5E7EB] p-8 text-center text-sm text-[#111827]/50">
      {message}
    </div>
  );
}

export function SectionHeading({
  title,
  description,
}: {
  title: string;
  description?: string;
}) {
  return (
    <div className="mb-4">
      <h2 className="text-lg font-semibold text-[#0B1F3A]">{title}</h2>
      {description && (
        <p className="mt-0.5 text-sm text-[#111827]/60">{description}</p>
      )}
    </div>
  );
}

export function Table({
  headers,
  children,
}: {
  headers: string[];
  children: ReactNode;
}) {
  return (
    <div className="overflow-x-auto rounded-[10px] border border-[#E5E7EB]">
      <table className="w-full min-w-[600px] border-collapse text-sm">
        <thead>
          <tr className="border-b border-[#E5E7EB] bg-[#F8FAFC]">
            {headers.map((h) => (
              <th
                key={h}
                className="px-4 py-2.5 text-left text-[11px] font-semibold uppercase tracking-wide text-[#111827]/50"
              >
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}
