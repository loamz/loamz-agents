import { Welcome } from "@/components/Welcome";

// Served at `/` (see next.config.ts), so the product opens on its front door, not straight into the chat.
export const dynamic = "force-dynamic";

export default function Page() {
  return <Welcome dataUrl={process.env.DATA_WORKSPACE_URL || null} />;
}
