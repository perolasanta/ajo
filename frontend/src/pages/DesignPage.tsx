import { useState } from "react";
import { AlertTriangle } from "lucide-react";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import { Field } from "../ui/Field";
import { DateTime, Money } from "../ui/Format";
import { EmptyState, Skeleton, Stepper, Toast } from "../ui/Feedback";
import { Input } from "../ui/Input";
import { ConfirmDialog, Dialog, Popover, Tooltip, WhyPopover } from "../ui/Overlay";
import { PageHeader } from "../ui/PageHeader";
import { Select } from "../ui/Select";
import { Table, TableCell, TableHead } from "../ui/Table";
import { Timeline } from "../ui/Timeline";

export default function DesignPage() {
  const [toast, setToast] = useState(true);

  return (
    <main className="mx-auto max-w-[1200px] space-y-8 p-6">
      <PageHeader title="Design review" breadcrumb="Development only">
        <Badge>under review</Badge>
      </PageHeader>

      <section className="grid items-start gap-5 md:grid-cols-2">
        <Card>
          <h2 className="font-serif text-lg font-semibold">Login</h2>

          <div className="mt-4 space-y-3">
            <Field label="Cooperative slug" helper="Your cooperative’s short name.">
              <Input placeholder="example-coop" />
            </Field>

            <Field label="Email" error="Enter a valid email address.">
              <Input type="email" value="not an email" readOnly aria-invalid="true" />
            </Field>

            <Field label="Password">
              <Input type="password" value="password" readOnly />
            </Field>

            <Button className="w-full">Sign in</Button>
          </div>
        </Card>

        <Card>
          <h2 className="font-serif text-lg font-semibold">Actions and statuses</h2>

          <div className="mt-4 flex flex-wrap gap-2">
            <Button>Primary</Button>
            <Button variant="secondary">Secondary</Button>
            <Button variant="ghost">Ghost</Button>
            <Button variant="danger">Danger</Button>
            <Button loading>Loading</Button>
            <Button disabled>Disabled</Button>
          </div>

          <div className="mt-5 flex flex-wrap gap-2">
            {[
              "draft",
              "awaiting confirmation",
              "under review",
              "approved",
              "ready",
              "disbursed",
              "repaying",
              "closed",
              "disputed",
              "expired",
            ].map((status) => (
              <Badge key={status}>{status}</Badge>
            ))}
          </div>
        </Card>
      </section>

      <section className="grid items-start gap-5 lg:grid-cols-2">
        <Card>
          <h2 className="font-serif text-lg font-semibold">Forms and overlays</h2>

          <div className="mt-4 space-y-3">
            <Select aria-label="Status">
              <option>All statuses</option>
              <option>Approved</option>
            </Select>

            <div className="flex flex-wrap gap-3">
              <Tooltip content="Shows the exact calculation">
                <button className="text-sm text-primary underline">Tooltip</button>
              </Tooltip>

              <Popover
                trigger={
                  <Button variant="secondary" size="sm">
                    Details
                  </Button>
                }
              >
                <p className="font-medium">Evidence</p>
                <p className="mt-1 text-muted">This action is recorded in the audit log.</p>
              </Popover>

              <Dialog
                title="Record contribution"
                description="This will create a ledger entry."
                trigger={<Button size="sm">Open dialog</Button>}
              >
                <p className="text-sm text-muted">Dialog content is keyboard accessible.</p>
              </Dialog>

              <ConfirmDialog
                title="Reverse repayment"
                consequence="The original repayment remains in the audit trail and a reversal is recorded."
                trigger={
                  <Button variant="danger" size="sm">
                    Reverse
                  </Button>
                }
                onConfirm={() => setToast(true)}
              />
            </div>
          </div>
        </Card>

        <Card>
          <h2 className="font-serif text-lg font-semibold">Explainable money</h2>

          <div className="mt-4 flex items-center justify-between">
            <Money kobo={125000000} className="text-xl font-semibold" />

            <WhyPopover
              steps={[
                "Monthly salary: ₦250,000.00",
                "Deduction cap: 40%",
                "Existing deductions: ₦25,000.00",
                "Available capacity: ₦75,000.00",
              ]}
            />
          </div>

          <p className="mt-3 text-sm text-muted">
            <DateTime value="2026-10-24T13:05:00Z" />
          </p>
        </Card>
      </section>

      <Card>
        <h2 className="font-serif text-lg font-semibold">Members table</h2>

        <Table className="mt-4">
          <TableHead>
            <tr>
              <th>Member no.</th>
              <th>Name</th>
              <th>Status</th>
              <th className="text-right">Savings</th>
              <th>Active loan</th>
            </tr>
          </TableHead>

          <tbody>
            <tr>
              <TableCell>AJ-0001</TableCell>
              <TableCell>Ada Okafor</TableCell>
              <TableCell>
                <Badge>approved</Badge>
              </TableCell>
              <TableCell numeric>
                <Money kobo={125000000} />
              </TableCell>
              <TableCell>
                <Badge>repaying</Badge>
              </TableCell>
            </tr>

            <tr>
              <TableCell>AJ-0002</TableCell>
              <TableCell>Uche Nwosu</TableCell>
              <TableCell>
                <Badge>awaiting confirmation</Badge>
              </TableCell>
              <TableCell numeric>
                <Money kobo={4250000} />
              </TableCell>
              <TableCell>
                <Badge>awaiting confirmation</Badge>
              </TableCell>
            </tr>
          </tbody>
        </Table>

        <div className="mt-4 flex gap-3">
          <Skeleton className="h-11 w-1/3" />
          <Skeleton className="h-11 w-1/4" />
        </div>
      </Card>

      <section className="grid items-start gap-5 lg:grid-cols-2">
        <Card>
          <h2 className="font-serif text-lg font-semibold">Loan timeline</h2>

          <div className="mt-5">
            <Timeline
              events={[
                {
                  title: "Application submitted",
                  actor: "Ada Okafor",
                  role: "Member",
                  time: "24 Oct 2026, 14:05 WAT",
                  channel: "Portal",
                  evidence: "consent_7a31",
                },
                {
                  title: "Terms confirmed",
                  actor: "Ada Okafor",
                  role: "Member",
                  time: "24 Oct 2026, 14:12 WAT",
                  channel: "WhatsApp",
                  evidence: "a3f9c2",
                },
              ]}
            />
          </div>
        </Card>

        <Card>
          <h2 className="font-serif text-lg font-semibold">Workflow</h2>

          <Stepper
            current={1}
            steps={["Prepare", "Authorize", "Record transfer", "Confirm paid"]}
          />

          <div className="mt-8">
            <EmptyState
              title="No payroll exceptions"
              detail="Exceptions from the current payroll run will appear here."
              action={<Button variant="secondary">View payroll</Button>}
            />
          </div>

          <p className="mt-4 flex items-center gap-2 text-sm text-danger">
            <AlertTriangle className="size-4" />A different authorizer must approve this.
          </p>
        </Card>
      </section>

      {toast && <Toast message="A review action was recorded." onDismiss={() => setToast(false)} />}
    </main>
  );
}
