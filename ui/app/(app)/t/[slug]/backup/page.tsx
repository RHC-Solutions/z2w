"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { getTenantBackup, runTenantBackupNow, BackupResult, fmtBytes } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { RefreshCw, Play, CheckCircle2, XCircle, Clock, Database } from "lucide-react";

function StatusBadge({ status }: { status: string }) {
  if (status === "success" || status === "completed")
    return <Badge className="text-[10px] bg-emerald-700 hover:bg-emerald-700"><CheckCircle2 className="w-2.5 h-2.5 mr-1" />Success</Badge>;
  if (status === "error" || status === "failed")
    return <Badge variant="destructive" className="text-[10px]"><XCircle className="w-2.5 h-2.5 mr-1" />Error</Badge>;
  if (status === "running")
    return <Badge className="text-[10px] bg-blue-700 hover:bg-blue-700"><Clock className="w-2.5 h-2.5 mr-1 animate-spin" />Running</Badge>;
  return <Badge variant="secondary" className="text-[10px]">{status}</Badge>;
}

function StatCard({ label, value, sub }: { label: string; value: string | number; sub?: string }) {
  return (
    <Card>
      <CardContent className="pt-4 pb-3">
        <p className="text-xs text-muted-foreground">{label}</p>
        <p className="text-2xl font-bold mt-1">{value}</p>
        {sub && <p className="text-xs text-muted-foreground mt-0.5">{sub}</p>}
      </CardContent>
    </Card>
  );
}

export default function BackupPage() {
  const { slug } = useParams<{ slug: string }>();
  const [data, setData] = useState<BackupResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [actionMsg, setActionMsg] = useState<{ text: string; ok: boolean } | null>(null);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      setData(await getTenantBackup(slug));
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load backup data");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, [slug]);

  async function handleRunNow() {
    setRunning(true);
    setActionMsg(null);
    try {
      const res = await runTenantBackupNow(slug);
      setActionMsg({ text: res.message || 'Backup started', ok: true });
    } catch (e: unknown) {
      setActionMsg({ text: e instanceof Error ? e.message : 'Failed to start backup', ok: false });
    } finally {
      setRunning(false);
    }
    setTimeout(() => { setActionMsg(null); load(); }, 4000);
  }

  if (loading) {
    return (
      <div className="p-6 space-y-4">
        <div className="grid grid-cols-3 gap-4">{[...Array(3)].map((_, i) => <Skeleton key={i} className="h-24" />)}</div>
        <Skeleton className="h-64" />
      </div>
    );
  }

  if (!data) {
    return <div className="p-6 text-sm text-destructive">{error || "Failed to load backup data."}</div>;
  }

  const statusCounts = data.status_counts;

  return (
    <div className="p-6 space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold">Ticket Backup</h1>
          <p className="text-sm text-muted-foreground">
            {data.backup_enabled ? `Runs daily at ${data.backup_time}` : "Backup disabled"}
            {!data.backup_enabled && (
              <Badge variant="outline" className="ml-2 text-[10px]">Disabled</Badge>
            )}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button size="sm" variant="ghost" onClick={() => load()}>
            <RefreshCw className="w-3.5 h-3.5" />
          </Button>
          <Button size="sm" onClick={handleRunNow} disabled={running}>
            <Play className="w-3.5 h-3.5 mr-1" />
            {running ? "Starting…" : "Run Now"}
          </Button>
        </div>
      </div>

      {/* Action feedback */}
      {actionMsg && (
        <div className={`text-xs px-3 py-2 rounded border ${actionMsg.ok ? 'bg-emerald-950/40 border-emerald-700/50 text-emerald-300' : 'bg-destructive/10 border-destructive/40 text-destructive'}`}>
          {actionMsg.text}
        </div>
      )}

      {/* Stats */}
      <div className="grid grid-cols-3 gap-4">
        <StatCard label="Total Runs" value={data.totals.total_runs} />
        <StatCard label="Tickets Backed Up" value={data.totals.total_tickets.toLocaleString()} />
        <StatCard label="Total Uploaded" value={fmtBytes(data.totals.total_bytes)} />
      </div>

      {/* Item status breakdown */}
      {Object.keys(statusCounts).length > 0 && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm flex items-center gap-2">
              <Database className="w-4 h-4 text-primary" /> Backup Item Status
            </CardTitle>
          </CardHeader>
          <CardContent className="flex flex-wrap gap-2">
            {Object.entries(statusCounts).map(([s, c]) => (
              <div key={s} className="flex items-center gap-1 text-xs bg-muted/40 border border-border/50 rounded px-2 py-1">
                <span className="text-muted-foreground">{s}:</span>
                <span className="font-semibold">{(c as number).toLocaleString()}</span>
              </div>
            ))}
          </CardContent>
        </Card>
      )}

      {/* Recent runs table */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm">Recent Runs</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          {data.runs.length === 0 ? (
            <p className="text-sm text-muted-foreground px-4 py-6 text-center">No backup runs yet.</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="text-xs">Date</TableHead>
                  <TableHead className="text-xs">Status</TableHead>
                  <TableHead className="text-xs text-right">Scanned</TableHead>
                  <TableHead className="text-xs text-right">Backed Up</TableHead>
                  <TableHead className="text-xs text-right">Files</TableHead>
                  <TableHead className="text-xs text-right">Size</TableHead>
                  <TableHead className="text-xs text-right">Errors</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.runs.map((r) => (
                  <TableRow key={r.id} className="text-xs">
                    <TableCell className="font-mono text-[11px] text-muted-foreground">
                      {r.run_date ? r.run_date.replace("T", " ").replace(/\.\d+$/, "") : "—"}
                    </TableCell>
                    <TableCell><StatusBadge status={r.status} /></TableCell>
                    <TableCell className="text-right tabular-nums">{r.tickets_scanned.toLocaleString()}</TableCell>
                    <TableCell className="text-right tabular-nums">{r.tickets_backed_up.toLocaleString()}</TableCell>
                    <TableCell className="text-right tabular-nums">{r.files_uploaded.toLocaleString()}</TableCell>
                    <TableCell className="text-right tabular-nums text-muted-foreground">
                      {r.bytes_uploaded > 0 ? fmtBytes(r.bytes_uploaded) : "—"}
                    </TableCell>
                    <TableCell className="text-right">
                      {r.errors_count > 0
                        ? <span className="text-destructive font-semibold">{r.errors_count}</span>
                        : <span className="text-emerald-400">0</span>
                      }
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
