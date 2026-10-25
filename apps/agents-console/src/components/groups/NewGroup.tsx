"use client";

import { useState } from "react";
import { Bot } from "lucide-react";
import { Button, Checkbox, Input, Page, Section } from "@/design";
import { api } from "@/lib/api";
import type { Agent } from "@/lib/api/agents";
import type { Group, Person } from "@/lib/api/groups";
import { reportError } from "@/lib/report-error";
import { PeoplePicker } from "./PeoplePicker";

type Props = { agents: Agent[]; onBack: () => void; onCreated: (group: Group) => void };

export function NewGroup({ agents, onBack, onCreated }: Props) {
  const [name, setName] = useState("");
  const [chosen, setChosen] = useState<string[]>([]);
  const [people, setPeople] = useState<Person[]>([]);
  const [direct, setDirect] = useState(false);
  const [busy, setBusy] = useState(false);
  const create = async () => {
    setBusy(true);
    try {
      const members = [...people.map((p) => ({ member: `user:${p.user_id}`, mode: "mentions" as const })), ...chosen.map((id) => ({ member: `agent:${id}`, mode: "mentions" as const }))];
      onCreated(await api.createGroup(direct ? people[0].name : name.trim(), members, direct ? "direct" : "group"));
    } catch (err) {
      reportError("Couldn't create the conversation", err);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Page title="New conversation" subtitle="Chat with colleagues and visible AI members." onBack={onBack} layout="columns">
      <Section title="Conversation">
        <div className="flex flex-col gap-3">
          <Checkbox label="Direct conversation with one colleague" checked={direct} onChange={(e) => { setDirect(e.target.checked); if (e.target.checked) setChosen([]); }} />
          {!direct && <Input value={name} maxLength={60} placeholder="Project team" onChange={(e) => setName(e.target.value)} aria-label="Group name" />}
          <p className="text-xs text-muted">Messages are stored on this server. End-to-end encryption is not available yet.</p>
        </div>
      </Section>
      <Section title="People" description="Only colleagues in your organization appear here.">
        <PeoplePicker chosen={people} onChange={setPeople} />
      </Section>
      {!direct && <Section title="AI agents" description="An agent and its AI provider can read every message in a chat it joins.">
        <ul className="flex flex-col gap-2">
          {agents.map((a) => <li key={a.id} className="flex items-center gap-2"><Bot aria-label="AI agent" /><Checkbox checked={chosen.includes(a.id)} onChange={(e) => setChosen(e.target.checked ? [...chosen, a.id] : chosen.filter((id) => id !== a.id))} label={a.role ? `${a.name} · ${a.role}` : a.name} /></li>)}
        </ul>
      </Section>}
      <div data-span="all" className="flex justify-end">
        <Button variant="primary" disabled={busy || (direct ? people.length !== 1 : !name.trim() || people.length + chosen.length === 0)} onClick={() => void create()}>{busy ? "Creating…" : "Create conversation"}</Button>
      </div>
    </Page>
  );
}
