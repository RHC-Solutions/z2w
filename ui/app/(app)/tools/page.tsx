"use client";

import { useState } from "react";
import { Card, CardContent, CardHeader, CardTitle, CardFooter } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Label } from "@/components/ui/label";
import {
  Activity,
  Globe,
  Zap,
  Search,
  RefreshCw,
  Terminal,
  Loader2,
} from "lucide-react";

type RunState = "idle" | "loading" | "done" | "err";

function ResultBox({ state, output }: { state: RunState; output: string }) {
  if (state === "idle") return null;
  return (
    <div
      className={`mt-2 rounded-lg border p-3 font-mono text-xs whitespace-pre-wrap break-all max-h-64 overflow-auto ${
        state === "loading"
          ? "border-border bg-muted/20 text-muted-foreground"
          : state === "err"
          ? "border-red-800 bg-red-950/30 text-red-300"
          : "border-border bg-muted/20 text-foreground"
      }`}
    >
      {state === "loading" ? (
        <span className="flex items-center gap-2">
          <Loader2 className="w-3 h-3 animate-spin" /> Running…
        </span>
      ) : (
        output
      )}
    </div>
  );
}

async function apiGetText(url: string): Promise<{ ok: boolean; text: string }> {
  try {
    const r = await fetch(url, { credentials: "include" });
    const text = await r.text();
    return { ok: r.ok, text };
  } catch (e) {
    return { ok: false, text: String(e) };
  }
}
async function apiGet(url: string) {
  const r = await fetch(url, { credentials: "include" });
  return r.json();
}
async function apiPost(url: string, body?: object) {
  const r = await fetch(url, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  return r.json();
}

// ── Ping ──────────────────────────────────────────────────────────────────────
function PingTool() {
  const [host, setHost] = useState("s3.wasabisys.com");
  const [state, setState] = useState<RunState>("idle");
  const [output, setOutput] = useState("");

  async function run() {
    setState("loading");
    setOutput("");
    const { ok, text } = await apiGetText(`/api/tools/ping?host=${encodeURIComponent(host)}`);
    setState(ok ? "done" : "err");
    setOutput(text || "No output");
  }

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center gap-2">
          <Activity className="w-4 h-4 text-primary" /> Ping
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="flex gap-2">
          <div className="flex-1">
            <Label className="text-xs text-muted-foreground mb-1 block">Host</Label>
            <Input value={host} onChange={(e) => setHost(e.target.value)} className="text-xs h-8" />
          </div>
        </div>
        <ResultBox state={state} output={output} />
      </CardContent>
      <CardFooter>
        <Button size="sm" onClick={run} disabled={state === "loading" || !host}>
          {state === "loading" ? <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" /> : <Activity className="w-3.5 h-3.5 mr-1" />}
          Run Ping
        </Button>
      </CardFooter>
    </Card>
  );
}

// ── Traceroute ─────────────────────────────────────────────────────────────────
function TracerouteTool() {
  const [host, setHost] = useState("s3.wasabisys.com");
  const [state, setState] = useState<RunState>("idle");
  const [output, setOutput] = useState("");

  async function run() {
    setState("loading");
    setOutput("");
    const { ok, text } = await apiGetText(`/api/tools/traceroute?host=${encodeURIComponent(host)}`);
    setState(ok ? "done" : "err");
    setOutput(text || "No output");
  }

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center gap-2">
          <Globe className="w-4 h-4 text-primary" /> Traceroute
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="flex gap-2">
          <div className="flex-1">
            <Label className="text-xs text-muted-foreground mb-1 block">Host</Label>
            <Input value={host} onChange={(e) => setHost(e.target.value)} className="text-xs h-8" />
          </div>
        </div>
        <ResultBox state={state} output={output} />
      </CardContent>
      <CardFooter>
        <Button size="sm" onClick={run} disabled={state === "loading" || !host}>
          {state === "loading" ? <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" /> : <Globe className="w-3.5 h-3.5 mr-1" />}
          Run Traceroute
        </Button>
      </CardFooter>
    </Card>
  );
}

// ── DNS ────────────────────────────────────────────────────────────────────────
function DnsTool() {
  const [host, setHost] = useState("s3.wasabisys.com");
  const [state, setState] = useState<RunState>("idle");
  const [output, setOutput] = useState("");

  async function run() {
    setState("loading");
    setOutput("");
    const { ok, text } = await apiGetText(`/api/tools/dns?host=${encodeURIComponent(host)}`);
    setState(ok ? "done" : "err");
    setOutput(text || "No output");
  }

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center gap-2">
          <Search className="w-4 h-4 text-primary" /> DNS Lookup
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="flex gap-2">
          <div className="flex-1">
            <Label className="text-xs text-muted-foreground mb-1 block">Hostname</Label>
            <Input value={host} onChange={(e) => setHost(e.target.value)} className="text-xs h-8" />
          </div>
        </div>
        <ResultBox state={state} output={output} />
      </CardContent>
      <CardFooter>
        <Button size="sm" onClick={run} disabled={state === "loading" || !host}>
          {state === "loading" ? <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" /> : <Search className="w-3.5 h-3.5 mr-1" />}
          Lookup
        </Button>
      </CardFooter>
    </Card>
  );
}

// ── Speed Test ─────────────────────────────────────────────────────────────────
function SpeedTestTool() {
  const [state, setState] = useState<RunState>("idle");
  const [output, setOutput] = useState("");

  async function run() {
    setState("loading");
    setOutput("Starting speed test — this may take several minutes…");
    const { ok, text } = await apiGetText(`/api/tools/speedtest`);
    setState(ok ? "done" : "err");
    setOutput(text || "No output");
  }

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center gap-2">
          <Zap className="w-4 h-4 text-primary" /> Wasabi Speed Test
        </CardTitle>
      </CardHeader>
      <CardContent>
        <p className="text-xs text-muted-foreground">
          Uploads a test object to Wasabi and measures upload/download throughput.
        </p>
        <ResultBox state={state} output={output} />
      </CardContent>
      <CardFooter>
        <Button size="sm" onClick={run} disabled={state === "loading"}>
          {state === "loading" ? <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" /> : <Zap className="w-3.5 h-3.5 mr-1" />}
          Run Speed Test
        </Button>
      </CardFooter>
    </Card>
  );
}

// ── Recheck All ────────────────────────────────────────────────────────────────
function RecheckTool() {
  const [state, setState] = useState<RunState>("idle");
  const [output, setOutput] = useState("");

  async function run() {
    setState("loading");
    setOutput("Starting recheck job…");
    const d = await apiPost(`/api/recheck_all`);
    setState(d.success ? "done" : "err");
    setOutput(d.message || JSON.stringify(d, null, 2));
  }

  async function checkStatus() {
    setState("loading");
    const d = await apiGet(`/api/recheck_status`);
    setState("done");
    setOutput(JSON.stringify(d, null, 2));
  }

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center gap-2">
          <RefreshCw className="w-4 h-4 text-primary" /> Recheck All Tickets
        </CardTitle>
      </CardHeader>
      <CardContent>
        <p className="text-xs text-muted-foreground">
          Re-processes all previously seen tickets to catch any missed attachments or failed uploads.
        </p>
        <ResultBox state={state} output={output} />
      </CardContent>
      <CardFooter className="flex gap-2">
        <Button size="sm" onClick={run} disabled={state === "loading"}>
          {state === "loading" ? <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5 mr-1" />}
          Start Recheck
        </Button>
        <Button size="sm" variant="outline" onClick={checkStatus} disabled={state === "loading"}>
          Check Status
        </Button>
      </CardFooter>
    </Card>
  );
}

// ── System Info ────────────────────────────────────────────────────────────────
function SysInfoTool() {
  const [state, setState] = useState<RunState>("idle");
  const [output, setOutput] = useState("");

  async function run() {
    setState("loading");
    setOutput("");
    try {
      const d = await apiGet(`/api/tenants/overview`);
      setState("done");
      setOutput(JSON.stringify(d, null, 2));
    } catch (e) {
      setState("err");
      setOutput(String(e));
    }
  }

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center gap-2">
          <Terminal className="w-4 h-4 text-primary" /> System Info
        </CardTitle>
      </CardHeader>
      <CardContent>
        <p className="text-xs text-muted-foreground">
          Dumps the current dashboard stats JSON for debugging.
        </p>
        <ResultBox state={state} output={output} />
      </CardContent>
      <CardFooter>
        <Button size="sm" onClick={run} disabled={state === "loading"}>
          {state === "loading" ? <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" /> : <Terminal className="w-3.5 h-3.5 mr-1" />}
          Fetch
        </Button>
      </CardFooter>
    </Card>
  );
}

export default function ToolsPage() {
  return (
    <div className="p-6">
      <div className="mb-5">
        <h1 className="text-xl font-bold">Tools</h1>
        <p className="text-sm text-muted-foreground">Network diagnostics &amp; system utilities</p>
      </div>
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <PingTool />
        <DnsTool />
        <TracerouteTool />
        <SpeedTestTool />
        <RecheckTool />
        <SysInfoTool />
      </div>
    </div>
  );
}
