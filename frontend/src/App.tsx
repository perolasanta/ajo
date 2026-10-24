import { useQuery, useQueryClient } from "@tanstack/react-query";
import { lazy, Suspense, useEffect } from "react";
import { Link, Navigate, NavLink, Outlet, Route, Routes, useNavigate } from "react-router-dom";
import { ensureCsrf, request } from "./api/client";
import { Button } from "./ui/Button";
type Session = { user_id: string; email: string; roles: string[] };
const LoginPage = lazy(() => import("./pages/LoginPage"));
const MembersPage = lazy(() => import("./pages/MembersPage"));
const MemberDetailPage = lazy(() => import("./pages/MemberDetailPage"));
function LoadingPage() { return <main className="p-8 text-sm text-slate-500">Loading…</main>; }
function ProtectedShell() {
  const navigate = useNavigate(); const queryClient = useQueryClient();
  const session = useQuery({ queryKey: ["session"], queryFn: () => request<Session>("/auth/me"), retry: false });
  useEffect(() => { const expired = () => { queryClient.removeQueries({ queryKey: ["session"] }); navigate("/login", { replace: true }); }; window.addEventListener("ajo:unauthenticated", expired); return () => window.removeEventListener("ajo:unauthenticated", expired); }, [navigate, queryClient]);
  if (session.isPending) return <LoadingPage />;
  if (session.isError || !session.data) return <Navigate to="/login" replace />;
  const staff = session.data.roles.some((role) => ["officer", "committee", "treasurer", "admin", "auditor"].includes(role));
  if (!staff) return <main className="p-8">Your account does not have officer access.</main>;
  const links = [["Members", "/members", true], ["Loans", "/loans", false], ["Disbursements", "/disbursements", false], ["Payroll", "/payroll", false], ["Audit", "/audit", false]] as const;
  async function logout() { await ensureCsrf(); await request("/auth/logout", { method: "POST" }); queryClient.clear(); navigate("/login"); }
  return <div className="min-h-screen md:grid md:grid-cols-[15rem_1fr]"><aside className="border-b border-slate-200 bg-white p-4 md:border-b-0 md:border-r"><Link to="/members" className="text-xl font-semibold text-brand-700">Ajo</Link><nav className="mt-8 flex gap-1 overflow-x-auto md:block" aria-label="Main navigation">{links.map(([name, path, ready]) => <NavLink key={path} to={path} className={({ isActive }) => `block rounded-lg px-3 py-2 text-sm ${ready && isActive ? "bg-brand-600 text-white" : "text-slate-700 hover:bg-slate-100"}`}>{name}</NavLink>)}</nav><div className="mt-8 border-t border-slate-200 pt-4 text-sm text-slate-600"><p className="truncate">{session.data.email}</p><Button className="mt-3 w-full bg-slate-700 hover:bg-slate-800" onClick={() => void logout()}>Log out</Button></div></aside><main className="p-5 md:p-8"><Suspense fallback={<LoadingPage />}><Outlet /></Suspense></main></div>;
}
function ComingSoon() { return <section><h1 className="text-2xl font-semibold">Coming next</h1><p className="mt-2 text-slate-600">This workspace is being prepared.</p></section>; }
export default function App() { return <Routes><Route path="/login" element={<Suspense fallback={<LoadingPage />}><LoginPage /></Suspense>} /><Route element={<ProtectedShell />}><Route path="/members" element={<MembersPage />} /><Route path="/members/:memberId" element={<MemberDetailPage />} /><Route path="/:feature" element={<ComingSoon />} /></Route><Route path="*" element={<Navigate to="/members" replace />} /></Routes>; }
