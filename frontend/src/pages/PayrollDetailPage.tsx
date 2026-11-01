import * as Dialog from "@radix-ui/react-dialog";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState, type ChangeEvent } from "react";
import { Link, useParams } from "react-router-dom";
import {
  ApiError,
  downloadExpectedPayrollCSV,
  dryRunPayrollFile,
  getPayrollRun,
  importPayrollFile,
  postPayrollRun,
  request,
  signOffPayrollRun,
} from "../api/client";
import type { PayrollDryRunResponse } from "../api/schema";
import {
  payrollFileSha256,
  nairaInputToKobo,
  payrollRunActions,
  payrollSignOffReady,
  payrollStepIndex,
  payrollSteps,
  uploadPayrollFile,
  validatePayrollFile,
} from "../payrollPolicy.mjs";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { EmptyState, Skeleton, Stepper } from "../ui/Feedback";
import { DateTime, Money } from "../ui/Format";
import { PageHeader } from "../ui/PageHeader";

type Session = { user_id: string; roles: string[] };

function DetailError({ error, retry }: { error: unknown; retry: () => void }) {
  const message = error instanceof ApiError ? error.message : "Please try again.";
  return (
    <EmptyState
      title="Payroll run could not be loaded"
      detail={`${message}${error instanceof ApiError && error.requestId ? ` Request ID: ${error.requestId}` : ""}`}
      action={<Button onClick={retry}>Try again</Button>}
    />
  );
}

function errorMessage(error: unknown, fallback: string) {
  return error instanceof Error ? error.message : fallback;
}

