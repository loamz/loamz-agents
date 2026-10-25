"use client";

import { useEffect } from "react";

export function MessengerPwa() {
  useEffect(() => {
    if ("serviceWorker" in navigator) void navigator.serviceWorker.register("/chat/sw.js", { scope: "/chat" }).catch(() => undefined);
  }, []);
  return null;
}
