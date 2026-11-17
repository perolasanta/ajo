import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState, type ChangeEvent } from "react";
import {
  ApiError,
  acceptBankStatementLine,
  getBankStatementLines,
  ignoreBankStatementLine,
  importBankStatementFile,
  matchBankStatementLines,
  previewBankStatementImport,
  request,
  undoBankStatementDecision,
} from "../api/client";
import type {
  BankStatementColumnMap,
  BankStatementImportResult,
  BankStatementPreview,
  BankStatementStatus,
} from "../api/client";
import {
  bankStatementImportErrorMessage,
  formatBankStatementImportNotice,
  openBankStatementPicker,
  parseBankStatementHeaders,
  suggestBankStatementMapping,
} from "../bankStatementsUpload.mjs";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import { EmptyState, Skeleton } from "../ui/Feedback";
import { Money } from "../ui/Format";
import { PageHeader } from "../ui/PageHeader";

const pageSize = 25;
const maxUploadBytes = 2 * 1024 * 1024;
type Session = { roles: string[] };
type StatementFilter = BankStatementStatus | "all";

const filters: Array<{ value: StatementFilter; label: string }> = [
  { value: "all", label: "All" },
  { value: "suggested", label: "Suggested" },
  { value: "unmatched", label: "Unmatched" },
  { value: "matched", label: "Matched" },
  { value: "ignored", label: "Ignored" },
];

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

function StatementRowsSkeleton() {
  return (
    <div className="space-y-3" aria-label="Loading bank statement lines">
      {[0, 1, 2].map((index) => (
        <Skeleton key={index} className="h-[88px] w-full" />
      ))}
    </div>
  );
}

function PreviewIssues({ preview }: { preview: BankStatementPreview }) {
  if (!preview.errors.length) return null;
  return (
    <div className="mt-3 rounded-control border border-amber-300 bg-amber-50 p-3 text-sm text-amber-950">
      <p className="font-medium">Some CSV rows need attention</p>
      <ul className="mt-1 list-disc pl-5">
        {preview.errors.map((issue, index) => (
          <li key={`${issue.row}-${index}`}>
            Row {issue.row}: {issue.message ?? issue.error ?? "Invalid row"}
          </li>
        ))}
      </ul>
    </div>
  );
}

