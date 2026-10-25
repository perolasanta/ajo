import { cva, type VariantProps } from "class-variance-authority";
import { LoaderCircle } from "lucide-react";
import type { ButtonHTMLAttributes, PropsWithChildren } from "react";
import { cn } from "../lib";

const buttonStyles = cva(
  "inline-flex h-10 items-center justify-center gap-2 rounded-control px-3.5 font-semibold transition duration-150 ease-out disabled:cursor-not-allowed disabled:bg-stone-200 disabled:text-muted max-sm:min-h-11",
  {
    variants: {
      variant: {
        primary: "bg-primary text-white hover:bg-primary-hover",
        secondary: "border border-border bg-surface text-ink hover:bg-bg",
        ghost: "text-ink hover:bg-bg",
        danger: "bg-danger text-white hover:bg-[#8f1d15]",
      },
      size: {
        sm: "h-8 px-2.5 text-xs max-sm:min-h-11",
        md: "px-3.5 text-sm",
        lg: "px-5 text-body",
      },
    },
    defaultVariants: { variant: "primary", size: "md" },
  },
);

export type ButtonProps = PropsWithChildren<
  ButtonHTMLAttributes<HTMLButtonElement> &
    VariantProps<typeof buttonStyles> & { loading?: boolean }
>;

export function Button({
  children,
  className,
  variant,
  size,
  loading = false,
  disabled,
  ...props
}: ButtonProps) {
  return (
    <button
      className={cn(
        buttonStyles({ variant, size }),
        loading && "!bg-primary !text-white",
        className,
      )}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...props}
    >
      {loading && <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />}
      {children}
    </button>
  );
}
