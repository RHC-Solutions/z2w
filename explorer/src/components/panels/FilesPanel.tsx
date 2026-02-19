"use client";

import { useState, useCallback } from "react";
import { RefreshCw, Download, Search, ExternalLink } from "lucide-react";
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

export function FilesPanel({ creds }: Props) {
  const [files, setFiles] = useState<Attachment[]>([]);
  const [exporting, setExporting] = useState(false);
  const [progress, setProgress] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [sortBy, setSortBy] = useState<SortKey>("size");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");

  const connected = Boolean(creds?.subdomain && creds?.token);

  const scanAndLoad = useCallback(async (download = false) => {
    if (!connected || !creds) return;
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

        for (const t of tickets) {
          try {
            const commentsData = await zendeskFetch(creds.subdomain, creds.email, creds.token, `/tickets/${t.id}/comments.json`) as { comments: Comment[] };
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
              // inline images from html_body
              const htmlBody = c.html_body || "";
              const seen = new Set(c.attachments?.map((a) => a.content_url) || []);
              const matches = htmlBody.matchAll(/src="(https?:\/\/[^"]+\.zendesk\.com\/attachments[^"]+)"/g);
              for (const m of matches) {
                const url = m[1];
                if (seen.has(url)) continue;
                seen.add(url);
                let name = "";
                try { name = new URL(url).searchParams.get("name") || "inline"; } catch { name = "inline"; }
                if (name.toLowerCase() === "redacted.txt") continue;
                attachments.push({ id: null, name, size: 0, contentType: "image/*", url, createdAt: c.created_at, updatedAt: c.created_at, ticketId: t.id });
              }
            }
          } catch { /* skip ticket */ }
        }

        ticketsScanned += tickets.length;
        const hasMore = page.meta?.has_more;
        const nextLink = page.links?.next;
        if (hasMore && nextLink) {
          try {
            const u = new URL(nextLink);
            pageUrl = u.pathname + u.search;
          } catch { pageUrl = null; }
        } else {
          pageUrl = null;
        }
        await new Promise((r) => setTimeout(r, 200));
      }

      attachments.sort((a, b) => (b.size || 0) - (a.size || 0));
      setFiles(attachments);
      setProgress(`Loaded ${attachments.length.toLocaleString()} attachments from ${ticketsScanned.toLocaleString()} tickets.`);

      if (download && attachments.length > 0) {
        const csvEscape = (v: unknown) => {
          const s = String(v ?? "");
          return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
        };
        const headers = ["ticket_id", "file_name", "size_bytes", "content_type", "created_at", "url"];
        const lines = [headers.join(","), ...attachments.map((a) =>
          [csvEscape(a.ticketId), csvEscape(a.name), csvEscape(a.size), csvEscape(a.contentType), csvEscape(a.createdAt), csvEscape(a.url)].join(",")
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
      setExporting(false);
    }
  }, [connected, creds]);

  const filtered = files.filter((f) =>
    !search || f.name.toLowerCase().includes(search.toLowerCase()) || String(f.ticketId).includes(search)
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
        <Button size="sm" className="h-8 gap-1.5" onClick={() => scanAndLoad(false)} disabled={exporting || !connected}>
          <RefreshCw className={`h-3.5 w-3.5 ${exporting ? "animate-spin" : ""}`} />
          {exporting ? "Scanning…" : "Scan"}
        </Button>
        <Button size="sm" variant="secondary" className="h-8 gap-1.5" onClick={() => scanAndLoad(true)} disabled={exporting || !connected}>
          <Download className="h-3.5 w-3.5" />
          Export CSV
        </Button>
      </div>

      {progress && <p className="text-xs text-muted-foreground">{progress}</p>}
      {error && <p className="text-xs text-destructive">{error}</p>}

      {!connected && (
        <Card>
          <CardContent className="pt-6 text-sm text-muted-foreground">
            Enter your Zendesk API credentials in Settings to browse files.
          </CardContent>
        </Card>
      )}

      {connected && files.length === 0 && !exporting && (
        <Card>
          <CardContent className="pt-6 text-sm text-muted-foreground">
            Click <strong>Scan</strong> to fetch all attachments from Zendesk tickets.
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
