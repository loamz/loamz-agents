"use client";

import { Loader2 } from "lucide-react";
import { ProductHome } from "@/components/ProductHome";
import { SignedOut } from "@/components/SignedOut";
import { useAuth } from "@/contexts/AuthContext";

/** The front door: the public landing for visitors, the product chooser for people who are signed in. */
export function Welcome({ dataUrl }: { dataUrl: string | null }) {
  const { user, isAuthenticated, isLoading, login, logout } = useAuth();
  if (isLoading) {
    return (
      <div className="flex min-h-dvh items-center justify-center bg-background">
        <Loader2 className="animate-spin text-muted" />
      </div>
    );
  }
  if (!isAuthenticated) return <SignedOut onSignIn={() => login()} />;
  return <ProductHome name={user?.name || user?.email || "there"} dataUrl={dataUrl} onSignOut={() => void logout()} />;
}
