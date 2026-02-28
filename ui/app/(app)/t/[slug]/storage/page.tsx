"use client";

import { useEffect, useState, useCallback } from "react";
import { useParams } from "next/navigation";
import { getTenantStorage, StorageResult, StorageTicket, fmtBytes } from "@/lib/api";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Search, ChevronLeft, ChevronRight, ExternalLink, RefreshCw, ArrowUpDown, HardDrive } from "lucide-react";
import { cn } from "@/lib/utils";

function SortHeader({ label, field, sort, order, onSort }: {
  label: string; field: string; sort: string; order: string; onSort: (f: string) => void;
}) {
  return (
    <button className="flex items-center gap-1 hover:text-foreground transition-colors" onClick={() => onSort(field)}>
      {label}
      <ArrowUpDown className={cn("w-3 h-3", sort === field ? "text-primary" : "text-muted-foreground/50")} />
    </button>
  );
}

function SizeBar({ bytes, max }: { bytes: number; max: number }) {
  const pct = max > 0 ? Math.min((bytes / max) * 100, 100) : 0;
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-1.5 bg-muted rounded-full overflow-hidden">
        <div className="h-full bg-primary/70 rounded-full" style={{ width: `${pct}%` }} />
      </div>
      <span className="text-[11px] tabular-nums text-muted-foreground w-16 text-right">{fmtBytes(bytes)}</span>
    </div>
  );
}

