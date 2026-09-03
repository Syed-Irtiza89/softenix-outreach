type HeaderProps = {
  total: number;
  pending: number;
  sent: number;
  dryRun: boolean;
  campaignStarting: boolean;
  selectedCount: number;
  credits: Array<{
    service: string;
    remaining_day: number;
    daily_limit: number;
    remaining_month: number;
    monthly_limit: number;
  }>;
  autoSend: boolean;
  smtpReady: boolean;
  sentToday: number;
  dailyCap: number;
  inSendWindow: boolean;
  nextSendWindow: string;
  autosendError: string;
  easternNow: string;
  onDryRunChange: (value: boolean) => void;
  onLaunchCampaign: () => void;
};

function StatCard({ label, value }: { label: string; value: number }) {
  return (
    <div className="min-w-[7.5rem] rounded-2xl border border-white/10 bg-white/[0.04] px-4 py-3">
      <p className="text-[11px] font-medium uppercase tracking-[0.16em] text-slate-500">{label}</p>
      <p className="mt-1 text-2xl font-semibold tabular-nums tracking-tight text-white">{value}</p>
    </div>
  );
}

const CREDIT_LABELS: Record<string, string> = {
  apollo: "Apollo",
  hunter: "Hunter",
  snovio: "Snov",
  builtwith: "BuiltWith",
};

export function Header({
  total,
  pending,
  sent,
  dryRun,
  campaignStarting,
  selectedCount,
  credits,
  autoSend,
  smtpReady,
  sentToday,
  dailyCap,
  inSendWindow,
  nextSendWindow,
  autosendError,
  easternNow,
  onDryRunChange,
  onLaunchCampaign,
}: HeaderProps) {
  return (
    <header className="flex flex-col gap-6 border-b border-white/8 pb-8">
      <div className="flex flex-col gap-5 lg:flex-row lg:items-start lg:justify-between">
        <div className="flex items-start gap-4">
          <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl bg-teal-400/15 ring-1 ring-teal-300/25">
            <span className="text-lg font-semibold tracking-tight text-teal-300">S</span>
          </div>
          <div>
            <p className="text-[11px] font-medium uppercase tracking-[0.22em] text-slate-500">
              Softenix Solution
            </p>
            <h1 className="mt-1 text-2xl font-semibold tracking-tight text-white sm:text-[28px]">
              Softenix Outreach AI
            </h1>
            <p className="mt-1 text-sm text-slate-400">
              Select a niche below. The agent rotates US cities and saves 20 emails per run.
            </p>
          </div>
        </div>

        <div className="flex flex-col items-stretch gap-3 sm:flex-row sm:items-center">
          <label className="inline-flex cursor-pointer items-center justify-between gap-3 rounded-2xl border border-white/10 bg-white/[0.03] px-4 py-3">
            <span className="text-left">
              <span className="block text-xs font-semibold text-white">Dry Run Mode</span>
              <span className="block text-[11px] text-slate-500">Safety mode — log actions, no real sends</span>
            </span>
            <span className="relative inline-flex h-6 w-11 shrink-0 items-center">
              <input
                type="checkbox"
                className="peer sr-only"
                checked={dryRun}
                onChange={(event) => onDryRunChange(event.target.checked)}
              />
              <span className="h-6 w-11 rounded-full bg-slate-700 transition peer-checked:bg-teal-400 peer-focus-visible:ring-4 peer-focus-visible:ring-teal-400/20" />
              <span className="absolute left-0.5 h-5 w-5 rounded-full bg-white transition peer-checked:translate-x-5" />
            </span>
          </label>

          <button
            type="button"
            disabled={campaignStarting || selectedCount === 0}
            onClick={onLaunchCampaign}
            className="inline-flex h-12 items-center justify-center rounded-2xl bg-teal-400 px-5 text-sm font-semibold text-slate-950 transition hover:bg-teal-300 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {campaignStarting
              ? "Launching…"
              : `Launch Campaign for Selected Leads${selectedCount ? ` (${selectedCount})` : ""}`}
          </button>
        </div>
      </div>

      {credits.length > 0 ? (
        <div className="flex flex-wrap gap-2">
          {credits.map((item) => (
            <div
              key={item.service}
              className="rounded-full border border-white/10 bg-white/[0.04] px-3 py-1.5 text-[11px] text-slate-300"
              title={`${item.remaining_month} / ${item.monthly_limit} left this month`}
            >
              <span className="font-semibold text-white">{CREDIT_LABELS[item.service] || item.service}</span>
              {" · "}
              {item.remaining_day}/{item.daily_limit} today
            </div>
          ))}
        </div>
      ) : null}

      <div className="flex flex-wrap gap-3">
        <StatCard label="Total Leads" value={total} />
        <StatCard label="Pending" value={pending} />
        <StatCard label="Sent" value={sent} />
        <StatCard label="Sent today" value={sentToday} />
        {autoSend ? (
          <div className="flex max-w-xl items-center rounded-2xl border border-teal-400/20 bg-teal-400/10 px-4 py-3 text-sm text-teal-100">
            Auto-send is on: {dailyCap}/day via Gmail
            {inSendWindow
              ? ` · window open (${easternNow})`
              : nextSendWindow
                ? ` · waits until ${nextSendWindow}`
                : ""}
            {!smtpReady ? " · add a real Gmail App Password in .env, then restart." : ""}
            {autosendError ? ` · ${autosendError}` : ""}
          </div>
        ) : dryRun ? (
          <div className="flex items-center rounded-2xl border border-amber-400/20 bg-amber-400/10 px-4 py-3 text-sm text-amber-100">
            Safety mode is on. Campaigns will not send real emails.
          </div>
        ) : (
          <div className="flex items-center rounded-2xl border border-rose-400/20 bg-rose-400/10 px-4 py-3 text-sm text-rose-100">
            Live mode. Emails send via Gmail during US business hours.
          </div>
        )}
      </div>
    </header>
  );
}
