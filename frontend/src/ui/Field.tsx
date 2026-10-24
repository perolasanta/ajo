import { AlertTriangle } from "lucide-react";
import {
  cloneElement,
  isValidElement,
  type PropsWithChildren,
  type ReactElement,
  type ReactNode,
} from "react";

type FieldControlProps = {
  id?: string;
  "aria-invalid"?: boolean | "true" | "false";
  "aria-describedby"?: string;
};

export function Field({
  label,
  helper,
  error,
  children,
}: PropsWithChildren<{ label: string; helper?: ReactNode; error?: ReactNode }>) {
  const id = label.toLowerCase().replace(/[^a-z0-9]+/g, "-");
  const descriptionId = `${id}-${error ? "error" : "helper"}`;
  const control = isValidElement<FieldControlProps>(children)
    ? cloneElement(children as ReactElement<FieldControlProps>, {
        id: children.props.id ?? id,
        "aria-invalid": error ? true : children.props["aria-invalid"],
        "aria-describedby": error || helper ? descriptionId : children.props["aria-describedby"],
      })
    : children;

  return (
    <div className="space-y-1.5">
      <label className="block text-sm font-medium" htmlFor={id}>
        {label}
      </label>
      {control}
      {error ? (
        <p id={descriptionId} className="flex items-center gap-1 text-xs text-danger" role="alert">
          <AlertTriangle className="size-3.5" aria-hidden="true" />
          {error}
        </p>
      ) : helper ? (
        <p id={descriptionId} className="text-xs text-muted">
          {helper}
        </p>
      ) : null}
    </div>
  );
}
