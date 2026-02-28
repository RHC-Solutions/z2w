"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Card, CardContent, CardHeader, CardTitle, CardFooter } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import {
  CheckCircle2,
  XCircle,
  Loader2,
  ArrowRight,
  ArrowLeft,
  Zap,
  PartyPopper,
} from "lucide-react";

type TestState = "idle" | "loading" | "ok" | "err";

interface TestResult {
  state: TestState;
  message: string;
}

function FieldRow({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1">
      <Label className="text-xs font-semibold text-muted-foreground">{label}</Label>
      {children}
      {hint && <p className="text-[11px] text-muted-foreground/70">{hint}</p>}
    </div>
  );
}

function TestBox({ result }: { result: TestResult }) {
  if (result.state === "idle") return null;
  const colors: Record<TestState, string> = {
    idle: "",
    loading: "border-border bg-muted/30 text-muted-foreground",
    ok: "border-emerald-700 bg-emerald-950/40 text-emerald-300",
    err: "border-red-800 bg-red-950/40 text-destructive",
  };
  const icons: Record<TestState, React.ReactNode> = {
    idle: null,
    loading: <Loader2 className="w-3.5 h-3.5 animate-spin" />,
    ok: <CheckCircle2 className="w-3.5 h-3.5" />,
    err: <XCircle className="w-3.5 h-3.5" />,
  };
  return (
    <div className={`flex items-start gap-2 p-2.5 rounded-lg border text-xs mt-2 ${colors[result.state]}`}>
      <span className="mt-0.5 shrink-0">{icons[result.state]}</span>
      <span>{result.message}</span>
    </div>
  );
}

const STEPS = ["Zendesk", "Wasabi", "Offload Test", "Backup Test", "Notifications"];

function StepProgress({ current }: { current: number }) {
  return (
    <div className="flex items-center gap-0 mb-6">
      {STEPS.map((label, i) => {
        const n = i + 1;
        const done = n < current;
        const active = n === current;
        return (
          <div key={n} className="flex items-center flex-1">
            <div className="flex flex-col items-center">
              <div
                className={`w-8 h-8 rounded-full flex items-center justify-center text-xs font-bold border-2 transition-colors
                  ${done ? "bg-emerald-600 border-emerald-600 text-white" : ""}
                  ${active ? "bg-primary border-primary text-primary-foreground" : ""}
                  ${!done && !active ? "bg-card border-border text-muted-foreground" : ""}
                `}
              >
                {done ? <CheckCircle2 className="w-4 h-4" /> : n}
              </div>
              <span
                className={`text-[10px] mt-1 whitespace-nowrap
                  ${active ? "text-primary font-semibold" : ""}
                  ${done ? "text-emerald-400" : ""}
                  ${!done && !active ? "text-muted-foreground" : ""}
                `}
              >
                {label}
              </span>
            </div>
            {i < STEPS.length - 1 && (
              <div className={`flex-1 h-0.5 mx-1 -mt-5 ${done ? "bg-emerald-600" : "bg-border"}`} />
            )}
          </div>
        );
      })}
    </div>
  );
}

