---
name: software-architecture
description: "Design and review the structure of software systems: components, boundaries, data flow, storage, scaling, failure and trade-offs, with decisions written down. Use when asked to design a system or feature, choose between approaches, split or merge services, or assess whether a design will hold up."
metadata:
  title: Software architecture
  tagline: Design systems and weigh the trade-offs
  icon: Network
  category: Developer
---

# Software Architecture Skill

Good architecture is a set of decisions that make the next year of change cheap. Your job is to make the decisions visible, justified and reversible where possible.

## 1. Frame the problem
Before drawing anything, write down, and confirm with the user:
- **Goal:** what the system must do, in one or two sentences.
- **Users and load:** who, how many, how often. Use numbers (requests per second, data size, growth per year). If unknown, state an assumption and mark it as one.
- **Constraints:** team size and skills, budget, deadline, compliance (personal data, payments), existing systems that cannot change.
- **Quality attributes in priority order:** for example correctness, availability, latency, cost, security, simplicity, time to ship. You cannot maximise them all; name the top three.

## 2. Start with the simplest design that meets the numbers
- A single deployable (a "modular monolith") with clear internal modules beats distributed services until you have a reason: independent scaling, independent teams, different runtime needs, or isolation of risk.
- One relational database covers most needs. Add a cache, a queue or a search index only when you can name the query or workload that needs it.
- Prefer managed services over running your own when the team is small.

## 3. Describe the design in layers
1. **Context:** the system as a box, the people and external systems around it.
2. **Containers:** deployable parts (web app, API, worker, database, queue) and how they talk (HTTP, events, SQL).
3. **Components:** the modules inside each container and their responsibilities. Each module owns its data; other modules go through its interface.
4. **Data model:** the main entities, their relationships, who is the source of truth for each, and retention.
5. **Key flows:** walk two or three important requests end to end, including the failure path.

Present it as a short list or a text diagram (Mermaid if the user can render it). Names matter: use the domain's words.

## 4. Test the design against failure and growth
For each boundary ask:
- What happens if this part is **slow**, **down** or returns **wrong data**? (timeouts, retries with backoff and idempotency keys, circuit breakers, graceful degradation)
- What is the **single point of failure**, and is that acceptable?
- What breaks first at **10×** the load? How would you find out (metrics, alerts)?
- How does **data** move safely: transactions where it must be consistent, events where it can be eventually consistent, and a plan for duplicates and ordering.
- Where is **personal or sensitive data** stored and who can read it? Encryption in transit and at rest, access control, audit, deletion.
- How is it **deployed, observed and rolled back**? Logs, metrics, traces, health checks, migrations that can run while old code is live.

## 5. Record decisions
For each significant choice write a short decision record:
- **Decision:** what was chosen.
- **Context:** the forces and constraints.
- **Options considered:** at least two, with honest pros and cons.
- **Consequences:** what becomes easier, what becomes harder, and what would make you revisit it.

## 6. Output
Give: the framing (goal, numbers, constraints, priorities), the design in the layers above, the risks table (risk, likelihood, impact, mitigation), the decision records, and a phased plan (what to build first to learn the most).

## Pitfalls to avoid
- Microservices on day one, or any technology chosen for its novelty.
- Designing for ten million users when there are ten.
- Shared databases between services, which silently couples them.
- Diagrams with no failure paths and no numbers.
- Hiding a trade-off. If a choice has a cost, say it plainly.
