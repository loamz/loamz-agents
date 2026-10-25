"use client";

import { useEffect } from "react";
import { serviceWorkerUrl } from "@/lib/trusted";

export function MessengerPwa() {
  useEffect(() => {
    if ("serviceWorker" in navigator) void navigator.serviceWorker.register(serviceWorkerUrl(), { scope: "/chat" }).catch(() => undefined);
  }, []);
  return null;
}
