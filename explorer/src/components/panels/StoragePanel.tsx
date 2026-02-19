"use client";

import { useState, useEffect, useCallback } from "react";
import { RefreshCw, Database, AlertTriangle, ArrowDownToLine, HardDrive } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { formatBytes, formatDate, z2wFetch } from "@/lib/api";
import type { StoredCreds } from "@/lib/storage";

interface StorageRow {
  ticket_id: number;
  subject: string;
  zd_status: string;
  attach_count: number;
  inline_count: number;
  total_size: number;
  last_seen_at: string;
}

interface StorageReport {
  rows: StorageRow[];
  summary: {
    total_tickets: number;
    total_files: number;
    total_bytes: number;
    by_status: Record<string, { tickets: number; files: number; bytes: number }>;
  };
  scan: {
    scanned: number;
    total: number;
    pct: number;
  };
  offloaded: {
    bytes: number;
    tickets: number;
    tickets_with_files: number;
  };
  plan_limit_gb: number;
  last_updated: string | null;
  next_run: string | null;
  is_empty: boolean;
}

interface Props {
  creds: StoredCreds | null;
}

const STATUS_BADGE: Record<string, string> = {
  open: "bg-blue-100 text-blue-800",
  pending: "bg-yellow-100 text-yellow-800",
  solved: "bg-green-100 text-green-800",
  closed: "bg-gray-100 text-gray-700",
};

