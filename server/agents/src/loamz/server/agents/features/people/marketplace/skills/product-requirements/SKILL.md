---
name: product-requirements
description: "Turn an idea into a clear product requirements document: problem, users, goals and non-goals, user stories with acceptance criteria, scope, risks and success measures. Use when asked to write a spec, PRD, feature brief or user stories, or to decide what to build first."
metadata:
  title: Product requirements
  tagline: Turn an idea into a spec people can build from
  icon: ClipboardList
  category: Productivity
---

# Product Requirements Skill

A good spec makes the right thing easy to build and the wrong thing easy to spot. It is short, specific and testable.

## 1. Find the real problem first
Ask, in this order, and stop when you have enough:
1. **Who has the problem?** A specific kind of person in a specific situation, not "users".
2. **What are they doing today instead?** The current workaround is the competitor.
3. **How bad is it?** Evidence: how often, how long it takes, what it costs, what people say. Mark anything you are assuming.
4. **What would success look like for them?** In their words.

If the request is a solution ("add a dashboard"), restate it as a problem ("managers cannot see which jobs are late") and confirm.

## 2. Write the document
Use these sections, each as short as the idea allows:

1. **Summary** (3 sentences): the problem, who it is for, the proposed solution.
2. **Background and evidence:** what you know, with sources; what you are assuming.
3. **Goals:** 2 to 4 outcomes, each measurable.
4. **Non-goals:** what this will deliberately not do. This section prevents most scope creep.
5. **Users and scenarios:** one short story per main user type: "When ..., I want ..., so that ...".
6. **Requirements:** a numbered list, each one testable.
   - **Must have** (the thing does not work without it), **Should have**, **Could have**, **Won't have (now)**.
   - Each requirement gets **acceptance criteria** in plain "Given / When / Then" form, including at least one failure or edge case (empty, too long, no permission, offline).
7. **Experience notes:** key screens or steps, wording that matters, accessibility needs. Link or describe sketches; do not design pixels here.
8. **Data and integrations:** what is stored, what personal data is involved and why, what other systems are touched.
9. **Success measures:** the metric, where it comes from, today's value if known, the target and the date to check.
10. **Risks and open questions:** each with an owner and what would resolve it.
11. **Rollout:** who gets it first, how to switch it off, what to tell people.

## 3. Prioritise honestly
- Order by (value to the user × confidence) ÷ effort. If effort is unknown, ask an engineer or mark it.
- Identify the **smallest release that tests the main assumption** and put only that in the first milestone.
- Say what is being cut and why.

## 4. Quality checks before handing over
- Could someone who was not in the conversation build and test this? If a requirement needs "obviously", rewrite it.
- Is every requirement traceable to a goal, and every goal to the problem?
- Are there words that hide decisions ("fast", "simple", "intuitive", "etc.")? Replace them with numbers or examples.
- Does it mention the failure paths, permissions and data deletion?

## Output
The document in the structure above, followed by a short list of the five questions that would most change it if answered differently.
