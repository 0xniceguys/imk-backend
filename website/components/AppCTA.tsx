export default function AppCTA() {
  return (
    <section id="app" className="py-28 px-6">
      <div className="max-w-6xl mx-auto">
        <div className="grid md:grid-cols-2 gap-0 rounded-2xl overflow-hidden border border-[#1e1e1e] bg-[#0d0d0d]">
          {/* Left — text */}
          <div className="px-10 md:px-14 py-14 flex flex-col justify-center">
            <p className="text-[11px] uppercase tracking-[0.3em] text-[#FFC500] mb-4">
              Available Now
            </p>
            <h2 className="cinzel text-3xl md:text-4xl font-bold leading-tight mb-5">
              Watch Live.
              <br />Bet On-Chain.
              <br />
              <span className="gold-text">Win Real Tokens.</span>
            </h2>
            <p className="text-sm text-[#555] leading-relaxed mb-8 max-w-sm">
              Download Immortal Kombat to watch AI battles, bet SKR tokens, and
              claim winnings — all on Solana.
            </p>

            <ul className="flex flex-col gap-3 mb-10">
              {[
                "Live HLS video stream of every battle",
                "Real-time health bar and round tracking",
                "On-chain betting with SKR tokens",
                "Instant payout via Privy wallet",
              ].map((item) => (
                <li key={item} className="flex items-center gap-3 text-sm text-[#777]">
                  <span className="w-1.5 h-1.5 rounded-full bg-[#FFC500] flex-shrink-0" />
                  {item}
                </li>
              ))}
            </ul>

            <div className="flex flex-wrap gap-3">
              <a
                href="#"
                className="px-6 py-3 bg-[#FFC500] text-black text-[12px] font-bold uppercase tracking-[0.15em] rounded-sm hover:bg-[#FFD84D] transition-colors"
              >
                Android APK
              </a>
              <span className="px-6 py-3 border border-[#1e1e1e] text-[#333] text-[12px] uppercase tracking-[0.15em] rounded-sm cursor-not-allowed">
                iOS — Soon
              </span>
            </div>
          </div>

          {/* Right — fighter + live badge */}
          <div className="relative hidden md:flex items-end justify-center overflow-hidden min-h-[420px] bg-[#090909]">
            {/* Gold radial */}
            <div
              className="absolute inset-0"
              style={{
                background:
                  "radial-gradient(ellipse 80% 70% at 50% 90%, rgba(255,197,0,0.07) 0%, transparent 70%)",
              }}
            />
            {/* Live badge */}
            <div className="absolute top-5 left-5 z-10 flex items-center gap-2 px-3 py-1.5 rounded-full bg-[#041306] border border-[#00FB60]/25">
              <span className="w-1.5 h-1.5 rounded-full bg-[#00FB60] pulse-dot" />
              <span className="text-[10px] text-[#00FB60] uppercase tracking-[0.18em]">
                Round 2 · Live
              </span>
            </div>
            {/* Fighter */}
            <img
              src="/figma/fighterCenter.png"
              alt="Fighter"
              className="relative z-10 h-[85%] w-auto object-contain object-bottom"
            />
          </div>
        </div>
      </div>
    </section>
  );
}
