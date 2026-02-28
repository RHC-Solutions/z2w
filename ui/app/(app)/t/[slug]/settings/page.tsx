"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import {
  getTenantSettings,
  saveTenantSettings,
  testTenantConnection,
  TenantSettings,
} from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { Separator } from "@/components/ui/separator";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Settings2,
  KeyRound,
  Cloud,
  Database,
  Bell,
  Clock,
  Download,
  Upload,
  CheckCircle2,
  XCircle,
  Loader2,
  Wifi,
} from "lucide-react";

function TestButton({ slug, type }: { slug: string; type: string }) {
  const [result, setResult] = useState<{ success: boolean; message: string } | null>(null);
  const [testing, setTesting] = useState(false);

  async function handleTest() {
    setTesting(true);
    setResult(null);
    try {
      const r = await testTenantConnection(slug, type);
      setResult(r);
    } catch (e: unknown) {
      setResult({ success: false, message: e instanceof Error ? e.message : "Error" });
    } finally {
      setTesting(false);
    }
  }

  return (
    <div className="flex items-center gap-2">
      <Button size="sm" variant="outline" onClick={handleTest} disabled={testing}>
        {testing
          ? <Loader2 className="w-3.5 h-3.5 animate-spin mr-1" />
          : <Wifi className="w-3.5 h-3.5 mr-1" />
        }
        Test Connection
      </Button>
      {result && (
        <span className={`text-xs flex items-center gap-1 ${result.success ? "text-emerald-400" : "text-destructive"}`}>
          {result.success ? <CheckCircle2 className="w-3.5 h-3.5" /> : <XCircle className="w-3.5 h-3.5" />}
          {result.message}
        </span>
      )}
    </div>
  );
}

type DraftSettings = Omit<TenantSettings, "slug">;

/** Replace every null/undefined field with a safe default so controlled inputs never crash. */
function normalizeSettings(s: TenantSettings): DraftSettings {
  const { slug: _slug, ...rest } = s;
  const strings: Array<keyof typeof rest> = [
    "display_name","color","zendesk_subdomain","zendesk_email","zendesk_api_token",
    "wasabi_endpoint","wasabi_access_key","wasabi_secret_key","wasabi_bucket_name",
    "ticket_backup_endpoint","ticket_backup_bucket",
    "telegram_bot_token","telegram_chat_id","slack_webhook_url",
    "scheduler_timezone","ticket_backup_time",
  ];
  const numbers: Array<keyof typeof rest> = [
    "full_offload_interval","continuous_offload_interval","ticket_backup_max_per_run",
    "max_attachments_per_run","storage_report_interval",
  ];
  const booleans: Array<keyof typeof rest> = [
    "is_active","attach_offload_enabled","ticket_backup_enabled",
    "alert_on_offload_error","alert_on_backup_error","alert_daily_report",
    "alert_daily_telegram","alert_daily_slack",
    "alert_include_offload_stats","alert_include_backup_stats","alert_include_errors_detail",
  ];
  const out = { ...rest } as Record<string, unknown>;
  for (const k of strings) if (out[k as string] == null) out[k as string] = "";
  for (const k of numbers) if (out[k as string] == null) out[k as string] = 0;
  for (const k of booleans) if (out[k as string] == null) out[k as string] = false;
  return out as DraftSettings;
}

function FieldRow({
  label,
  hint,
  children,
  full,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
  full?: boolean;
}) {
  return (
    <div className={full ? "col-span-2" : ""}>
      <Label className="text-xs font-semibold text-muted-foreground mb-1 block">
        {label}
      </Label>
      {children}
      {hint && <p className="text-[11px] text-muted-foreground/70 mt-0.5">{hint}</p>}
    </div>
  );
}

