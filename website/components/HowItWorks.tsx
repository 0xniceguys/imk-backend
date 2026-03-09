const steps = [
  {
    n: "01",
    icon: "🥊",
    title: "Watch Live Battles",
    body: "Trained AI fighters go head-to-head in Mortal Kombat 4 — fully automated and streamed live.",
  },
  {
    n: "02",
    icon: "💰",
    title: "Bet SKR Tokens",
    body: "Back P1 or P2 before the match locks. Every bet is a Solana smart-contract transaction — fully transparent.",
  },
  {
    n: "03",
    icon: "⚡",
    title: "Collect Winnings",
    body: "Match ends, smart contract settles automatically. SKR lands in your wallet — no middleman.",
  },
];

export default function HowItWorks() {
  return (
    <section id="how" className="py-28 px-6">
      <div className="max-w-6xl mx-auto">
        {/* Header */}
        <div className="text-center mb-16">
          <p className="text-[11px] uppercase tracking-[0.3em] text-[#FFC500] mb-3">
            The Process
          </p>
          <h2 className="cinzel text-3xl md:text-5xl font-bold text-white">
            How It Works
          </h2>
        </div>

        {/* Step cards */}
        <div className="grid md:grid-cols-3 gap-5 mb-10">
          {steps.map((s) => (
            <div
              key={s.n}
              className="group p-8 rounded-xl bg-[#111] border border-[#1e1e1e] hover:border-[#FFC500]/30 transition-all duration-300 hover:-translate-y-1"
            >
              <div className="flex items-start justify-between mb-6">
                <span className="cinzel text-5xl font-black text-[#FFC500]/15 group-hover:text-[#FFC500]/30 transition-colors">
                  {s.n}
                </span>
                <span className="text-3xl">{s.icon}</span>
              </div>
              {/* Accent line */}
              <div className="w-8 h-0.5 bg-[#FFC500] mb-4 group-hover:w-14 transition-all duration-300" />
              <h3 className="cinzel text-base text-white mb-3">{s.title}</h3>
              <p className="text-sm text-[#555] leading-relaxed">{s.body}</p>
            </div>
          ))}
        </div>

        {/* HUD mockup */}
        <div className="rounded-2xl bg-[#0e0e0e] border border-[#1e1e1e] p-8 md:p-10">
          <p className="text-[10px] uppercase tracking-[0.3em] text-[#444] mb-6">
            Live Match HUD — Round 2
          </p>
          {/* P1 bar */}
          <div className="flex items-center gap-4 mb-4">
            <span className="cinzel text-[13px] text-[#FFC500] w-5 flex-shrink-0">P1</span>
            <div className="flex-1 h-2.5 bg-[#111] rounded-full overflow-hidden">
              <div className="h-full w-[72%] bg-[#00FB60] rounded-full" />
            </div>
            <span className="text-[13px] text-[#00FB60] w-8 text-right">72%</span>
          </div>
          {/* P2 bar */}
          <div className="flex items-center gap-4 mb-6">
            <span className="cinzel text-[13px] text-[#FFC500] w-5 flex-shrink-0">P2</span>
            <div className="flex-1 h-2.5 bg-[#111] rounded-full overflow-hidden">
              <div className="h-full w-[45%] bg-[#D70000] rounded-full" />
            </div>
            <span className="text-[13px] text-[#D70000] w-8 text-right">45%</span>
          </div>
          {/* Timer */}
          <div className="flex justify-center">
            <div className="cinzel text-3xl font-black text-[#FFC500] px-8 py-3 rounded bg-[#1a1500] border border-[#FFC500]/20">
              87
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
