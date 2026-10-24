import type { ReactNode } from "react";
export function PageHeader({
  title,
  children,
  breadcrumb,
}: {
  title: string;
  children?: ReactNode;
  breadcrumb?: string;
}) {
  return (
    <header className="mb-6 flex items-end justify-between gap-4">
      <div>
        {breadcrumb && <p className="mb-1 text-xs text-muted">{breadcrumb}</p>}
        <h1 className="font-serif text-xl font-semibold tracking-tight">{title}</h1>
      </div>
      {children}
    </header>
  );
}
