import * as DialogPrimitive from "@radix-ui/react-dialog";
import * as PopoverPrimitive from "@radix-ui/react-popover";
import * as TooltipPrimitive from "@radix-ui/react-tooltip";
import { X } from "lucide-react";
import type { PropsWithChildren, ReactNode } from "react";
import { Button } from "./Button";

export function Dialog({
  title,
  description,
  trigger,
  children,
  open,
  onOpenChange,
}: PropsWithChildren<{
  title: string;
  description?: string;
  trigger?: ReactNode;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
}>) {
  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Trigger asChild>{trigger}</DialogPrimitive.Trigger>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-40 bg-ink/30" />
        <DialogPrimitive.Content className="fixed left-1/2 top-1/2 z-50 w-[calc(100%-2rem)] max-w-lg -translate-x-1/2 -translate-y-1/2 rounded-card border border-border bg-surface p-5 shadow-lg">
          <div className="flex justify-between gap-4">
            <div>
              <DialogPrimitive.Title className="font-serif text-lg font-semibold">
                {title}
              </DialogPrimitive.Title>
              {description && (
                <DialogPrimitive.Description className="mt-1 text-sm text-muted">
                  {description}
                </DialogPrimitive.Description>
              )}
            </div>
            <DialogPrimitive.Close className="rounded-control p-1 text-muted" aria-label="Close">
              <X className="size-5" />
            </DialogPrimitive.Close>
          </div>
          <div className="mt-5">{children}</div>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}
export function ConfirmDialog({
  title,
  consequence,
  trigger,
  onConfirm,
}: {
  title: string;
  consequence: string;
  trigger: ReactNode;
  onConfirm: (reason: string) => void | Promise<void>;
}) {
  return (
    <Dialog title={title} description={consequence} trigger={trigger}>
      <ConfirmForm onConfirm={onConfirm} />
    </Dialog>
  );
}
function ConfirmForm({ onConfirm }: { onConfirm: (reason: string) => void | Promise<void> }) {
  return (
    <form
      className="space-y-4"
      onSubmit={(e) => {
        e.preventDefault();
        const reason = new FormData(e.currentTarget).get("reason")?.toString().trim();
        if (reason) void onConfirm(reason);
      }}
    >
      <label className="block text-sm font-medium">
        Reason
        <textarea
          required
          name="reason"
          className="mt-1.5 block min-h-24 w-full rounded-card border border-border p-3 text-sm"
          placeholder="Explain why this action is necessary."
        />
      </label>
      <Button type="submit" variant="danger">
        Confirm action
      </Button>
    </form>
  );
}
export function Tooltip({ content, children }: PropsWithChildren<{ content: ReactNode }>) {
  return (
    <TooltipPrimitive.Provider delayDuration={150}>
      <TooltipPrimitive.Root>
        <TooltipPrimitive.Trigger asChild>{children}</TooltipPrimitive.Trigger>
        <TooltipPrimitive.Portal>
          <TooltipPrimitive.Content
            className="z-50 max-w-xs rounded-control bg-ink px-2.5 py-1.5 text-xs text-white shadow-lg"
            sideOffset={6}
          >
            {content}
            <TooltipPrimitive.Arrow className="fill-ink" />
          </TooltipPrimitive.Content>
        </TooltipPrimitive.Portal>
      </TooltipPrimitive.Root>
    </TooltipPrimitive.Provider>
  );
}
export function Popover({ trigger, children }: PropsWithChildren<{ trigger: ReactNode }>) {
  return (
    <PopoverPrimitive.Root>
      <PopoverPrimitive.Trigger asChild>{trigger}</PopoverPrimitive.Trigger>
      <PopoverPrimitive.Portal>
        <PopoverPrimitive.Content
          className="z-50 w-80 rounded-card border border-border bg-surface p-4 text-sm shadow-lg"
          sideOffset={8}
        >
          {children}
        </PopoverPrimitive.Content>
      </PopoverPrimitive.Portal>
    </PopoverPrimitive.Root>
  );
}
export function WhyPopover({ label = "Why?", steps }: { label?: string; steps: string[] }) {
  return (
    <Popover
      trigger={
        <button
          type="button"
          className="text-sm font-medium text-primary underline underline-offset-2"
        >
          {label}
        </button>
      }
    >
      <p className="font-semibold">How this is calculated</p>
      <ol className="mt-2 list-decimal space-y-1 pl-4 text-muted">
        {steps.map((step) => (
          <li key={step}>{step}</li>
        ))}
      </ol>
    </Popover>
  );
}
