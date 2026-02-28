"use client";

import { useEffect, useState, useCallback } from "react";
import { useParams } from "next/navigation";
import { getTenantTickets, TicketsResult, TicketRow, fmtBytes } from "@/lib/api";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Search, ChevronLeft, ChevronRight, ExternalLink, RefreshCw, ArrowUpDown } from "lucide-react";
import { cn } from "@/lib/utils";

function StatusBadge({ status, error }: { status: string; error: string | null }) {
  if (error) return <Badge variant="destructive" className="text-[10px]">error</Badge>;
  if (status === "offloaded") return <Badge className="text-[10px] bg-emerald-700 hover:bg-emerald-700">offloaded</Badge>;
  if (status === "skipped") return <Badge variant="secondary" className="text-[10px]">skipped</Badge>;
  return <Badge variant="outline" className="text-[10px]">{status}</Badge>;
}

function SortHeader({ label, field, sort, order, onSort }: {
  label: string; field: string; sort: string; order: string;
  onSort: (f: string) => void;
}) {
  return (
    <button
      className="flex items-center gap-1 hover:text-foreground transition-colors"
      onClick={() => onSort(field)}
    >
      {label}
      <ArrowUpDown className={cn("w-3 h-3", sort === field ? "text-primary" : "text-muted-foreground/50")} />
    </button>
  );
}

export default function TicketsPage() {
  const { slug } = useParams<{ slug: string }>();
  const [data, setData] = useState<TicketsResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(1);
  const [q, setQ] = useState("");
  const [qDraft, setQDraft] = useState("");
  const [status, setStatus] = useState("");
  const [sort, setSort] = useState("processed_at");
  const [order, setOrder] = useState("desc");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const d = await getTenantTickets(slug, { page, q: q || undefined, status: status || undefined, sort, order });
      setData(d);
    } catch (_e) {
      // ignore
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

  const statusCounts = data?.status_counts ?? {};
  const statusTabs = ["", "offloaded", "skipped", "has_error"];

  return (
    <div className="flex flex-col h-full">
      {/* Toolbar */}
      <div className="flex items-center gap-2 px-4 py-3 border-b border-border flex-wrap shrink-0">
        <div className="flex items-center gap-1">
          {statusTabs.map((s) => (
            <button
              key={s || "all"}
              onClick={() => { setStatus(s); setPage(1); }}
              className={cn(
                "text-[11px] px-2.5 py-1 rounded border transition-colors",
                status === s
                  ? "bg-primary/20 border-primary text-primary"
                  : "border-border text-muted-foreground hover:border-muted-foreground"
              )}
            >
              {s === "" ? "All" : s === "has_error" ? "Errors" : s}
              {s !== "" && statusCounts[s] != null ? <span className="ml-1 opacity-70">({statusCounts[s]})</span> : null}
            </button>
          ))}
        </div>

        <form onSubmit={handleSearch} className="flex items-center gap-1 ml-auto">
          <div className="relative">
            <Search className="absolute left-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
            <Input
              className="h-8 pl-7 w-56 text-xs"
              placeholder="Search ticket ID, status…"
              value={qDraft}
              onChange={(e) => setQDraft(e.target.value)}
            />
          </div>
          <Button type="submit" size="sm" variant="secondary" className="h-8 px-2">
            <Search className="w-3.5 h-3.5" />
          </Button>
        </form>
        <Button size="sm" variant="ghost" className="h-8 px-2" onClick={() => load()}>
          <RefreshCw className="w-3.5 h-3.5" />
        </Button>
      </div>

      {/* Stats bar */}
      {data && !loading && (
        <div className="flex items-center gap-4 px-4 py-1.5 border-b border-border/40 text-xs text-muted-foreground shrink-0">
          <span>{data.total.toLocaleString()} tickets</span>
          {Object.entries(statusCounts).map(([s, c]) => (
            <span key={s}>{s}: {c}</span>
          ))}
        </div>
      )}

      {/* Table */}
      <div className="flex-1 overflow-auto">
        {loading ? (
          <div className="p-4 space-y-2">
            {[...Array(10)].map((_, i) => <Skeleton key={i} className="h-10 w-full" />)}
          </div>
        ) : !data || data.tickets.length === 0 ? (
          <div className="flex items-center justify-center h-40 text-sm text-muted-foreground">
            No tickets found.
          </div>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-28 text-xs">
                  <SortHeader label="Ticket ID" field="ticket_id" sort={sort} order={order} onSort={handleSort} />
                </TableHead>
                <TableHead className="text-xs">Status</TableHead>
                <TableHead className="text-xs text-right">
                  <SortHeader label="Attachments" field="attachments_count" sort={sort} order={order} onSort={handleSort} />
                </TableHead>
                <TableHead className="text-xs text-right">Inlines</TableHead>
                <TableHead className="text-xs text-right">Size</TableHead>
                <TableHead className="text-xs">
                  <SortHeader label="Processed At" field="processed_at" sort={sort} order={order} onSort={handleSort} />
                </TableHead>
                <TableHead className="text-xs">Error</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.tickets.map((t: TicketRow) => (
                <TableRow key={t.ticket_id} className="text-xs">
                  <TableCell>
                    <a
                      href={t.ticket_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="flex items-center gap-1 text-primary hover:underline font-mono"
                    >
                      #{t.ticket_id}
                      <ExternalLink className="w-3 h-3" />
                    </a>
                  </TableCell>
                  <TableCell>
                    <StatusBadge status={t.status} error={t.error_message} />
                  </TableCell>
                  <TableCell className="text-right tabular-nums">{t.attachments_count}</TableCell>
                  <TableCell className="text-right tabular-nums">{t.inlines_offloaded}</TableCell>
                  <TableCell className="text-right tabular-nums text-muted-foreground">
                    {t.bytes_offloaded > 0 ? fmtBytes(t.bytes_offloaded) : "—"}
                  </TableCell>
                  <TableCell className="text-muted-foreground font-mono text-[11px]">
                    {t.processed_at ? t.processed_at.replace("T", " ").replace(/\.\d+$/, "") : "—"}
                  </TableCell>
                  <TableCell className="max-w-xs">
                    {t.error_message ? (
                      <span className="text-destructive text-[10px] truncate block" title={t.error_message}>
                        {t.error_message.slice(0, 80)}{t.error_message.length > 80 ? "…" : ""}
                      </span>
                    ) : null}
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
