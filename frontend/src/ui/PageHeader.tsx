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
    <header className="mb-6 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
      <div className="min-w-0">
        {breadcrumb && <p className="mb-1 text-xs text-muted">{breadcrumb}</p>}
        <h1 className="font-serif text-[32px] font-semibold leading-none tracking-[-0.035em]">
          {title}
        </h1>
      </div>
      {children && <div className="w-full sm:w-auto">{children}</div>}
    </header>
  );
}
