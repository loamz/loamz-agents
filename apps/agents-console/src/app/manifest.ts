import type { MetadataRoute } from "next";

export default function manifest(): MetadataRoute.Manifest {
  return {
    id: "/chat",
    name: "Loamz Messenger",
    short_name: "Loamz",
    description: "Conversations with colleagues and visible AI agents.",
    start_url: "/chat",
    scope: "/chat",
    display: "standalone",
    icons: [{ src: "/chat/favicon.svg", sizes: "any", type: "image/svg+xml", purpose: "any" }],
  };
}