export default function BankStatementsPage() {
  const queryClient = useQueryClient();
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [selectedStatus, setSelectedStatus] = useState<StatementFilter>("all");
  const [page, setPage] = useState(1);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [headers, setHeaders] = useState<string[]>([]);
  const [columnMap, setColumnMap] = useState<BankStatementColumnMap>({
    date: "",
    narration: "",
    amount: "",
    credit: "",
    debit: "",
    reference: "",
  });
  const [preview, setPreview] = useState<BankStatementPreview | null>(null);
  const [importResult, setImportResult] = useState<BankStatementImportResult | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);

  const session = useQuery({
    queryKey: ["session"],
    queryFn: () => request<Session>("/auth/me"),
    retry: false,
  });
  const roles = session.data?.roles ?? [];
  const canManage = roles.includes("officer") || roles.includes("treasurer");
  const lines = useQuery({
    queryKey: ["bank-statement-lines", selectedStatus, page],
    queryFn: () =>
      getBankStatementLines({
        page,
        pageSize,
        status: selectedStatus === "all" ? undefined : selectedStatus,
      }),
    retry: false,
  });
  const invalidateLines = () =>
    queryClient.invalidateQueries({ queryKey: ["bank-statement-lines"] });

  const previewMutation = useMutation({
    mutationFn: () => {
      if (!selectedFile) throw new Error("Choose a CSV file first.");
      return previewBankStatementImport(selectedFile, columnMap);
    },
    onSuccess: (result) => {
      setPreview(result);
      setImportResult(null);
      setNotice(null);
    },
  });
  const importMutation = useMutation({
    mutationFn: () => {
      if (!selectedFile) throw new Error("Choose a CSV file first.");
      return importBankStatementFile(selectedFile, columnMap);
    },
    onSuccess: async (result) => {
      setImportResult(result);
      setNotice(formatBankStatementImportNotice(result));
      await invalidateLines();
    },
  });
  const matchMutation = useMutation({
    mutationFn: matchBankStatementLines,
    onSuccess: async (result) => {
      setNotice(
        `Matching complete: ${result.matched_count} suggestions found; ${result.ambiguous_count} lines need review.`,
      );
      await invalidateLines();
    },
  });
  const acceptMutation = useMutation({
    mutationFn: (lineId: string) => acceptBankStatementLine(lineId, crypto.randomUUID()),
    onSuccess: invalidateLines,
  });
  const ignoreMutation = useMutation({
    mutationFn: ({ lineId, note }: { lineId: string; note: string }) =>
      ignoreBankStatementLine(lineId, note),
    onSuccess: invalidateLines,
  });
  const undoMutation = useMutation({
    mutationFn: undoBankStatementDecision,
    onSuccess: invalidateLines,
  });

  const mappingIsValid =
    Boolean(columnMap.date && columnMap.narration) &&
    Boolean(columnMap.amount || columnMap.credit || columnMap.debit);
  const totalPages = Math.max(1, Math.ceil((lines.data?.total ?? 0) / pageSize));
  const isBusy =
    previewMutation.isPending ||
    importMutation.isPending ||
    matchMutation.isPending ||
    acceptMutation.isPending ||
    ignoreMutation.isPending ||
    undoMutation.isPending;
  const actionError =
    acceptMutation.error ?? ignoreMutation.error ?? undoMutation.error ?? matchMutation.error;

  const updateColumn = (key: keyof BankStatementColumnMap, value: string) => {
    setColumnMap((current) => ({ ...current, [key]: value }));
    setPreview(null);
    setImportResult(null);
  };

  const handleFileChange = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0] ?? null;
    setSelectedFile(file);
    setHeaders([]);
    setPreview(null);
    setImportResult(null);
    setNotice(null);
    setFileError(null);
    if (!file) return;
    if (file.size > maxUploadBytes) {
      setSelectedFile(null);
      setFileError("Bank statement CSV files must be 2 MiB or smaller.");
      return;
    }
    try {
      const csvHeaders = parseBankStatementHeaders(await file.text());
      if (!csvHeaders.length) {
        setSelectedFile(null);
        setFileError("The CSV file must start with a header row.");
        return;
      }
      setHeaders(csvHeaders);
      setColumnMap(suggestBankStatementMapping(csvHeaders));
    } catch {
      setSelectedFile(null);
      setFileError("The CSV header could not be read. Check the file encoding and try again.");
    }
  };

  const handleIgnore = (lineId: string) => {
    const note = window.prompt("Add a note for this ignored statement line:")?.trim();
    if (note) ignoreMutation.mutate({ lineId, note });
  };

  const renderColumnSelect = (
    label: string,
    key: keyof BankStatementColumnMap,
    required = false,
  ) => (
    <label className="text-sm font-medium">
      {label}{required ? " (required)" : " (optional)"}
      <select
        className="mt-1 min-h-11 w-full rounded-control border border-border bg-white px-3"
        value={columnMap[key] ?? ""}
        onChange={(event) => updateColumn(key, event.target.value)}
        disabled={!canManage || !headers.length || isBusy}
      >
        <option value="">Not mapped</option>
        {headers.map((header, index) => (
          <option key={`${index}-${header}`} value={header}>
            {header || `Column ${index + 1}`}
          </option>
        ))}
      </select>
    </label>
  );

  return (
    <div className="space-y-5">
      <PageHeader title="Bank statements" breadcrumb="Payments / Bank statements" />
      <Card>
        <div className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
          <div>
            <h2 className="text-lg font-semibold">Import statement</h2>
            <p className="mt-1 max-w-2xl text-sm text-muted">
              Upload a CSV, map its columns, review the server preview, then import evidence lines.
              Imports never post to the ledger. Accepting a suggested line uses the existing payment
              confirmation and maker-checker checks.
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
              onChange={handleFileChange}
            />
            <Button
              type="button"
              disabled={!canManage || isBusy}
              onClick={() => {
                if (!openBankStatementPicker(fileInputRef.current)) {
                  setFileError("The file upload control is unavailable. Refresh and try again.");
                }
              }}
            >
              Choose CSV
            </Button>
          </div>
        </div>
        {selectedFile && (
          <p className="mt-3 text-sm text-muted" role="status">
            Selected file: {selectedFile.name}
          </p>
        )}
        {fileError && (
          <p className="mt-3 text-sm text-danger" role="alert">
            {fileError}
          </p>
        )}
        {!canManage && !session.isPending && (
          <p className="mt-3 text-sm text-muted" role="status">
            Only officers and treasurers can import or decide bank statement lines.
          </p>
        )}
        <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {renderColumnSelect("Date column", "date", true)}
          {renderColumnSelect("Narration column", "narration", true)}
          {renderColumnSelect("Signed amount column", "amount")}
          {renderColumnSelect("Credit column", "credit")}
          {renderColumnSelect("Debit column", "debit")}
          {renderColumnSelect("Reference column", "reference")}
        </div>
        {selectedFile && (
          <div className="mt-4 flex flex-wrap gap-2">
            <Button
              type="button"
              variant="secondary"
              disabled={!canManage || !mappingIsValid || isBusy}
              loading={previewMutation.isPending}
              onClick={() => previewMutation.mutate()}
            >
              Preview CSV
            </Button>
            {preview && (
              <Button
                type="button"
                disabled={
                  !canManage ||
                  preview.total_rows === 0 ||
                  preview.duplicate_file ||
                  isBusy
                }
                loading={importMutation.isPending}
                onClick={() => importMutation.mutate()}
              >
                Import {preview.total_rows} valid rows
              </Button>
            )}
          </div>
        )}
        {previewMutation.isError && (
          <p className="mt-3 text-sm text-danger" role="alert">
            {errorMessage(previewMutation.error, "Unable to preview the CSV.")}
          </p>
        )}
        {importMutation.isError && (
          <p className="mt-3 text-sm text-danger" role="alert">
            {bankStatementImportErrorMessage(
              importMutation.error instanceof ApiError ? importMutation.error.message : undefined,
              importMutation.error instanceof ApiError
                ? importMutation.error.requestId
                : undefined,
            )}
          </p>
        )}
        {preview?.duplicate_file && (
          <p className="mt-3 text-sm text-amber-900" role="status">
            This file has already been imported. It will not be imported again.
          </p>
        )}
        {preview && (
          <>
            <p className="mt-3 text-sm text-muted" role="status">
              Preview: {preview.total_rows} valid rows in {preview.filename}.
            </p>
            {!importResult && <PreviewIssues preview={preview} />}
            {preview.preview.length > 0 && (
              <div className="mt-3 overflow-x-auto">
                <table className="w-full min-w-[40rem] text-left text-sm">
                  <thead>
                    <tr className="border-b border-border text-muted">
                      <th className="py-2 pr-3">CSV row</th>
                      <th className="py-2 pr-3">Date</th>
                      <th className="py-2 pr-3">Narration</th>
                      <th className="py-2 pr-3">Credit</th>
                      <th className="py-2 pr-3">Debit</th>
                    </tr>
                  </thead>
                  <tbody>
                    {preview.preview.map((row) => (
                      <tr key={row.row} className="border-b border-border">
                        <td className="py-2 pr-3">{row.row}</td>
                        <td className="py-2 pr-3">{row.date}</td>
                        <td className="py-2 pr-3">{row.narration}</td>
                        <td className="py-2 pr-3"><Money kobo={row.credit_kobo} /></td>
                        <td className="py-2 pr-3"><Money kobo={row.debit_kobo} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
        {importResult?.errors.length ? (
          <PreviewIssues preview={{ ...preview!, errors: importResult.errors }} />
        ) : null}
        {notice && (
          <p className="mt-3 text-sm text-primary" role="status">
            {notice}
          </p>
        )}
      </Card>

      <div className="flex flex-wrap items-center gap-2">
        {filters.map((filter) => (
          <Button
            key={filter.value}
            type="button"
            variant={selectedStatus === filter.value ? "primary" : "secondary"}
            onClick={() => {
              setSelectedStatus(filter.value);
              setPage(1);
            }}
          >
            {filter.label}
          </Button>
        ))}
        <Button
          type="button"
          variant="secondary"
          disabled={!canManage || isBusy}
          loading={matchMutation.isPending}
          onClick={() => matchMutation.mutate()}
        >
          Match pending payments
        </Button>
      </div>

      {actionError && (
        <p className="text-sm text-danger" role="alert">
          {errorMessage(actionError, "The statement action could not be completed.")}
        </p>
      )}
      {lines.isPending ? (
        <StatementRowsSkeleton />
      ) : lines.isError ? (
        <EmptyState
          title="Statement lines could not be loaded"
          detail={errorMessage(lines.error, "Please try again.")}
          action={<Button onClick={() => void lines.refetch()}>Try again</Button>}
        />
      ) : lines.data?.items.length === 0 ? (
        <EmptyState
          title="No statement lines found"
          detail="Import a CSV or match pending payments to create suggestions."
        />
      ) : (
        <>
          <div className="space-y-3">
            {lines.data?.items.map((row) => (
              <Card key={row.id}>
                <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                  <div>
                    <div className="flex items-center gap-2">
                      <strong>{row.narration}</strong>
                      <span className="rounded-full bg-[#E8F4F1] px-2 py-1 text-xs font-medium text-[#15332E]">
                        {row.status.replaceAll("_", " ")}
                      </span>
                    </div>
                    <p className="mt-1 text-sm text-muted">
                      {row.line_date ?? "Date unavailable"} · {row.reference ?? "No reference"}
                    </p>
                  </div>
                  <div className="text-right">
                    <div className="font-medium text-primary">
                      <Money kobo={row.credit_kobo || row.debit_kobo} />
                      {row.debit_kobo > 0 && <span className="ml-1 text-xs text-muted">debit evidence</span>}
                    </div>
                    <div className="mt-1 flex flex-wrap justify-end gap-2">
                      {row.status === "suggested" && canManage && (
                        <Button
                          type="button"
                          disabled={isBusy}
                          loading={acceptMutation.isPending}
                          onClick={() => acceptMutation.mutate(row.id)}
                        >
                          Accept suggestion
                        </Button>
                      )}
                      {(row.status === "unmatched" || row.status === "suggested") && canManage && (
                        <Button
                          type="button"
                          variant="secondary"
                          disabled={isBusy}
                          loading={ignoreMutation.isPending}
                          onClick={() => handleIgnore(row.id)}
                        >
                          Ignore
                        </Button>
                      )}
                      {(row.status === "matched" || row.status === "ignored") && canManage && (
                        <Button
                          type="button"
                          variant="secondary"
                          disabled={isBusy}
                          loading={undoMutation.isPending}
                          onClick={() => undoMutation.mutate(row.id)}
                        >
                          Undo decision
                        </Button>
                      )}
                    </div>
                  </div>
                </div>
              </Card>
            ))}
          </div>
          {(lines.data?.total ?? 0) > 0 && (
            <div className="mt-4 flex items-center justify-between gap-3 text-sm">
              <span>{lines.data?.total} statement lines · Page {page} of {totalPages}</span>
              <div className="flex gap-2">
                <Button
                  type="button"
                  variant="secondary"
                  disabled={page <= 1 || lines.isFetching}
                  onClick={() => setPage((current) => current - 1)}
                >
                  Previous
                </Button>
                <Button
                  type="button"
                  variant="secondary"
                  disabled={page >= totalPages || lines.isFetching}
                  onClick={() => setPage((current) => current + 1)}
                >
                  Next
                </Button>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
