const stats = [
  { value: "24/7", label: "Live Battles" },
  { value: "SKR", label: "Token Used" },
  { value: "Solana", label: "Blockchain" },
  { value: "AI", label: "Powered Fighters" },
];

export default function Stats() {
  return (
    <section className="border-y border-[#1e1e1e] bg-[#0c0c0c]">
      <div className="max-w-6xl mx-auto px-6 py-12 grid grid-cols-2 md:grid-cols-4 gap-8">
        {stats.map((s) => (
          <div key={s.label} className="flex flex-col items-center text-center">
            <span className="cinzel text-3xl md:text-4xl font-black gold-text mb-1">
              {s.value}
            </span>
            <span className="text-[11px] uppercase tracking-[0.22em] text-[#555]">
              {s.label}
            </span>
          </div>
        ))}
      </div>
    </section>
  );
}
