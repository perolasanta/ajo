import { useMemo, useRef, useState } from "react";
import { openBankStatementPicker } from "../bankStatementsUpload.mjs";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import { EmptyState } from "../ui/Feedback";
import { Money } from "../ui/Format";
import { PageHeader } from "../ui/PageHeader";

type StatementStatus = "suggested" | "ambiguous" | "unmatched" | "matched" | "ignored";

type StatementRecord = {
  id: string;
  line_date: string;
  narration: string;
  reference?: string | null;
  amount_kobo: number;
  status: StatementStatus;
  matched_payment_id?: string | null;
};

const demoRows: StatementRecord[] = [
  {
    id: "st-100",
    line_date: "2024-03-03",
    narration: "Transfer REF-2024-01",
    reference: "REF-2024-01",
    amount_kobo: 125000,
    status: "suggested",
    matched_payment_id: "pay-10",
  },
  {
    id: "st-101",
    line_date: "2024-03-05",
    narration: "Internet banking",
    reference: null,
    amount_kobo: 45000,
    status: "ambiguous",
    matched_payment_id: null,
  },
  {
    id: "st-102",
    line_date: "2024-03-08",
    narration: "Miscellaneous credit",
    reference: "MISC-990",
    amount_kobo: 78000,
    status: "matched",
    matched_payment_id: "pay-21",
  },
];

const filters: Array<{ value: StatementStatus | "all"; label: string }> = [
  { value: "all", label: "All" },
  { value: "suggested", label: "Suggested" },
  { value: "ambiguous", label: "Ambiguous" },
  { value: "unmatched", label: "Unmatched" },
  { value: "matched", label: "Matched" },
  { value: "ignored", label: "Ignored" },
];

export default function BankStatementsPage() {
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [selectedStatus, setSelectedStatus] = useState<StatementStatus | "all">("all");
  const [selectedFileName, setSelectedFileName] = useState<string | null>(null);
  const [rows, setRows] = useState<StatementRecord[]>(demoRows);
  const visibleRows = useMemo(
    () =>
      selectedStatus === "all"
        ? rows
        : rows.filter((entry) => entry.status === selectedStatus),
    [selectedStatus, rows],
  );

  const acceptRow = (rowId: string) => {
    setRows((current) =>
      current.map((row) =>
        row.id === rowId ? { ...row, status: "matched" as const, matched_payment_id: row.matched_payment_id ?? "pay-auto" } : row,
      ),
    );
  };

  const ignoreRow = (rowId: string) => {
    const note = window.prompt("Add a note for this ignored statement line:")?.trim();
    if (!note) return;
    setRows((current) =>
      current.map((row) =>
        row.id === rowId ? { ...row, status: "ignored" as const, matched_payment_id: null } : row,
      ),
    );
  };

  const userMayAccept = true;
  const handleUploadClick = () => {
    if (openBankStatementPicker(fileInputRef.current)) {
      return;
    }
    window.alert("The file upload control is unavailable. Refresh and try again.");
  };

  return (
    <div className="space-y-5">
      <PageHeader title="Bank statements" breadcrumb="Payments / Bank statements" />
      <Card>
        <div className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
          <div>
            <h2 className="text-lg font-semibold">Import statement</h2>
            <p className="mt-1 text-sm text-muted">
              Upload a generic CSV, map the columns, review the preview, and import only the verified evidence lines.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <input
              ref={fileInputRef}
              type="file"
              accept=".csv,text/csv,application/vnd.ms-excel"
              className="hidden"
              aria-label="Upload bank statement CSV"
              onClick={(event) => {
                event.currentTarget.value = "";
              }}
              onChange={(event) => {
                const file = event.target.files?.[0];
                setSelectedFileName(file ? file.name : null);
              }}
            />
            <Button type="button" onClick={handleUploadClick}>
              Upload CSV
            </Button>
          </div>
        </div>
        {selectedFileName && (
          <p className="mt-3 text-sm text-muted">Selected file: {selectedFileName}</p>
        )}
        <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          <label className="text-sm">
            Date column
            <input className="mt-1 w-full rounded-control border border-border bg-white p-2" value="Date" readOnly />
          </label>
          <label className="text-sm">
            Narration column
            <input className="mt-1 w-full rounded-control border border-border bg-white p-2" value="Narration" readOnly />
          </label>
          <label className="text-sm">
            Credit column
            <input className="mt-1 w-full rounded-control border border-border bg-white p-2" value="Credit" readOnly />
          </label>
          <label className="text-sm">
            Reference column
            <input className="mt-1 w-full rounded-control border border-border bg-white p-2" value="Reference" readOnly />
          </label>
        </div>
      </Card>

      <div className="flex flex-wrap gap-2">
        {filters.map((filter) => (
          <Button
            key={filter.value}
            type="button"
            variant={selectedStatus === filter.value ? "primary" : "secondary"}
            onClick={() => setSelectedStatus(filter.value)}
          >
            {filter.label}
          </Button>
        ))}
      </div>

      {visibleRows.length === 0 ? (
        <EmptyState
          title="No statement lines found"
          detail="Upload a file or match a new statement to create suggestions."
        />
      ) : (
        <div className="space-y-3">
          {visibleRows.map((row) => (
            <Card key={row.id}>
              <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                <div>
                  <div className="flex items-center gap-2">
                    <strong>{row.narration}</strong>
                    <span className="rounded-full bg-[#E8F4F1] px-2 py-1 text-xs font-medium text-[#15332E]">
                      {row.status}
                    </span>
                  </div>
                  <p className="mt-1 text-sm text-muted">
                    {row.line_date} · {row.reference ?? "No reference"}
                  </p>
                </div>
                <div className="text-right">
                  <div className="font-medium text-primary">
                    <Money kobo={row.amount_kobo} />
                  </div>
                  <div className="mt-1 flex gap-2">
                    {row.status === "suggested" && userMayAccept && (
                      <Button type="button" onClick={() => acceptRow(row.id)}>
                        Accept
                      </Button>
                    )}
                    {row.status !== "matched" && (
                      <Button type="button" variant="secondary" onClick={() => ignoreRow(row.id)}>
                        Ignore
                      </Button>
                    )}
                  </div>
                </div>
              </div>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
