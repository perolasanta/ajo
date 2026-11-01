import * as Dialog from "@radix-ui/react-dialog";
import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { LogOut, Menu, X } from "lucide-react";
import { lazy, Suspense, useEffect, useState } from "react";
import { Link, Navigate, NavLink, Outlet, Route, Routes, useNavigate } from "react-router-dom";
import { request } from "./api/client";
import { visibleNavigation } from "./navigation";
import { canViewPayroll } from "./payrollPolicy.mjs";
import type { MemberListItem } from "./api/schema";
import { Avatar } from "./ui/Avatar";
import { Skeleton } from "./ui/Feedback";

type Session = { user_id: string; email: string; roles: string[] };

const LoginPage = lazy(() => import("./pages/LoginPage"));
const MembersPage = lazy(() => import("./pages/MembersPage"));
const MemberDetailPage = lazy(() => import("./pages/MemberDetailPage"));
const LoansPage = lazy(() => import("./pages/LoansPage"));
const NewLoanApplicationPage = lazy(() =>
  import("./pages/LoansPage").then((module) => ({ default: module.NewLoanApplicationPage })),
);
const LoanDetailPage = lazy(() => import("./pages/LoanDetailPage"));
const DisbursementsPage = lazy(() => import("./pages/DisbursementsPage"));
const DisbursementDetailPage = lazy(() => import("./pages/DisbursementDetailPage"));
const DesignPage = lazy(() => import("./pages/DesignPage"));
const PayrollPage = lazy(() => import("./pages/PayrollPage"));

const fakeMembers: MemberListItem[] = [
  {
    id: "ada",
    member_no: "AJ-0001",
    full_name: "Ada Okafor",
    phone_e164: "+234 803 000 0001",
    email: "ada@example.test",
    status: "approved",
    joined_on: "2025-01-01",
  },
  {
    id: "uche",
    member_no: "AJ-0002",
    full_name: "Uche Nwosu",
    phone_e164: "+234 803 000 0002",
    email: "uche@example.test",
    status: "awaiting confirmation",
    joined_on: "2025-02-01",
  },
  {
    id: "tunde",
    member_no: "AJ-0003",
    full_name: "Tunde Adebayo",
    phone_e164: "+234 803 000 0003",
    email: "tunde@example.test",
    status: "repaying",
    joined_on: "2025-03-01",
  },
];

function LoadingPage() {
  return (
    <main className="p-6">
      <Skeleton className="h-7 w-40" />
      <Skeleton className="mt-6 h-60 w-full" />
    </main>
  );
}

function Shell({
  session,
  onLogout,
  children,
}: {
  session: Session;
  onLogout?: () => void;
  children: React.ReactNode;
}) {
  const [drawer, setDrawer] = useState(false);
  const links = visibleNavigation(session.roles);
  const userName = session.email
    .split("@")[0]
    .replace(/\b\w/g, (character) => character.toUpperCase());
  const closeDrawer = () => setDrawer(false);
  const navigationContent = (
    <>
      <Link
        to="/dashboard"
        className="font-serif text-[30px] font-semibold leading-none tracking-[-0.04em] text-white"
        onClick={closeDrawer}
      >
        Ajo
      </Link>
      <p className="mt-2 text-xs text-white/65">Example Cooperative Society</p>
      <nav className="mt-8 space-y-1" aria-label="Main navigation">
        {links.map(({ name, path }) => (
          <NavLink
            key={path}
            to={path}
            onClick={closeDrawer}
            className={({ isActive }) =>
              `flex min-h-10 items-center justify-between rounded-control px-3 text-sm text-white/85 transition hover:bg-white/10 ${isActive ? "bg-white/12 font-medium text-white" : ""}`
            }
          >
            {name}
          </NavLink>
        ))}
      </nav>
      <div className="mt-auto flex items-center gap-3 border-t border-white/15 pt-4">
        <Avatar name={userName} className="bg-white/15 text-white" />
        <div className="min-w-0">
          <p className="truncate text-sm font-medium text-white">{userName}</p>
          <p className="text-xs text-white/65">{session.roles[0] ?? "member"}</p>
        </div>
      </div>
      {onLogout && (
        <button
          type="button"
          className="mt-3 flex min-h-10 items-center rounded-control px-3 text-sm text-white/85 transition hover:bg-white/10"
          onClick={() => {
            closeDrawer();
            onLogout();
          }}
        >
          <LogOut className="mr-2 size-4" />
          Log out
        </button>
      )}
    </>
  );

  return (
    <div className="min-h-[100dvh] bg-bg lg:grid lg:grid-cols-[240px_minmax(0,1fr)]">
      <aside className="hidden flex-col bg-sidebar p-5 lg:sticky lg:top-0 lg:flex lg:h-dvh lg:overflow-y-auto">
        {navigationContent}
      </aside>
      <div className="min-w-0">
        <header className="sticky top-0 z-30 flex h-14 items-center justify-between border-b border-border bg-[#F2F2EC]/95 px-4 backdrop-blur lg:hidden">
          <Dialog.Root open={drawer} onOpenChange={setDrawer}>
            <Dialog.Trigger asChild>
              <button
                type="button"
                className="grid size-11 place-items-center rounded-control"
                aria-label="Open navigation"
                aria-expanded={drawer}
              >
                <Menu className="size-5" />
              </button>
            </Dialog.Trigger>
            <Dialog.Portal>
              <Dialog.Overlay className="fixed inset-0 z-40 bg-[#0E2F2B]/55 data-[state=closed]:animate-out data-[state=open]:animate-in" />
              <Dialog.Content className="fixed inset-y-0 left-0 z-50 flex w-[min(86vw,300px)] flex-col bg-sidebar px-[max(1.25rem,env(safe-area-inset-left))] pb-[max(1.25rem,env(safe-area-inset-bottom))] pt-[max(1.25rem,env(safe-area-inset-top))] shadow-xl transition-transform duration-200 data-[state=closed]:-translate-x-full data-[state=open]:translate-x-0 motion-reduce:transition-none">
                <Dialog.Title className="sr-only">Navigation</Dialog.Title>
                <Dialog.Close asChild>
                  <button
                    type="button"
                    className="absolute right-3 top-3 grid size-11 place-items-center rounded-control text-white"
                    aria-label="Close navigation"
                  >
                    <X className="size-5" />
                  </button>
                </Dialog.Close>
                {navigationContent}
              </Dialog.Content>
            </Dialog.Portal>
          </Dialog.Root>
          <Link to="/dashboard" className="font-serif text-2xl font-semibold tracking-[-0.04em]">
            Ajo
          </Link>
          <UserMenu session={session} onLogout={onLogout} />
        </header>
        <main className="mx-auto min-w-0 max-w-[1200px] p-4 sm:p-6">{children}</main>
      </div>
    </div>
  );
}

