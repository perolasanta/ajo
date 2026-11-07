import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { request } from "../api/client";
import { Button } from "../ui/Button";
import { PageHeader } from "../ui/PageHeader";
import { Card } from "../ui/Card";

type TelegramStatus = { linked: boolean };
type LinkToken = { deep_link: string; expires_in_seconds: number };
export default function NotificationsPage() {
  const client = useQueryClient();
  const status = useQuery({ queryKey: ["telegram-link"], queryFn: () => request<TelegramStatus>("/me/telegram") });
  const create = useMutation({ mutationFn: () => request<LinkToken>("/me/telegram/link-token", { method: "POST" }) });
  const disconnect = useMutation({ mutationFn: () => request<TelegramStatus>("/me/telegram", { method: "DELETE" }), onSuccess: async () => { await client.invalidateQueries({ queryKey: ["telegram-link"] }); } });
  const connect = () => {
    const popup = window.open("about:blank", "_blank");
    create.mutate(undefined, { onSuccess: (result) => { if (popup) popup.location.href = result.deep_link; } });
  };
  return <><PageHeader title="Notifications" /><p className="-mt-4 mb-6 text-sm text-muted">Manage where your personal action alerts are sent.</p><Card className="mt-6 p-6"><h2 className="font-serif text-xl font-semibold">Telegram</h2>{status.isPending ? <p className="mt-4" role="status">Loading Telegram status…</p> : status.isError ? <p className="mt-4 text-red-800" role="alert">Unable to load Telegram status. Refresh to try again.</p> : <><p className="mt-3">{status.data.linked ? "Connected" : "Not connected"}</p>{create.isError && <p role="alert" className="mt-3 text-red-800">Unable to create a connection link. Please try again.</p>}{create.data && <p className="mt-3 text-sm">Link expires in 10 minutes. <a className="underline" href={create.data.deep_link} target="_blank" rel="noreferrer">Open Telegram</a></p>}<div className="mt-5 flex gap-3">{!status.data.linked && <Button onClick={connect} disabled={create.isPending}>{create.isPending ? "Creating link…" : "Connect"}</Button>}{status.data.linked && <Button variant="secondary" onClick={() => disconnect.mutate()} disabled={disconnect.isPending}>{disconnect.isPending ? "Disconnecting…" : "Disconnect"}</Button>}</div>{disconnect.isError && <p role="alert" className="mt-3 text-red-800">Unable to disconnect Telegram. Please try again.</p>}</>}</Card></>;
}
