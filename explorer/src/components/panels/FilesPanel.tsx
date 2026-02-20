"use client";

import { useState, useCallback, useEffect, useRef } from "react";
import { RefreshCw, Download, ExternalLink } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
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
import { zendeskFetch, formatBytes, formatDate, getSubdomain } from "@/lib/api";
import type { StoredCreds } from "@/lib/storage";

interface Attachment {
  id: number | null;
  name: string;
  size: number;
  contentType: string;
  url: string;
  createdAt: string;
  updatedAt: string;
  ticketId: number;
}

interface Comment {
  id: number;
  attachments: Array<{
    id: number;
    file_name: string;
    size: number;
    content_type: string;
    content_url: string;
    created_at: string;
  }>;
  html_body?: string;
  created_at: string;
}

type SortKey = "name" | "size" | "contentType" | "ticketId" | "createdAt";

interface Props {
  creds: StoredCreds | null;
}

const REFRESH_INTERVAL = 5 * 60 * 1000; // 5 minutes

export function FilesPanel({ creds }: Props) {
  const [files, setFiles] = useState<Attachment[]>([]);
  const [exporting, setExporting] = useState(false);
  const [progress, setProgress] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [sortBy, setSortBy] = useState<SortKey>("size");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");
  const [lastScanTime, setLastScanTime] = useState<string | null>(null);
  const [nextRefresh, setNextRefresh] = useState<string | null>(null);

  const connected = Boolean(creds?.subdomain && creds?.token);
  const scanningRef = useRef(false);
  const filesRef = useRef<Attachment[]>([]);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const hasInitialScan = useRef(false);

  /** Fetch attachments from a list of tickets. */
  const fetchAttachmentsForTickets = useCallback(async (
    tickets: Array<{ id: number; subject: string }>,
  ): Promise<Attachment[]> => {
    if (!creds) return [];
    const attachments: Attachment[] = [];
    for (const t of tickets) {
      try {
        const commentsData = await zendeskFetch(
          creds.subdomain, creds.email, creds.token,
          `/tickets/${t.id}/comments.json`,
        ) as { comments: Comment[] };
        for (const c of commentsData.comments || []) {
          for (const a of c.attachments || []) {
            if ((a.file_name || "").toLowerCase() === "redacted.txt") continue;
            attachments.push({
              id: a.id,
              name: a.file_name || "",
              size: a.size || 0,
              contentType: a.content_type || "",
              url: a.content_url || "",
              createdAt: a.created_at || "",
              updatedAt: a.created_at || "",
              ticketId: t.id,
            });
          }
          const htmlBody = c.html_body || "";
          const seen = new Set(c.attachments?.map((a) => a.content_url) || []);
          const matches = htmlBody.matchAll(
            /src="(https?:\/\/[^"]+\.zendesk\.com\/attachments[^"]+)"/g,
          );
          for (const m of matches) {
            const url = m[1];
            if (seen.has(url)) continue;
            seen.add(url);
            let name = "";
            try { name = new URL(url).searchParams.get("name") || "inline"; } catch { name = "inline"; }
            if (name.toLowerCase() === "redacted.txt") continue;
            attachments.push({
              id: null, name, size: 0, contentType: "image/*", url,
              createdAt: c.created_at, updatedAt: c.created_at, ticketId: t.id,
            });
          }
        }
      } catch { /* skip ticket */ }
    }
    return attachments;
  }, [creds]);

  /** Full scan — fetches ALL tickets and their attachments. */
  const fullScan = useCallback(async (download = false) => {
    if (!connected || !creds || scanningRef.current) return;
    scanningRef.current = true;
    setExporting(true);
    setError(null);
    setProgress("Fetching tickets…");

    const attachments: Attachment[] = [];
    const sub = getSubdomain(creds.subdomain);

    try {
      let pageUrl: string | null = "/tickets.json?page[size]=50";
      let ticketsScanned = 0;

      while (pageUrl) {
        setProgress(`Scanning tickets… (${ticketsScanned} scanned, ${attachments.length} files found)`);
        const page = await zendeskFetch(creds.subdomain, creds.email, creds.token, pageUrl) as {
          tickets: Array<{ id: number; subject: string }>;
          meta?: { has_more: boolean };
          links?: { next?: string };
        };
        const tickets = page.tickets || [];
        if (tickets.length === 0) break;

        const batch = await fetchAttachmentsForTickets(tickets);
        attachments.push(...batch);
        ticketsScanned += tickets.length;

        // Live-update the table during scan
        const sorted = [...attachments].sort((a, b) => (b.size || 0) - (a.size || 0));
        filesRef.current = sorted;
        setFiles(sorted);

        const hasMore = page.meta?.has_more;
        const nextLink = page.links?.next;
        if (hasMore && nextLink) {
          try {
            const u = new URL(nextLink);
            const raw = u.pathname + u.search;
            pageUrl = raw.replace(/^\/api\/v2/, "");
          } catch { pageUrl = null; }
        } else {
          pageUrl = null;
        }
        await new Promise((r) => setTimeout(r, 200));
      }

      attachments.sort((a, b) => (b.size || 0) - (a.size || 0));
      filesRef.current = attachments;
      setFiles(attachments);

      const now = new Date();
      setLastScanTime(now.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" }));
      const next = new Date(now.getTime() + REFRESH_INTERVAL);
      setNextRefresh(next.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" }));
      setProgress(`Loaded ${attachments.length.toLocaleString()} files from ${ticketsScanned.toLocaleString()} tickets.`);

      if (download && attachments.length > 0) {
        const csvEscape = (v: unknown) => {
          const s = String(v ?? "");
          return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
        };
        const headers = ["ticket_id", "file_name", "size_bytes", "content_type", "created_at", "url"];
        const lines = [headers.join(","), ...attachments.map((a) =>
          [csvEscape(a.ticketId), csvEscape(a.name), csvEscape(a.size), csvEscape(a.contentType), csvEscape(a.createdAt), csvEscape(a.url)].join(","),
        )];
        const blob = new Blob([lines.join("\n")], { type: "text/csv;charset=utf-8;" });
        const link = document.createElement("a");
        link.href = URL.createObjectURL(blob);
        link.download = `${sub}-attachments-${new Date().toISOString().slice(0, 10)}.csv`;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        URL.revokeObjectURL(link.href);
      }
    } catch (e) {
      setError(String(e));
    } finally {
      scanningRef.current = false;
      setExporting(false);
    }
  }, [connected, creds, fetchAttachmentsForTickets]);

  /** Delta refresh — fetch only recently updated tickets and merge. */
  const deltaRefresh = useCallback(async () => {
    if (!connected || !creds || scanningRef.current) return;
    scanningRef.current = true;
    setProgress("Refreshing recent changes…");

    try {
      const since = new Date(Date.now() - 10 * 60 * 1000).toISOString();
      const data = await zendeskFetch(
        creds.subdomain, creds.email, creds.token,
        `/search.json?query=${encodeURIComponent(`type:ticket updated>${since}`)}&per_page=100`,
      ) as { results: Array<{ id: number; subject: string }> };

      const updatedTickets = data.results || [];
      if (updatedTickets.length === 0) {
        const now = new Date();
        setLastScanTime(now.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" }));
        const next = new Date(now.getTime() + REFRESH_INTERVAL);
        setNextRefresh(next.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" }));
        setProgress(`No changes. ${filesRef.current.length.toLocaleString()} files total.`);
        return;
      }

      setProgress(`Refreshing ${updatedTickets.length} updated ticket(s)…`);
      const updatedIds = new Set(updatedTickets.map((t) => t.id));
      const newAttachments = await fetchAttachmentsForTickets(updatedTickets);

      // Merge: remove old entries for updated tickets, add new ones
      const kept = filesRef.current.filter((f) => !updatedIds.has(f.ticketId));
      const merged = [...kept, ...newAttachments].sort((a, b) => (b.size || 0) - (a.size || 0));
      filesRef.current = merged;
      setFiles(merged);

      const now = new Date();
      setLastScanTime(now.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" }));
      const next = new Date(now.getTime() + REFRESH_INTERVAL);
      setNextRefresh(next.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" }));
      setProgress(
        `Updated ${updatedTickets.length} ticket(s), ${newAttachments.length} file(s) refreshed. ${merged.length.toLocaleString()} files total.`,
      );
    } catch (e) {
      setError(`Delta refresh failed: ${e}`);
    } finally {
      scanningRef.current = false;
    }
  }, [connected, creds, fetchAttachmentsForTickets]);

  // Auto-start full scan on mount
  useEffect(() => {
    if (!connected || hasInitialScan.current) return;
    hasInitialScan.current = true;
    fullScan();
  }, [connected, fullScan]);

  // Delta refresh every 5 min
  useEffect(() => {
    if (!connected) return;
    timerRef.current = setInterval(() => {
      if (!scanningRef.current && filesRef.current.length > 0) {
        deltaRefresh();
      }
    }, REFRESH_INTERVAL);
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [connected, deltaRefresh]);

  const filtered = files.filter((f) =>
    !search || f.name.toLowerCase().includes(search.toLowerCase()) || String(f.ticketId).includes(search),
  );

  const sorted = [...filtered].sort((a, b) => {
    const va = a[sortBy] ?? "";
    const vb = b[sortBy] ?? "";
    const cmp = va < vb ? -1 : va > vb ? 1 : 0;
    return sortDir === "asc" ? cmp : -cmp;
  });

  const sub = creds?.subdomain ? getSubdomain(creds.subdomain) : "";
  const ticketBaseUrl = sub ? `https://${sub}.zendesk.com/agent/tickets/` : null;

  return (
    <div className="space-y-3">
      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex items-center gap-1.5 flex-1 min-w-[160px]">
          <Input
            placeholder="Search by name or ticket…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="h-8 text-sm"
          />
        </div>
        <Select value={sortBy} onValueChange={(v) => setSortBy(v as SortKey)}>
          <SelectTrigger className="h-8 w-32 text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="size">Size</SelectItem>
            <SelectItem value="name">Name</SelectItem>
            <SelectItem value="ticketId">Ticket</SelectItem>
            <SelectItem value="contentType">Type</SelectItem>
            <SelectItem value="createdAt">Created</SelectItem>
          </SelectContent>
        </Select>
        <Button size="sm" variant="secondary" className="h-8 px-2" onClick={() => setSortDir((d) => d === "asc" ? "desc" : "asc")}>
          {sortDir === "asc" ? "↑" : "↓"}
        </Button>
        <Button size="sm" className="h-8 gap-1.5" onClick={() => fullScan(false)} disabled={exporting || !connected}>
          <RefreshCw className={`h-3.5 w-3.5 ${exporting ? "animate-spin" : ""}`} />
          {exporting ? "Scanning…" : "Full Scan"}
        </Button>
        <Button size="sm" variant="secondary" className="h-8 gap-1.5" onClick={() => fullScan(true)} disabled={exporting || !connected}>
          <Download className="h-3.5 w-3.5" />
          Export CSV
        </Button>
      </div>

      {/* Status bar */}
      <div className="flex items-center gap-3 text-xs text-muted-foreground">
        {progress && <span>{progress}</span>}
        {lastScanTime && (
          <span className="ml-auto whitespace-nowrap">
            Last: <b className="text-foreground">{lastScanTime}</b>
            {nextRefresh && <> · Next: <b className="text-foreground">{nextRefresh}</b></>}
          </span>
        )}
      </div>

      {error && <p className="text-xs text-destructive">{error}</p>}

      {!connected && (
        <Card>
          <CardContent className="pt-6 text-sm text-muted-foreground">
            Enter your Zendesk API credentials in Settings to browse files.
          </CardContent>
        </Card>
      )}

      {connected && files.length === 0 && !exporting && !progress && (
        <Card>
          <CardContent className="pt-6 text-sm text-muted-foreground">
            Scan will start automatically…
          </CardContent>
        </Card>
      )}

      {sorted.length > 0 && (
        <>
          <p className="text-xs text-muted-foreground">{sorted.length.toLocaleString()} file(s)</p>
          <Card>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead className="w-24">Size</TableHead>
                  <TableHead className="w-32">Type</TableHead>
                  <TableHead className="w-24">Ticket</TableHead>
                  <TableHead className="w-32">Created</TableHead>
                  <TableHead className="w-10"></TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {sorted.slice(0, 500).map((f, i) => (
                  <TableRow key={i} className="text-sm">
                    <TableCell className="max-w-xs truncate font-medium">{f.name}</TableCell>
                    <TableCell className="text-xs text-muted-foreground">{formatBytes(f.size)}</TableCell>
                    <TableCell className="text-xs text-muted-foreground truncate max-w-[128px]">{f.contentType}</TableCell>
                    <TableCell className="text-xs">
                      {ticketBaseUrl ? (
                        <a href={`${ticketBaseUrl}${f.ticketId}`} target="_blank" rel="noopener noreferrer" className="text-primary hover:underline">
                          #{f.ticketId}
                        </a>
                      ) : `#${f.ticketId}`}
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">{formatDate(f.createdAt)}</TableCell>
                    <TableCell>
                      <a href={f.url} target="_blank" rel="noopener noreferrer" className="text-muted-foreground hover:text-primary">
                        <ExternalLink className="h-3.5 w-3.5" />
                      </a>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            {sorted.length > 500 && (
              <p className="text-xs text-muted-foreground px-4 py-2">Showing first 500 results. Use Export CSV to get all {sorted.length.toLocaleString()}.</p>
            )}
          </Card>
        </>
      )}
    </div>
  );
}
