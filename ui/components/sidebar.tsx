"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState, useEffect } from "react";
import {
  Users, LayoutDashboard, FileText, Settings, Wrench,
  ChevronDown, ChevronUp, LogOut, Database, Ticket,
  Archive, PlusCircle, ShieldCheck,
} from "lucide-react";
import { cn } from "@/lib/utils";

interface Tenant {
  slug: string;
  display_name: string;
  is_active: boolean;
  color: string;
}

export function Sidebar() {
  const path = usePathname();
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  useEffect(() => {
    fetch("/api/tenants/list", { credentials: "include" })
      .then((r) => r.json())
      .then((d) => {
        setTenants(d.tenants ?? []);
        const init: Record<string, boolean> = {};
        (d.tenants ?? []).forEach((t: Tenant) => { init[t.slug] = true; });
        setExpanded(init);
      })
      .catch(() => {});
  }, []);

  // Refresh tenant list (color, name) when settings are saved
  useEffect(() => {
    function refetch() {
      fetch("/api/tenants/list", { credentials: "include" })
        .then((r) => r.json())
        .then((d) => setTenants(d.tenants ?? []))
        .catch(() => {});
    }
    window.addEventListener("z2w:tenantUpdated", refetch);
    return () => window.removeEventListener("z2w:tenantUpdated", refetch);
  }, []);

  const toggle = (slug: string) =>
    setExpanded((p) => ({ ...p, [slug]: !p[slug] }));

  const isActive = (href: string) => path === href || path.startsWith(href + "/");

  return (
    <aside className="flex flex-col w-56 min-w-56 h-screen bg-sidebar border-r border-sidebar-border overflow-y-auto">
      {/* Logo */}
      <div className="flex items-center gap-2 px-4 py-4 border-b border-sidebar-border">
        <img src="/static/logo.svg" alt="Z2W" className="w-8 h-8 rounded-md flex-shrink-0" />
        <div className="min-w-0">
          <div className="text-[17px] font-bold leading-tight text-sidebar-foreground">Z2W</div>
          <div className="text-[12px] text-muted-foreground leading-tight">RHC Solutions</div>
        </div>
      </div>

      <nav className="flex-1 py-3 px-2 space-y-0.5">
        {/* GLOBAL */}
        <p className="px-2 pb-1 pt-2 text-[11px] font-semibold uppercase tracking-widest text-muted-foreground">Global</p>
        <NavItem href="/tenants" icon={<Users size={15} />} label="Tenants" active={isActive("/tenants")} />
        <NavItem href="/wizard" icon={<PlusCircle size={15} />} label="Add Tenant" active={isActive("/wizard")} />

        {/* PER-TENANT */}
        {tenants.map((t) => (
          <div key={t.slug} className="mt-1">
            <button
              onClick={() => toggle(t.slug)}
              className={cn(
                "w-full flex items-center gap-2 px-2 py-2 rounded-md text-sm transition-colors",
                "text-sidebar-foreground hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
              )}
            >
              <Database size={15} className={cn("flex-shrink-0", t.is_active ? "text-primary" : "text-muted-foreground")}
                style={t.color ? { color: t.color } : undefined} />
              <span className="flex-1 text-left truncate font-medium">{t.display_name}</span>
              {!t.is_active && <span className="text-[9px] text-muted-foreground border border-border rounded px-1">off</span>}
              {expanded[t.slug] ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
            </button>
            {expanded[t.slug] && (
              <div className="ml-4 pl-2 border-l border-border space-y-0.5 mb-1">
                <NavItem href={`/t/${t.slug}/dashboard`} icon={<LayoutDashboard size={14} />} label="Dashboard" active={isActive(`/t/${t.slug}/dashboard`)} small />
                <NavItem href={`/t/${t.slug}/tickets`} icon={<Ticket size={14} />} label="Tickets" active={isActive(`/t/${t.slug}/tickets`)} small />
                <NavItem href={`/t/${t.slug}/backup`} icon={<Archive size={14} />} label="Backup" active={isActive(`/t/${t.slug}/backup`)} small />
                <NavItem href={`/t/${t.slug}/security`} icon={<ShieldCheck size={14} />} label="Security" active={isActive(`/t/${t.slug}/security`)} small />
                <NavItem href={`/t/${t.slug}/logs`} icon={<FileText size={14} />} label="Logs" active={isActive(`/t/${t.slug}/logs`)} small />
                <NavItem href={`/t/${t.slug}/settings`} icon={<Settings size={14} />} label="Settings" active={isActive(`/t/${t.slug}/settings`)} small />
              </div>
            )}
          </div>
        ))}

        {/* SYSTEM */}
        <p className="px-2 pb-1 pt-4 text-[11px] font-semibold uppercase tracking-widest text-muted-foreground">System</p>
        <NavItem href="/tools" icon={<Wrench size={15} />} label="Tools" active={isActive("/tools")} />
      </nav>

      {/* Logout */}
      <div className="border-t border-sidebar-border p-2">
        <a
          href="/logout"
          className="flex items-center gap-2 px-2 py-2 rounded-md text-sm text-muted-foreground hover:bg-sidebar-accent hover:text-sidebar-accent-foreground transition-colors"
        >
          <LogOut size={14} />
          <span>Sign out</span>
        </a>
      </div>
    </aside>
  );
}

function NavItem({
  href, icon, label, active, small,
}: {
  href: string; icon: React.ReactNode; label: string; active: boolean; small?: boolean;
}) {
  return (
    <Link
      href={href}
      className={cn(
        "flex items-center gap-2 rounded-md transition-colors",
        small ? "px-2 py-1.5 text-[13px]" : "px-2 py-2 text-sm",
        active
          ? "bg-sidebar-accent text-sidebar-accent-foreground font-medium"
          : "text-sidebar-foreground hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
      )}
    >
      {icon}
      <span>{label}</span>
    </Link>
  );
}