function SwitchRow({
  label,
  checked,
  onCheckedChange,
  indent,
}: {
  label: string;
  checked: boolean | null | undefined;
  onCheckedChange: (v: boolean) => void;
  indent?: boolean;
}) {
  return (
    <div className={`flex items-center gap-2 text-sm ${indent ? "ml-5" : ""}`}>
      <Switch checked={checked ?? false} onCheckedChange={onCheckedChange} />
      <span className="text-muted-foreground">{label}</span>
    </div>
  );
}

export default function SettingsPage() {
  const { slug } = useParams<{ slug: string }>();
  const [settings, setSettings] = useState<DraftSettings | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState<boolean | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    getTenantSettings(slug)
      .then((s) => setSettings(normalizeSettings(s)))
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [slug]);

  function set<K extends keyof DraftSettings>(key: K, value: DraftSettings[K]) {
    setSettings((prev) => prev ? { ...prev, [key]: value } : prev);
  }

  async function handleSave() {
    if (!settings) return;
    setSaving(true);
    setSaved(null);
    try {
      await saveTenantSettings(slug, settings);
      setSaved(true);
      setTimeout(() => setSaved(null), 3000);
      // Notify sidebar to refresh tenant list (color, name changes)
      window.dispatchEvent(new CustomEvent('z2w:tenantUpdated'));
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Save failed");
      setSaved(false);
    } finally {
      setSaving(false);
    }
  }

  function handleExport() {
    window.location.href = `/api/t/${slug}/settings/export`;
  }

  function handleImport() {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = ".json,application/json";
    input.onchange = async () => {
      if (!input.files?.[0]) return;
      const form = new FormData();
      form.append("file", input.files[0]);
      const r = await fetch(`/api/t/${slug}/settings/import`, { method: "POST", credentials: "include", body: form });
      const d = await r.json();
      if (d.success) {
        setSaved(true);
        setTimeout(() => window.location.reload(), 1200);
      } else {
        setError(d.message || "Import failed");
      }
    };
    input.click();
  }

  if (loading) {
    return (
      <div className="p-6 space-y-4 max-w-3xl mx-auto">
        {[...Array(4)].map((_, i) => (
          <Skeleton key={i} className="h-48 w-full rounded-xl" />
        ))}
      </div>
    );
  }

  if (!settings) {
    return (
      <div className="p-6 max-w-3xl mx-auto">
        <p className="text-destructive">{error || "Failed to load settings."}</p>
      </div>
    );
  }

  const SaveBar = () => (
    <div className="flex items-center justify-between">
      <div className="flex items-center gap-2">
        <Button onClick={handleSave} disabled={saving} size="sm" className="min-w-[90px]">
          {saving ? "Saving…" : "Save"}
        </Button>
        {saved === true && (
          <span className="flex items-center gap-1 text-xs text-emerald-400">
            <CheckCircle2 className="w-3.5 h-3.5" /> Saved
          </span>
        )}
        {saved === false && (
          <span className="flex items-center gap-1 text-xs text-destructive">
            <XCircle className="w-3.5 h-3.5" /> {error}
          </span>
        )}
      </div>
    </div>
  );

  return (
    <div className="p-6 max-w-3xl mx-auto space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold">{settings.display_name || slug} — Settings</h1>
          <p className="text-sm text-muted-foreground">{settings.zendesk_subdomain}.zendesk.com</p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={handleExport}>
            <Download className="w-3.5 h-3.5 mr-1" /> Export
          </Button>
          <Button variant="outline" size="sm" onClick={handleImport}>
            <Upload className="w-3.5 h-3.5 mr-1" /> Import
          </Button>
        </div>
      </div>

      {/* General */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm flex items-center gap-2">
            <Settings2 className="w-4 h-4 text-primary" /> General
          </CardTitle>
        </CardHeader>
        <CardContent className="grid grid-cols-2 gap-4">
          <FieldRow label="Display Name" full>
            <Input value={settings.display_name} onChange={(e) => set("display_name", e.target.value)} />
          </FieldRow>
          <FieldRow label="Tenant Color" hint="Used for the sidebar icon and dashboard badge">
            <div className="flex items-center gap-2">
              <div
                className="w-8 h-8 rounded-md border border-border flex-shrink-0 cursor-pointer relative overflow-hidden"
                style={{ backgroundColor: settings.color || "#14b8a6" }}
              >
                <input
                  type="color"
                  value={settings.color || "#14b8a6"}
                  onChange={(e) => set("color", e.target.value)}
                  className="absolute inset-0 opacity-0 cursor-pointer w-full h-full"
                  title="Pick a color"
                />
              </div>
              <div className="flex flex-wrap gap-1.5">
                {["#14b8a6","#3b82f6","#8b5cf6","#ec4899","#f97316","#22c55e","#ef4444","#eab308","#06b6d4","#64748b"].map((c) => (
                  <button
                    key={c}
                    type="button"
                    onClick={() => set("color", c)}
                    className="w-5 h-5 rounded-full border-2 transition-all hover:scale-110"
                    style={{
                      backgroundColor: c,
                      borderColor: settings.color === c ? "white" : "transparent",
                    }}
                  />
                ))}
              </div>
              <Input
                value={settings.color || ""}
                onChange={(e) => set("color", e.target.value)}
                placeholder="#14b8a6"
                className="w-28 font-mono text-xs"
                maxLength={7}
              />
            </div>
          </FieldRow>
        </CardContent>
      </Card>

      {/* Zendesk */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm flex items-center gap-2">
            <KeyRound className="w-4 h-4 text-primary" /> Zendesk
          </CardTitle>
        </CardHeader>
        <CardContent className="grid grid-cols-2 gap-4">
          <FieldRow label="Subdomain" hint={`${settings.zendesk_subdomain || "…"}.zendesk.com`}>
            <Input value={settings.zendesk_subdomain} onChange={(e) => set("zendesk_subdomain", e.target.value)} />
          </FieldRow>
          <FieldRow label="Admin Email">
            <Input type="email" value={settings.zendesk_email} onChange={(e) => set("zendesk_email", e.target.value)} />
          </FieldRow>
          <FieldRow label="API Token" hint="Leave as ••• to keep unchanged" full>
            <Input
              type="password"
              value={settings.zendesk_api_token}
              onChange={(e) => set("zendesk_api_token", e.target.value)}
              placeholder="(unchanged if ●●●)"
            />
          </FieldRow>
        </CardContent>
        <div className="px-6 pb-4 flex items-center justify-between"><SaveBar /><TestButton slug={slug} type="zendesk" /></div>
      </Card>

      {/* Wasabi — Offload */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm flex items-center gap-2">
            <Cloud className="w-4 h-4 text-emerald-400" /> Wasabi — Offload Bucket
          </CardTitle>
        </CardHeader>
        <CardContent className="grid grid-cols-2 gap-4">
          <FieldRow label="Endpoint">
            <Input value={settings.wasabi_endpoint} onChange={(e) => set("wasabi_endpoint", e.target.value)} placeholder="s3.wasabisys.com" />
          </FieldRow>
          <FieldRow label="Bucket Name">
            <Input value={settings.wasabi_bucket_name} onChange={(e) => set("wasabi_bucket_name", e.target.value)} />
          </FieldRow>
          <FieldRow label="Access Key">
            <Input value={settings.wasabi_access_key} onChange={(e) => set("wasabi_access_key", e.target.value)} />
          </FieldRow>
          <FieldRow label="Secret Key">
            <Input type="password" value={settings.wasabi_secret_key} onChange={(e) => set("wasabi_secret_key", e.target.value)} placeholder="(unchanged if ●●●)" />
          </FieldRow>
        </CardContent>
        <div className="px-6 pb-4 flex items-center justify-between"><SaveBar /><TestButton slug={slug} type="wasabi" /></div>
      </Card>

      {/* Wasabi — Backup */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm flex items-center gap-2">
            <Database className="w-4 h-4 text-emerald-400" /> Wasabi — Backup Bucket
          </CardTitle>
        </CardHeader>
        <CardContent className="grid grid-cols-2 gap-4">
          <FieldRow label="Endpoint">
            <Input value={settings.ticket_backup_endpoint} onChange={(e) => set("ticket_backup_endpoint", e.target.value)} placeholder="s3.eu-central-1.wasabisys.com" />
          </FieldRow>
          <FieldRow label="Bucket Name">
            <Input value={settings.ticket_backup_bucket} onChange={(e) => set("ticket_backup_bucket", e.target.value)} />
          </FieldRow>
        </CardContent>
        <div className="px-6 pb-4 flex items-center justify-between"><SaveBar /><TestButton slug={slug} type="wasabi_backup" /></div>
      </Card>

      {/* Scheduler */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm flex items-center gap-2">
            <Clock className="w-4 h-4 text-primary" /> Scheduler
          </CardTitle>
        </CardHeader>
        <CardContent className="grid grid-cols-2 gap-4">
          <FieldRow label="Timezone">
            <Input value={settings.scheduler_timezone} onChange={(e) => set("scheduler_timezone", e.target.value)} placeholder="UTC" />
          </FieldRow>
          <FieldRow label="Full Offload Interval (min)" hint="How often the full offload job runs (affects all tenants; takes effect immediately)">
            <Input type="number" min={1} value={settings.full_offload_interval ?? 5} onChange={(e) => set("full_offload_interval", Number(e.target.value))} />
          </FieldRow>
          <FieldRow label="Continuous Offload Interval (min)">
            <Input type="number" value={settings.continuous_offload_interval} onChange={(e) => set("continuous_offload_interval", Number(e.target.value))} />
          </FieldRow>
          <FieldRow label="Backup Time (HH:MM)">
            <Input value={settings.ticket_backup_time} onChange={(e) => set("ticket_backup_time", e.target.value)} placeholder="01:00" />
          </FieldRow>
          <FieldRow label="Backup Max Per Run (0=unlimited)">
            <Input type="number" value={settings.ticket_backup_max_per_run} onChange={(e) => set("ticket_backup_max_per_run", Number(e.target.value))} />
          </FieldRow>
          <FieldRow label="Max Attachments Per Run (0=unlimited)">
            <Input type="number" value={settings.max_attachments_per_run} onChange={(e) => set("max_attachments_per_run", Number(e.target.value))} />
          </FieldRow>
          <FieldRow label="Storage Report Interval (min)">
            <Input type="number" value={settings.storage_report_interval} onChange={(e) => set("storage_report_interval", Number(e.target.value))} />
          </FieldRow>
          <div className="col-span-2 space-y-2 pt-1">
            <SwitchRow label="Attachment offload enabled" checked={settings.attach_offload_enabled} onCheckedChange={(v) => set("attach_offload_enabled", v)} />
            <SwitchRow label="Ticket backup enabled" checked={settings.ticket_backup_enabled} onCheckedChange={(v) => set("ticket_backup_enabled", v)} />
          </div>
        </CardContent>
        <div className="px-6 pb-4"><SaveBar /></div>
      </Card>

      {/* Telegram */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm flex items-center gap-2">
            <svg className="w-4 h-4" viewBox="0 0 32 32" fill="none"><circle cx="16" cy="16" r="16" fill="#29B6F6"/><path d="M6.5 15.8 L25.5 8.5 L20.5 24 L15 18.5 L22 12 L13 17.5 Z" fill="#fff"/></svg>
            Telegram
          </CardTitle>
        </CardHeader>
        <CardContent className="grid grid-cols-2 gap-4">
          <FieldRow label="Bot Token">
            <Input type="password" value={settings.telegram_bot_token} onChange={(e) => set("telegram_bot_token", e.target.value)} placeholder="(unchanged if ●●●)" />
          </FieldRow>
          <FieldRow label="Chat ID">
            <Input value={settings.telegram_chat_id} onChange={(e) => set("telegram_chat_id", e.target.value)} />
          </FieldRow>
        </CardContent>
        <div className="px-6 pb-4 flex items-center justify-between"><SaveBar /><TestButton slug={slug} type="telegram" /></div>
      </Card>

      {/* Slack */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm flex items-center gap-2">
            <svg className="w-4 h-4" viewBox="0 0 32 32" fill="none"><circle cx="16" cy="16" r="16" fill="#4A154B"/><text x="9" y="21" fontSize="14" fill="#ECB22E" fontWeight="bold">#</text></svg>
            Slack
          </CardTitle>
        </CardHeader>
        <CardContent className="grid grid-cols-1 gap-4">
          <FieldRow label="Webhook URL" full>
            <Input value={settings.slack_webhook_url} onChange={(e) => set("slack_webhook_url", e.target.value)} placeholder="https://hooks.slack.com/services/…" />
          </FieldRow>
        </CardContent>
        <div className="px-6 pb-4 flex items-center justify-between"><SaveBar /><TestButton slug={slug} type="slack" /></div>
      </Card>

      {/* Alerts */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm flex items-center gap-2">
            <Bell className="w-4 h-4 text-primary" /> Alerts &amp; Notifications
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div>
            <p className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider mb-2">Immediate Alerts (on failure)</p>
            <div className="space-y-2">
              <SwitchRow label="Alert when offload job crashes" checked={settings.alert_on_offload_error} onCheckedChange={(v) => set("alert_on_offload_error", v)} />
              <SwitchRow label="Alert when backup job crashes" checked={settings.alert_on_backup_error} onCheckedChange={(v) => set("alert_on_backup_error", v)} />
            </div>
          </div>
          <Separator />
          <div>
            <p className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider mb-2">Daily Report (sent at 00:01)</p>
            <div className="space-y-2">
              <SwitchRow label="Enable daily summary report" checked={settings.alert_daily_report} onCheckedChange={(v) => set("alert_daily_report", v)} />
              <SwitchRow label="Send via Telegram" checked={settings.alert_daily_telegram} onCheckedChange={(v) => set("alert_daily_telegram", v)} indent />
              <SwitchRow label="Send via Slack" checked={settings.alert_daily_slack} onCheckedChange={(v) => set("alert_daily_slack", v)} indent />
            </div>
          </div>
          <Separator />
          <div>
            <p className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider mb-2">Daily Report Content</p>
            <div className="space-y-2">
              <SwitchRow label="Include attachment offload stats" checked={settings.alert_include_offload_stats} onCheckedChange={(v) => set("alert_include_offload_stats", v)} />
              <SwitchRow label="Include closed-ticket backup stats" checked={settings.alert_include_backup_stats} onCheckedChange={(v) => set("alert_include_backup_stats", v)} />
              <SwitchRow label="Include error detail lines" checked={settings.alert_include_errors_detail} onCheckedChange={(v) => set("alert_include_errors_detail", v)} />
            </div>
          </div>
        </CardContent>
        <div className="px-6 pb-4"><SaveBar /></div>
      </Card>

      {/* Active toggle */}
      <Card>
        <CardContent className="pt-4 flex items-center justify-between">
          <div>
            <p className="text-sm font-medium">Tenant Active</p>
            <p className="text-xs text-muted-foreground">When disabled the scheduler skips this tenant.</p>
          </div>
          <div className="flex items-center gap-2">
            <Switch
              checked={settings.is_active ?? false}
              onCheckedChange={(v) => {
                set("is_active", v);
                // Save immediately with explicit value (never use blind toggle)
                fetch(`/api/t/${slug}/settings`, {
                  method: "POST",
                  headers: { "Content-Type": "application/json" },
                  credentials: "include",
                  body: JSON.stringify({ is_active: v }),
                });
              }}
            />
            <Badge variant={settings.is_active ? "default" : "secondary"}>
              {settings.is_active ? "Active" : "Paused"}
            </Badge>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
