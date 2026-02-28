"use client";

import { useEffect, useState, useRef } from "react";
import { useParams } from "next/navigation";
import {
  getBucketSecurity, setBucketWhitelist, BucketInfo, BucketsSecurityResult,
} from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Skeleton } from "@/components/ui/skeleton";
import {
  ShieldCheck, ShieldAlert, RefreshCw, X, Plus, Save,
  Globe, Lock, Copy, ListPlus,
} from "lucide-react";
import { cn } from "@/lib/utils";

// ── helpers ────────────────────────────────────────────────────────────────────

function ipValid(s: string): boolean {
  // basic ipv4 / ipv4 cidr / ipv6 / ipv6 cidr check
  return /^(\d{1,3}\.){3}\d{1,3}(\/\d{1,2})?$/.test(s) ||
    /^[0-9a-fA-F:]+(:\/\d{1,3})?$/.test(s);
}

// ── BucketCard ─────────────────────────────────────────────────────────────────

function BucketCard({
  info, onSaved, otherIps, otherLabel,
}: {
  info: BucketInfo;
  onSaved: () => void;
  otherIps?: string[];
  otherLabel?: string;
}) {
  const { slug } = useParams<{ slug: string }>();

  const [ips, setIps] = useState<string[]>(info.ips);
  const [inputVal, setInputVal] = useState("");
  const [bulkVal, setBulkVal] = useState("");
  const [bulkOpen, setBulkOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<{ text: string; ok: boolean } | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // sync when parent refreshes
  useEffect(() => { setIps(info.ips); }, [info.ips]);

  function copyFromOther() {
    if (!otherIps || otherIps.length === 0) {
      setMsg({ text: `${otherLabel ?? "Other bucket"} has no IPs to copy`, ok: false });
      return;
    }
    const toAdd = otherIps.filter((ip) => !ips.includes(ip));
    if (toAdd.length === 0) {
      setMsg({ text: "All IPs from the other bucket are already present", ok: false });
      return;
    }
    setIps((p) => [...p, ...toAdd]);
    setMsg({ text: `Copied ${toAdd.length} IP${toAdd.length !== 1 ? "s" : ""} from ${otherLabel ?? "other bucket"}`, ok: true });
  }

  function addBulk() {
    // split on newlines, commas, semicolons, or whitespace
    const tokens = bulkVal
      .split(/[\n,;\s]+/)
      .map((s) => s.trim())
      .filter(Boolean);
    const invalid: string[] = [];
    const added: string[] = [];
    const dupes: string[] = [];
    const current = new Set(ips);
    for (const t of tokens) {
      if (!ipValid(t)) { invalid.push(t); continue; }
      if (current.has(t)) { dupes.push(t); continue; }
      current.add(t);
      added.push(t);
    }
    if (added.length) setIps((p) => [...p, ...added]);
    const parts: string[] = [];
    if (added.length)  parts.push(`Added ${added.length} IP${added.length !== 1 ? "s" : ""}`);
    if (dupes.length)  parts.push(`${dupes.length} duplicate${dupes.length !== 1 ? "s" : ""} skipped`);
    if (invalid.length) parts.push(`${invalid.length} invalid: ${invalid.slice(0, 5).join(", ")}${invalid.length > 5 ? "…" : ""}`);
    setMsg({ text: parts.join(" · ") || "Nothing to add", ok: invalid.length === 0 && added.length > 0 });
    if (added.length) { setBulkVal(""); setBulkOpen(false); }
  }

  function addIp() {
    const v = inputVal.trim();
    if (!v) return;
    if (!ipValid(v)) {
      setMsg({ text: `"${v}" is not a valid IP/CIDR`, ok: false });
      return;
    }
    if (ips.includes(v)) {
      setMsg({ text: `${v} is already in the list`, ok: false });
      return;
    }
    setIps((p) => [...p, v]);
    setInputVal("");
    setMsg(null);
    inputRef.current?.focus();
  }

  function removeIp(ip: string) {
    setIps((p) => p.filter((i) => i !== ip));
    setMsg(null);
  }

  async function save() {
    setSaving(true);
    setMsg(null);
    try {
      const res = await setBucketWhitelist(slug, info.which, ips);
      setMsg({ text: res.message || "Saved", ok: res.success });
      if (res.success) {
        setIps(res.ips);
        onSaved();
      }
    } catch (e: unknown) {
      setMsg({ text: e instanceof Error ? e.message : "Save failed", ok: false });
    } finally {
      setSaving(false);
    }
  }

  const label = info.which === "attach" ? "Offload Bucket" : "Backup Bucket";
  const isOpen = ips.length === 0;
  const dirty = JSON.stringify(ips) !== JSON.stringify(info.ips);

  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            {isOpen ? (
              <Globe size={16} className="text-amber-400 flex-shrink-0" />
            ) : (
              <Lock size={16} className="text-emerald-400 flex-shrink-0" />
            )}
            <CardTitle className="text-base">{label}</CardTitle>
          </div>
          {isOpen ? (
            <Badge className="text-[10px] bg-amber-700 hover:bg-amber-700 gap-1">
              <ShieldAlert size={10} /> Open access
            </Badge>
          ) : (
            <Badge className="text-[10px] bg-emerald-700 hover:bg-emerald-700 gap-1">
              <ShieldCheck size={10} /> {ips.length} IP{ips.length !== 1 ? "s" : ""} allowed
            </Badge>
          )}
        </div>
        <p className="text-xs text-muted-foreground mt-1">
          <span className="font-mono">{info.bucket}</span>
          {" · "}
          <span>{info.endpoint}</span>
        </p>
        {info.error && (
          <p className="text-xs text-destructive mt-1">{info.error}</p>
        )}
      </CardHeader>

      <CardContent className="space-y-3">
        {/* current IPs */}
        {ips.length === 0 ? (
          <p className="text-xs text-muted-foreground italic">
            No IP restrictions — bucket is publicly accessible (subject to credentials).
          </p>
        ) : (
          <div className="flex flex-wrap gap-1.5">
            {ips.map((ip) => (
              <span
                key={ip}
                className="inline-flex items-center gap-1 bg-muted rounded px-2 py-0.5 text-xs font-mono"
              >
                {ip}
                <button
                  onClick={() => removeIp(ip)}
                  className="text-muted-foreground hover:text-destructive transition-colors"
                  aria-label={`Remove ${ip}`}
                >
                  <X size={11} />
                </button>
              </span>
            ))}
          </div>
        )}

        {/* add IP */}
        <div className="flex gap-2">
          <Input
            ref={inputRef}
            value={inputVal}
            onChange={(e) => setInputVal(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addIp(); } }}
            placeholder="e.g. 203.0.113.5/32"
            className="h-8 text-xs font-mono"
          />
          <Button size="sm" variant="outline" onClick={addIp} className="h-8 px-3 gap-1.5">
            <Plus size={13} /> Add
          </Button>
        </div>

        {/* bulk add */}
        <div>
          <button
            onClick={() => { setBulkOpen((o) => !o); setMsg(null); }}
            className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors"
          >
            <ListPlus size={13} />
            {bulkOpen ? "Hide bulk add" : "Bulk add"}
          </button>
          {bulkOpen && (
            <div className="mt-2 space-y-2">
              <Textarea
                value={bulkVal}
                onChange={(e) => setBulkVal(e.target.value)}
                placeholder={"Paste IPs, one per line or comma-separated:\n203.0.113.1/32\n198.51.100.0/24\n10.0.0.1"}
                className="text-xs font-mono h-28 resize-y"
                autoFocus
              />
              <div className="flex justify-end gap-2">
                <Button size="sm" variant="ghost" onClick={() => { setBulkVal(""); setBulkOpen(false); }} className="h-7 px-3 text-xs">
                  Cancel
                </Button>
                <Button size="sm" variant="outline" onClick={addBulk} disabled={!bulkVal.trim()} className="h-7 px-3 gap-1.5 text-xs">
                  <Plus size={12} /> Add all
                </Button>
              </div>
            </div>
          )}
        </div>

        {/* copy from other bucket */}
        {otherIps !== undefined && (
          <Button
            size="sm"
            variant="ghost"
            onClick={copyFromOther}
            disabled={otherIps.length === 0}
            className="h-7 px-2 gap-1.5 text-xs text-muted-foreground hover:text-foreground w-full justify-start"
          >
            <Copy size={12} />
            Copy IPs from {otherLabel ?? "other bucket"}
            {otherIps.length > 0 && (
              <span className="ml-1 opacity-60">({otherIps.length})</span>
            )}
          </Button>
        )}

        {/* feedback */}
        {msg && (
          <p className={cn("text-xs", msg.ok ? "text-emerald-400" : "text-destructive")}>
            {msg.text}
          </p>
        )}

        {/* save */}
        <div className="flex justify-end pt-1">
          <Button
            size="sm"
            onClick={save}
            disabled={saving || !dirty}
            className="gap-1.5 h-8"
          >
            {saving ? (
              <RefreshCw size={13} className="animate-spin" />
            ) : (
              <Save size={13} />
            )}
            Save
            {dirty && <span className="ml-1 text-[10px] opacity-70">(unsaved)</span>}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

// ── page ───────────────────────────────────────────────────────────────────────

export default function SecurityPage() {
  const { slug } = useParams<{ slug: string }>();
  const [data, setData] = useState<BucketsSecurityResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      setData(await getBucketSecurity(slug));
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load security data");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, [slug]);

  return (
    <div className="space-y-6 p-6">
      {/* header */}
      <div className="flex items-center justify-between gap-4">
        <div className="flex items-center gap-2">
          <ShieldCheck size={20} className="text-primary" />
          <h1 className="text-xl font-semibold">Security</h1>
        </div>
        <Button variant="outline" size="sm" onClick={load} disabled={loading} className="gap-1.5 h-8">
          <RefreshCw size={13} className={loading ? "animate-spin" : ""} />
          Refresh
        </Button>
      </div>

      {/* description */}
      <p className="text-sm text-muted-foreground max-w-2xl">
        Manage IP allowlists for each Wasabi bucket using S3 bucket policies. When an allowlist is set,
        only requests from those IP addresses (or CIDR ranges) are permitted. Leave the list empty to
        allow access from any IP (credentials still required).
      </p>

      {/* error */}
      {error && (
        <div className="rounded-md border border-destructive/50 bg-destructive/10 px-4 py-3 text-sm text-destructive">
          {error}
        </div>
      )}

      {/* skeletons */}
      {loading && !data && (
        <div className="grid gap-4 md:grid-cols-2">
          <Skeleton className="h-48 rounded-xl" />
          <Skeleton className="h-48 rounded-xl" />
        </div>
      )}

      {/* bucket cards */}
      {data && (
        <div className="grid gap-4 md:grid-cols-2">
          {data.buckets.map((b) => {
            const other = data.buckets.find((x) => x.which !== b.which);
            const otherLabel = other?.which === "attach" ? "Offload Bucket" : other?.which === "backup" ? "Backup Bucket" : undefined;
            return (
              <BucketCard
                key={b.which}
                info={b}
                onSaved={load}
                otherIps={other?.ips}
                otherLabel={otherLabel}
              />
            );
          })}
        </div>
      )}

      {data && data.buckets.length === 0 && (
        <p className="text-sm text-muted-foreground">No buckets configured for this tenant.</p>
      )}
    </div>
  );
}
