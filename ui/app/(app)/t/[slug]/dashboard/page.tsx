"use client";

import { useEffect, useState, useCallback } from "react";
import { useParams } from "next/navigation";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Skeleton } from "@/components/ui/skeleton";
import { fmtBytes, fmtNum, type DashboardStats } from "@/lib/api";
import {
  AlertTriangle, Play, Square, RefreshCw, Clock,
  HardDrive, ArrowUpFromLine, Archive, Zap, AlertCircle,
} from "lucide-react";

const REFRESH_S = 60;

export default function DashboardPage() {
  const { slug } = useParams<{ slug: string }>();
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [countdown, setCountdown] = useState(REFRESH_S);
  const [actionMsg, setActionMsg] = useState("");

  const load = useCallback(() => {
    fetch(`/api/t/${slug}/dashboard_stats`, { credentials: "include" })
      .then((r) => r.json())
      .then((d) => setStats(d))
      .finally(() => { setLoading(false); setCountdown(REFRESH_S); });
  }, [slug]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    const t = setInterval(() => {
      setCountdown((c) => {
        if (c <= 1) { load(); return REFRESH_S; }
        return c - 1;
      });
    }, 1000);
    return () => clearInterval(t);
  }, [load]);

  async function action(url: string, label: string) {
    setActionMsg(`Running ${label}…`);
    await fetch(url, { method: "POST", credentials: "include" });
    setActionMsg(`${label} triggered`);
    setTimeout(() => { setActionMsg(""); load(); }, 2000);
  }

  if (loading) return <PageSkeleton />;
  if (!stats) return <div className="p-6 text-muted-foreground">Failed to load stats.</div>;

  return (
    <div className="p-6 space-y-5 max-w-6xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <div className="flex items-center gap-2.5">
            {stats.color && (
              <span
                className="w-3 h-3 rounded-full flex-shrink-0"
                style={{ backgroundColor: stats.color }}
              />
            )}
            <h1 className="text-2xl font-bold">
              {stats.display_name || slug}
            </h1>
            <span className="text-sm text-muted-foreground font-normal">/ Dashboard</span>
          </div>
          <p className="text-sm text-muted-foreground">{slug}.zendesk.com · auto-refreshes every {REFRESH_S}s</p>
        </div>
        <div className="flex items-center gap-2">
          {actionMsg && <span className="text-xs text-muted-foreground">{actionMsg}</span>}
          <Badge variant="outline" className="text-xs gap-1">
            <RefreshCw size={11} className="animate-spin" style={{ animationDuration: "3s" }} />
            {countdown}s
          </Badge>
          <Button size="sm" variant="outline" onClick={() => action(`/api/t/${slug}/run_now`, "Offload")}>
            <Play size={13} className="mr-1.5" /> Run Offload
          </Button>
          <Button size="sm" variant="outline" onClick={() => action(`/api/t/${slug}/backup_now`, "Backup")}>
            <ArrowUpFromLine size={13} className="mr-1.5" /> Run Backup
          </Button>
        </div>
      </div>

      {/* Red flag alerts */}
      {stats.red_flags?.length > 0 && (
        <div className="space-y-2">
          {stats.red_flags.map((f) => (
            <Alert key={f} variant="destructive">
              <AlertTriangle size={14} />
              <AlertDescription className="font-medium">{f}</AlertDescription>
            </Alert>
          ))}
        </div>
      )}

      {/* Stat cards */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
        <StatCard icon={<ArrowUpFromLine size={15} />} label="Tickets Offloaded" value={fmtNum(stats.total_tickets)} sub={`${fmtNum(stats.total_attachments)} attach · ${fmtNum(stats.total_inlines)} inline`} color="primary" />
        <StatCard icon={<HardDrive size={15} />} label="Saved to Wasabi" value={fmtBytes(stats.total_bytes)} sub={`${fmtNum(stats.total_attachments + stats.total_inlines)} files`} color="primary" />
        <StatCard icon={<Archive size={15} />} label="Tickets Backed Up" value={fmtNum(stats.backup_success)} color="success" />
        <StatCard icon={<Clock size={15} />} label="Last Offload" value={stats.last_offload_ago ?? "Never"} sub={stats.offload_scheduler_running ? "● Running" : "● Stopped"} subColor={stats.offload_scheduler_running ? "success" : "muted"} />
        <StatCard icon={<AlertCircle size={15} />} label="Ticket Errors" value={fmtNum(stats.error_tickets_count)} color={stats.error_tickets_count > 0 ? "destructive" : undefined} />
        <StatCard icon={<Zap size={15} />} label="Today's Activity" value={fmtNum(stats.today_tickets)} sub={`${stats.today_att} attach · ${stats.today_inlines} inline · ${stats.today_runs} runs`} />
      </div>

      {/* Scheduler + recent errors */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-sm font-semibold">Scheduler</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <SchedulerRow
              label="OFFLOAD"
              running={stats.offload_scheduler_running}
              next={stats.offload_next}
              onStart={() => action("/api/scheduler/offload/start", "Start Offload")}
              onStop={() => action("/api/scheduler/offload/stop", "Stop Offload")}
            />
            <SchedulerRow
              label="BACKUP"
              running={stats.backup_scheduler_running}
              next={stats.backup_next}
              onStart={() => action("/api/scheduler/backup/start", "Start Backup")}
              onStop={() => action("/api/scheduler/backup/stop", "Stop Backup")}
            />
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-sm font-semibold">Recent Errors</CardTitle>
          </CardHeader>
          <CardContent>
            {(!stats.recent_errors || stats.recent_errors.length === 0) ? (
              <p className="text-sm text-muted-foreground">No errors 🎉</p>
            ) : (
              <div className="space-y-2">
                {stats.recent_errors.map((e) => (
                  <div key={e.ticket_id} className="text-xs border border-border rounded-md p-2">
                    <div className="font-medium text-destructive">Ticket #{e.ticket_id}</div>
                    <div className="text-muted-foreground truncate">{e.error}</div>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

function StatCard({
  icon, label, value, sub, color, subColor,
}: {
  icon: React.ReactNode; label: string; value: string; sub?: string;
  color?: "primary" | "success" | "destructive";
  subColor?: "success" | "muted";
}) {
  const vc = color === "primary" ? "text-primary" : color === "success" ? "text-[var(--success)]" : color === "destructive" ? "text-destructive" : "text-foreground";
  const sc = subColor === "success" ? "text-[var(--success)]" : "text-muted-foreground";
  return (
    <Card className="bg-card border-border">
      <CardContent className="pt-4 pb-4">
        <div className="flex items-center gap-1.5 text-xs text-muted-foreground mb-1">{icon} {label}</div>
        <div className={`text-xl font-bold leading-tight ${vc}`}>{value}</div>
        {sub && <div className={`text-[11px] mt-0.5 ${sc}`}>{sub}</div>}
      </CardContent>
    </Card>
  );
}

function SchedulerRow({
  label, running, next, onStart, onStop,
}: {
  label: string; running: boolean; next?: string | null;
  onStart: () => void; onStop: () => void;
}) {
  const nextTime = next ? new Date(next).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" }) : null;
  return (
    <div className="flex items-center justify-between gap-2">
      <div className="min-w-0">
        <div className="text-xs font-semibold text-muted-foreground">{label}</div>
        <div className={`text-sm font-medium flex items-center gap-1.5 ${running ? "text-[var(--success)]" : "text-muted-foreground"}`}>
          <span className="text-base leading-none">{running ? "●" : "○"}</span>
          {running ? `Running${nextTime ? ` · next ${nextTime}` : ""}` : "Stopped"}
        </div>
      </div>
      <div className="flex gap-1.5 flex-shrink-0">
        <Button size="sm" className="h-7 text-xs bg-[var(--success)]/10 text-[var(--success)] hover:bg-[var(--success)]/20 border-0" onClick={onStart} disabled={running}>Start</Button>
        <Button size="sm" variant="outline" className="h-7 text-xs" onClick={onStop} disabled={!running}>Stop</Button>
      </div>
    </div>
  );
}

function PageSkeleton() {
  return (
    <div className="p-6 space-y-5">
      <Skeleton className="h-8 w-48" />
      <div className="grid grid-cols-3 lg:grid-cols-6 gap-3">
        {Array(6).fill(0).map((_, i) => <Skeleton key={i} className="h-24" />)}
      </div>
      <div className="grid grid-cols-2 gap-4">
        <Skeleton className="h-40" />
        <Skeleton className="h-40" />
      </div>
    </div>
  );
}
