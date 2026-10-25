import { describe, expect, it } from "vitest";
import type { GroupMember } from "@/lib/api/groups";
import { receiptFor, receiptLine, tickFor } from "./receipts";

const member = (user_id: string | null, name: string, kind: "human" | "agent" = "human") =>
  ({ user_id, name, kind, agent_id: kind === "agent" ? name : null, member: `${kind === "human" ? "user" : "agent"}:${name}` }) as unknown as GroupMember;

const members = [member("alice", "Alice"), member("bob", "Bob"), member("carol", "Carol"), member(null, "Scout", "agent")];

describe("receipts", () => {
  it("sorts the other people into seen, delivered and waiting, and ignores agents and the sender", () => {
    const r = receiptFor(5, members, true, { bob: 5 }, { bob: 5, carol: 7 }, "alice");
    expect(r).toEqual({ seen: ["Bob"], delivered: ["Carol"], waiting: [] });
    expect(receiptFor(5, members, false, {}, {}, "alice")).toEqual({ seen: [], delivered: [], waiting: [] });
  });

  it("describes them in words", () => {
    expect(receiptLine({ seen: ["Bob"], delivered: ["Carol"], waiting: ["Dave"] })).toBe("Seen by Bob · Delivered to Carol · Waiting for Dave");
    expect(receiptLine({ seen: [], delivered: [], waiting: [] })).toBe("");
  });

  it("ticks: seen by everyone, delivered to everyone, or just sent", () => {
    expect(tickFor({ seen: ["Bob", "Carol"], delivered: [], waiting: [] }, false)).toBe("seen");
    expect(tickFor({ seen: ["Bob"], delivered: ["Carol"], waiting: [] }, false)).toBe("delivered");
    expect(tickFor({ seen: ["Bob"], delivered: [], waiting: ["Carol"] }, false)).toBe("sent");
    expect(tickFor({ seen: [], delivered: [], waiting: [] }, true)).toBe("seen"); // only agents: they read it
    expect(tickFor({ seen: [], delivered: [], waiting: [] }, false)).toBe("sent");
  });
});
