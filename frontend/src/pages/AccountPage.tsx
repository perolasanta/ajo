import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useEffect, useState } from "react";
import { request } from "../api/client";
import {
  PASSWORD_CHANGED_NOTICE,
  PASSWORD_NOTICE_DURATION_MS,
  passwordNoticeAfterEdit,
} from "../passwordNotice.js";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import { Input } from "../ui/Input";
import { PageHeader } from "../ui/PageHeader";

type Profile = {
  user_id: string;
  full_name: string | null;
  email: string;
  roles: string[];
  mfa_enabled: boolean;
};
type SessionSummary = {
  id: string;
  device: string;
  created_at: string;
  last_seen_at: string;
  this_device: boolean;
};
type TelegramStatus = { linked: boolean };
type LinkToken = { deep_link: string; expires_in_seconds: number };

function formatDate(value: string): string {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

export default function AccountPage() {
  const client = useQueryClient();
  const [passwordChangedAt, setPasswordChangedAt] = useState<number | null>(null);
  const [passwordValidationError, setPasswordValidationError] = useState("");
  const profile = useQuery({
    queryKey: ["session"],
    queryFn: () => request<Profile>("/auth/me"),
    retry: false,
  });
  const sessions = useQuery({
    queryKey: ["account-sessions"],
    queryFn: () => request<{ items: SessionSummary[] }>("/me/sessions"),
    retry: false,
  });
  const telegram = useQuery({
    queryKey: ["telegram-link"],
    queryFn: () => request<TelegramStatus>("/me/telegram"),
    retry: false,
  });
  const changePassword = useMutation({
    mutationFn: (body: { current_password: string; new_password: string }) =>
      request<{ changed: boolean }>("/me/password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }),
    onSuccess: async () => {
      setPasswordValidationError("");
      setPasswordChangedAt(Date.now());
      await client.invalidateQueries({ queryKey: ["account-sessions"] });
    },
  });
  const revokeSession = useMutation({
    mutationFn: (sessionId: string) =>
      request<{ revoked: boolean }>(`/me/sessions/${sessionId}/revoke`, { method: "POST" }),
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: ["account-sessions"] });
    },
  });
  const revokeOthers = useMutation({
    mutationFn: () =>
      request<{ revoked_count: number }>("/me/sessions/revoke-others", { method: "POST" }),
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: ["account-sessions"] });
    },
  });
  const createLink = useMutation({
    mutationFn: () => request<LinkToken>("/me/telegram/link-token", { method: "POST" }),
  });
  const disconnectTelegram = useMutation({
    mutationFn: () => request<TelegramStatus>("/me/telegram", { method: "DELETE" }),
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: ["telegram-link"] });
    },
  });

  useEffect(() => {
    if (passwordChangedAt === null) return;
    const remaining = Math.max(0, passwordChangedAt + PASSWORD_NOTICE_DURATION_MS - Date.now());
    const timeout = window.setTimeout(() => setPasswordChangedAt(null), remaining);
    return () => window.clearTimeout(timeout);
  }, [passwordChangedAt]);

  function handlePasswordEdit() {
    setPasswordChangedAt((shownAt) => passwordNoticeAfterEdit(shownAt, Date.now()));
  }

  function submitPassword(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const values = new FormData(form);
    const currentPassword = String(values.get("current_password") ?? "");
    const newPassword = String(values.get("new_password") ?? "");
    const confirmation = String(values.get("confirm_password") ?? "");
    if (newPassword !== confirmation) {
      setPasswordValidationError("The new passwords do not match.");
      setPasswordChangedAt(null);
      return;
    }
    setPasswordValidationError("");
    setPasswordChangedAt(null);
    changePassword.mutate(
      { current_password: currentPassword, new_password: newPassword },
      { onSuccess: () => form.reset() },
    );
  }

  function connectTelegram() {
    const popup = window.open("about:blank", "_blank");
    createLink.mutate(undefined, {
      onSuccess: (result) => {
        if (popup) popup.location.href = result.deep_link;
      },
      onError: () => popup?.close(),
    });
  }

  return (
    <>
      <PageHeader title="Account" />
      <p className="-mt-4 mb-6 text-sm text-muted">
        Manage your profile, sign-in security, active sessions, and personal notifications.
      </p>

      <Card className="p-6">
        <h2 className="font-serif text-xl font-semibold">Profile</h2>
        {profile.isPending ? (
          <p className="mt-4" role="status">
            Loading profile…
          </p>
        ) : profile.isError || !profile.data ? (
          <div className="mt-4">
            <p role="alert" className="text-red-800">
              Unable to load your profile.
            </p>
            <Button className="mt-3" variant="secondary" onClick={() => void profile.refetch()}>
              Retry
            </Button>
          </div>
        ) : (
          <>
            <dl className="mt-4 grid gap-4 sm:grid-cols-2">
              <div>
                <dt className="text-sm text-muted">Name</dt>
                <dd className="mt-1 font-medium">{profile.data.full_name?.trim() || "Not set"}</dd>
              </div>
              <div>
                <dt className="text-sm text-muted">Email</dt>
                <dd className="mt-1 break-all font-medium">{profile.data.email}</dd>
              </div>
              <div>
                <dt className="text-sm text-muted">Role(s)</dt>
                <dd className="mt-1 font-medium">
                  {profile.data.roles.length > 0 ? profile.data.roles.join(", ") : "Member"}
                </dd>
              </div>
            </dl>
            <p className="mt-4 text-sm text-muted">
              Profile details are read-only here. An admin can edit names on the Staff page.
            </p>
          </>
        )}
      </Card>

      <Card className="mt-6 p-6">
        <h2 className="font-serif text-xl font-semibold">Security</h2>
        {profile.isSuccess && (
          <p className="mt-3 text-sm">
            Multi-factor authentication: {profile.data.mfa_enabled ? "Enabled" : "Not enabled"}.
            Status only; enrollment and recovery codes are not managed here.
          </p>
        )}
        <form className="mt-5 grid max-w-xl gap-4" onSubmit={submitPassword}>
          <h3 className="font-medium">Change password</h3>
          <label className="grid gap-1 text-sm font-medium">
            Current password
            <Input
              required
              name="current_password"
              type="password"
              autoComplete="current-password"
              maxLength={1024}
              onChange={handlePasswordEdit}
            />
          </label>
          <label className="grid gap-1 text-sm font-medium">
            New password
            <Input
              required
              name="new_password"
              type="password"
              autoComplete="new-password"
              minLength={12}
              maxLength={128}
              onChange={handlePasswordEdit}
            />
          </label>
          <label className="grid gap-1 text-sm font-medium">
            Confirm new password
            <Input
              required
              name="confirm_password"
              type="password"
              autoComplete="new-password"
              minLength={12}
              maxLength={128}
              onChange={handlePasswordEdit}
            />
          </label>
          {passwordChangedAt !== null && (
            <p role="status" className="text-sm text-ink">
              {PASSWORD_CHANGED_NOTICE}
            </p>
          )}
          {passwordValidationError && (
            <p role="alert" className="text-sm text-red-800">
              {passwordValidationError}
            </p>
          )}
          {changePassword.isError && (
            <p role="alert" className="text-sm text-red-800">
              {changePassword.error instanceof Error &&
              "status" in changePassword.error &&
              changePassword.error.status === 429
                ? "Too many attempts. Please wait before trying again."
                : "Unable to change password. Check your current password and password requirements."}
            </p>
          )}
          <div>
            <Button type="submit" disabled={changePassword.isPending}>
              {changePassword.isPending ? "Updating password…" : "Update password"}
            </Button>
          </div>
        </form>

        <div className="mt-8 border-t border-border pt-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h3 className="font-medium">Active sessions</h3>
              <p className="mt-1 text-sm text-muted">Sign out sessions you no longer recognize.</p>
            </div>
            <Button
              variant="secondary"
              disabled={revokeOthers.isPending || sessions.data?.items.length === 0}
              onClick={() => revokeOthers.mutate()}
            >
              {revokeOthers.isPending ? "Signing out…" : "Sign out other sessions"}
            </Button>
          </div>
          {sessions.isPending ? (
            <p className="mt-4" role="status">
              Loading active sessions…
            </p>
          ) : sessions.isError ? (
            <div className="mt-4">
              <p role="alert" className="text-red-800">
                Unable to load active sessions.
              </p>
              <Button className="mt-3" variant="secondary" onClick={() => void sessions.refetch()}>
                Retry
              </Button>
            </div>
          ) : sessions.data.items.length === 0 ? (
            <p className="mt-4 text-sm text-muted">No active sessions to show.</p>
          ) : (
            <ul className="mt-4 divide-y divide-border">
              {sessions.data.items.map((item) => (
                <li
                  key={item.id}
                  className="flex flex-wrap items-center justify-between gap-4 py-4"
                >
                  <div>
                    <p className="font-medium">
                      {item.device}
                      {item.this_device ? " · This device" : ""}
                    </p>
                    <p className="mt-1 text-sm text-muted">
                      Created {formatDate(item.created_at)} · Last seen{" "}
                      {formatDate(item.last_seen_at)}
                    </p>
                  </div>
                  {!item.this_device && (
                    <Button
                      variant="secondary"
                      disabled={revokeSession.isPending}
                      onClick={() => revokeSession.mutate(item.id)}
                    >
                      Revoke
                    </Button>
                  )}
                </li>
              ))}
            </ul>
          )}
          {(revokeSession.isError || revokeOthers.isError) && (
            <p role="alert" className="mt-3 text-sm text-red-800">
              Unable to revoke the selected session. Refresh and try again.
            </p>
          )}
          {revokeOthers.isSuccess && (
            <p role="status" className="mt-3 text-sm">
              Other sessions have been signed out.
            </p>
          )}
        </div>
      </Card>

      <Card className="mt-6 p-6">
        <h2 className="font-serif text-xl font-semibold">Notifications</h2>
        <p className="mt-2 text-sm text-muted">
          Manage where your personal action alerts are sent.
        </p>
        <div className="mt-5 border-t border-border pt-5">
          <h3 className="font-medium">Telegram</h3>
          {telegram.isPending ? (
            <p className="mt-3" role="status">
              Loading Telegram status…
            </p>
          ) : telegram.isError ? (
            <div className="mt-3">
              <p role="alert" className="text-red-800">
                Unable to load Telegram status.
              </p>
              <Button className="mt-3" variant="secondary" onClick={() => void telegram.refetch()}>
                Retry
              </Button>
            </div>
          ) : (
            <>
              <p className="mt-3">{telegram.data.linked ? "Connected" : "Not connected"}</p>
              {createLink.data && (
                <p className="mt-3 text-sm">
                  Connection link expires in {Math.ceil(createLink.data.expires_in_seconds / 60)}{" "}
                  minutes.{" "}
                  <a
                    className="underline"
                    href={createLink.data.deep_link}
                    target="_blank"
                    rel="noreferrer"
                  >
                    Open Telegram
                  </a>
                </p>
              )}
              {(createLink.isError || disconnectTelegram.isError) && (
                <p role="alert" className="mt-3 text-sm text-red-800">
                  Unable to update Telegram connection. Please try again.
                </p>
              )}
              <div className="mt-5 flex flex-wrap gap-3">
                {!telegram.data.linked ? (
                  <Button onClick={connectTelegram} disabled={createLink.isPending}>
                    {createLink.isPending ? "Creating link…" : "Connect Telegram"}
                  </Button>
                ) : (
                  <Button
                    variant="secondary"
                    onClick={() => disconnectTelegram.mutate()}
                    disabled={disconnectTelegram.isPending}
                  >
                    {disconnectTelegram.isPending ? "Disconnecting…" : "Disconnect Telegram"}
                  </Button>
                )}
                <Button variant="secondary" onClick={() => void telegram.refetch()}>
                  Refresh status
                </Button>
              </div>
            </>
          )}
        </div>
      </Card>
    </>
  );
}
