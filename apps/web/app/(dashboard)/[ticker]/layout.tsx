import Link from "next/link";

const NAV_ITEMS: { href: string; label: string; index: number }[] = [
  { href: "", label: "Overview", index: 1 },
  { href: "/segments", label: "Segments", index: 2 },
  { href: "/valuation", label: "Valuation", index: 3 },
  { href: "/dcf", label: "DCF", index: 4 },
  { href: "/comps", label: "Comps", index: 5 },
  { href: "/sotp", label: "SOTP", index: 6 },
  { href: "/scenarios", label: "Scenarios", index: 7 },
  { href: "/sensitivities", label: "Sensitivities", index: 8 },
  { href: "/reverse-valuation", label: "Reverse Valuation", index: 9 },
  { href: "/value-unlock", label: "Value Unlock", index: 10 },
  { href: "/ai-analyst", label: "AI Analyst", index: 11 },
  { href: "/risks", label: "Risks", index: 12 },
  { href: "/research-history", label: "Research History", index: 13 },
  { href: "/memo", label: "Investment Memo", index: 14 },
];

export default function TickerLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: { ticker: string };
}) {
  const ticker = params.ticker?.toUpperCase() ?? "";
  const base = `/${ticker}`;

  return (
    <div className="min-h-screen bg-white">
      <header className="sticky top-0 z-10 border-b border-[#E5E7EB] bg-white/95 backdrop-blur">
        <div className="mx-auto flex max-w-[1400px] items-center justify-between px-6 py-3">
          <Link href="/" className="text-sm font-semibold tracking-tight text-[#0B1F3A]">
            SOTP INTELLIGENCE
          </Link>
          <div className="flex items-center gap-2">
            <span className="text-xs uppercase tracking-wide text-[#111827]/40">
              Company
            </span>
            <span className="rounded-[6px] bg-[#0B1F3A] px-2.5 py-1 text-sm font-semibold text-white">
              {ticker}
            </span>
          </div>
        </div>
      </header>

      <div className="mx-auto flex max-w-[1400px]">
        <nav className="sticky top-[49px] hidden h-[calc(100vh-49px)] w-56 shrink-0 overflow-y-auto border-r border-[#E5E7EB] py-4 md:block">
          <ul>
            {NAV_ITEMS.map((item) => (
              <li key={item.href}>
                <Link
                  href={`${base}${item.href}`}
                  className="group flex items-center gap-2.5 px-5 py-2 text-sm text-[#111827]/70 transition-colors hover:bg-[#2563EB]/[0.06] hover:text-[#0B1F3A]"
                >
                  <span className="w-4 text-[10px] tabular-nums text-[#111827]/30 group-hover:text-[#2563EB]">
                    {item.index}
                  </span>
                  {item.label}
                </Link>
              </li>
            ))}
          </ul>
        </nav>

        <main className="min-w-0 flex-1 px-6 py-6">{children}</main>
      </div>
    </div>
  );
}
