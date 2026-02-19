"use client";

import { useState } from "react";
import { Save, Eye, EyeOff, Trash2 } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import type { StoredCreds } from "@/lib/storage";
import { clearCreds } from "@/lib/storage";

interface Props {
  creds: StoredCreds | null;
  onSave: (creds: StoredCreds) => void;
}

export function SettingsPanel({ creds, onSave }: Props) {
  const [subdomain, setSubdomain] = useState(creds?.subdomain ?? "");
  const [email, setEmail] = useState(creds?.email ?? "");
  const [token, setToken] = useState(creds?.token ?? "");
  const [showToken, setShowToken] = useState(false);
  const [saved, setSaved] = useState(false);

  const handleSave = () => {
    const s = subdomain.trim().replace(/^https?:\/\//, "").replace(/\.zendesk\.com$/i, "").split(".")[0];
    onSave({ subdomain: s, email: email.trim(), token: token.trim() });
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  };

  const handleClear = () => {
    clearCreds();
    setSubdomain("");
    setEmail("");
    setToken("");
  };

  return (
    <div className="max-w-lg space-y-4">
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Zendesk API credentials</CardTitle>
          <CardDescription>
            Credentials are stored only in your browser (localStorage). They are never sent to the z2w server.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="space-y-1.5">
            <label className="text-sm font-medium">Subdomain</label>
            <Input
              placeholder="yourcompany (from yourcompany.zendesk.com)"
              value={subdomain}
              onChange={(e) => setSubdomain(e.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <label className="text-sm font-medium">Email</label>
            <Input
              type="email"
              placeholder="you@company.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <label className="text-sm font-medium">API Token</label>
            <div className="relative">
              <Input
                type={showToken ? "text" : "password"}
                placeholder="Your Zendesk API token"
                value={token}
                onChange={(e) => setToken(e.target.value)}
                className="pr-10"
              />
              <button
                type="button"
                className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                onClick={() => setShowToken((s) => !s)}
              >
                {showToken ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
              </button>
            </div>
          </div>
          <div className="flex gap-2 pt-1">
            <Button onClick={handleSave} size="sm" className="gap-1.5">
              <Save className="h-3.5 w-3.5" />
              {saved ? "Saved!" : "Save"}
            </Button>
            {creds && (
              <Button variant="destructive" size="sm" onClick={handleClear} className="gap-1.5">
                <Trash2 className="h-3.5 w-3.5" />
                Clear
              </Button>
            )}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Connection status</CardTitle>
        </CardHeader>
        <CardContent>
          {creds?.subdomain && creds?.token ? (
            <div className="space-y-1 text-sm">
              <div className="flex items-center gap-2">
                <Badge variant="default" className="bg-primary text-primary-foreground">Connected</Badge>
                <span className="text-muted-foreground">{creds.subdomain}.zendesk.com</span>
              </div>
              <p className="text-muted-foreground text-xs">User: {creds.email || "—"}</p>
            </div>
          ) : (
            <Badge variant="secondary">Not connected — enter credentials above</Badge>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">About</CardTitle>
          <CardDescription>Zendesk Explorer is integrated into the z2w offload service.</CardDescription>
        </CardHeader>
        <CardContent className="text-sm text-muted-foreground space-y-1">
          <p>Browse tickets, files, and storage usage directly from your Zendesk account.</p>
          <p>Data is fetched live from Zendesk API. Nothing is cached on the z2w server.</p>
          <a href="/dashboard" className="text-primary hover:underline block mt-2">← Back to z2w offload dashboard</a>
        </CardContent>
      </Card>
    </div>
  );
}
