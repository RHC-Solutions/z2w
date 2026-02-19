"use client";

import { useState, useEffect, useCallback } from "react";
import { Ticket, Paperclip, HardDrive, Settings } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { loadCreds, saveCreds, type StoredCreds } from "@/lib/storage";

import { TicketsPanel } from "./panels/TicketsPanel";
import { FilesPanel } from "./panels/FilesPanel";
import { StoragePanel } from "./panels/StoragePanel";
import { SettingsPanel } from "./panels/SettingsPanel";

type PanelId = "tickets" | "files" | "storage" | "settings";

const NAV: { id: PanelId; label: string; icon: React.ElementType }[] = [
  { id: "tickets",  label: "Tickets",          icon: Ticket   },
  { id: "files",    label: "Files & Attachments", icon: Paperclip },
  { id: "storage",  label: "Storage Overview", icon: HardDrive },
  { id: "settings", label: "Settings",         icon: Settings  },
];

export function ExplorerShell() {
  const [active, setActive] = useState<PanelId>("tickets");
  const [creds, setCreds] = useState<StoredCreds | null>(null);
  const [mounted, setMounted] = useState(false);

  useEffect(() => { setMounted(true); setCreds(loadCreds()); }, []);

  const handleSave = useCallback((c: StoredCreds) => { saveCreds(c); setCreds(c); }, []);
  const connected = Boolean(creds?.subdomain && creds?.token);

  if (!mounted) return null;

  return (
    <div className="flex flex-col h-screen bg-background font-sans overflow-hidden">
      {/* Tab bar */}
      <div className="flex items-center gap-0 border-b border-border bg-card px-4 shrink-0">
        {NAV.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            onClick={() => setActive(id)}
            className={cn(
              "flex items-center gap-2 px-4 py-3 text-sm font-medium border-b-2 transition-colors",
              active === id
                ? "border-primary text-primary font-semibold"
                : "border-transparent text-muted-foreground hover:text-foreground"
            )}
          >
            <Icon className="h-4 w-4" />
            {label}
          </button>
        ))}
        <div className="ml-auto flex items-center gap-2 py-2">
          {connected ? (
            <Badge className="bg-primary text-primary-foreground text-xs">
              ✓ {creds?.subdomain}
            </Badge>
          ) : (
            <Badge variant="secondary" className="text-xs">Not connected</Badge>
          )}
        </div>
      </div>

      {/* Panel content */}
      <div className="flex-1 overflow-auto p-4">
        {active === "tickets" && <TicketsPanel creds={creds} />}
        {active === "files"   && <FilesPanel   creds={creds} />}
        {active === "storage" && <StoragePanel creds={creds} />}
        {active === "settings" && <SettingsPanel creds={creds} onSave={handleSave} />}
      </div>
    </div>
  );
}
