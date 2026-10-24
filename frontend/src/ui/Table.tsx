import type { PropsWithChildren, TableHTMLAttributes } from "react";
export function Table({ children, className = "", ...props }: PropsWithChildren<TableHTMLAttributes<HTMLTableElement>>) { return <div className="overflow-x-auto"><table className={`w-full text-left text-sm tabular-nums ${className}`} {...props}>{children}</table></div>; }
