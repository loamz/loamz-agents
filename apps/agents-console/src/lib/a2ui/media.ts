/**
 * Where a card's pictures, videos and audio may come from. An agent chooses the address, so it is never loaded as written:
 *  - a file in the conversation's workspace (`sandbox:chart.png`, or a bare path) is served by the authenticated file endpoint;
 *  - a small `data:` image is shown as it is;
 *  - a web picture (`https://`) goes through the server's picture proxy, which fetches it once, checks it is a picture of a
 *    sane size, caches it and refuses private addresses: the person's browser never contacts the site, so nothing about
 *    them leaks and a slow site is slow once;
 *  - anything else (`http:`, `javascript:`, `file:`, other schemes) is not shown.
 * Video and audio come only from the workspace.
 */
import { API_BASE, buildWorkspaceFileUrl } from "@/lib/api/_client";

export type MediaKind = "image" | "video" | "audio";

const DATA_IMAGE = /^data:image\/(png|jpe?g|gif|webp);base64,[a-z0-9+/=]+$/i;
const MAX_DATA_URL = 2_000_000;
const SCHEME = /^[a-z][a-z0-9+.-]*:/i;

export function resolveMedia(url: unknown, kind: MediaKind, threadId?: string | null): string | null {
  if (typeof url !== "string") return null;
  const raw = url.trim();
  if (!raw) return null;
  if (/^data:/i.test(raw)) return kind === "image" && raw.length <= MAX_DATA_URL && DATA_IMAGE.test(raw) ? raw : null;
  if (/^https:\/\//i.test(raw)) return kind === "image" ? `${API_BASE}/media/image?url=${encodeURIComponent(raw)}` : null;
  if (/^sandbox:/i.test(raw)) return threadId ? buildWorkspaceFileUrl(threadId, raw) : null;
  if (SCHEME.test(raw) || raw.startsWith("//")) return null;
  return threadId ? buildWorkspaceFileUrl(threadId, raw) : null;
}
