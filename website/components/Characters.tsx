const fighters = [
  {
    name: "Scorpion",
    tag: "Hell-born assassin",
    img: "/characters/scorpio.png",
    color: "#FFC500",
    wr: 67,
  },
  {
    name: "Cage",
    tag: "Hollywood warrior",
    img: "/characters/cage.png",
    color: "#AEAEAE",
    wr: 58,
  },
  {
    name: "Sonya",
    tag: "Special forces agent",
    img: "/characters/sonya.png",
    color: "#00FB60",
    wr: 61,
  },
];

export default function Characters() {
  return (
    <section id="fighters" className="py-28 px-6 bg-[#060606]">
      <div className="max-w-6xl mx-auto">
        {/* Header */}
        <div className="text-center mb-16">
          <p className="text-[11px] uppercase tracking-[0.3em] text-[#FFC500] mb-3">
            The Roster
          </p>
          <h2 className="cinzel text-3xl md:text-5xl font-bold text-white mb-4">
            Meet The Fighters
          </h2>
          <p className="text-sm text-[#555] max-w-sm mx-auto leading-relaxed">
            Each fighter is a trained AI model. Stats update after every match.
          </p>
        </div>

        {/* Cards */}
        <div className="grid md:grid-cols-3 gap-6">
          {fighters.map((f) => (
            <div
              key={f.name}
              className="group relative bg-[#111] border border-[#1e1e1e] rounded-xl overflow-hidden hover:border-[#2a2a2a] transition-all duration-300 hover:-translate-y-1.5"
            >
              {/* Fighter image area */}
              <div className="relative h-56 flex items-end justify-center overflow-hidden bg-[#0d0d0d]">
                {/* colour glow */}
                <div
                  className="absolute inset-0 opacity-0 group-hover:opacity-100 transition-opacity duration-500"
                  style={{
                    background: `radial-gradient(ellipse 80% 60% at 50% 80%, ${f.color}18 0%, transparent 70%)`,
                  }}
                />
                {/* gradient fade at bottom */}
                <div className="absolute inset-x-0 bottom-0 h-24 bg-gradient-to-t from-[#111] to-transparent z-10" />
                {/* The fighter */}
                <img
                  src={f.img}
                  alt={f.name}
                  className="absolute bottom-0 h-[90%] w-auto object-contain mx-auto left-0 right-0 group-hover:scale-105 transition-transform duration-500"
                />
              </div>

              {/* Info */}
              <div className="px-6 pb-6 pt-4 relative z-10">
                <div className="flex items-end justify-between mb-1">
                  <h3
                    className="cinzel text-xl font-bold tracking-wide"
                    style={{ color: f.color }}
                  >
                    {f.name.toUpperCase()}
                  </h3>
                  <div className="text-right">
                    <div className="text-[10px] uppercase tracking-wider text-[#444] mb-0.5">
                      Win Rate
                    </div>
                    <div className="cinzel text-base font-bold" style={{ color: f.color }}>
                      {f.wr}%
                    </div>
                  </div>
                </div>
                <p className="text-[11px] uppercase tracking-[0.18em] text-[#444] mb-4">
                  {f.tag}
                </p>

                {/* Win rate bar */}
                <div className="h-0.5 bg-[#1e1e1e] rounded-full overflow-hidden">
                  <div
                    className="h-full rounded-full transition-all duration-700"
                    style={{ width: `${f.wr}%`, background: f.color }}
                  />
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
