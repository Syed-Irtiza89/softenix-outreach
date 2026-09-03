type AgentCommandProps = {
  prompt: string;
  loading: boolean;
  techStack: string[];
  onPromptChange: (value: string) => void;
  onSubmit: () => void;
};

export function AgentCommand({ prompt, loading, techStack, onPromptChange, onSubmit }: AgentCommandProps) {
  return (
    <section className="rounded-3xl border border-teal-300/20 bg-teal-400/[0.04] p-5 sm:p-6">
      <div className="mb-4">
        <h2 className="text-sm font-medium text-white">AI agent command</h2>
        <p className="mt-1 text-sm text-slate-400">
          Example: optional Apollo/Hunter command. Daily leads: use the niche dropdown below.
        </p>
      </div>
      <form
        className="flex flex-col gap-3 sm:flex-row"
        onSubmit={(event) => {
          event.preventDefault();
          onSubmit();
        }}
      >
        <label className="sr-only" htmlFor="agent-command">
          Agent command
        </label>
        <input
          id="agent-command"
          value={prompt}
          onChange={(event) => onPromptChange(event.target.value)}
          disabled={loading}
          placeholder='Go to Apollo and get 5 plumbers in Austin TX'
          className="h-12 flex-1 rounded-2xl border border-white/10 bg-slate-950/60 px-4 text-sm text-slate-100 outline-none placeholder:text-slate-500 transition focus:border-teal-400/40 focus:ring-4 focus:ring-teal-400/10 disabled:opacity-60"
        />
        <button
          type="submit"
          disabled={loading || !prompt.trim()}
          className="inline-flex h-12 items-center justify-center gap-2 rounded-2xl bg-teal-400 px-5 text-sm font-semibold text-slate-950 transition hover:bg-teal-300 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {loading ? (
            <>
              <span className="h-4 w-4 animate-spin rounded-full border-2 border-slate-950/20 border-t-slate-950" />
              Running…
            </>
          ) : (
            "Run agent"
          )}
        </button>
      </form>
      {techStack.length > 0 ? (
        <div className="mt-4 flex flex-wrap gap-2">
          {techStack.map((name) => (
            <span
              key={name}
              className="rounded-full border border-white/10 bg-white/[0.04] px-2.5 py-1 text-[11px] text-slate-300"
            >
              {name}
            </span>
          ))}
        </div>
      ) : null}
    </section>
  );
}