export function StoragePanel({ creds }: Props) {
  const [report, setReport] = useState<StorageReport | null>(null);
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState("all");

  const loadReport = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = (await z2wFetch("/api/storage_report")) as StorageReport;
      setReport(data);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadReport();
  }, [loadReport]);

  const handleRefresh = async () => {
    setRefreshing(true);
    try {
      await z2wFetch("/api/storage_report/refresh", { method: "POST" });
      await new Promise((r) => setTimeout(r, 3000));
      await loadReport();
    } catch {
      /* ignore */
    } finally {
      setRefreshing(false);
    }
  };

  const filteredRows =
    report?.rows.filter(
      (r) => activeTab === "all" || r.zd_status === activeTab,
    ) ?? [];
  const statusTabs = ["all", "open", "pending", "solved", "closed"];

  // Compute plan usage
  const planLimitBytes = (report?.plan_limit_gb ?? 0) * 1024 * 1024 * 1024;
  const zdUsedBytes = report?.summary.total_bytes ?? 0;
  const planPct =
    planLimitBytes > 0
      ? Math.min((zdUsedBytes / planLimitBytes) * 100, 100)
      : 0;
  const remainingBytes =
    planLimitBytes > 0 ? Math.max(planLimitBytes - zdUsedBytes, 0) : 0;

  return (
    <div className="space-y-4">
      {/* Scan progress banner */}
      {report && report.scan.scanned < report.scan.total && (
        <Card className="border-yellow-500/40 bg-yellow-500/5">
          <CardContent className="py-3 flex items-center gap-3">
            <RefreshCw className="h-4 w-4 text-yellow-600 animate-spin" />
            <div className="flex-1">
              <p className="text-sm font-medium">Storage scan in progress</p>
              <div className="flex items-center gap-2 mt-1">
                <div className="flex-1 h-2 bg-muted rounded-full overflow-hidden">
                  <div
                    className="h-full bg-yellow-500 rounded-full transition-all"
                    style={{ width: `${report.scan.pct}%` }}
                  />
                </div>
                <span className="text-xs text-muted-foreground whitespace-nowrap">
                  {report.scan.scanned.toLocaleString()} /{" "}
                  {report.scan.total.toLocaleString()} tickets (
                  {report.scan.pct}%)
                </span>
              </div>
              <p className="text-xs text-muted-foreground mt-1">
                Sizes below reflect only scanned tickets. Full data available
                once the scan completes.
              </p>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Summary cards — Zendesk focused */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        {/* Zendesk plan storage */}
        {report && report.plan_limit_gb > 0 ? (
          <Card>
            <CardHeader className="pb-1 pt-3 px-4">
              <CardDescription className="text-xs flex items-center gap-1">
                <HardDrive className="h-3 w-3" /> Zendesk Plan
              </CardDescription>
            </CardHeader>
            <CardContent className="px-4 pb-3">
              <p className="text-xl font-bold">{report.plan_limit_gb} GB</p>
              <div className="flex items-center gap-2 mt-1">
                <div className="flex-1 h-1.5 bg-muted rounded-full overflow-hidden">
                  <div
                    className="h-full bg-primary rounded-full"
                    style={{ width: `${planPct}%` }}
                  />
                </div>
                <span className="text-xs text-muted-foreground">
                  {planPct.toFixed(1)}%
                </span>
              </div>
              <p className="text-xs text-muted-foreground mt-0.5">
                {formatBytes(remainingBytes)} remaining
              </p>
            </CardContent>
          </Card>
        ) : (
          <Card>
            <CardHeader className="pb-1 pt-3 px-4">
              <CardDescription className="text-xs flex items-center gap-1">
                <HardDrive className="h-3 w-3" /> Zendesk Plan
              </CardDescription>
            </CardHeader>
            <CardContent className="px-4 pb-3">
              <p className="text-sm text-muted-foreground">Not configured</p>
              <p className="text-xs text-muted-foreground mt-0.5">
                Set in{" "}
                <a
                  href="/settings"
                  target="_top"
                  className="text-primary underline"
                >
                  Settings
                </a>
              </p>
            </CardContent>
          </Card>
        )}

        {/* Zendesk storage still in use */}
        <Card>
          <CardHeader className="pb-1 pt-3 px-4">
            <CardDescription className="text-xs flex items-center gap-1">
              <Database className="h-3 w-3" /> Still in Zendesk
            </CardDescription>
          </CardHeader>
          <CardContent className="px-4 pb-3">
            <p className="text-xl font-bold text-destructive">
              {report ? formatBytes(report.summary.total_bytes) : "–"}
            </p>
            <p className="text-xs text-muted-foreground mt-0.5">
              {report?.summary.total_files ?? "–"} files ·{" "}
              {report?.summary.total_tickets ?? "–"} tickets
            </p>
          </CardContent>
        </Card>

        {/* Freed from Zendesk */}
        <Card>
          <CardHeader className="pb-1 pt-3 px-4">
            <CardDescription className="text-xs flex items-center gap-1">
              <ArrowDownToLine className="h-3 w-3" /> Freed from Zendesk
            </CardDescription>
          </CardHeader>
          <CardContent className="px-4 pb-3">
            <p className="text-xl font-bold text-green-600">
              {report ? formatBytes(report.offloaded.bytes) : "–"}
            </p>
            <p className="text-xs text-muted-foreground mt-0.5">
              {report?.offloaded.tickets.toLocaleString() ?? "–"} of{" "}
              {report?.offloaded.tickets_with_files.toLocaleString() ?? "–"}{" "}
              tickets with files
            </p>
          </CardContent>
        </Card>
      </div>

      {/* Last scanned / Next scan row */}
      <div className="flex items-center gap-4 text-xs text-muted-foreground">
        <span>
          Last scanned:{" "}
          <b className="text-foreground">
            {report?.last_updated ? formatDate(report.last_updated) : "–"}
          </b>
        </span>
        <span>
          Next scan:{" "}
          <b className="text-foreground">
            {report?.next_run ? formatDate(report.next_run) : "–"}
          </b>
        </span>
        <Button
          size="sm"
          variant="outline"
          className="h-6 text-xs px-2 gap-1 ml-auto"
          onClick={handleRefresh}
          disabled={refreshing}
        >
          <RefreshCw
            className={`h-3 w-3 ${refreshing ? "animate-spin" : ""}`}
          />
          {refreshing ? "Scanning…" : "Scan Now"}
        </Button>
      </div>

      {error && (
        <Card className="border-destructive">
          <CardContent className="pt-4 flex items-center gap-2 text-sm text-destructive">
            <AlertTriangle className="h-4 w-4" /> {error}
          </CardContent>
        </Card>
      )}

      {report?.is_empty && (
        <Card>
          <CardContent className="pt-6 text-sm text-muted-foreground">
            Storage scan has not started yet. Click <b>Scan Now</b> to begin,
            or wait for the next scheduled run.
          </CardContent>
        </Card>
      )}

      {!report?.is_empty && report && (
        <Tabs value={activeTab} onValueChange={setActiveTab}>
          <div className="flex items-center justify-between mb-2">
            <TabsList className="h-8">
              {statusTabs.map((s) => {
                const stat =
                  s === "all"
                    ? report.summary
                    : report.summary.by_status[s];
                const count =
                  s === "all"
                    ? report.summary.total_tickets
                    : (stat as { tickets: number })?.tickets ?? 0;
                return (
                  <TabsTrigger
                    key={s}
                    value={s}
                    className="text-xs h-7 capitalize px-2"
                  >
                    {s}{" "}
                    {count > 0 && (
                      <Badge
                        variant="secondary"
                        className="ml-1 px-1 py-0 text-xs h-4"
                      >
                        {count}
                      </Badge>
                    )}
                  </TabsTrigger>
                );
              })}
            </TabsList>
          </div>

          {statusTabs.map((s) => (
            <TabsContent key={s} value={s} className="mt-0">
              <Card>
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead className="w-20">Ticket</TableHead>
                      <TableHead>Subject</TableHead>
                      <TableHead className="w-24">Status</TableHead>
                      <TableHead className="w-20">Files</TableHead>
                      <TableHead className="w-28">Size</TableHead>
                      <TableHead className="w-32">Last scanned</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {loading && (
                      <TableRow>
                        <TableCell
                          colSpan={6}
                          className="text-center text-muted-foreground py-8"
                        >
                          Loading…
                        </TableCell>
                      </TableRow>
                    )}
                    {!loading && filteredRows.length === 0 && (
                      <TableRow>
                        <TableCell
                          colSpan={6}
                          className="text-center text-muted-foreground py-8"
                        >
                          No tickets with attachments
                        </TableCell>
                      </TableRow>
                    )}
                    {!loading &&
                      filteredRows.slice(0, 200).map((row) => (
                        <TableRow key={row.ticket_id} className="text-sm">
                          <TableCell className="font-mono text-xs text-muted-foreground">
                            <a
                              href={`https://${creds?.subdomain ?? ""}.zendesk.com/agent/tickets/${row.ticket_id}`}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="text-primary hover:underline"
                            >
                              #{row.ticket_id}
                            </a>
                          </TableCell>
                          <TableCell className="max-w-xs truncate">
                            {row.subject || "(no subject)"}
                          </TableCell>
                          <TableCell>
                            <span
                              className={`inline-flex items-center px-1.5 py-0.5 rounded text-xs font-medium ${STATUS_BADGE[row.zd_status] || "bg-gray-100 text-gray-700"}`}
                            >
                              {row.zd_status}
                            </span>
                          </TableCell>
                          <TableCell className="text-xs text-muted-foreground">
                            {row.attach_count > 0 && (
                              <span>{row.attach_count} 📎</span>
                            )}
                            {row.inline_count > 0 && (
                              <span className="ml-1">
                                {row.inline_count} 🖼️
                              </span>
                            )}
                            {row.attach_count === 0 &&
                              row.inline_count === 0 &&
                              "—"}
                          </TableCell>
                          <TableCell className="text-xs font-medium text-destructive">
                            {formatBytes(row.total_size)}
                          </TableCell>
                          <TableCell className="text-xs text-muted-foreground">
                            {formatDate(row.last_seen_at)}
                          </TableCell>
                        </TableRow>
                      ))}
                  </TableBody>
                </Table>
                {filteredRows.length > 200 && (
                  <p className="text-xs text-muted-foreground px-4 py-2">
                    Showing top 200 of{" "}
                    {filteredRows.length.toLocaleString()}
                  </p>
                )}
              </Card>
            </TabsContent>
          ))}
        </Tabs>
      )}
    </div>
  );
}
