import { useQuery } from "@tanstack/react-query";
import { formatNaira, getJson } from "./lib";

type Health = { status: string; env: string; version: string };

export default function App() {
  const health = useQuery({ queryKey: ["health"], queryFn: () => getJson<Health>("/api/v1/health") });

  return (
    <main className="mx-auto max-w-3xl p-6">
      <h1 className="text-2xl font-semibold tracking-tight">Ajo</h1>
      <p className="mt-1 text-slate-600">Cooperative management you can trust.</p>

      <section className="mt-6 rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
        <h2 className="text-sm font-medium text-slate-500">API status</h2>
        {health.isPending && <p className="mt-2">Checking…</p>}
        {health.isError && <p className="mt-2 text-red-700">API not reachable. Start it with <code>make api</code>.</p>}
        {health.data && (
          <p className="mt-2">
            <span className="inline-block h-2 w-2 rounded-full bg-emerald-500" /> {health.data.status} · {health.data.env} ·
            build {health.data.version}
          </p>
        )}
        <p className="mt-4 text-sm text-slate-500">Money check: {formatNaira(125_000_000)}</p>
      </section>
    </main>
  );
}
