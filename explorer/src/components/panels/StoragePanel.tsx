"use client";

import { useState, useEffect, useCallback } from "react";
import { RefreshCw, Cloud, Database, AlertTriangle } from "lucide-react";
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
  last_updated: string | null;
  next_run: string | null;
  is_empty: boolean;
}

interface WasabiStats {
  object_count: number;
  total_bytes: number;
  total_gb: number;
  total_mb: number;
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
  const [wasabi, setWasabi] = useState<WasabiStats | null>(null);
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState("all");

  const loadReport = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await z2wFetch("/api/storage_report") as StorageReport;
      setReport(data);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  const loadWasabi = useCallback(async () => {
    try {
      const data = await z2wFetch("/api/wasabi_stats") as WasabiStats;
      setWasabi(data);
    } catch { /* ignore */ }
  }, []);

  useEffect(() => {
    loadReport();
    loadWasabi();
  }, [loadReport, loadWasabi]);

  const handleRefresh = async () => {
    setRefreshing(true);
    try {
      await z2wFetch("/api/storage_report/refresh", { method: "POST" });
      // Poll for results
      await new Promise((r) => setTimeout(r, 3000));
      await loadReport();
    } catch { /* ignore */ } finally {
      setRefreshing(false);
    }
  };

  const filteredRows = report?.rows.filter((r) => activeTab === "all" || r.zd_status === activeTab) ?? [];
  const statusTabs = ["all", "open", "pending", "solved", "closed"];

  return (
    <div className="space-y-4">
      {/* Summary cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Card>
          <CardHeader className="pb-1 pt-3 px-4">
            <CardDescription className="text-xs flex items-center gap-1"><Database className="h-3 w-3" /> Zendesk storage in use</CardDescription>
          </CardHeader>
          <CardContent className="px-4 pb-3">
            <p className="text-xl font-bold text-destructive">{report ? formatBytes(report.summary.total_bytes) : "–"}</p>
            <p className="text-xs text-muted-foreground mt-0.5">{report?.summary.total_files ?? "–"} files · {report?.summary.total_tickets ?? "–"} tickets</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-1 pt-3 px-4">
            <CardDescription className="text-xs flex items-center gap-1"><Cloud className="h-3 w-3" /> Wasabi storage used</CardDescription>
          </CardHeader>
          <CardContent className="px-4 pb-3">
            <p className="text-xl font-bold text-primary">
              {wasabi ? (wasabi.total_gb >= 1 ? `${wasabi.total_gb.toFixed(2)} GB` : `${wasabi.total_mb?.toFixed(1)} MB`) : "–"}
            </p>
            <p className="text-xs text-muted-foreground mt-0.5">{wasabi?.object_count?.toLocaleString() ?? "–"} objects</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-1 pt-3 px-4">
            <CardDescription className="text-xs">Last scanned</CardDescription>
          </CardHeader>
          <CardContent className="px-4 pb-3">
            <p className="text-sm font-semibold">{report?.last_updated ? formatDate(report.last_updated) : "–"}</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-1 pt-3 px-4">
            <CardDescription className="text-xs">Next scan</CardDescription>
          </CardHeader>
          <CardContent className="px-4 pb-3 flex items-start justify-between">
            <p className="text-sm font-semibold">{report?.next_run ? formatDate(report.next_run) : "–"}</p>
            <Button size="sm" variant="secondary" className="h-6 text-xs px-2 gap-1" onClick={handleRefresh} disabled={refreshing}>
              <RefreshCw className={`h-3 w-3 ${refreshing ? "animate-spin" : ""}`} />
              {refreshing ? "…" : "Now"}
            </Button>
          </CardContent>
        </Card>
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
            Storage scan is in progress. Data will appear once the first scan completes.
          </CardContent>
        </Card>
      )}

      {!report?.is_empty && report && (
        <Tabs value={activeTab} onValueChange={setActiveTab}>
          <div className="flex items-center justify-between mb-2">
            <TabsList className="h-8">
              {statusTabs.map((s) => {
                const stat = s === "all" ? report.summary : report.summary.by_status[s];
                const count = s === "all" ? report.summary.total_tickets : (stat as { tickets: number })?.tickets ?? 0;
                return (
                  <TabsTrigger key={s} value={s} className="text-xs h-7 capitalize px-2">
                    {s} {count > 0 && <Badge variant="secondary" className="ml-1 px-1 py-0 text-xs h-4">{count}</Badge>}
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
                      <TableHead className="w-28">Size in ZD</TableHead>
                      <TableHead className="w-32">Last scanned</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {loading && (
                      <TableRow><TableCell colSpan={6} className="text-center text-muted-foreground py-8">Loading…</TableCell></TableRow>
                    )}
                    {!loading && filteredRows.length === 0 && (
                      <TableRow><TableCell colSpan={6} className="text-center text-muted-foreground py-8">No tickets with attachments</TableCell></TableRow>
                    )}
                    {!loading && filteredRows.slice(0, 200).map((row) => (
                      <TableRow key={row.ticket_id} className="text-sm">
                        <TableCell className="font-mono text-xs text-muted-foreground">
                          <a href={`https://${creds?.subdomain ?? ""}.zendesk.com/agent/tickets/${row.ticket_id}`} target="_blank" rel="noopener noreferrer" className="text-primary hover:underline">
                            #{row.ticket_id}
                          </a>
                        </TableCell>
                        <TableCell className="max-w-xs truncate">{row.subject || "(no subject)"}</TableCell>
                        <TableCell>
                          <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-xs font-medium ${STATUS_BADGE[row.zd_status] || "bg-gray-100 text-gray-700"}`}>
                            {row.zd_status}
                          </span>
                        </TableCell>
                        <TableCell className="text-xs text-muted-foreground">
                          {row.attach_count > 0 && <span>{row.attach_count} 📎</span>}
                          {row.inline_count > 0 && <span className="ml-1">{row.inline_count} 🖼️</span>}
                          {row.attach_count === 0 && row.inline_count === 0 && "—"}
                        </TableCell>
                        <TableCell className="text-xs font-medium text-destructive">{formatBytes(row.total_size)}</TableCell>
                        <TableCell className="text-xs text-muted-foreground">{formatDate(row.last_seen_at)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
                {filteredRows.length > 200 && (
                  <p className="text-xs text-muted-foreground px-4 py-2">Showing top 200 of {filteredRows.length.toLocaleString()}</p>
                )}
              </Card>
            </TabsContent>
          ))}
        </Tabs>
      )}
    </div>
  );
}
