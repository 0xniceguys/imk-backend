"use client";
import { useState, useEffect } from "react";

export default function Navbar() {
  const [scrolled, setScrolled] = useState(false);
  useEffect(() => {
    const fn = () => setScrolled(window.scrollY > 50);
    window.addEventListener("scroll", fn, { passive: true });
    return () => window.removeEventListener("scroll", fn);
  }, []);

  return (
    <nav
      className={`fixed inset-x-0 top-0 z-50 transition-all duration-500 ${
        scrolled
          ? "bg-black/80 backdrop-blur-xl border-b border-[#2a2a2a]"
          : "bg-transparent"
      }`}
    >
      <div className="max-w-6xl mx-auto px-6 h-16 flex items-center justify-between">
        {/* Logo */}
        <a href="/" className="flex items-center gap-3 group">
          <img
            src="/figma/logoVector.png"
            alt="IMK"
            className="w-8 h-8 object-contain group-hover:opacity-80 transition-opacity"
          />
          <span className="cinzel text-[13px] tracking-[0.18em] text-white/90 uppercase hidden sm:block">
            Immortal Kombat
          </span>
        </a>

        {/* Links */}
        <div className="hidden md:flex items-center gap-8">
          {["How It Works", "Fighters", "Get App"].map((label, i) => (
            <a
              key={label}
              href={`#${["how", "fighters", "app"][i]}`}
              className="text-[12px] uppercase tracking-[0.18em] text-[#666] hover:text-[#FFC500] transition-colors duration-200"
            >
              {label}
            </a>
          ))}
        </div>

        {/* CTA */}
        <a
          href="#app"
          className="flex items-center gap-2 px-5 py-2.5 bg-[#FFC500] text-black text-[12px] font-bold uppercase tracking-[0.15em] rounded-sm hover:bg-[#FFD84D] transition-colors duration-200"
        >
          Watch Live
        </a>
      </div>
    </nav>
  );
}
