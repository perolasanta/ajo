import QRCode from "qrcode";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ensureCsrf, request } from "../api/client";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import { Input } from "../ui/Input";
type LoginReply = {
  mfa_required?: boolean;
  provisioning_uri?: string;
  mfa_enrollment?: { provisioning_uri?: string };
};
export default function LoginPage() {
  const navigate = useNavigate();
  const [stage, setStage] = useState<"credentials" | "verify" | "enroll">("credentials");
  const [code, setCode] = useState("");
  const [error, setError] = useState("");
  const [enrollment, setEnrollment] = useState<{ uri: string; secret: string }>();
  const [qr, setQr] = useState("");
  useEffect(() => {
    if (enrollment) void QRCode.toDataURL(enrollment.uri, { margin: 1, width: 220 }).then(setQr);
  }, [enrollment]);
  async function credentials(form: HTMLFormElement) {
    setError("");
    const fields = new FormData(form);
    try {
      await ensureCsrf();
      const data = await request<LoginReply>("/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          cooperative_slug: fields.get("cooperative_slug"),
          email: fields.get("email"),
          password: fields.get("password"),
        }),
      });
      const setup = data.mfa_enrollment ?? data;
      if (setup.provisioning_uri) {
        const secret = new URL(setup.provisioning_uri).searchParams.get("secret");
        if (!secret) throw new Error("Missing enrolment secret");
        setEnrollment({ uri: setup.provisioning_uri, secret });
        setStage("enroll");
      } else if (data.mfa_required) setStage("verify");
      else navigate("/members", { replace: true });
    } catch {
      setError("We couldn’t sign you in with those details.");
    }
  }
  async function verify(endpoint: "/auth/mfa/enroll" | "/auth/mfa/verify") {
    setError("");
    try {
      await request(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code }),
      });
      navigate("/members", { replace: true });
    } catch {
      setError("We couldn’t sign you in with those details.");
    }
  }
  return (
    <main className="mx-auto flex min-h-[100dvh] max-w-md items-center p-4 sm:p-5">
      <Card className="w-full">
        <h1 className="font-serif text-xl font-semibold text-primary">Ajo</h1>
        <p className="mt-1 text-sm text-muted">Officer portal</p>
        {stage === "credentials" && (
          <form
            className="mt-6 space-y-4"
            onSubmit={(event) => {
              event.preventDefault();
              void credentials(event.currentTarget);
            }}
          >
            <label className="block text-sm font-medium">
              Cooperative slug
              <Input required name="cooperative_slug" autoComplete="organization" />
            </label>
            <label className="block text-sm font-medium">
              Email
              <Input required name="email" type="email" autoComplete="email" />
            </label>
            <label className="block text-sm font-medium">
              Password
              <Input required name="password" type="password" autoComplete="current-password" />
            </label>
            {error && (
              <p role="alert" className="text-sm text-danger">
                {error}
              </p>
            )}
            <Button className="w-full" type="submit">
              Sign in
            </Button>
          </form>
        )}
        {stage === "enroll" && enrollment && (
          <section className="mt-6">
            <h2 className="font-semibold">Set up your authenticator</h2>
            <p className="mt-2 text-sm text-muted">Scan this code, or enter the secret manually.</p>
            {qr && (
              <img className="mx-auto my-4 max-w-full" src={qr} alt="Authenticator setup QR code" />
            )}
            <p className="break-all rounded-control bg-bg p-3 font-mono text-xs">
              {enrollment.secret}
            </p>
            <CodeForm
              code={code}
              setCode={setCode}
              error={error}
              submit={() => verify("/auth/mfa/enroll")}
              label="Verify setup"
            />
          </section>
        )}
        {stage === "verify" && (
          <section className="mt-6">
            <h2 className="font-semibold">Verification code</h2>
            <p className="mt-2 text-sm text-muted">
              Enter the 6-digit code from your authenticator.
            </p>
            <CodeForm
              code={code}
              setCode={setCode}
              error={error}
              submit={() => verify("/auth/mfa/verify")}
              label="Continue"
            />
          </section>
        )}
      </Card>
    </main>
  );
}
function CodeForm({
  code,
  setCode,
  error,
  submit,
  label,
}: {
  code: string;
  setCode: (value: string) => void;
  error: string;
  submit: () => void;
  label: string;
}) {
  return (
    <form
      className="mt-4 space-y-3"
      onSubmit={(event) => {
        event.preventDefault();
        void submit();
      }}
    >
      <Input
        required
        inputMode="numeric"
        pattern="[0-9]{6}"
        maxLength={6}
        value={code}
        onChange={(event) => setCode(event.target.value)}
        aria-label="Six digit verification code"
      />
      {error && (
        <p role="alert" className="text-sm text-red-700">
          {error}
        </p>
      )}
      <Button className="w-full" type="submit">
        {label}
      </Button>
    </form>
  );
}