export default function StoragePage() {
  const { slug } = useParams<{ slug: string }>();
  const [data, setData] = useState<StorageResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(1);
  const [q, setQ] = useState("");
  const [qDraft, setQDraft] = useState("");
  const [status, setStatus] = useState("");
  const [sort, setSort] = useState("size");
  const [order, setOrder] = useState("desc");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const d = await getTenantStorage(slug, { page, q: q || undefined, status: status || undefined, sort, order });
      setData(d);
    } finally {
      setLoading(false);
    }
  }, [slug, page, q, status, sort, order]);

  useEffect(() => { load(); }, [load]);

  function handleSearch(e: React.FormEvent) {
    e.preventDefault();
    setQ(qDraft);
    setPage(1);
  }

  function handleSort(field: string) {
    if (sort === field) {
      setOrder(o => o === "desc" ? "asc" : "desc");
    } else {
      setSort(field);
      setOrder("desc");
    }
    setPage(1);
  }

  async function handleRefreshSnapshot() {
    await fetch(`/api/storage_report/refresh`, { method: "POST", credentials: "include" });
    setTimeout(() => load(), 2000);
  }

  const statusCounts = data?.status_counts ?? {};
  const maxBytes = data?.tickets[0]?.size_bytes ?? 1;

  return (
    <div className="flex flex-col h-full">
      {/* Toolbar */}
      <div className="flex items-center gap-2 px-4 py-3 border-b border-border flex-wrap shrink-0">
        {/* Status tabs */}
        <div className="flex items-center gap-1">
          <button
            onClick={() => { setStatus(""); setPage(1); }}
            className={cn("text-[11px] px-2.5 py-1 rounded border transition-colors",
              status === "" ? "bg-primary/20 border-primary text-primary" : "border-border text-muted-foreground hover:border-muted-foreground"
            )}
          >
            All
          </button>
          {Object.entries(statusCounts).slice(0, 5).map(([s, c]) => (
            <button
              key={s}
              onClick={() => { setStatus(s); setPage(1); }}
              className={cn("text-[11px] px-2.5 py-1 rounded border transition-colors",
                status === s ? "bg-primary/20 border-primary text-primary" : "border-border text-muted-foreground hover:border-muted-foreground"
              )}
            >
              {s} <span className="opacity-60">({(c as number).toLocaleString()})</span>
            </button>
          ))}
        </div>

        <form onSubmit={handleSearch} className="flex items-center gap-1 ml-auto">
          <div className="relative">
            <Search className="absolute left-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
            <Input className="h-8 pl-7 w-56 text-xs" placeholder="Search subject, ticket…" value={qDraft} onChange={(e) => setQDraft(e.target.value)} />
          </div>
          <Button type="submit" size="sm" variant="secondary" className="h-8 px-2"><Search className="w-3.5 h-3.5" /></Button>
        </form>
        <Button size="sm" variant="outline" className="h-8 px-2 text-xs" onClick={handleRefreshSnapshot}>
          <RefreshCw className="w-3.5 h-3.5 mr-1" /> Refresh Snapshot
        </Button>
        <Button size="sm" variant="ghost" className="h-8 px-2" onClick={() => load()}>
          <RefreshCw className="w-3.5 h-3.5" />
        </Button>
      </div>

      {/* Stats bar */}
      {data && !loading && (
        <div className="flex items-center gap-6 px-4 py-2 border-b border-border/40 shrink-0">
          <div className="flex items-center gap-2 text-xs">
            <HardDrive className="w-3.5 h-3.5 text-primary" />
            <span className="text-muted-foreground">Total Zendesk Storage:</span>
            <span className="font-semibold">{fmtBytes(data.totals.total_bytes)}</span>
          </div>
          <div className="text-xs text-muted-foreground">
            {data.totals.count.toLocaleString()} tickets with attachments
          </div>
          {data.last_updated && (
            <div className="text-xs text-muted-foreground ml-auto">
              Last snapshot: {data.last_updated.replace("T", " ").replace(/\.\d+$/, "")}
            </div>
          )}
        </div>
      )}

      {/* Table */}
      <div className="flex-1 overflow-auto">
        {loading ? (
          <div className="p-4 space-y-2">
            {[...Array(10)].map((_, i) => <Skeleton key={i} className="h-10 w-full" />)}
          </div>
        ) : !data || data.tickets.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-48 gap-3 text-sm text-muted-foreground">
            <HardDrive className="w-10 h-10 text-muted-foreground/30" />
            <p>No storage data. Run a storage snapshot to populate this view.</p>
            <Button size="sm" onClick={handleRefreshSnapshot}>
              <RefreshCw className="w-3.5 h-3.5 mr-1" /> Run Snapshot
            </Button>
          </div>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="text-xs w-24">
                  <SortHeader label="Ticket" field="ticket_id" sort={sort} order={order} onSort={handleSort} />
                </TableHead>
                <TableHead className="text-xs">Subject</TableHead>
                <TableHead className="text-xs">
                  <SortHeader label="Status" field="status" sort={sort} order={order} onSort={handleSort} />
                </TableHead>
                <TableHead className="text-xs text-right">Files</TableHead>
                <TableHead className="text-xs w-40">
                  <SortHeader label="Size" field="size" sort={sort} order={order} onSort={handleSort} />
                </TableHead>
                <TableHead className="text-xs">Updated</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.tickets.map((t: StorageTicket) => (
                <TableRow key={t.ticket_id} className="text-xs">
                  <TableCell>
                    <a href={t.ticket_url} target="_blank" rel="noopener noreferrer" className="flex items-center gap-1 text-primary hover:underline font-mono">
                      #{t.ticket_id}<ExternalLink className="w-2.5 h-2.5" />
                    </a>
                  </TableCell>
                  <TableCell className="max-w-xs">
                    <span className="truncate block" title={t.subject}>{t.subject || "—"}</span>
                  </TableCell>
                  <TableCell>
                    <Badge variant="outline" className="text-[10px]">{t.zd_status || "—"}</Badge>
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {t.files}
                    {t.inline > 0 && <span className="text-muted-foreground ml-1 text-[10px]">(+{t.inline} inline)</span>}
                  </TableCell>
                  <TableCell className="w-40">
                    <SizeBar bytes={t.size_bytes} max={maxBytes} />
                  </TableCell>
                  <TableCell className="text-muted-foreground font-mono text-[10px]">
                    {t.updated_at ? t.updated_at.replace("T", " ").replace(/\.\d+$/, "") : "—"}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </div>

      {/* Pagination */}
      {data && data.pages > 1 && (
        <div className="flex items-center justify-between px-4 py-2 border-t border-border shrink-0 text-xs text-muted-foreground">
          <span>Page {data.page} of {data.pages} · {data.total.toLocaleString()} total</span>
          <div className="flex items-center gap-1">
            <Button size="sm" variant="ghost" className="h-7 px-2" disabled={page <= 1} onClick={() => setPage(p => p - 1)}>
              <ChevronLeft className="w-3.5 h-3.5" />
            </Button>
            {[...Array(Math.min(data.pages, 7))].map((_, i) => {
              const p = Math.max(1, Math.min(data.pages - 6, page - 3)) + i;
              return (
                <Button key={p} size="sm" variant={p === page ? "default" : "ghost"} className="h-7 w-7 p-0 text-xs" onClick={() => setPage(p)}>
                  {p}
                </Button>
              );
            })}
            <Button size="sm" variant="ghost" className="h-7 px-2" disabled={page >= data.pages} onClick={() => setPage(p => p + 1)}>
              <ChevronRight className="w-3.5 h-3.5" />
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
