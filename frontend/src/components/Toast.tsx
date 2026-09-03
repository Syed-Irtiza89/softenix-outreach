import type { ToastMessage } from "../types";

type ToastProps = {
  toasts: ToastMessage[];
};

export function Toast({ toasts }: ToastProps) {
  if (toasts.length === 0) return null;

  return (
    <div className="fixed right-4 top-4 z-[60] flex w-[min(24rem,calc(100vw-2rem))] flex-col gap-2">
      {toasts.map((toast) => (
        <div
          key={toast.id}
          className={`rounded-2xl border px-4 py-3 text-sm shadow-xl ${
            toast.tone === "success"
              ? "border-teal-400/25 bg-[#10251f] text-teal-100"
              : toast.tone === "error"
                ? "border-rose-400/25 bg-[#2a1418] text-rose-100"
                : "border-white/10 bg-[#1a1d27] text-slate-100"
          }`}
        >
          {toast.text}
        </div>
      ))}
    </div>
  );
}
