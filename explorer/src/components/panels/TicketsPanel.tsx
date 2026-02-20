"use client";

import { useState, useEffect, useCallback } from "react";
import {
  RefreshCw, ExternalLink, ChevronLeft, ChevronRight,
  Search, ArrowUp, ArrowDown, ArrowUpDown, CheckCircle2, XCircle, Clock, Archive,
} from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import { zendeskFetch, formatBytes, formatDate, getSubdomain, z2wFetch } from "@/lib/api";
import type { StoredCreds } from "@/lib/storage";

interface Ticket {
  id: number;
  subject: string;
  status: "open" | "pending" | "solved" | "closed";
  created_at: string;
  updated_at: string;
  requester_id?: number;
}

interface TicketStatus {
  offloaded: boolean;
  attachments_count: number;
  inlines_count: number;
  processed_at: string | null;
  backup_status: string | null;   // "success" | "pending" | "failed" | "skipped" | null
  backed_up_at: string | null;
}

const ZD_STATUS_COLORS: Record<string, string> = {
  open:    "bg-blue-100 text-blue-800 dark:bg-blue-900/40 dark:text-blue-300",
  pending: "bg-yellow-100 text-yellow-800 dark:bg-yellow-900/40 dark:text-yellow-300",
  solved:  "bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-300",
  closed:  "bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300",
};

const BACKUP_COLORS: Record<string, string> = {
  success: "text-green-600 dark:text-green-400",
  pending: "text-yellow-600 dark:text-yellow-400",
  failed:  "text-red-600 dark:text-red-400",
  skipped: "text-muted-foreground",
};

const PAGE_SIZES = [25, 50, 100];
type SortKey = "id" | "created_at" | "updated_at" | "status";
type OffloadFilter = "all" | "offloaded" | "not_offloaded";
type BackupFilter  = "all" | "success" | "pending" | "failed" | "none";

function BackupBadge({ status }: { status: string | null }) {
  if (!status) return <span className="text-muted-foreground/40 text-xs">–</span>;
  const icon = status === "success"
    ? <CheckCircle2 className="h-3 w-3" />
    : status === "failed"
    ? <XCircle className="h-3 w-3" />
    : status === "pending"
    ? <Clock className="h-3 w-3" />
    : <Archive className="h-3 w-3" />;
  return (
    <span className={`inline-flex items-center gap-0.5 text-xs font-medium ${BACKUP_COLORS[status] ?? "text-muted-foreground"}`}>
      {icon}{status}
    </span>
  );
}

function OffloadBadge({ status, count, inlines }: { status: TicketStatus | undefined; count?: number; inlines?: number }) {
  if (!status) return <span className="text-muted-foreground/40 text-xs">–</span>;
  if (status.offloaded) {
    const parts = [];
    if (count) parts.push(`${count} att`);
    if (inlines) parts.push(`${inlines} inline`);
    return (
      <span className="inline-flex items-center gap-0.5 text-xs font-medium text-green-600 dark:text-green-400">
        <CheckCircle2 className="h-3 w-3" />{parts.length ? parts.join(", ") : "yes"}
      </span>
    );
  }
  return <span className="text-muted-foreground/40 text-xs">–</span>;
}

function SortIcon({ col, sortBy, sortDir }: { col: SortKey; sortBy: SortKey; sortDir: "asc" | "desc" }) {
  if (col !== sortBy) return <ArrowUpDown className="h-3 w-3 ml-1 opacity-30 inline" />;
  return sortDir === "asc"
    ? <ArrowUp className="h-3 w-3 ml-1 inline" />
    : <ArrowDown className="h-3 w-3 ml-1 inline" />;
}

interface Props { creds: StoredCreds | null; }