async function apiPost(url: string, body: object) {
  const r = await fetch(url, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return r.json();
}

export default function WizardPage() {
  const router = useRouter();
  const [step, setStep] = useState(1);
  const [done, setDone] = useState(false);
  const [doneSlug, setDoneSlug] = useState("");
  const [doneMsg, setDoneMsg] = useState("");

  // Step 1 — Zendesk
  const [zdSubdomain, setZdSubdomain] = useState("");
  const [zdEmail, setZdEmail] = useState("");
  const [zdToken, setZdToken] = useState("");
  const [zdDisplay, setZdDisplay] = useState("");
  const [zdTest, setZdTest] = useState<TestResult>({ state: "idle", message: "" });
  const [zdOk, setZdOk] = useState(false);

  // Step 2 — Wasabi
  const [wsEndpoint, setWsEndpoint] = useState("s3.wasabisys.com");
  const [wsAccess, setWsAccess] = useState("");
  const [wsSecret, setWsSecret] = useState("");
  const [wsBucket, setWsBucket] = useState("");
  const [bkEndpoint, setBkEndpoint] = useState("s3.eu-central-1.wasabisys.com");
  const [bkBucket, setBkBucket] = useState("");
  const [wsTest, setWsTest] = useState<TestResult>({ state: "idle", message: "" });
  const [bkTest, setBkTest] = useState<TestResult>({ state: "idle", message: "" });

  // Step 3 — Offload test
  const [olTicket, setOlTicket] = useState("");
  const [olTest, setOlTest] = useState<TestResult>({ state: "idle", message: "" });

  // Step 4 — Backup test
  const [bk2Test, setBk2Test] = useState<TestResult>({ state: "idle", message: "" });

  // Step 5 — Notifications
  const [tgToken, setTgToken] = useState("");
  const [tgChat, setTgChat] = useState("");
  const [slWebhook, setSlWebhook] = useState("");
  const [notifTest, setNotifTest] = useState<TestResult>({ state: "idle", message: "" });
  const [saving, setSaving] = useState(false);

  async function testZendesk() {
    setZdTest({ state: "loading", message: "Testing…" });
    setZdOk(false);
    const d = await apiPost("/api/wizard/test_zendesk", {
      subdomain: zdSubdomain,
      email: zdEmail,
      api_token: zdToken,
    });
    setZdTest({ state: d.success ? "ok" : "err", message: d.message });
    setZdOk(d.success);
  }

  async function testWasabi() {
    setWsTest({ state: "loading", message: "Testing…" });
    const d = await apiPost("/api/wizard/test_wasabi", {
      endpoint: wsEndpoint,
      access_key: wsAccess,
      secret_key: wsSecret,
      bucket: wsBucket,
    });
    setWsTest({ state: d.success ? "ok" : "err", message: d.message });
  }

  async function testBackupBucket() {
    setBkTest({ state: "loading", message: "Testing…" });
    const d = await apiPost("/api/wizard/test_wasabi", {
      endpoint: bkEndpoint,
      access_key: wsAccess,
      secret_key: wsSecret,
      bucket: bkBucket,
    });
    setBkTest({ state: d.success ? "ok" : "err", message: d.message });
  }

  async function testOffload() {
    setOlTest({ state: "loading", message: "Running offload test…" });
    const d = await apiPost("/api/wizard/test_offload", {
      ticket_id: olTicket,
      zendesk_subdomain: zdSubdomain,
      zendesk_email: zdEmail,
      zendesk_api_token: zdToken,
      wasabi_endpoint: wsEndpoint,
      wasabi_access_key: wsAccess,
      wasabi_secret_key: wsSecret,
      wasabi_bucket: wsBucket,
    });
    setOlTest({ state: d.success ? "ok" : "err", message: d.message });
  }

  async function testBackupFull() {
    setBk2Test({ state: "loading", message: "Testing backup bucket…" });
    const d = await apiPost("/api/wizard/test_backup", {
      backup_endpoint: bkEndpoint,
      wasabi_access_key: wsAccess,
      wasabi_secret_key: wsSecret,
      backup_bucket: bkBucket,
    });
    setBk2Test({ state: d.success ? "ok" : "err", message: d.message });
  }

  async function testNotifications() {
    setNotifTest({ state: "loading", message: "Testing…" });
    const d = await apiPost("/api/wizard/test_notifications", {
      telegram_bot_token: tgToken,
      telegram_chat_id: tgChat,
      slack_webhook_url: slWebhook,
    });
    const results = d.results || {};
    const parts = Object.entries(results).map(([k, v]) => `${k}: ${v}`).join(" | ");
    setNotifTest({ state: d.success ? "ok" : "err", message: parts || d.message });
  }

  async function saveTenant() {
    setSaving(true);
    const d = await apiPost("/api/wizard/save", {
      zendesk_subdomain: zdSubdomain,
      zendesk_email: zdEmail,
      zendesk_api_token: zdToken,
      display_name: zdDisplay,
      wasabi_endpoint: wsEndpoint,
      wasabi_access_key: wsAccess,
      wasabi_secret_key: wsSecret,
      wasabi_bucket: wsBucket,
      backup_endpoint: bkEndpoint,
      backup_bucket: bkBucket,
      telegram_bot_token: tgToken,
      telegram_chat_id: tgChat,
      slack_webhook_url: slWebhook,
    });
    setSaving(false);
    if (d.success) {
      setDoneSlug(d.slug);
      setDoneMsg(d.message || "Tenant created successfully!");
      setDone(true);
    } else {
      setNotifTest({ state: "err", message: d.message || "Save failed" });
    }
  }

  if (done) {
    return (
      <div className="p-6 max-w-xl mx-auto">
        <Card className="text-center py-10 px-6">
          <CardContent className="space-y-4">
            <PartyPopper className="w-12 h-12 text-emerald-400 mx-auto" />
            <h2 className="text-2xl font-bold text-emerald-400">Tenant Created!</h2>
            <p className="text-muted-foreground">{doneMsg}</p>
            <div className="flex gap-3 justify-center pt-2">
              <Button onClick={() => router.push(`/t/${doneSlug}/dashboard`)}>
                Open Dashboard
              </Button>
              <Button variant="outline" onClick={() => router.push("/tenants")}>
                All Tenants
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="p-6 max-w-2xl mx-auto">
      <div className="mb-4">
        <h1 className="text-xl font-bold">Add Tenant</h1>
        <p className="text-sm text-muted-foreground">Connect a new Zendesk account — 5-step setup wizard</p>
      </div>
      <StepProgress current={step} />

      {/* ── Step 1: Zendesk ── */}
      {step === 1 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Step 1 — Zendesk Credentials</CardTitle>
            <p className="text-sm text-muted-foreground">Enter your Zendesk subdomain and an API token with read access.</p>
          </CardHeader>
          <CardContent className="space-y-4">
            <FieldRow label="Zendesk Subdomain" hint={`https://${zdSubdomain || "yourcompany"}.zendesk.com`}>
              <Input value={zdSubdomain} onChange={(e) => setZdSubdomain(e.target.value)} placeholder="yourcompany" />
            </FieldRow>
            <FieldRow label="Admin Email">
              <Input type="email" value={zdEmail} onChange={(e) => setZdEmail(e.target.value)} placeholder="admin@company.com" />
            </FieldRow>
            <FieldRow label="API Token" hint="Zendesk → Admin → Apps & Integrations → API → Add token">
              <Input type="password" value={zdToken} onChange={(e) => setZdToken(e.target.value)} placeholder="your_api_token" />
            </FieldRow>
            <FieldRow label="Display Name (optional)">
              <Input value={zdDisplay} onChange={(e) => setZdDisplay(e.target.value)} placeholder="My Company Support" />
            </FieldRow>
            <TestBox result={zdTest} />
          </CardContent>
          <CardFooter className="flex gap-2">
            <Button variant="outline" size="sm" onClick={testZendesk} disabled={!zdSubdomain || !zdEmail || !zdToken}>
              <Zap className="w-3.5 h-3.5 mr-1" /> Test Connection
            </Button>
            <Button size="sm" disabled={!zdOk} onClick={() => setStep(2)}>
              Next <ArrowRight className="w-3.5 h-3.5 ml-1" />
            </Button>
          </CardFooter>
        </Card>
      )}

      {/* ── Step 2: Wasabi ── */}
      {step === 2 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Step 2 — Wasabi Storage</CardTitle>
            <p className="text-sm text-muted-foreground">Configure the two Wasabi buckets: one for attachment offload, one for ticket backup.</p>
          </CardHeader>
          <CardContent className="space-y-4">
            <p className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider">Offload Bucket (attachments &amp; inline images)</p>
            <div className="grid grid-cols-2 gap-3">
              <FieldRow label="Endpoint">
                <Input value={wsEndpoint} onChange={(e) => setWsEndpoint(e.target.value)} />
              </FieldRow>
              <FieldRow label="Access Key">
                <Input value={wsAccess} onChange={(e) => setWsAccess(e.target.value)} />
              </FieldRow>
              <FieldRow label="Secret Key">
                <Input type="password" value={wsSecret} onChange={(e) => setWsSecret(e.target.value)} />
              </FieldRow>
              <FieldRow label="Bucket Name">
                <Input value={wsBucket} onChange={(e) => setWsBucket(e.target.value)} />
              </FieldRow>
            </div>
            <TestBox result={wsTest} />
            <Separator />
            <p className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider">Backup Bucket (ticket JSON + HTML)</p>
            <div className="grid grid-cols-2 gap-3">
              <FieldRow label="Endpoint">
                <Input value={bkEndpoint} onChange={(e) => setBkEndpoint(e.target.value)} />
              </FieldRow>
              <FieldRow label="Bucket Name">
                <Input value={bkBucket} onChange={(e) => setBkBucket(e.target.value)} />
              </FieldRow>
            </div>
            <TestBox result={bkTest} />
          </CardContent>
          <CardFooter className="flex gap-2 flex-wrap">
            <Button variant="outline" size="sm" onClick={() => setStep(1)}>
              <ArrowLeft className="w-3.5 h-3.5 mr-1" /> Back
            </Button>
            <Button variant="outline" size="sm" onClick={testWasabi} disabled={!wsEndpoint || !wsAccess || !wsSecret || !wsBucket}>
              <Zap className="w-3.5 h-3.5 mr-1" /> Test Offload Bucket
            </Button>
            <Button variant="outline" size="sm" onClick={testBackupBucket} disabled={!bkEndpoint || !bkBucket}>
              <Zap className="w-3.5 h-3.5 mr-1" /> Test Backup Bucket
            </Button>
            <Button size="sm" onClick={() => setStep(3)}>
              Next <ArrowRight className="w-3.5 h-3.5 ml-1" />
            </Button>
          </CardFooter>
        </Card>
      )}

      {/* ── Step 3: Offload Test ── */}
      {step === 3 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Step 3 — Offload Test</CardTitle>
            <p className="text-sm text-muted-foreground">Enter a Zendesk ticket ID with attachments to verify the end-to-end offload pipeline.</p>
          </CardHeader>
          <CardContent className="space-y-4">
            <FieldRow label="Ticket ID" hint="The test will upload up to 2 attachments from this ticket to Wasabi.">
              <Input type="number" value={olTicket} onChange={(e) => setOlTicket(e.target.value)} placeholder="12345" />
            </FieldRow>
            <TestBox result={olTest} />
          </CardContent>
          <CardFooter className="flex gap-2">
            <Button variant="outline" size="sm" onClick={() => setStep(2)}>
              <ArrowLeft className="w-3.5 h-3.5 mr-1" /> Back
            </Button>
            <Button variant="outline" size="sm" onClick={testOffload} disabled={!olTicket}>
              <Zap className="w-3.5 h-3.5 mr-1" /> Run Offload Test
            </Button>
            <Button size="sm" onClick={() => setStep(4)}>
              Next <ArrowRight className="w-3.5 h-3.5 ml-1" />
            </Button>
          </CardFooter>
        </Card>
      )}

      {/* ── Step 4: Backup Test ── */}
      {step === 4 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Step 4 — Ticket Backup Test</CardTitle>
            <p className="text-sm text-muted-foreground">Verify write access to the backup bucket.</p>
          </CardHeader>
          <CardContent className="space-y-4">
            <p className="text-sm text-muted-foreground">
              This will write a small test file to <Badge variant="outline">{bkBucket || "your-backup-bucket"}</Badge> and immediately delete it.
            </p>
            <TestBox result={bk2Test} />
          </CardContent>
          <CardFooter className="flex gap-2">
            <Button variant="outline" size="sm" onClick={() => setStep(3)}>
              <ArrowLeft className="w-3.5 h-3.5 mr-1" /> Back
            </Button>
            <Button variant="outline" size="sm" onClick={testBackupFull}>
              <Zap className="w-3.5 h-3.5 mr-1" /> Run Backup Test
            </Button>
            <Button size="sm" onClick={() => setStep(5)}>
              Next <ArrowRight className="w-3.5 h-3.5 ml-1" />
            </Button>
          </CardFooter>
        </Card>
      )}

      {/* ── Step 5: Notifications ── */}
      {step === 5 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Step 5 — Notifications <span className="text-xs font-normal text-muted-foreground">(optional)</span></CardTitle>
            <p className="text-sm text-muted-foreground">Configure Telegram and/or Slack for run reports and alerts.</p>
          </CardHeader>
          <CardContent className="space-y-4">
            <FieldRow label="Telegram Bot Token">
              <Input type="password" value={tgToken} onChange={(e) => setTgToken(e.target.value)} placeholder="1234567890:AAH…" />
            </FieldRow>
            <FieldRow label="Telegram Chat ID">
              <Input value={tgChat} onChange={(e) => setTgChat(e.target.value)} placeholder="-1001234567890" />
            </FieldRow>
            <FieldRow label="Slack Webhook URL">
              <Input value={slWebhook} onChange={(e) => setSlWebhook(e.target.value)} placeholder="https://hooks.slack.com/services/…" />
            </FieldRow>
            <TestBox result={notifTest} />
          </CardContent>
          <CardFooter className="flex gap-2">
            <Button variant="outline" size="sm" onClick={() => setStep(4)}>
              <ArrowLeft className="w-3.5 h-3.5 mr-1" /> Back
            </Button>
            <Button variant="outline" size="sm" onClick={testNotifications} disabled={!tgToken && !slWebhook}>
              <Zap className="w-3.5 h-3.5 mr-1" /> Test Notifications
            </Button>
            <Button size="sm" onClick={saveTenant} disabled={saving}>
              {saving ? <><Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" /> Saving…</> : "💾 Save & Finish"}
            </Button>
          </CardFooter>
        </Card>
      )}
    </div>
  );
}
