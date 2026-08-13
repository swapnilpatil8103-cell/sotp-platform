"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

const EXAMPLE_TICKERS = ["AAPL", "GOOGL", "AMZN", "MSFT", "META"];

const FEATURE_TAGS = [
  "Live Public Data",
  "Deterministic Valuation",
  "AI Research",
  "Human-Controlled Judgment",
];

export default function Home() {
  const [ticker, setTicker] = useState("");
  const router = useRouter();

  function handleAnalyze() {
    const value = ticker.trim().toUpperCase();
    if (!value) return;
    router.push(`/dashboard/${encodeURIComponent(value)}`);
  }

  return (
    <main className="min-h-screen flex flex-col items-center justify-center px-6 py-24">
      <div className="w-full max-w-2xl text-center">
        <h1 className="text-4xl sm:text-5xl font-semibold tracking-tight text-primary">
          SOTP INTELLIGENCE
        </h1>
        <p className="mt-4 text-base sm:text-lg text-primary/70">
          Institutional-grade, SEC-data-driven valuation. Sourced data,
          deterministic math, AI-assisted research, human-controlled
          judgment.
        </p>

        <div className="mt-10 flex flex-col sm:flex-row items-center gap-3 justify-center">
          <input
            type="text"
            value={ticker}
            onChange={(e) => setTicker(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleAnalyze()}
            placeholder="Enter a ticker, e.g. AAPL"
            className="w-full sm:w-72 rounded-[10px] border border-primary/20 px-4 py-3 text-primary placeholder:text-primary/40 focus:outline-none focus:ring-2 focus:ring-secondary"
          />
          <button
            onClick={handleAnalyze}
            className="w-full sm:w-auto rounded-[10px] bg-secondary px-6 py-3 text-white font-medium hover:bg-secondary/90 transition-colors"
          >
            Analyze
          </button>
        </div>

        <div className="mt-6 flex flex-wrap items-center justify-center gap-2">
          {EXAMPLE_TICKERS.map((t) => (
            <button
              key={t}
              onClick={() => setTicker(t)}
              className="rounded-[10px] border border-primary/15 px-3 py-1.5 text-sm text-primary/70 hover:border-secondary hover:text-secondary transition-colors"
            >
              {t}
            </button>
          ))}
        </div>

        <div className="mt-16 flex flex-wrap items-center justify-center gap-3">
          {FEATURE_TAGS.map((tag) => (
            <span
              key={tag}
              className="rounded-[10px] bg-primary/5 px-3 py-1.5 text-xs font-medium tracking-wide text-primary/70 uppercase"
            >
              {tag}
            </span>
          ))}
        </div>
      </div>
    </main>
  );
}
