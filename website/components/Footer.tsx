export default function Footer() {
  return (
    <footer className="border-t border-[#1a1a1a] bg-[#060606] py-14 px-6">
      <div className="max-w-6xl mx-auto">
        <div className="grid md:grid-cols-3 gap-10 mb-12">
          {/* Brand */}
          <div>
            <div className="flex items-center gap-3 mb-4">
              <img src="/figma/logoVector.png" alt="IMK" className="w-6 h-6 object-contain" />
              <span className="cinzel text-[12px] tracking-[0.2em] uppercase text-white/80">
                Immortal Kombat
              </span>
            </div>
            <p className="text-[13px] text-[#444] leading-relaxed max-w-xs">
              AI-powered fighting battles on Solana. Every fight is real. Every bet is on-chain.
            </p>
          </div>

          {/* Product */}
          <div>
            <p className="text-[11px] uppercase tracking-[0.22em] text-[#444] mb-4">Product</p>
            <ul className="space-y-2.5">
              {[
                ["How It Works", "#how"],
                ["Fighters", "#fighters"],
                ["Get App", "#app"],
              ].map(([label, href]) => (
                <li key={label}>
                  <a href={href} className="text-[13px] text-[#555] hover:text-[#FFC500] transition-colors">
                    {label}
                  </a>
                </li>
              ))}
            </ul>
          </div>

          {/* Tech */}
          <div>
            <p className="text-[11px] uppercase tracking-[0.22em] text-[#444] mb-4">Powered By</p>
            <ul className="space-y-2.5">
              {["Solana Blockchain", "Privy Wallet", "SKR Token", "Mortal Kombat 4"].map((item) => (
                <li key={item} className="text-[13px] text-[#555]">{item}</li>
              ))}
            </ul>
          </div>
        </div>

        {/* Bottom */}
        <div className="pt-8 border-t border-[#131313] flex flex-col md:flex-row items-start md:items-center justify-between gap-4">
          <span className="text-[12px] text-[#333]">
            © 2026 Immortal Kombat. All rights reserved.
          </span>
          <div className="flex items-center gap-2">
            <span className="w-1.5 h-1.5 rounded-full bg-[#00FB60] pulse-dot" />
            <span className="text-[12px] text-[#00FB60]">immortalkombat.timesnap.xyz</span>
          </div>
        </div>
      </div>
    </footer>
  );
}
