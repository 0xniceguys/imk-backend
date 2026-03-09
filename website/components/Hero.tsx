export default function Hero() {
  return (
    <section className="relative min-h-screen flex flex-col items-center justify-center overflow-hidden">
      {/* Background arena image */}
      <div className="absolute inset-0 z-0">
        <img
          src="/figma/getstartedimage.png"
          alt=""
          aria-hidden
          className="w-full h-full object-cover opacity-15"
        />
        <div className="absolute inset-0 bg-gradient-to-b from-black/60 via-black/40 to-[#080808]" />
        {/* Radial gold glow */}
        <div
          className="absolute inset-0 opacity-100"
          style={{
            background:
              "radial-gradient(ellipse 80% 60% at 50% 40%, rgba(255,197,0,0.06) 0%, transparent 70%)",
          }}
        />
      </div>

      {/* Main content */}
      <div className="relative z-10 flex flex-col items-center text-center px-6 pt-24 pb-16 w-full max-w-5xl mx-auto">
        {/* Live pill */}
        <div className="fade-up flex items-center gap-2.5 px-4 py-2 rounded-full bg-[#041306] border border-[#00FB60]/20 mb-10">
          <span className="w-2 h-2 rounded-full bg-[#00FB60] pulse-dot flex-shrink-0" />
          <span className="text-[11px] text-[#00FB60] tracking-[0.25em] uppercase font-medium">
            AI Battles Live On-Chain
          </span>
        </div>

        {/* Logo */}
        <img
          src="/figma/logoVector.png"
          alt="Immortal Kombat"
          className="fade-up-1 w-16 h-16 object-contain mb-6 float"
        />

        {/* Title */}
        <h1 className="fade-up-2 cinzel font-black leading-none tracking-tight mb-5"
          style={{ fontSize: "clamp(52px, 10vw, 112px)" }}
        >
          <span className="block text-white gold-glow-text">IMMORTAL</span>
          <span className="block gold-text">KOMBAT</span>
        </h1>

        <p className="fade-up-3 text-[#999] text-base md:text-lg max-w-lg leading-relaxed mb-2">
          AI fighters battle live in Mortal Kombat 4. Watch, bet SKR tokens, and win on Solana.
        </p>
        <p className="fade-up-3 text-[11px] text-[#555] tracking-[0.3em] uppercase mb-10">
          No humans &nbsp;·&nbsp; Pure algorithm &nbsp;·&nbsp; Real stakes
        </p>

        {/* CTA buttons */}
        <div className="fade-up-4 flex flex-col sm:flex-row gap-3 mb-16">
          <a
            href="#app"
            className="px-8 py-3.5 bg-[#FFC500] text-black text-[13px] font-bold uppercase tracking-[0.15em] rounded-sm hover:bg-[#FFD84D] transition-colors duration-200"
          >
            Download App
          </a>
          <a
            href="#how"
            className="px-8 py-3.5 border border-[#2a2a2a] text-[#999] text-[13px] uppercase tracking-[0.15em] rounded-sm hover:border-[#FFC500]/50 hover:text-[#FFC500] transition-colors duration-200"
          >
            How It Works
          </a>
        </div>

        {/* Fighters stage */}
        <div className="fade-up-4 relative w-full max-w-2xl h-[260px] md:h-[320px]">
          {/* Left fighter */}
          <img
            src="/figma/battleLeft.png"
            alt="Player 1"
            className="float absolute bottom-0 left-0 w-[170px] md:w-[220px] h-full object-contain object-bottom"
          />

          {/* VS centre */}
          <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
            <span
              className="cinzel font-black text-5xl md:text-6xl"
              style={{ color: "rgba(255,197,0,0.18)", letterSpacing: "0.3em" }}
            >
              VS
            </span>
          </div>

          {/* Right fighter (mirrored) */}
          <img
            src="/figma/battleRight.png"
            alt="Player 2"
            className="float2 absolute bottom-0 right-0 w-[170px] md:w-[220px] h-full object-contain object-bottom"
          />

          {/* Ground line */}
          <div className="absolute bottom-0 inset-x-0 h-px bg-gradient-to-r from-transparent via-[#FFC500]/20 to-transparent" />
        </div>
      </div>

      {/* Scroll cue */}
      <div className="absolute bottom-8 left-1/2 -translate-x-1/2 z-10 flex flex-col items-center gap-2 opacity-30">
        <span className="text-[10px] uppercase tracking-[0.3em] text-[#666]">Scroll</span>
        <div className="w-px h-8 bg-gradient-to-b from-[#666] to-transparent" />
      </div>
    </section>
  );
}
