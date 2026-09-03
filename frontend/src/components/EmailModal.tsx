import type { Lead } from "../types";

type EmailModalProps = {
  lead: Lead;
  subject: string;
  body: string;
  saving: boolean;
  onSubjectChange: (value: string) => void;
  onBodyChange: (value: string) => void;
  onClose: () => void;
  onSave: () => void;
};

export function EmailModal({
  lead,
  subject,
  body,
  saving,
  onSubjectChange,
  onBodyChange,
  onClose,
  onSave,
}: EmailModalProps) {
  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-slate-950/70 p-4 backdrop-blur-sm sm:items-center">
      <button type="button" className="absolute inset-0 cursor-default" aria-label="Close modal" onClick={onClose} />
      <div className="relative z-10 w-full max-w-2xl rounded-3xl border border-white/10 bg-[#12141c] p-6 shadow-2xl">
        <div className="mb-5 flex items-start justify-between gap-4">
          <div>
            <p className="text-[11px] font-medium uppercase tracking-[0.18em] text-slate-500">Review & edit email</p>
            <h2 className="mt-1 text-lg font-semibold text-white">{lead.name}</h2>
            <p className="mt-2 text-sm text-slate-400">
              {lead.email}
              {lead.rating ? ` · Rating ${lead.rating}` : ""}
            </p>
            {lead.website ? (
              <a
                href={lead.website}
                target="_blank"
                rel="noreferrer"
                className="mt-1 inline-block text-sm text-teal-300 hover:text-teal-200"
              >
                {lead.website.replace(/^https?:\/\//, "")}
              </a>
            ) : null}
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-xl px-2 py-1 text-sm text-slate-400 hover:bg-white/5 hover:text-white"
          >
            Close
          </button>
        </div>

        <label className="mb-2 block text-xs font-medium uppercase tracking-wide text-slate-500" htmlFor="draft-subject">
          Subject
        </label>
        <input
          id="draft-subject"
          value={subject}
          onChange={(event) => onSubjectChange(event.target.value)}
          className="mb-4 h-11 w-full rounded-2xl border border-white/10 bg-slate-950/70 px-4 text-sm text-slate-100 outline-none focus:border-teal-400/40 focus:ring-4 focus:ring-teal-400/10"
        />

        <label className="mb-2 block text-xs font-medium uppercase tracking-wide text-slate-500" htmlFor="draft-body">
          Email draft
        </label>
        <textarea
          id="draft-body"
          value={body}
          onChange={(event) => onBodyChange(event.target.value)}
          rows={14}
          className="w-full resize-y rounded-2xl border border-white/10 bg-slate-950/70 px-4 py-3 text-sm leading-6 text-slate-100 outline-none focus:border-teal-400/40 focus:ring-4 focus:ring-teal-400/10"
        />

        <div className="mt-5 flex flex-col-reverse gap-3 sm:flex-row sm:justify-end">
          <button
            type="button"
            onClick={onClose}
            className="h-11 rounded-2xl border border-white/10 px-4 text-sm font-medium text-slate-300 hover:bg-white/5"
          >
            Cancel
          </button>
          <button
            type="button"
            disabled={saving || !body.trim()}
            onClick={onSave}
            className="inline-flex h-11 items-center justify-center gap-2 rounded-2xl bg-teal-400 px-5 text-sm font-semibold text-slate-950 hover:bg-teal-300 disabled:opacity-50"
          >
            {saving ? (
              <>
                <span className="h-4 w-4 animate-spin rounded-full border-2 border-slate-950/20 border-t-slate-950" />
                Saving…
              </>
            ) : (
              "Save Edits"
            )}
          </button>
        </div>
      </div>
    </div>
  );
}