function downloadBase64Report(encoded: string, filename: string, mediaType: string) {
  const binary = window.atob(encoded);
  const bytes = Uint8Array.from(binary, (character) => character.charCodeAt(0));
  const url = URL.createObjectURL(new Blob([bytes], { type: mediaType }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export default function PayrollDetailPage() {
  const { runId } = useParams();
  const queryClient = useQueryClient();
  const selectionVersion = useRef(0);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [selectedFileSha256, setSelectedFileSha256] = useState<string | null>(null);
  const [dryRunResult, setDryRunResult] = useState<PayrollDryRunResponse | null>(null);
  const [dryRunSha256, setDryRunSha256] = useState<string | null>(null);
  const [fileValidationError, setFileValidationError] = useState<string | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const [downloadPending, setDownloadPending] = useState(false);
  const [postDialogOpen, setPostDialogOpen] = useState(false);
  const [signOffDialogOpen, setSignOffDialogOpen] = useState(false);
  const [bankReceiptInput, setBankReceiptInput] = useState("");
  const [postSuccess, setPostSuccess] = useState<string | null>(null);
  const [signOffSuccess, setSignOffSuccess] = useState<string | null>(null);
  const session = useQuery({
    queryKey: ["session"],
    queryFn: () => request<Session>("/auth/me"),
    retry: false,
  });
  const run = useQuery({
    queryKey: ["payroll-run", runId],
    queryFn: () => getPayrollRun(runId ?? ""),
    enabled: Boolean(runId),
  });
  const dryRunMutation = useMutation({
    mutationFn: async (file: File) => {
      const localHash = await payrollFileSha256(file);
      const result = await uploadPayrollFile(file, (validFile) =>
        dryRunPayrollFile(runId ?? "", validFile),
      );
      if (result.file_sha256 !== localHash) {
        throw new Error(
          "The server file hash did not match the selected CSV. Select the file again.",
        );
      }
      return { result, localHash };
    },
    onSuccess: ({ result, localHash }) => {
      setDryRunResult(result);
      setDryRunSha256(result.file_sha256);
      setSelectedFileSha256(localHash);
      setUploadError(null);
    },
  });
  const importMutation = useMutation({
    mutationFn: async (file: File) => {
      if (!dryRunResult || !dryRunSha256 || dryRunSha256 !== selectedFileSha256) {
        throw new Error("Run and review a dry-run of this exact CSV before importing.");
      }
      const localHash = await payrollFileSha256(file);
      if (localHash !== dryRunResult.file_sha256) {
        throw new Error(
          "This file differs from the dry-run. Run the dry-run again before importing.",
        );
      }
      return uploadPayrollFile(file, (validFile) => importPayrollFile(runId ?? "", validFile));
    },
    onSuccess: async (result) => {
      if (result.errors.length > 0) {
        setUploadError(
          "Import was not completed. Review the returned row errors and run a new dry-run.",
        );
        return;
      }
      setUploadError(null);
      await queryClient.invalidateQueries({ queryKey: ["payroll-run", runId] });
      await queryClient.invalidateQueries({ queryKey: ["payroll-runs"] });
    },
  });
  const postMutation = useMutation({
    mutationFn: () => postPayrollRun(runId ?? ""),
    onSuccess: async ({ posted }) => {
      setPostSuccess(`Payroll posted. ${posted} repayment${posted === 1 ? "" : "s"} processed.`);
      setPostDialogOpen(false);
      await queryClient.invalidateQueries({ queryKey: ["payroll-run", runId] });
      await queryClient.invalidateQueries({ queryKey: ["payroll-runs"] });
    },
  });
  const signOffMutation = useMutation({
    mutationFn: (bankTotalKobo: number) => signOffPayrollRun(runId ?? "", bankTotalKobo),
    onSuccess: async (reports) => {
      const period = `${run.data?.period_year ?? "payroll"}-${String(run.data?.period_month ?? "").padStart(2, "0")}`;
      downloadBase64Report(reports.csv_base64, `${period}-sign-off.csv`, "text/csv;charset=utf-8");
      downloadBase64Report(reports.json_base64, `${period}-sign-off.json`, "application/json");
      setSignOffSuccess("Payroll signed off. The CSV and JSON sign-off reports were downloaded.");
      setSignOffDialogOpen(false);
      await queryClient.invalidateQueries({ queryKey: ["payroll-run", runId] });
      await queryClient.invalidateQueries({ queryKey: ["payroll-runs"] });
    },
  });

  const selectFile = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0] ?? null;
    selectionVersion.current += 1;
    const thisSelection = selectionVersion.current;
    setSelectedFile(file);
    setSelectedFileSha256(null);
    setDryRunResult(null);
    setDryRunSha256(null);
    setFileValidationError(null);
    setUploadError(null);
    dryRunMutation.reset();
    importMutation.reset();
    if (!file) return;
    const error = validatePayrollFile(file);
    if (error) {
      setFileValidationError(error);
      return;
    }
    void payrollFileSha256(file)
      .then((hash) => {
        if (selectionVersion.current === thisSelection) setSelectedFileSha256(hash);
      })
      .catch((error: unknown) => {
        if (selectionVersion.current === thisSelection) {
          setFileValidationError(errorMessage(error, "Unable to verify the selected file."));
        }
      });
  };

  const downloadExpected = async () => {
    setDownloadError(null);
    setDownloadPending(true);
    try {
      const blob = await downloadExpectedPayrollCSV(runId ?? "");
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `payroll-${run.data?.period_year}-${String(run.data?.period_month).padStart(2, "0")}-expected.csv`;
      document.body.append(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) {
      setDownloadError(errorMessage(error, "Unable to download the expected CSV."));
    } finally {
      setDownloadPending(false);
    }
  };

  if (run.isPending || session.isPending) {
    return (
      <div className="space-y-4" aria-label="Loading payroll run">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-20 w-full" />
        <Skeleton className="h-48 w-full" />
      </div>
    );
  }
  if (run.isError) return <DetailError error={run.error} retry={() => void run.refetch()} />;
  if (session.isError || !session.data) {
    return <EmptyState title="Session unavailable" detail="Sign in again to continue." />;
  }
  const data = run.data;
  const dryRunMatches = Boolean(
    dryRunResult &&
    selectedFile &&
    selectedFileSha256 &&
    dryRunSha256 === dryRunResult.file_sha256 &&
    selectedFileSha256 === dryRunResult.file_sha256,
  );
  const dryRunHasErrors = Boolean(dryRunResult && dryRunResult.errors.length > 0);
  const actions = payrollRunActions(
    session.data.roles,
    session.data.user_id,
    data,
    dryRunMatches && !dryRunHasErrors,
  );
  const classificationCounts = dryRunResult?.rows.reduce<Record<string, number>>((counts, row) => {
    counts[row.classification] = (counts[row.classification] ?? 0) + 1;
    return counts;
  }, {});
  const bankReceiptKobo = nairaInputToKobo(bankReceiptInput);
  const signOffVarianceKobo =
    data.file_total_kobo !== null && bankReceiptKobo !== null
      ? data.file_total_kobo - bankReceiptKobo
      : null;
  const unresolvedExceptions = data.exceptions.filter(
    (exception) => exception.classification !== "matched" && exception.resolution_note === null,
  );
  const signOffReady = payrollSignOffReady(data.file_total_kobo, bankReceiptKobo, data.exceptions);

  return (
    <>
      <PageHeader
        title={`Payroll run ${data.period_year}-${String(data.period_month).padStart(2, "0")}`}
        breadcrumb="Monthly deduction run"
      />
      <Link className="mb-4 inline-block text-sm text-primary hover:underline" to="/payroll">
        Back to payroll runs
      </Link>
      <div className="mb-6 rounded-card border border-border bg-surface p-4 sm:p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-sm text-muted">Run status</p>
          <Badge status={data.status} />
        </div>
        <div className="mt-5 overflow-hidden">
          <Stepper steps={payrollSteps} current={payrollStepIndex(data.status, dryRunMatches)} />
        </div>
      </div>

      <section className="rounded-card border border-border bg-surface p-4 sm:p-5">
        <h2 className="font-serif text-xl font-semibold">Reconciliation summary</h2>
        <dl className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
          <div>
            <dt className="text-xs text-muted">Expected total</dt>
            <dd className="mt-1 font-medium">
              <Money kobo={data.expected_total_kobo} />
            </dd>
          </div>
          <div>
            <dt className="text-xs text-muted">Imported file total</dt>
            <dd className="mt-1 font-medium">
              {data.file_total_kobo === null ? (
                "Not imported"
              ) : (
                <Money kobo={data.file_total_kobo} />
              )}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-muted">Bank receipt total</dt>
            <dd className="mt-1 font-medium">
              {data.bank_receipt_total_kobo === null ? (
                "Not recorded"
              ) : (
                <Money kobo={data.bank_receipt_total_kobo} />
              )}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-muted">Receipt variance (file − receipt)</dt>
            <dd className="mt-1 font-medium">
              {data.variance_kobo === null ? "Not reconciled" : <Money kobo={data.variance_kobo} />}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-muted">Loan component</dt>
            <dd className="mt-1 font-medium">
              <Money kobo={data.component_totals_kobo.loan} />
            </dd>
          </div>
          <div>
            <dt className="text-xs text-muted">Savings component</dt>
            <dd className="mt-1 font-medium">
              <Money kobo={data.component_totals_kobo.savings} />
            </dd>
          </div>
          <div>
            <dt className="text-xs text-muted">Dues component</dt>
            <dd className="mt-1 font-medium">
              <Money kobo={data.component_totals_kobo.dues} />
            </dd>
          </div>
        </dl>
        <div className="mt-5 border-t border-border pt-4">
          <h3 className="text-sm font-semibold">Row counts</h3>
          <dl className="mt-2 grid grid-cols-2 gap-3 text-sm sm:grid-cols-3">
            {Object.entries(data.counts).map(([classification, count]) => (
              <div key={classification} className="flex justify-between gap-3">
                <dt className="capitalize text-muted">{classification.replaceAll("_", " ")}</dt>
                <dd className="font-medium tabular-nums">{count}</dd>
              </div>
            ))}
          </dl>
        </div>
      </section>

      {actions.canExportExpected && (
        <section className="mt-6 rounded-card border border-border bg-surface p-4 sm:p-5">
          <h2 className="font-serif text-xl font-semibold">Payroll file</h2>
          <p className="mt-1 text-sm text-muted">
            Upload a CSV up to 5 MiB. The dry-run result is held in this page only; after a reload,
            run it again before importing.
          </p>
          <div className="mt-4 flex flex-wrap gap-2">
            <Button
              type="button"
              variant="secondary"
              loading={downloadPending}
              onClick={() => void downloadExpected()}
            >
              Download expected CSV
            </Button>
          </div>
          {downloadError && (
            <p className="mt-2 text-sm text-danger" role="alert">
              {downloadError}
            </p>
          )}
          <label className="mt-4 block text-sm font-medium">
            Payroll CSV file
            <input
              className="mt-1 block min-h-11 w-full rounded-control border border-border bg-surface px-3 py-2 text-sm file:mr-3 file:rounded-control file:border-0 file:bg-primary-soft file:px-3 file:py-1 file:font-medium"
              type="file"
              accept=".csv,text/csv"
              onClick={(event) => {
                event.currentTarget.value = "";
              }}
              onChange={selectFile}
              disabled={dryRunMutation.isPending || importMutation.isPending}
              aria-describedby="payroll-upload-help"
            />
          </label>
          <p id="payroll-upload-help" className="mt-1 text-xs text-muted">
            Only .csv files with a text/csv content type are accepted. Maximum size: 5 MiB (5 × 1024
            × 1024 bytes).
          </p>
          {selectedFile && !fileValidationError && (
            <p className="mt-2 break-all text-xs text-muted">
              Selected: {selectedFile.name}
              {selectedFileSha256 ? ` · SHA-256 ${selectedFileSha256}` : " · Verifying file…"}
            </p>
          )}
          {fileValidationError && (
            <p className="mt-2 text-sm text-danger" role="alert">
              {fileValidationError}
            </p>
          )}
          <div className="mt-4 flex flex-wrap gap-2">
            <Button
              type="button"
              variant="secondary"
              loading={dryRunMutation.isPending}
              disabled={
                !selectedFile ||
                !selectedFileSha256 ||
                Boolean(fileValidationError) ||
                importMutation.isPending
              }
              onClick={() => {
                if (selectedFile) {
                  setUploadError(null);
                  setDryRunResult(null);
                  setDryRunSha256(null);
                  dryRunMutation.mutate(selectedFile);
                }
              }}
            >
              Run dry-run
            </Button>
            <Button
              type="button"
              disabled={
                !actions.canImport ||
                !selectedFile ||
                dryRunMutation.isPending ||
                importMutation.isPending
              }
              loading={importMutation.isPending}
              onClick={() => {
                if (selectedFile) {
                  setUploadError(null);
                  importMutation.mutate(selectedFile);
                }
              }}
            >
              Import this file
            </Button>
          </div>
          {uploadError && (
            <p className="mt-3 text-sm text-danger" role="alert">
              {uploadError}
            </p>
          )}
          {dryRunMutation.isError && (
            <p className="mt-3 text-sm text-danger" role="alert">
              {errorMessage(dryRunMutation.error, "The dry-run failed. Try again.")}
            </p>
          )}
          {importMutation.isError && (
            <p className="mt-3 text-sm text-danger" role="alert">
              {errorMessage(importMutation.error, "The import failed. Try again.")}
            </p>
          )}
          {importMutation.data?.errors.length ? (
            <div
              className="mt-3 rounded-control border border-danger/30 bg-danger-soft p-3"
              role="alert"
            >
              <p className="font-medium">Import did not complete; review these row errors.</p>
              <ul className="mt-2 space-y-1 text-sm">
                {importMutation.data.errors.map((item, index) => (
                  <li key={`${item.row}-${index}`}>
                    Row {item.row}: {item.error}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          {dryRunResult && dryRunMatches && (
            <div
              className="mt-5 rounded-card border border-border bg-bg p-4"
              aria-label="Dry-run result"
            >
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <h3 className="font-semibold">Dry-run result</h3>
                  <p className="mt-1 text-xs text-muted">
                    Not saved. This result is shown only in this page session and must be re-run
                    after reload.
                  </p>
                </div>
                <Badge status={dryRunHasErrors ? "disputed" : "ready"}>
                  {dryRunHasErrors ? "Errors found" : "Ready to review"}
                </Badge>
              </div>
              <dl className="mt-4 grid grid-cols-1 gap-3 text-sm sm:grid-cols-3">
                <div>
                  <dt className="text-muted">Expected total</dt>
                  <dd className="mt-1 font-medium">
                    <Money kobo={data.expected_total_kobo} />
                  </dd>
                </div>
                <div>
                  <dt className="text-muted">File total</dt>
                  <dd className="mt-1 font-medium">
                    <Money kobo={dryRunResult.file_total_kobo} />
                  </dd>
                </div>
                <div>
                  <dt className="text-muted">Variance (file − expected)</dt>
                  <dd className="mt-1 font-medium">
                    <Money kobo={dryRunResult.file_total_kobo - data.expected_total_kobo} />
                  </dd>
                </div>
              </dl>
              <div className="mt-4 flex flex-wrap gap-2 text-xs">
                {Object.entries(classificationCounts ?? {}).map(([classification, count]) => (
                  <span
                    key={classification}
                    className="rounded-full border border-border bg-surface px-2 py-1"
                  >
                    {classification.replaceAll("_", " ")}: {count}
                  </span>
                ))}
                <span className="rounded-full border border-border bg-surface px-2 py-1">
                  Errors: {dryRunResult.errors.length}
                </span>
              </div>
              <h4 className="mt-5 text-sm font-semibold">Row classifications</h4>
              {dryRunResult.rows.length === 0 ? (
                <p className="mt-2 text-sm text-muted">No parsed data rows.</p>
              ) : (
                <div className="mt-2 max-h-[28rem] space-y-2 overflow-y-auto pr-1">
                  {dryRunResult.rows.map((row, index) => (
                    <article
                      key={`${row.row}-${index}`}
                      className="rounded-control border border-border bg-surface p-3"
                    >
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <p className="font-medium">
                          Row {row.row} ·{" "}
                          <span className="break-all">
                            {row.staff_no_raw || "(blank staff number)"}
                          </span>
                        </p>
                        <Badge status={row.classification} />
                      </div>
                      <p className="mt-2 text-sm text-muted">
                        Actual amount: <Money kobo={row.actual_kobo} />
                      </p>
                    </article>
                  ))}
                </div>
              )}
              {dryRunResult.errors.length > 0 && (
                <div className="mt-4 rounded-control border border-danger/30 bg-danger-soft p-3">
                  <h4 className="font-semibold">Errors by row</h4>
                  <ul className="mt-2 space-y-1 text-sm">
                    {dryRunResult.errors.map((item, index) => (
                      <li key={`${item.row}-${index}`}>
                        Row {item.row}: {item.error}
                      </li>
                    ))}
                  </ul>
                  <p className="mt-3 text-sm font-medium">
                    Import stays disabled until the dry-run has no errors.
                  </p>
                </div>
              )}
              {dryRunHasErrors && !dryRunMutation.isPending && (
                <p className="mt-3 text-sm text-danger">
                  Correct the source CSV and select it again to run a fresh dry-run.
                </p>
              )}
            </div>
          )}
        </section>
      )}

      <section className="mt-6 rounded-card border border-border bg-surface p-4 sm:p-5">
        <h2 className="font-serif text-xl font-semibold">Persisted exceptions</h2>
        <p className="mt-1 text-sm text-muted">
          Only imported exception fields are stored and shown. Original file row numbers are not
          retained.
        </p>
        {data.exceptions.length === 0 ? (
          <p className="mt-4 text-sm text-muted">No persisted payroll exceptions.</p>
        ) : (
          <div className="mt-4 space-y-3">
            {data.exceptions.map((exception, index) => (
              <article
                key={`${exception.staff_no_raw}-${exception.classification}-${index}`}
                className="rounded-control border border-border p-3"
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <p className="break-all font-medium">{exception.staff_no_raw}</p>
                  <Badge status={exception.classification} />
                </div>
                <dl className="mt-3 grid gap-2 text-sm sm:grid-cols-2">
                  <div className="flex justify-between gap-3">
                    <dt className="text-muted">Actual amount</dt>
                    <dd>
                      {exception.actual_kobo === null ? (
                        "—"
                      ) : (
                        <Money kobo={exception.actual_kobo} />
                      )}
                    </dd>
                  </div>
                  {exception.resolution_note && (
                    <div className="sm:col-span-2">
                      <dt className="text-muted">Resolution note</dt>
                      <dd className="mt-1 break-words">{exception.resolution_note}</dd>
                    </div>
                  )}
                </dl>
              </article>
            ))}
          </div>
        )}
      </section>

      <section className="mt-6 rounded-card border border-border bg-surface p-4 sm:p-5">
        <h2 className="font-serif text-xl font-semibold">Available actions</h2>
        {actions.canPost && (
          <div className="mt-3">
            <p className="text-sm text-muted">
              Posting applies eligible payroll repayments. Confirm the totals and component split
              first.
            </p>
            <Button
              className="mt-3"
              type="button"
              onClick={() => {
                postMutation.reset();
                setPostSuccess(null);
                setPostDialogOpen(true);
              }}
            >
              Post run
            </Button>
          </div>
        )}
        {session.data.roles.includes("admin") && data.status === "imported" && (
          <p className="mt-2 text-sm text-muted">
            Posting is reserved for treasurers; administrators cannot post runs.
          </p>
        )}
        {actions.canSignOff && (
          <div className="mt-3">
            <p className="text-sm text-muted">
              Review the bank receipt total, variance, and persisted exceptions before sign-off.
            </p>
            <Button
              className="mt-3"
              type="button"
              onClick={() => {
                signOffMutation.reset();
                setBankReceiptInput("");
                setSignOffSuccess(null);
                setSignOffDialogOpen(true);
              }}
            >
              Sign off run
            </Button>
          </div>
        )}
        {actions.signOffBlockedBySelf && (
          <p className="mt-2 text-sm text-muted">
            A different administrator must sign off because you imported or posted this run.
          </p>
        )}
        {actions.signOffBlockedMissingPoster && (
          <p className="mt-2 text-sm text-muted">
            Sign-off is unavailable because the original importer or poster is not recorded.
          </p>
        )}
        {!actions.canExportExpected &&
          !actions.canPost &&
          !actions.canSignOff &&
          !actions.signOffBlockedBySelf &&
          !actions.signOffBlockedMissingPoster &&
          !(session.data.roles.includes("admin") && data.status === "imported") && (
            <p className="mt-2 text-sm text-muted">
              No actions are available for your role in this run state.
            </p>
          )}
        {postSuccess && (
          <p className="mt-3 text-sm text-success" role="status">
            {postSuccess}
          </p>
        )}
        {signOffSuccess && (
          <p className="mt-3 text-sm text-success" role="status">
            {signOffSuccess}
          </p>
        )}

        <Dialog.Root open={postDialogOpen} onOpenChange={setPostDialogOpen}>
          <Dialog.Portal>
            <Dialog.Overlay className="fixed inset-0 z-40 bg-ink/50" />
            <Dialog.Content className="fixed left-1/2 top-1/2 z-50 max-h-[90dvh] w-[min(92vw,38rem)] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-card border border-border bg-surface p-4 shadow-xl sm:p-6">
              <Dialog.Title className="font-serif text-xl font-semibold">
                Confirm payroll posting
              </Dialog.Title>
              <Dialog.Description className="mt-1 text-sm text-muted">
                Posting is a treasurer action. Review these totals before confirming.
              </Dialog.Description>
              <dl className="mt-5 grid grid-cols-1 gap-3 text-sm sm:grid-cols-2">
                <div>
                  <dt className="text-muted">Expected total</dt>
                  <dd className="mt-1 font-medium">
                    <Money kobo={data.expected_total_kobo} />
                  </dd>
                </div>
                <div>
                  <dt className="text-muted">Imported file total</dt>
                  <dd className="mt-1 font-medium">
                    {data.file_total_kobo === null ? (
                      "Not imported"
                    ) : (
                      <Money kobo={data.file_total_kobo} />
                    )}
                  </dd>
                </div>
                <div>
                  <dt className="text-muted">Loan component</dt>
                  <dd className="mt-1 font-medium">
                    <Money kobo={data.component_totals_kobo.loan} />
                  </dd>
                </div>
                <div>
                  <dt className="text-muted">Savings component</dt>
                  <dd className="mt-1 font-medium">
                    <Money kobo={data.component_totals_kobo.savings} />
                  </dd>
                </div>
                <div>
                  <dt className="text-muted">Dues component</dt>
                  <dd className="mt-1 font-medium">
                    <Money kobo={data.component_totals_kobo.dues} />
                  </dd>
                </div>
              </dl>
              {postMutation.isError && (
                <p className="mt-4 text-sm text-danger" role="alert">
                  {errorMessage(postMutation.error, "Unable to post this payroll run.")}
                </p>
              )}
              <div className="mt-5 flex flex-wrap justify-end gap-2">
                <Button type="button" variant="secondary" onClick={() => setPostDialogOpen(false)}>
                  Cancel
                </Button>
                <Button
                  type="button"
                  loading={postMutation.isPending}
                  onClick={() => postMutation.mutate()}
                >
                  Confirm post
                </Button>
              </div>
            </Dialog.Content>
          </Dialog.Portal>
        </Dialog.Root>

        <Dialog.Root open={signOffDialogOpen} onOpenChange={setSignOffDialogOpen}>
          <Dialog.Portal>
            <Dialog.Overlay className="fixed inset-0 z-40 bg-ink/50" />
            <Dialog.Content className="fixed left-1/2 top-1/2 z-50 max-h-[90dvh] w-[min(92vw,38rem)] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-card border border-border bg-surface p-4 shadow-xl sm:p-6">
              <Dialog.Title className="font-serif text-xl font-semibold">
                Confirm admin sign-off
              </Dialog.Title>
              <Dialog.Description className="mt-1 text-sm text-muted">
                Sign-off requires a bank receipt that matches the imported file and resolved payroll
                exceptions.
              </Dialog.Description>
              <dl className="mt-5 grid grid-cols-1 gap-3 text-sm sm:grid-cols-2">
                <div>
                  <dt className="text-muted">Expected total</dt>
                  <dd className="mt-1 font-medium">
                    <Money kobo={data.expected_total_kobo} />
                  </dd>
                </div>
                <div>
                  <dt className="text-muted">Imported file total</dt>
                  <dd className="mt-1 font-medium">
                    {data.file_total_kobo === null ? (
                      "Not imported"
                    ) : (
                      <Money kobo={data.file_total_kobo} />
                    )}
                  </dd>
                </div>
                <div>
                  <dt className="text-muted">Bank receipt total</dt>
                  <dd className="mt-1 font-medium">
                    {bankReceiptKobo === null ? "Enter below" : <Money kobo={bankReceiptKobo} />}
                  </dd>
                </div>
                <div>
                  <dt className="text-muted">Variance (file − receipt)</dt>
                  <dd className="mt-1 font-medium">
                    {signOffVarianceKobo === null ? (
                      "Enter receipt total"
                    ) : (
                      <Money kobo={signOffVarianceKobo} />
                    )}
                  </dd>
                </div>
              </dl>
              <label className="mt-4 block text-sm font-medium">
                Bank receipt total (₦)
                <input
                  className="mt-1 min-h-11 w-full rounded-control border border-border bg-surface px-3 text-sm"
                  type="text"
                  inputMode="decimal"
                  autoComplete="off"
                  value={bankReceiptInput}
                  onChange={(event) => setBankReceiptInput(event.target.value)}
                  aria-describedby="payroll-bank-receipt-help"
                />
              </label>
              <p id="payroll-bank-receipt-help" className="mt-1 text-xs text-muted">
                Enter naira with up to two decimal places; the amount is sent to the API as whole
                kobo.
              </p>
              {bankReceiptInput && bankReceiptKobo === null && (
                <p className="mt-2 text-sm text-danger" role="alert">
                  Enter a non-negative amount with no more than two decimal places.
                </p>
              )}
              {signOffVarianceKobo !== null && signOffVarianceKobo !== 0 && (
                <p className="mt-2 text-sm text-danger" role="alert">
                  The bank receipt total must match the imported file total before sign-off.
                </p>
              )}
              <div className="mt-5">
                <h3 className="text-sm font-semibold">
                  Persisted exceptions ({data.exceptions.length})
                </h3>
                {data.exceptions.length === 0 ? (
                  <p className="mt-2 text-sm text-muted">No exceptions recorded.</p>
                ) : (
                  <div className="mt-2 max-h-48 space-y-2 overflow-y-auto pr-1">
                    {data.exceptions.map((exception, index) => (
                      <article
                        key={`${exception.staff_no_raw}-${index}`}
                        className="rounded-control border border-border p-3 text-sm"
                      >
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <span className="break-all font-medium">{exception.staff_no_raw}</span>
                          <Badge status={exception.classification} />
                        </div>
                        <p className="mt-1 text-muted">
                          Actual amount:{" "}
                          {exception.actual_kobo === null ? (
                            "—"
                          ) : (
                            <Money kobo={exception.actual_kobo} />
                          )}
                        </p>
                        {exception.resolution_note && (
                          <p className="mt-1 break-words text-muted">
                            Resolution: {exception.resolution_note}
                          </p>
                        )}
                      </article>
                    ))}
                  </div>
                )}
              </div>
              {unresolvedExceptions.length > 0 && (
                <p className="mt-3 text-sm text-danger" role="alert">
                  Add resolution notes for all {unresolvedExceptions.length} unresolved exception(s)
                  before sign-off.
                </p>
              )}
              {signOffMutation.isError && (
                <p className="mt-3 text-sm text-danger" role="alert">
                  {errorMessage(signOffMutation.error, "Unable to sign off this payroll run.")}
                </p>
              )}
              <div className="mt-5 flex flex-wrap justify-end gap-2">
                <Button
                  type="button"
                  variant="secondary"
                  onClick={() => setSignOffDialogOpen(false)}
                >
                  Cancel
                </Button>
                <Button
                  type="button"
                  disabled={!signOffReady || signOffMutation.isPending}
                  loading={signOffMutation.isPending}
                  onClick={() => {
                    if (bankReceiptKobo !== null) signOffMutation.mutate(bankReceiptKobo);
                  }}
                >
                  Confirm sign-off
                </Button>
              </div>
            </Dialog.Content>
          </Dialog.Portal>
        </Dialog.Root>
      </section>

      <section className="mt-6 rounded-card border border-border bg-surface p-4 text-sm text-muted sm:p-5">
        <h2 className="font-serif text-lg font-semibold text-ink">Run history</h2>
        <dl className="mt-3 grid gap-3 sm:grid-cols-2">
          <div>
            <dt>Created by</dt>
            <dd className="mt-1 break-all text-ink">{data.created_by}</dd>
          </div>
          <div>
            <dt>Created</dt>
            <dd className="mt-1 text-ink">
              <DateTime value={data.created_at} />
            </dd>
          </div>
          {data.imported_by && (
            <div>
              <dt>Imported by</dt>
              <dd className="mt-1 break-all text-ink">{data.imported_by}</dd>
            </div>
          )}
          {data.imported_at && (
            <div>
              <dt>Imported</dt>
              <dd className="mt-1 text-ink">
                <DateTime value={data.imported_at} />
              </dd>
            </div>
          )}
          {data.posted_by && (
            <div>
              <dt>Posted by</dt>
              <dd className="mt-1 break-all text-ink">{data.posted_by}</dd>
            </div>
          )}
          {data.posted_at && (
            <div>
              <dt>Posted</dt>
              <dd className="mt-1 text-ink">
                <DateTime value={data.posted_at} />
              </dd>
            </div>
          )}
          {data.signed_off_at && (
            <div>
              <dt>Signed off</dt>
              <dd className="mt-1 text-ink">
                <DateTime value={data.signed_off_at} />
              </dd>
            </div>
          )}
        </dl>
      </section>
    </>
  );
}
