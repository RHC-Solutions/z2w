"use client";

import { useState, useEffect, useCallback } from "react";
import { RefreshCw, ExternalLink, ChevronLeft, ChevronRight, Search } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { zendeskFetch, formatDate, getSubdomain } from "@/lib/api";
import type { StoredCreds } from "@/lib/storage";

interface Ticket {
  id: number;
  subject: string;
  status: "open" | "pending" | "solved" | "closed";
  created_at: string;
  updated_at: string;
  requester_id?: number;
}

const STATUS_COLORS: Record<string, string> = {
  open: "bg-blue-100 text-blue-800",
  pending: "bg-yellow-100 text-yellow-800",
  solved: "bg-green-100 text-green-800",
  closed: "bg-gray-100 text-gray-700",
};

const PAGE_SIZES = [25, 50, 100];

interface Props {
  creds: StoredCreds | null;
}

export function TicketsPanel({ creds }: Props) {
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [totalCount, setTotalCount] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [searchInput, setSearchInput] = useState("");
  const [sortBy, setSortBy] = useState<"id" | "created_at" | "updated_at" | "status">("id");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");

  const connected = Boolean(creds?.subdomain && creds?.token);

  const load = useCallback(async () => {
    if (!connected || !creds) return;
    setLoading(true);
    setError(null);
    try {
      const sep = "?";
      let path = `/tickets.json${sep}per_page=${pageSize}&page=${page}&sort_by=${sortBy}&sort_order=${sortDir}`;
      if (search) path += `&q=${encodeURIComponent(search)}`;
      const data = await zendeskFetch(creds.subdomain, creds.email, creds.token, path) as {
        tickets: Ticket[];
        count: number;
      };
      setTickets(data.tickets || []);
      setTotalCount(data.count || 0);
    } catch (e) {
      setError(String(e));
      setTickets([]);
    } finally {
      setLoading(false);
    }
  }, [connected, creds, page, pageSize, sortBy, sortDir, search]);

  useEffect(() => { load(); }, [load]);

  const totalPages = Math.max(1, Math.ceil(Math.min(totalCount, 10000) / pageSize));
  const subdomain = creds?.subdomain ? getSubdomain(creds.subdomain) : "";
  const ticketUrl = subdomain ? `https://${subdomain}.zendesk.com/agent/tickets/` : null;

  return (
    <div className="space-y-3">
      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex items-center gap-1.5 flex-1 min-w-[200px]">
          <Input
            placeholder="Search tickets…"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") { setSearch(searchInput.trim()); setPage(1); } }}
            className="h-8 text-sm"
          />
          <Button size="sm" variant="secondary" className="h-8 px-2" onClick={() => { setSearch(searchInput.trim()); setPage(1); }}>
            <Search className="h-3.5 w-3.5" />
          </Button>
        </div>
        <Select value={sortBy} onValueChange={(v) => { setSortBy(v as typeof sortBy); setPage(1); }}>
          <SelectTrigger className="h-8 w-36 text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="id">ID</SelectItem>
            <SelectItem value="created_at">Created</SelectItem>
            <SelectItem value="updated_at">Updated</SelectItem>
            <SelectItem value="status">Status</SelectItem>
          </SelectContent>
        </Select>
        <Button size="sm" variant="secondary" className="h-8 px-2" onClick={() => { setSortDir((d) => d === "asc" ? "desc" : "asc"); setPage(1); }}>
          {sortDir === "asc" ? "↑" : "↓"}
        </Button>
        <Select value={String(pageSize)} onValueChange={(v) => { setPageSize(Number(v)); setPage(1); }}>
          <SelectTrigger className="h-8 w-24 text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {PAGE_SIZES.map((n) => (
              <SelectItem key={n} value={String(n)}>{n} / page</SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Button size="sm" variant="secondary" className="h-8 gap-1.5" onClick={load} disabled={loading}>
          <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
          {loading ? "Loading…" : "Refresh"}
        </Button>
      </div>

      {/* Pagination info */}
      <div className="flex items-center justify-between text-xs text-muted-foreground">
        <span>
          {totalCount > 0
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
                <TableHead className="w-20">ID</TableHead>
                <TableHead>Subject</TableHead>
                <TableHead className="w-24">Status</TableHead>
                <TableHead className="w-36">Created</TableHead>
                <TableHead className="w-36">Updated</TableHead>
                <TableHead className="w-10"></TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {loading && (
                <TableRow>
                  <TableCell colSpan={6} className="text-center text-muted-foreground py-8">Loading…</TableCell>
                </TableRow>
              )}
              {!loading && tickets.length === 0 && (
                <TableRow>
                  <TableCell colSpan={6} className="text-center text-muted-foreground py-8">No tickets</TableCell>
                </TableRow>
              )}
              {!loading && tickets.map((t) => (
                <TableRow key={t.id} className="text-sm">
                  <TableCell className="font-mono text-xs text-muted-foreground">#{t.id}</TableCell>
                  <TableCell className="max-w-xs truncate">{t.subject || "(no subject)"}</TableCell>
                  <TableCell>
                    <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-xs font-medium ${STATUS_COLORS[t.status] || ""}`}>
                      {t.status}
                    </span>
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground">{formatDate(t.created_at)}</TableCell>
                  <TableCell className="text-xs text-muted-foreground">{formatDate(t.updated_at)}</TableCell>
                  <TableCell>
                    {ticketUrl && (
                      <a href={`${ticketUrl}${t.id}`} target="_blank" rel="noopener noreferrer" className="text-primary hover:text-primary/80">
                        <ExternalLink className="h-3.5 w-3.5" />
                      </a>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Card>
      )}
    </div>
  );
}
