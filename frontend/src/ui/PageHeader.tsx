import type { ReactNode } from "react";
export function PageHeader({ title, children }: { title: string; children?: ReactNode }) { return <header className="mb-6 flex items-center justify-between gap-4"><h1 className="text-2xl font-semibold tracking-tight">{title}</h1>{children}</header>; }
