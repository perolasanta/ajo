import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { lazy, Suspense, useEffect, useState } from "react";
import { Link, Navigate, NavLink, Outlet, Route, Routes, useNavigate } from "react-router-dom";
import { LogOut, Menu, X } from "lucide-react";
import { ensureCsrf, request } from "./api/client";
import { Badge } from "./ui/Badge";
import { Skeleton } from "./ui/Feedback";

type Session = { user_id: string; email: string; roles: string[] };
const LoginPage = lazy(() => import("./pages/LoginPage"));
const MembersPage = lazy(() => import("./pages/MembersPage"));
const MemberDetailPage = lazy(() => import("./pages/MemberDetailPage"));
const DesignPage = lazy(() => import("./pages/DesignPage"));
function LoadingPage() {
  return (
    <main className="p-6">
      <Skeleton className="h-7 w-40" />
      <Skeleton className="mt-6 h-60 w-full" />
    </main>
  );
}
const navigation = [
  {
    name: "Dashboard",
    path: "/dashboard",
    roles: ["officer", "committee", "treasurer", "admin", "auditor"],
  },
  { name: "Members", path: "/members", roles: ["officer", "admin", "auditor"] },
  { name: "Loans", path: "/loans", roles: ["officer", "committee", "admin", "auditor"] },
  { name: "Disbursements", path: "/disbursements", roles: ["treasurer", "admin", "auditor"] },
  { name: "Payroll", path: "/payroll", roles: ["treasurer", "officer", "admin", "auditor"] },
  { name: "Audit", path: "/audit", roles: ["admin", "auditor"] },
];
function ProtectedShell() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [drawer, setDrawer] = useState(false);
  const session = useQuery({
    queryKey: ["session"],
    queryFn: () => request<Session>("/auth/me"),
    retry: false,
  });
  useEffect(() => {
    const expired = () => {
      queryClient.removeQueries({ queryKey: ["session"] });
      navigate("/login", { replace: true });
    };
    window.addEventListener("ajo:unauthenticated", expired);
    return () => window.removeEventListener("ajo:unauthenticated", expired);
  }, [navigate, queryClient]);
  if (session.isPending) return <LoadingPage />;
  if (session.isError || !session.data) return <Navigate to="/login" replace />;
  const staff = session.data.roles.some((role) => role !== "member");
  if (!staff) return <main className="p-8">Your account does not have officer access.</main>;
  const links = navigation.filter((item) =>
    item.roles.some((role) => session.data.roles.includes(role)),
  );
  async function logout() {
    try {
      await ensureCsrf();
      await request("/auth/logout", { method: "POST" });
    } finally {
      queryClient.clear();
      navigate("/login");
    }
  }
  const side = (
    <>
      <Link
        to="/dashboard"
        className="font-serif text-xl font-semibold text-primary"
        onClick={() => setDrawer(false)}
      >
        Ajo
      </Link>
      <p className="mt-1 text-xs text-muted">Cooperative ledger</p>
      <nav className="mt-7 space-y-1" aria-label="Main navigation">
        {links.map(({ name, path }) => (
          <NavLink
            key={path}
            to={path}
            onClick={() => setDrawer(false)}
            className={({ isActive }) =>
              `block rounded-control px-3 py-2 text-sm ${isActive ? "bg-primary-soft font-medium text-primary" : "text-ink hover:bg-bg"}`
            }
          >
            {name}
          </NavLink>
        ))}
      </nav>
    </>
  );
  return (
    <div className="min-h-screen lg:grid lg:grid-cols-[232px_1fr]">
      <aside className="hidden border-r border-border bg-surface p-5 lg:block">{side}</aside>
      {drawer && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <button
            className="absolute inset-0 bg-ink/30"
            onClick={() => setDrawer(false)}
            aria-label="Close menu"
          />
          <aside className="relative h-full w-[232px] bg-surface p-5 shadow-lg">
            {side}
            <button
              className="absolute right-3 top-3 p-2"
              onClick={() => setDrawer(false)}
              aria-label="Close menu"
            >
              <X className="size-5" />
            </button>
          </aside>
        </div>
      )}
      <div>
        <header className="flex min-h-16 items-center justify-between border-b border-border bg-surface px-4 lg:px-6">
          <button className="p-2 lg:hidden" onClick={() => setDrawer(true)} aria-label="Open menu">
            <Menu className="size-5" />
          </button>
          <p className="text-sm font-medium">Example Cooperative</p>
          <DropdownMenu.Root>
            <DropdownMenu.Trigger className="inline-flex items-center gap-2 rounded-control p-1.5 text-sm">
              <span className="hidden text-muted sm:inline">{session.data.email}</span>
              <Badge>{session.data.roles[0] ?? "member"}</Badge>
            </DropdownMenu.Trigger>
            <DropdownMenu.Portal>
              <DropdownMenu.Content
                align="end"
                className="z-50 mt-2 w-48 rounded-card border border-border bg-surface p-1 shadow-lg"
              >
                <DropdownMenu.Item
                  className="cursor-pointer rounded-control px-3 py-2 text-sm outline-none hover:bg-bg"
                  onSelect={() => void logout()}
                >
                  <LogOut className="mr-2 inline size-4" />
                  Log out
                </DropdownMenu.Item>
              </DropdownMenu.Content>
            </DropdownMenu.Portal>
          </DropdownMenu.Root>
        </header>
        <main className="mx-auto max-w-[1200px] p-4 sm:p-6">
          <Suspense fallback={<LoadingPage />}>
            <Outlet />
          </Suspense>
        </main>
      </div>
    </div>
  );
}
function ComingSoon() {
  return (
    <section className="max-w-lg">
      <h1 className="font-serif text-xl font-semibold">Coming soon</h1>
      <p className="mt-2 text-sm text-muted">
        This workspace is being prepared. Members is available now.
      </p>
    </section>
  );
}
export default function App() {
  return (
    <Routes>
      <Route
        path="/login"
        element={
          <Suspense fallback={<LoadingPage />}>
            <LoginPage />
          </Suspense>
        }
      />
      {import.meta.env.DEV && (
        <Route
          path="/design"
          element={
            <Suspense fallback={<LoadingPage />}>
              <DesignPage />
            </Suspense>
          }
        />
      )}
      <Route element={<ProtectedShell />}>
        <Route path="/members" element={<MembersPage />} />
        <Route path="/members/:memberId" element={<MemberDetailPage />} />
        <Route path="/:feature" element={<ComingSoon />} />
      </Route>
      <Route path="*" element={<Navigate to="/dashboard" replace />} />
    </Routes>
  );
}