export function TicketsPanel({ creds }: Props) {
  const [tickets, setTickets]       = useState<Ticket[]>([]);
  const [totalCount, setTotalCount] = useState(0);
  const [page, setPage]             = useState(1);
  const [pageSize, setPageSize]     = useState(50);
  const [loading, setLoading]       = useState(false);
  const [error, setError]           = useState<string | null>(null);

  // Search / filters
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch]           = useState("");
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [offloadFilter, setOffloadFilter] = useState<OffloadFilter>("all");
  const [backupFilter, setBackupFilter]   = useState<BackupFilter>("all");

  // Sort
  const [sortBy, setSortBy]   = useState<SortKey>("id");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");

  // z2w status maps keyed by ticket_id string
  const [sizeMap,   setSizeMap]   = useState<Record<string, number>>({});
  const [statusMap, setStatusMap] = useState<Record<string, TicketStatus>>({});

  const connected = Boolean(creds?.subdomain && creds?.token);

  const load = useCallback(async () => {
    if (!connected || !creds) return;
    setLoading(true);
    setError(null);
    try {
      let path = `/tickets.json?per_page=${pageSize}&page=${page}&sort_by=${sortBy}&sort_order=${sortDir}`;
      if (search)       path += `&q=${encodeURIComponent(search)}`;
      if (statusFilter !== "all") path += `&status=${statusFilter}`;
      const data = await zendeskFetch(creds.subdomain, creds.email, creds.token, path) as {
        tickets: Ticket[];
        count: number;
      };
      const tix = data.tickets || [];
      setTickets(tix);
      setTotalCount(data.count || 0);

      if (tix.length > 0) {
        const ids = tix.map((t) => t.id).join(",");
        // Fetch sizes + statuses in parallel
        const [sizes, statuses] = await Promise.allSettled([
          z2wFetch(`/api/ticket_sizes?ids=${ids}`),
          z2wFetch(`/api/ticket_status?ids=${ids}`),
        ]);
        if (sizes.status === "fulfilled")
          setSizeMap((p) => ({ ...p, ...(sizes.value as Record<string, number>) }));
        if (statuses.status === "fulfilled")
          setStatusMap((p) => ({ ...p, ...(statuses.value as Record<string, TicketStatus>) }));
      }
    } catch (e) {
      setError(String(e));
      setTickets([]);
    } finally {
      setLoading(false);
    }
  }, [connected, creds, page, pageSize, sortBy, sortDir, search, statusFilter]);

  useEffect(() => { load(); }, [load]);

  // Client-side filter for offload / backup (since Zendesk API doesn't know about these)
  const filteredTickets = tickets.filter((t) => {
    const s = statusMap[String(t.id)];
    if (offloadFilter === "offloaded"     && !(s?.offloaded))  return false;
    if (offloadFilter === "not_offloaded" && s?.offloaded)     return false;
    if (backupFilter !== "all") {
      const bs = s?.backup_status ?? null;
      if (backupFilter === "none"    && bs !== null)            return false;
      if (backupFilter !== "none"    && bs !== backupFilter)    return false;
    }
    return true;
  });

  const totalPages = Math.max(1, Math.ceil(Math.min(totalCount, 10000) / pageSize));
  const subdomain  = creds?.subdomain ? getSubdomain(creds.subdomain) : "";
  const ticketUrl  = subdomain ? `https://${subdomain}.zendesk.com/agent/tickets/` : null;

  function toggleSort(col: SortKey) {
    if (col === sortBy) setSortDir((d) => d === "asc" ? "desc" : "asc");
    else { setSortBy(col); setSortDir("asc"); }
    setPage(1);
  }

  const applySearch = () => { setSearch(searchInput.trim()); setPage(1); };

  return (
    <div className="space-y-3">
      {/* ── Toolbar row 1: search + sort ── */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex items-center gap-1.5 flex-1 min-w-[200px]">
          <Input
            placeholder="Search tickets…"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") applySearch(); }}
            className="h-8 text-sm"
          />
          <Button size="sm" variant="secondary" className="h-8 px-2" onClick={applySearch}>
            <Search className="h-3.5 w-3.5" />
          </Button>
        </div>

        {/* Zendesk status filter */}
        <Select value={statusFilter} onValueChange={(v) => { setStatusFilter(v); setPage(1); }}>
          <SelectTrigger className="h-8 w-32 text-xs"><SelectValue placeholder="ZD status" /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All statuses</SelectItem>
            <SelectItem value="open">Open</SelectItem>
            <SelectItem value="pending">Pending</SelectItem>
            <SelectItem value="solved">Solved</SelectItem>
            <SelectItem value="closed">Closed</SelectItem>
          </SelectContent>
        </Select>

        {/* Offload filter */}
        <Select value={offloadFilter} onValueChange={(v) => setOffloadFilter(v as OffloadFilter)}>
          <SelectTrigger className="h-8 w-36 text-xs"><SelectValue placeholder="Offload" /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All offload</SelectItem>
            <SelectItem value="offloaded">Offloaded ✓</SelectItem>
            <SelectItem value="not_offloaded">Not offloaded</SelectItem>
          </SelectContent>
        </Select>

        {/* Backup filter */}
        <Select value={backupFilter} onValueChange={(v) => setBackupFilter(v as BackupFilter)}>
          <SelectTrigger className="h-8 w-36 text-xs"><SelectValue placeholder="Backup" /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All backup</SelectItem>
            <SelectItem value="success">Backed up ✓</SelectItem>
            <SelectItem value="pending">Backup pending</SelectItem>
            <SelectItem value="failed">Backup failed</SelectItem>
            <SelectItem value="none">No backup</SelectItem>
          </SelectContent>
        </Select>

        {/* Page size */}
        <Select value={String(pageSize)} onValueChange={(v) => { setPageSize(Number(v)); setPage(1); }}>
          <SelectTrigger className="h-8 w-24 text-xs"><SelectValue /></SelectTrigger>
          <SelectContent>
            {PAGE_SIZES.map((n) => <SelectItem key={n} value={String(n)}>{n} / page</SelectItem>)}
          </SelectContent>
        </Select>

        <Button size="sm" variant="secondary" className="h-8 gap-1.5" onClick={load} disabled={loading}>
          <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
          {loading ? "Loading…" : "Refresh"}
        </Button>
      </div>

      {/* ── Pagination bar ── */}
      <div className="flex items-center justify-between text-xs text-muted-foreground">
        <span>
          {offloadFilter !== "all" || backupFilter !== "all"
            ? `${filteredTickets.length} shown (filtered) · ${totalCount.toLocaleString()} total`
            : totalCount > 0
            ? `${((page - 1) * pageSize + 1).toLocaleString()}–${Math.min(page * pageSize, totalCount).toLocaleString()} of ${totalCount.toLocaleString()}`
            : "0 tickets"}
          {totalCount > 10000 && <span className="ml-1">(first 10,000 via offset API)</span>}
        </span>
        <div className="flex items-center gap-1">
          <Button size="sm" variant="ghost" className="h-6 px-1.5" onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={page <= 1 || loading}>
            <ChevronLeft className="h-3.5 w-3.5" />
          </Button>
          <span className="px-1">Page {page} of {totalPages}</span>
          <Button size="sm" variant="ghost" className="h-6 px-1.5" onClick={() => setPage((p) => Math.min(totalPages, p + 1))} disabled={page >= totalPages || loading}>
            <ChevronRight className="h-3.5 w-3.5" />
          </Button>
        </div>
      </div>

      {!connected && (
        <Card>
          <CardContent className="pt-6 text-sm text-muted-foreground">
            Enter your Zendesk API credentials in Settings to browse tickets.
          </CardContent>
        </Card>
      )}
      {error && (
        <Card className="border-destructive">
          <CardContent className="pt-4 text-sm text-destructive">{error}</CardContent>
        </Card>
      )}

      {connected && !error && (
        <Card>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-20 cursor-pointer select-none" onClick={() => toggleSort("id")}>
                  ID <SortIcon col="id" sortBy={sortBy} sortDir={sortDir} />
                </TableHead>
                <TableHead className="cursor-pointer select-none" onClick={() => toggleSort("id")}>
                  Subject
                </TableHead>
                <TableHead className="w-24 cursor-pointer select-none" onClick={() => toggleSort("status")}>
                  Status <SortIcon col="status" sortBy={sortBy} sortDir={sortDir} />
                </TableHead>
                <TableHead className="w-28">Offload</TableHead>
                <TableHead className="w-24">Backup</TableHead>
                <TableHead className="w-24">Size</TableHead>
                <TableHead className="w-36 cursor-pointer select-none" onClick={() => toggleSort("created_at")}>
                  Created <SortIcon col="created_at" sortBy={sortBy} sortDir={sortDir} />
                </TableHead>
                <TableHead className="w-36 cursor-pointer select-none" onClick={() => toggleSort("updated_at")}>
                  Updated <SortIcon col="updated_at" sortBy={sortBy} sortDir={sortDir} />
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {loading && (
                <TableRow>
                  <TableCell colSpan={8} className="text-center text-muted-foreground py-8">Loading…</TableCell>
                </TableRow>
              )}
              {!loading && filteredTickets.length === 0 && (
                <TableRow>
                  <TableCell colSpan={8} className="text-center text-muted-foreground py-8">No tickets</TableCell>
                </TableRow>
              )}
              {!loading && filteredTickets.map((t) => {
                const sizeBytes = sizeMap[String(t.id)] ?? null;
                const ts = statusMap[String(t.id)];
                return (
                  <TableRow key={t.id} className="text-sm">
                    <TableCell className="font-mono text-xs">
                      {ticketUrl
                        ? <a href={`${ticketUrl}${t.id}`} target="_blank" rel="noopener noreferrer" className="text-primary hover:underline flex items-center gap-0.5">#{t.id}<ExternalLink className="h-2.5 w-2.5 opacity-50" /></a>
                        : `#${t.id}`}
                    </TableCell>
                    <TableCell className="max-w-xs truncate">
                      {ticketUrl
                        ? <a href={`${ticketUrl}${t.id}`} target="_blank" rel="noopener noreferrer" className="hover:underline hover:text-primary">{t.subject || "(no subject)"}</a>
                        : (t.subject || "(no subject)")}
                    </TableCell>
                    <TableCell>
                      <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-xs font-medium ${ZD_STATUS_COLORS[t.status] || ""}`}>
                        {t.status}
                      </span>
                    </TableCell>
                    <TableCell>
                      <OffloadBadge status={ts} count={ts?.attachments_count} inlines={ts?.inlines_count} />
                    </TableCell>
                    <TableCell>
                      <BackupBadge status={ts?.backup_status ?? null} />
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {sizeBytes != null && sizeBytes > 0
                        ? <span className="text-destructive font-medium">{formatBytes(sizeBytes)}</span>
                        : <span className="text-muted-foreground/40">–</span>}
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">{formatDate(t.created_at)}</TableCell>
                    <TableCell className="text-xs text-muted-foreground">{formatDate(t.updated_at)}</TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </Card>
      )}
    </div>
  );
}
