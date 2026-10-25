"use client";

import { useEffect, useState } from "react";
import { UserRound } from "lucide-react";
import { Button, Checkbox, Input } from "@/design";
import { api } from "@/lib/api";
import type { Person } from "@/lib/api/groups";

/** The directory search is scoped by the verified server session, including when a query changes mid-request. */
export function PeoplePicker({ chosen, exclude = [], onChange }: {
  chosen: Person[];
  exclude?: string[];
  onChange: (people: Person[]) => void;
}) {
  const [query, setQuery] = useState("");
  const [people, setPeople] = useState<Person[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let active = true;
    const timer = setTimeout(() => {
      setLoading(true);
      api.searchPeople(query).then((rows) => {
        if (active) { setPeople(rows); setError(""); }
      }).catch(() => {
        if (active) setError("The organization directory is unavailable.");
      }).finally(() => { if (active) setLoading(false); });
    }, 200);
    return () => { active = false; clearTimeout(timer); };
  }, [query]);
  const rows = [...chosen, ...people.filter((p) => !chosen.some((c) => c.user_id === p.user_id))].filter((p) => !exclude.includes(p.user_id));
  return (
    <div className="flex flex-col gap-3">
      <Input aria-label="Find colleagues" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search your organization" />
      {error && <p role="status" className="text-sm text-danger">{error}</p>}
      {loading && <p role="status" className="text-sm text-muted">Searching…</p>}
      <ul className="flex flex-col gap-2">
        {rows.map((person) => (
          <li key={person.user_id} className="flex items-center gap-2">
            <UserRound aria-hidden />
            <Checkbox label={person.email ? `${person.name} · ${person.email}` : person.name} checked={chosen.some((p) => p.user_id === person.user_id)} onChange={(e) => onChange(e.target.checked ? [...chosen, person] : chosen.filter((p) => p.user_id !== person.user_id))} />
          </li>
        ))}
      </ul>
      {chosen.length > 0 && <Button variant="ghost" onClick={() => onChange([])}>Clear selection</Button>}
    </div>
  );
}
