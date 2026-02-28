"use client";

import { useState, FormEvent } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Loader2 } from "lucide-react";

export default function LoginPage() {
  const router = useRouter();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError("");
    setLoading(true);
    const fd = new FormData(e.currentTarget);
    try {
      const res = await fetch("/api/login", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          username: fd.get("username") as string,
          password: fd.get("password") as string,
        }),
      });
      const data = await res.json();
      if (data.ok) {
        const params = new URLSearchParams(window.location.search);
        const next = params.get("next") || "/tenants";
        router.push(next);
        router.refresh();
      } else {
        setError(data.error || "Invalid credentials");
      }
    } catch {
      setError("Connection error — please try again");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-background px-4">
      <div className="w-full max-w-sm">
        <div className="flex items-center justify-center gap-3 mb-8">
          <img src="/static/logo.svg" alt="Z2W" className="w-10 h-10 rounded-lg" />
          <div>
            <h1 className="text-2xl font-bold leading-tight">Z2W</h1>
            <p className="text-sm text-muted-foreground">RHC Solutions</p>
          </div>
        </div>
        <Card>
          <CardHeader className="pb-4">
            <CardTitle className="text-lg">Sign in</CardTitle>
            <CardDescription>Enter your admin credentials to continue</CardDescription>
          </CardHeader>
          <CardContent>
            <form onSubmit={onSubmit} className="space-y-4">
              {error && (
                <Alert variant="destructive">
                  <AlertDescription>{error}</AlertDescription>
                </Alert>
              )}
              <div className="space-y-1.5">
                <Label htmlFor="username">Username</Label>
                <Input id="username" name="username" placeholder="admin" autoComplete="username" required />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="password">Password</Label>
                <Input id="password" name="password" type="password" autoComplete="current-password" required />
              </div>
              <Button type="submit" className="w-full" disabled={loading}>
                {loading && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                Sign in
              </Button>
              <div className="relative my-2">
                <div className="absolute inset-0 flex items-center">
                  <span className="w-full border-t border-border" />
                </div>
                <div className="relative flex justify-center text-xs uppercase text-muted-foreground">
                  <span className="bg-card px-2">or</span>
                </div>
              </div>
              <a href="/login/oauth">
                <Button type="button" variant="outline" className="w-full">
                  Continue with Microsoft
                </Button>
              </a>
            </form>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
