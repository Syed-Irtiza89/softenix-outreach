export const NICHES = [
  { id: "plumbers", label: "Plumbers" },
  { id: "HVAC", label: "HVAC / AC repair" },
  { id: "electricians", label: "Electricians" },
  { id: "roofers", label: "Roofers" },
  { id: "dentists", label: "Dentists" },
  { id: "restaurants", label: "Restaurants" },
  { id: "painters", label: "Painters" },
  { id: "landscapers", label: "Landscapers" },
  { id: "general contractors", label: "General contractors" },
  { id: "auto repair", label: "Auto repair" },
  { id: "cleaning service", label: "Cleaning services" },
  { id: "pest control", label: "Pest control" },
] as const;

export const CITIES = [
  { id: "USA", label: "All US cities (auto-rotate)" },
  { id: "Austin, TX", label: "Austin, TX" },
  { id: "Houston, TX", label: "Houston, TX" },
  { id: "Dallas, TX", label: "Dallas, TX" },
  { id: "San Antonio, TX", label: "San Antonio, TX" },
  { id: "Denver, CO", label: "Denver, CO" },
  { id: "Phoenix, AZ", label: "Phoenix, AZ" },
  { id: "Miami, FL", label: "Miami, FL" },
  { id: "Orlando, FL", label: "Orlando, FL" },
  { id: "Tampa, FL", label: "Tampa, FL" },
  { id: "Atlanta, GA", label: "Atlanta, GA" },
  { id: "Charlotte, NC", label: "Charlotte, NC" },
  { id: "Raleigh, NC", label: "Raleigh, NC" },
  { id: "Nashville, TN", label: "Nashville, TN" },
  { id: "Richmond, VA", label: "Richmond, VA" },
  { id: "Columbus, OH", label: "Columbus, OH" },
  { id: "Indianapolis, IN", label: "Indianapolis, IN" },
  { id: "Kansas City, MO", label: "Kansas City, MO" },
  { id: "Las Vegas, NV", label: "Las Vegas, NV" },
  { id: "Salt Lake City, UT", label: "Salt Lake City, UT" },
  { id: "Portland, OR", label: "Portland, OR" },
] as const;

type SearchSectionProps = {
  niche: string;
  city: string;
  loading: boolean;
  onNicheChange: (value: string) => void;
  onCityChange: (value: string) => void;
  onSubmit: () => void;
};

export function SearchSection({
  niche,
  city,
  loading,
  onNicheChange,
  onCityChange,
  onSubmit,
}: SearchSectionProps) {
  return (
    <section className="rounded-3xl border border-white/10 bg-white/[0.03] p-5 sm:p-6">
      <div className="mb-4">
        <h2 className="text-sm font-medium text-white">Find 20 emails</h2>
        <p className="mt-1 text-sm text-slate-400">
          Select a niche. Cities rotate automatically. If DuckDuckGo finds too few emails, the
          agent switches sources until 20 valid leads are saved.
        </p>
      </div>

      <form
        className="flex flex-col gap-3 sm:flex-row"
        onSubmit={(event) => {
          event.preventDefault();
          onSubmit();
        }}
      >
        <label className="sr-only" htmlFor="niche-select">
          Niche
        </label>
        <select
          id="niche-select"
          value={niche}
          disabled={loading}
          onChange={(event) => onNicheChange(event.target.value)}
          className="h-12 min-w-[12rem] rounded-2xl border border-white/10 bg-slate-950/60 px-3 text-sm text-slate-100 outline-none focus:border-teal-400/40 focus:ring-4 focus:ring-teal-400/10 disabled:opacity-60"
        >
          {NICHES.map((item) => (
            <option key={item.id} value={item.id}>
              {item.label}
            </option>
          ))}
        </select>
        <label className="sr-only" htmlFor="city-select">
          City
        </label>
        <select
          id="city-select"
          value={city}
          disabled={loading}
          onChange={(event) => onCityChange(event.target.value)}
          className="h-12 min-w-[14rem] flex-1 rounded-2xl border border-white/10 bg-slate-950/60 px-3 text-sm text-slate-100 outline-none focus:border-teal-400/40 focus:ring-4 focus:ring-teal-400/10 disabled:opacity-60"
        >
          {CITIES.map((item) => (
            <option key={item.id} value={item.id}>
              {item.label}
            </option>
          ))}
        </select>
        <button
          type="submit"
          disabled={loading || !niche.trim()}
          className="inline-flex h-12 items-center justify-center gap-2 rounded-2xl bg-teal-400 px-5 text-sm font-semibold text-slate-950 transition hover:bg-teal-300 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {loading ? (
            <>
              <span className="h-4 w-4 animate-spin rounded-full border-2 border-slate-950/20 border-t-slate-950" />
              Finding 20 emails…
            </>
          ) : (
            "Find 20 emails"
          )}
        </button>
      </form>
    </section>
  );
}
