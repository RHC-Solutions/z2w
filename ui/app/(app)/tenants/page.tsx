"use client";

import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { fmtBytes, fmtNum, type TenantCard, type Fleet } from "@/lib/api";
import {
  AlertTriangle, CheckCircle2, Users, HardDrive,
  ArrowUpFromLine, Archive, RefreshCw, Settings, ExternalLink,
} from "lucide-react";
import Link from "next/link";

export default function TenantsPage() {
  const [cards, setCards] = useState<TenantCard[]>([]);
  const [fleet, setFleet] = useState<Fleet | null>(null);
  const [loading, setLoading] = useState(true);

  const load = () => {
    setLoading(true);
    fetch("/api/tenants/overview", { credentials: "include" })
      .then((r) => r.json())
      .then((d) => { setCards(d.cards ?? []); setFleet(d.fleet ?? null); })
      .finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, []);

  return (
    <div className="p-6 space-y-6 max-w-7xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Tenants</h1>
          <p className="text-muted-foreground text-sm mt-0.5">All configured Zendesk instances</p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={load} disabled={loading}>
            <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
          </Button>
          <Link href="/wizard">
            <Button size="sm">+ Add Tenant</Button>
          </Link>
        </div>
      </div>

      {/* Fleet summary */}
      {fleet && (
        <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
          <FleetStat icon={<Users size={15} />} label="Tenants" value={`${fleet.active} / ${fleet.total}`} sub="active" />
          <FleetStat icon={<ArrowUpFromLine size={15} />} label="Tickets Offloaded" value={fmtNum(fleet.tickets_processed)} sub={`${fmtNum(fleet.total_attachments)} attach · ${fmtNum(fleet.total_inlines)} inline`} />
          <FleetStat icon={<HardDrive size={15} />} label="Offloaded to Wasabi" value={fmtBytes(fleet.total_bytes)} sub="across all tenants" />
          <FleetStat icon={<Archive size={15} />} label="Tickets Backed Up" value={fmtNum(fleet.tickets_backed_up)} sub="closed-ticket archive" />
          <FleetStat
            icon={<AlertTriangle size={15} />}
            label="Issues"
            value={String(fleet.issues)}
            sub={fleet.issues === 0 ? "all clear" : "tenant need attention"}
            accent={fleet.issues > 0 ? "destructive" : "success"}
          />
        </div>
      )}

      {/* Tenant cards */}
      {loading ? (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {[1, 2].map((i) => <Skeleton key={i} className="h-64 rounded-xl" />)}
        </div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {cards.map((c) => <TenantCardView key={c.slug} card={c} onToggle={load} />)}
        </div>
      )}
    </div>
  );
}

function FleetStat({
  icon, label, value, sub, accent,
}: {
  icon: React.ReactNode; label: string; value: string; sub?: string;
  accent?: "destructive" | "success";
}) {
  return (
    <Card className="bg-card border-border">
      <CardContent className="pt-4 pb-4">
        <div className={`flex items-center gap-1.5 text-xs text-muted-foreground mb-1 ${accent === "destructive" ? "text-destructive" : accent === "success" ? "text-[var(--success)]" : ""}`}>
          {icon} {label}
        </div>
        <div className={`text-2xl font-bold leading-tight ${accent === "destructive" ? "text-destructive" : accent === "success" ? "text-[var(--success)]" : "text-primary"}`}>
          {value}
        </div>
        {sub && <div className="text-xs text-muted-foreground mt-0.5">{sub}</div>}
      </CardContent>
    </Card>
  );
}

