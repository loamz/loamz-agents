import type { GroupMember } from "@/lib/api/groups";

/** Who has seen a message, who has it on a device, and who has neither yet: the other people in the chat, never agents. */
export type Receipt = { seen: string[]; delivered: string[]; waiting: string[] };

export function receiptFor(
  seq: number,
  members: GroupMember[],
  me: boolean,
  readBy: Record<string, number>,
  deliveredBy: Record<string, number>,
  sender?: string,
): Receipt {
  const receipt: Receipt = { seen: [], delivered: [], waiting: [] };
  if (!me) return receipt;
  for (const m of members) {
    if (m.kind !== "human" || !m.user_id || m.user_id === sender) continue;
    if ((readBy[m.user_id] ?? -1) >= seq) receipt.seen.push(m.name);
    else if ((deliveredBy[m.user_id] ?? -1) >= seq) receipt.delivered.push(m.name);
    else receipt.waiting.push(m.name);
  }
  return receipt;
}

/** "Seen by Bob · Delivered to Carol · Waiting for Dave": the tooltip of a message's ticks. */
export function receiptLine(r: Receipt): string {
  const parts: string[] = [];
  if (r.seen.length) parts.push(`Seen by ${r.seen.join(", ")}`);
  if (r.delivered.length) parts.push(`Delivered to ${r.delivered.join(", ")}`);
  if (r.waiting.length) parts.push(`Waiting for ${r.waiting.join(", ")}`);
  return parts.join(" · ");
}

/** The tick a message gets: sent, delivered to everyone, or seen by everyone. Agents never hold it back. */
export function tickFor(r: Receipt, fallbackSeen: boolean): "sent" | "delivered" | "seen" {
  const people = r.seen.length + r.delivered.length + r.waiting.length;
  if (people === 0) return fallbackSeen ? "seen" : "sent";
  if (r.seen.length === people) return "seen";
  if (r.waiting.length === 0) return "delivered";
  return "sent";
}
