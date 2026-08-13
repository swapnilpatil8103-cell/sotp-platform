"use client";

import { useMemo, useState } from "react";
import { Card, MetricCard, SectionHeading } from "@/components/ui";

/**
 * Triangulation view: SOTP / DCF / Comps side by side with user-adjustable
 * weights. There is no single backend "triangulate" endpoint (each
 * methodology is computed independently on its own tab — POST
 * /valuation/sotp, /valuation/dcf, /valuation/comps) so this page composes
 * their already-computed implied prices client-side. Enter the
 * implied-price-per-share figure produced on each of the DCF/Comps/SOTP
 * tabs, adjust weights, and this page computes the weighted blend
 * deterministically (no AI, no server round-trip needed for the blend
 * itself).
 */
export default function ValuationTriangulationPage({ params }: { params: { ticker: string } }) {
  const ticker = params.ticker.toUpperCase();
  const [sotpPrice, setSotpPrice] = useState<number | "">("");
  const [dcfPrice, setDcfPrice] = useState<number | "">("");
  const [compsPrice, setCompsPrice] = useState<number | "">("");
  const [wSotp, setWSotp] = useState(0.4);
  const [wDcf, setWDcf] = useState(0.35);
  const [wComps, setWComps] = useState(0.25);
  const [marketPrice, setMarketPrice] = useState<number | "">("");

  const totalWeight = wSotp + wDcf + wComps;
  const blended = useMemo(() => {
    if (totalWeight === 0) return null;
    const parts: [number | "", number][] = [
      [sotpPrice, wSotp],
      [dcfPrice, wDcf],
      [compsPrice, wComps],
    ];
    const supplied = parts.filter(([p]) => p !== "") as [number, number][];
    if (supplied.length === 0) return null;
    const w = supplied.reduce((s, [, ww]) => s + ww, 0);
    if (w === 0) return null;
    return supplied.reduce((s, [p, ww]) => s + p * ww, 0) / w;
  }, [sotpPrice, dcfPrice, compsPrice, wSotp, wDcf, wComps, totalWeight]);

  const upside =
    blended != null && marketPrice !== "" ? ((blended - Number(marketPrice)) / Number(marketPrice)) * 100 : null;

  return (
    <div className="space-y-6">
      <SectionHeading
        title="Valuation Triangulation"
        description={`Blend ${ticker}'s SOTP / DCF / Comps implied prices under adjustable weights. Compute each methodology on its own tab first, then bring the implied price/share here.`}
      />

      <Card title="Methodology Inputs">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          <MethodInput label="SOTP implied price/share" price={sotpPrice} setPrice={setSotpPrice} weight={wSotp} setWeight={setWSotp} />
          <MethodInput label="DCF implied price/share" price={dcfPrice} setPrice={setDcfPrice} weight={wDcf} setWeight={setWDcf} />
          <MethodInput label="Comps implied price/share" price={compsPrice} setPrice={setCompsPrice} weight={wComps} setWeight={setWComps} />
        </div>
        <div className="mt-4 max-w-xs">
          <label className="mb-1 block text-xs font-medium text-[#111827]/60">Current market price (optional)</label>
          <input
            type="number"
            value={marketPrice}
            onChange={(e) => setMarketPrice(e.target.value === "" ? "" : Number(e.target.value))}
            className="w-full rounded-[8px] border border-[#E5E7EB] px-3 py-2 text-sm tabular-nums"
          />
        </div>
      </Card>

      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
        <MetricCard label="Blended Fair Value" value={blended != null ? `$${blended.toFixed(2)}` : "—"} tone="success" />
        <MetricCard label="Total Weight" value={totalWeight.toFixed(2)} sub={totalWeight !== 1 ? "Weights need not sum to 1 — normalized automatically" : undefined} />
        <MetricCard
          label="Upside / Downside"
          value={upside != null ? `${upside.toFixed(1)}%` : "—"}
          tone={upside != null ? (upside >= 0 ? "success" : "danger") : "default"}
        />
      </div>
    </div>
  );
}

function MethodInput({
  label,
  price,
  setPrice,
  weight,
  setWeight,
}: {
  label: string;
  price: number | "";
  setPrice: (v: number | "") => void;
  weight: number;
  setWeight: (v: number) => void;
}) {
  return (
    <div className="rounded-[10px] border border-[#E5E7EB] bg-white p-4">
      <label className="mb-1 block text-xs font-medium text-[#111827]/60">{label}</label>
      <input
        type="number"
        value={price}
        onChange={(e) => setPrice(e.target.value === "" ? "" : Number(e.target.value))}
        className="mb-3 w-full rounded-[8px] border border-[#E5E7EB] px-3 py-2 text-sm tabular-nums"
        placeholder="$"
      />
      <label className="mb-1 block text-xs font-medium text-[#111827]/60">Weight</label>
      <input
        type="range"
        min={0}
        max={1}
        step={0.05}
        value={weight}
        onChange={(e) => setWeight(Number(e.target.value))}
        className="w-full accent-[#2563EB]"
      />
      <p className="mt-1 text-right text-xs tabular-nums text-[#0B1F3A]">{weight.toFixed(2)}</p>
    </div>
  );
}