function TenantCardView({ card, onToggle }: { card: TenantCard; onToggle: () => void }) {
  const [toggling, setToggling] = useState(false);

  async function handleToggle() {
    setToggling(true);
    await fetch(`/api/tenants/${card.slug}/toggle`, { method: "POST", credentials: "include" });
    onToggle();
    setToggling(false);
  }

  return (
    <Link href={`/t/${card.slug}/dashboard`} className="block group">
    <Card className="bg-card border-border hover:border-primary/40 group-hover:bg-card/80 transition-colors cursor-pointer">
      <CardContent className="pt-5 pb-5">
        {/* Top row */}
        <div className="flex items-start justify-between mb-4">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-primary/10 flex items-center justify-center flex-shrink-0">
              <HardDrive size={18} className="text-primary" />
            </div>
            <div className="min-w-0">
              <div className="font-semibold text-base leading-tight">{card.display_name}</div>
              <div className="text-xs text-muted-foreground mt-0.5">
                {card.zendesk_subdomain}.zendesk.com
                {card.wasabi_bucket && <> · <span>{card.wasabi_bucket}</span></>}
              </div>
            </div>
          </div>
          <Badge variant={card.is_active ? "default" : "secondary"} className={card.is_active ? "bg-[var(--success)]/15 text-[var(--success)] border-[var(--success)]/30" : ""}>
            {card.is_active ? "Active" : "Inactive"}
          </Badge>
        </div>

        {/* Stats row */}
        <div className="grid grid-cols-5 gap-1 mb-4 text-center">
          <StatMini label="OFFLOADED" value={fmtNum(card.tickets_processed)} color="primary" />
          <StatMini label="ATTACHMENTS" value={fmtNum(card.total_attachments)} />
          <StatMini label="INLINES" value={fmtNum(card.total_inlines_offloaded)} />
          <StatMini label="BACKED UP" value={fmtNum(card.tickets_backed_up)} color="success" />
          <StatMini label="ERRORS" value={fmtNum(card.errors_today)} color={card.errors_today > 0 ? "destructive" : undefined} />
        </div>

        {/* Storage */}
        <div className="text-xs text-muted-foreground mb-3 flex items-center gap-3">
          <span className="flex items-center gap-1"><HardDrive size={12} /> Wasabi: <strong className="text-foreground">{fmtBytes(card.total_bytes_offloaded)}</strong></span>
          <span>{fmtNum(card.total_runs)} runs</span>
          {card.last_offload_ago && <span className="text-muted-foreground">{card.last_offload_ago}</span>}
        </div>

        {/* Last backup */}
        {card.last_backup_run && (
          <div className="text-xs text-muted-foreground mb-3 border-t border-border pt-3">
            <div className="font-medium text-foreground/70 mb-1">LAST BACKUP RUN</div>
            <div>
              {new Date(card.last_backup_run.run_date).toLocaleString("en-GB", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" })} UTC
              {" · "}scanned {card.last_backup_run.tickets_scanned}
              {" · "}backed up {card.last_backup_run.tickets_backed_up}
              {" · "}{card.last_backup_run.files_uploaded} files ({fmtBytes(card.last_backup_run.bytes_uploaded)})
              {card.last_backup_run.errors_count > 0 && (
                <span className="text-destructive"> · {card.last_backup_run.errors_count} error{card.last_backup_run.errors_count !== 1 ? "s" : ""}</span>
              )}
            </div>
            <Badge
              variant="outline"
              className={`mt-1.5 text-[11px] ${card.last_backup_run.status === "completed" ? "border-[var(--success)]/40 text-[var(--success)]" : "border-destructive/40 text-destructive"}`}
            >
              {card.last_backup_run.status.replace(/_/g, " ")}
            </Badge>
          </div>
        )}

        {/* Red flags */}
        {card.red_flags.length > 0 && (
          <div className="space-y-1.5 mb-3">
            {card.red_flags.map((f) => (
              <Alert key={f} variant="destructive" className="py-2 px-3">
                <AlertTriangle size={13} className="mr-1.5 inline" />
                <AlertDescription className="inline text-xs">{f}</AlertDescription>
              </Alert>
            ))}
          </div>
        )}

        {/* No issues */}
        {card.red_flags.length === 0 && (
          <div className="flex items-center gap-1.5 text-xs text-[var(--success)] mb-3">
            <CheckCircle2 size={13} /> No issues
          </div>
        )}

        {/* Footer info */}
        <div className="text-xs text-muted-foreground mb-4">
          {card.last_offload_ago ? `Last offload: ${card.last_offload_ago}` : "Never offloaded"}
        </div>

        {/* Actions */}
        <div className="flex gap-2" onClick={(e) => e.preventDefault()}>
          <Link href={`/t/${card.slug}/dashboard`} className="flex-1" onClick={(e) => e.stopPropagation()}>
            <Button size="sm" className="w-full">
              <ExternalLink size={13} className="mr-1.5" /> Open
            </Button>
          </Link>
          <Link href={`/t/${card.slug}/settings`} onClick={(e) => e.stopPropagation()}>
            <Button size="sm" variant="outline">
              <Settings size={13} className="mr-1.5" /> Settings
            </Button>
          </Link>
          <Button size="sm" variant="outline" onClick={(e) => { e.preventDefault(); e.stopPropagation(); handleToggle(); }} disabled={toggling}>
            {card.is_active ? "Disable" : "Enable"}
          </Button>
        </div>
      </CardContent>
    </Card>
    </Link>
  );
}

function StatMini({ label, value, color }: { label: string; value: string; color?: string }) {
  const c = color === "primary" ? "text-primary" : color === "success" ? "text-[var(--success)]" : color === "destructive" ? "text-destructive" : "text-foreground";
  return (
    <div>
      <div className={`text-lg font-bold leading-tight ${c}`}>{value}</div>
      <div className="text-[10px] text-muted-foreground leading-tight">{label}</div>
    </div>
  );
}
