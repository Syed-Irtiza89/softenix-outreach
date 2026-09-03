import { isCampaignEligible } from "../api";
import type { Lead } from "../types";

type LeadsTableProps = {
  leads: Lead[];
  loading: boolean;
  generatingId: string | null;
  selectedIds: string[];
  onToggle: (id: string) => void;
  onToggleAll: (checked: boolean) => void;
  onGenerate: (lead: Lead) => void;
  onReview: (lead: Lead) => void;
};

function StatusBadge({ status }: { status: Lead["status"] }) {
  if (status === "sent" || status === "opened") {
    return (
      <span className="inline-flex items-center rounded-full bg-teal-400/15 px-2.5 py-1 text-[11px] font-medium uppercase tracking-wide text-teal-300">
        {status === "opened" ? "Sent · Opened" : "Sent"}
      </span>
    );
  }
  if (status === "dryrun") {
    return (
      <span className="inline-flex items-center rounded-full bg-violet-400/15 px-2.5 py-1 text-[11px] font-medium uppercase tracking-wide text-violet-300">
        Dry-Run
      </span>
    );
  }
  if (status === "generating") {
    return (
      <span className="inline-flex items-center rounded-full bg-amber-400/15 px-2.5 py-1 text-[11px] font-medium uppercase tracking-wide text-amber-300">
        Pending
      </span>
    );
  }
  if (status === "failed") {
    return (
      <span className="inline-flex items-center rounded-full bg-rose-400/15 px-2.5 py-1 text-[11px] font-medium uppercase tracking-wide text-rose-300">
        Pending
      </span>
    );
  }
  return (
    <span className="inline-flex items-center rounded-full bg-white/8 px-2.5 py-1 text-[11px] font-medium uppercase tracking-wide text-slate-300">
      Pending
    </span>
  );
}

export function LeadsTable({
  leads,
  loading,
  generatingId,
  selectedIds,
  onToggle,
  onToggleAll,
  onGenerate,
  onReview,
}: LeadsTableProps) {
  const selectable = leads.filter(isCampaignEligible);
  const allSelected = selectable.length > 0 && selectable.every((lead) => selectedIds.includes(lead.id));

  return (
    <section className="overflow-hidden rounded-3xl border border-white/10 bg-white/[0.03]">
      <div className="border-b border-white/8 px-5 py-4 sm:px-6">
        <h2 className="text-sm font-medium text-white">Leads</h2>
        <p className="mt-1 text-sm text-slate-400">
          {leads.length === 0
            ? "Run a scrape to fill this table."
            : `${selectedIds.length} selected · ${leads.length} with a working email`}
        </p>
      </div>

      <div className="overflow-x-auto">
        <table className="min-w-full text-left text-sm">
          <thead className="bg-white/[0.02] text-[11px] uppercase tracking-[0.16em] text-slate-500">
            <tr>
              <th className="w-12 px-5 py-3 sm:px-6">
                <input
                  type="checkbox"
                  checked={allSelected}
                  onChange={(event) => onToggleAll(event.target.checked)}
                  disabled={selectable.length === 0}
                  aria-label="Select all eligible leads"
                  className="h-4 w-4 rounded border-white/20 bg-slate-950 text-teal-400"
                />
              </th>
              <th className="px-5 py-3 font-medium sm:px-6">Company</th>
              <th className="px-5 py-3 font-medium sm:px-6">Name</th>
              <th className="px-5 py-3 font-medium sm:px-6">Title</th>
              <th className="px-5 py-3 font-medium sm:px-6">Direct Email</th>
              <th className="px-5 py-3 font-medium sm:px-6">Website</th>
              <th className="px-5 py-3 font-medium sm:px-6">Status</th>
              <th className="px-5 py-3 text-right font-medium sm:px-6">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-white/6">
            {loading && leads.length === 0 ? (
              <tr>
                <td colSpan={9} className="px-6 py-16 text-center text-slate-400">
                  <span className="mx-auto mb-3 block h-6 w-6 animate-spin rounded-full border-2 border-white/15 border-t-teal-300" />
                  Scraping listings and validating public emails…
                </td>
              </tr>
            ) : null}

            {!loading && leads.length === 0 ? (
              <tr>
                <td colSpan={9} className="px-6 py-16 text-center text-slate-500">
                  No valid leads yet. Ask the agent: “Get 5 plumbers in Austin TX from Apollo”.
                </td>
              </tr>
            ) : null}

            {leads.map((lead) => {
              const eligible = isCampaignEligible(lead);
              const checked = selectedIds.includes(lead.id);
              return (
                <tr key={lead.id} className="hover:bg-white/[0.02]">
                  <td className="px-5 py-4 sm:px-6">
                    <input
                      type="checkbox"
                      checked={checked}
                      disabled={!eligible}
                      onChange={() => onToggle(lead.id)}
                      aria-label={`Select ${lead.name}`}
                      className="h-4 w-4 rounded border-white/20 bg-slate-950 text-teal-400 disabled:opacity-30"
                    />
                  </td>
                  <td className="px-5 py-4 font-medium text-slate-100 sm:px-6">{lead.name}</td>
                  <td className="px-5 py-4 text-slate-200 sm:px-6">{lead.contactName || "—"}</td>
                  <td className="px-5 py-4 text-slate-300 sm:px-6">{lead.title || lead.rating || "—"}</td>
                  <td className="px-5 py-4 font-mono text-[13px] text-slate-300 sm:px-6">{lead.email}</td>
                  <td className="px-5 py-4 sm:px-6">
                    {lead.website ? (
                      <a
                        href={lead.website}
                        target="_blank"
                        rel="noreferrer"
                        className="inline-block max-w-[220px] truncate text-teal-300/90 hover:text-teal-200"
                      >
                        {lead.website.replace(/^https?:\/\//, "")}
                      </a>
                    ) : (
                      <span className="text-slate-500">—</span>
                    )}
                  </td>
                  <td className="px-5 py-4 sm:px-6">
                    <StatusBadge status={lead.status} />
                  </td>
                  <td className="px-5 py-4 sm:px-6">
                    <div className="flex flex-wrap justify-end gap-2">
                      <button
                        type="button"
                        disabled={lead.status === "sent" || lead.status === "opened" || generatingId === lead.id}
                        onClick={() => onGenerate(lead)}
                        className="inline-flex items-center justify-center rounded-xl border border-white/10 bg-white/5 px-3 py-2 text-xs font-semibold text-slate-100 transition hover:border-teal-300/30 hover:bg-teal-400/10 disabled:cursor-not-allowed disabled:opacity-40"
                      >
                        {generatingId === lead.id ? "Generating…" : "Generate AI Draft"}
                      </button>
                      <button
                        type="button"
                        onClick={() => onReview(lead)}
                        className="inline-flex items-center justify-center rounded-xl border border-teal-300/25 bg-teal-400/10 px-3 py-2 text-xs font-semibold text-teal-100 transition hover:bg-teal-400/20"
                      >
                        Review Email
                      </button>
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