function UserMenu({ session, onLogout }: { session: Session; onLogout?: () => void }) {
  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger className="inline-flex size-11 items-center justify-center rounded-full">
        <Avatar name={session.email} />
        <span className="sr-only">Open user menu</span>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content
          align="end"
          className="z-50 mt-2 w-48 rounded-card border border-border bg-surface p-1 shadow-lg"
        >
          <DropdownMenu.Item
            className="cursor-pointer rounded-control px-3 py-2 text-sm outline-none hover:bg-bg"
            onSelect={onLogout}
          >
            <LogOut className="mr-2 inline size-4" />
            Log out
          </DropdownMenu.Item>
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}

function ProtectedShell() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
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
  if (!session.data.roles.some((role) => role !== "member"))
    return <main className="p-8">Your account does not have officer access.</main>;
  const logout = async () => {
    try {
      await request("/auth/logout", { method: "POST" });
    } finally {
      queryClient.clear();
      navigate("/login", { replace: true });
    }
  };
  return (
    <Shell session={session.data} onLogout={() => void logout()}>
      <Suspense fallback={<LoadingPage />}>
        <Outlet />
      </Suspense>
    </Shell>
  );
}

function PayrollGate() {
  const session = useQuery({
    queryKey: ["session"],
    queryFn: () => request<Session>("/auth/me"),
    retry: false,
  });
  if (session.isPending) return <LoadingPage />;
  if (session.isError || !session.data) return <Navigate to="/login" replace />;
  if (!canViewPayroll(session.data.roles)) return <Navigate to="/dashboard" replace />;
  return <Outlet />;
}

function ComingSoon() {
  return (
    <section className="max-w-lg">
      <h1 className="font-serif text-[32px] font-semibold tracking-[-0.035em]">Coming soon</h1>
      <p className="mt-2 text-sm text-muted">
        This workspace is being prepared. Members is available now.
      </p>
    </section>
  );
}
function DesignShell() {
  return (
    <Shell
      session={{
        user_id: "design",
        email: "aisha.bello@example.test",
        roles: ["officer", "admin"],
      }}
    >
      <MembersPage fakeMembers={fakeMembers} />
    </Shell>
  );
}
function ResponsiveReview() {
  return (
    <main className="min-h-[100dvh] bg-bg p-4">
      <h1 className="font-serif text-[32px] font-semibold tracking-[-0.035em]">
        Responsive review
      </h1>
      <div className="mt-6 grid gap-6 overflow-x-auto pb-4 xl:grid-cols-2">
        {[320, 390, 768, 1280].map((width) => (
          <figure key={width} className="w-fit">
            <figcaption className="mb-2 text-sm font-medium">{width}px</figcaption>
            <iframe
              title={`Shell at ${width}px`}
              src="/design/shell"
              className="h-[760px] border border-border bg-bg"
              style={{ width }}
            />
          </figure>
        ))}
      </div>
    </main>
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
        <>
          <Route
            path="/design"
            element={
              <Suspense fallback={<LoadingPage />}>
                <DesignPage />
              </Suspense>
            }
          />
          <Route
            path="/design/shell"
            element={
              <Suspense fallback={<LoadingPage />}>
                <DesignShell />
              </Suspense>
            }
          />
          <Route path="/design/responsive" element={<ResponsiveReview />} />
        </>
      )}
      <Route element={<ProtectedShell />}>
        <Route path="/members" element={<MembersPage />} />
        <Route path="/members/:memberId" element={<MemberDetailPage />} />
        <Route path="/loans" element={<LoansPage />} />
        <Route path="/loans/new" element={<NewLoanApplicationPage />} />
        <Route path="/loans/:applicationId" element={<LoanDetailPage />} />
        <Route path="/disbursements" element={<DisbursementsPage />} />
        <Route path="/disbursements/:disbursementId" element={<DisbursementDetailPage />} />
        <Route element={<PayrollGate />}>
          <Route path="/payroll" element={<PayrollPage />} />
        </Route>
        <Route path="/:feature" element={<ComingSoon />} />
      </Route>
      <Route path="*" element={<Navigate to="/dashboard" replace />} />
    </Routes>
  );
}
