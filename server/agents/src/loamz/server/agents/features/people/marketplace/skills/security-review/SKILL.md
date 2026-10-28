---
name: security-review
description: "Review code, designs and configurations for security problems and explain the fixes: threat modelling, common vulnerabilities, secrets, authentication and data protection. Use when asked to security-review something, threat-model a feature, check for vulnerabilities, or prepare for an audit."
metadata:
  title: Security review
  tagline: Find weaknesses before someone else does
  icon: ShieldCheck
  category: Developer
---

# Security Review Skill

Think like a careful attacker, report like a helpful colleague. Find what matters, explain why, and show the fix. Only analyse systems the user owns or is authorised to test, and never produce working attack tooling against third parties.

## 1. Scope and threat model (5 minutes, not 5 pages)
- **What are we protecting?** The valuable data and actions (accounts, money, personal data, admin powers).
- **Who might attack?** An anonymous visitor, a logged-in user attacking another user, a malicious insider, a compromised dependency or service.
- **Where does untrusted input enter?** Forms, URLs, headers, files, webhooks, message queues, AI prompts and tool results.
- **Where are the trust boundaries?** Browser to server, service to service, server to database, tenant to tenant.

List the top threats as "an attacker who can ___ could ___". Review in that order.

## 2. Check the usual suspects
**Access control** (the most common serious failure)
- Every request is authorised on the server, for that object: can *this* user read or change *this* record? Look for IDs in URLs that are not checked against the owner (IDOR), missing tenant filters, admin endpoints with no role check.

**Injection**
- SQL built with string joins; shell commands built from input; template or HTML output without escaping (XSS); unsafe deserialisation; path traversal in file names; server-side requests to user-supplied URLs (SSRF: block internal and metadata addresses).

**Authentication and sessions**
- Password hashing with a modern algorithm (Argon2id, bcrypt, scrypt); rate limits and lockout on login and reset; multi-factor for sensitive roles; session cookies `HttpOnly`, `Secure`, `SameSite`; tokens with short life, audience and signature checked; logout and revocation that work.

**Secrets and configuration**
- Keys, tokens and passwords in code, logs, error messages, client bundles or the repository history. Debug mode and verbose errors in production. Default credentials. Overly broad cloud permissions and public storage buckets.

**Data protection**
- Encryption in transit everywhere; sensitive data encrypted at rest; minimal collection and retention; personal data in logs; backups protected; a working way to delete a person's data.

**Dependencies and supply chain**
- Known-vulnerable packages, unpinned versions, install scripts, unreviewed GitHub Actions or build steps with secrets.

**AI features**
- Prompt injection through documents, web pages and tool results; tools that act without confirmation; secrets or other users' data reachable from the prompt; output rendered as HTML.

## 3. Report findings
For each finding, in order of risk:
- **Title and severity** (Critical, High, Medium, Low) with a one-line reason (impact × how easy).
- **Where:** file and line, or endpoint, or setting.
- **What can happen:** the realistic abuse, in plain words.
- **Fix:** the specific change, with corrected code or configuration.
- **How to verify:** a test or check that proves it is fixed.

Group related items. Do not pad the report with theoretical issues; if something is only a hardening suggestion, label it that way.

## 4. Close
- A short summary: what is solid, what is urgent, what to do this week.
- Mention what you could not check (no access to production, no runtime test) so the user knows the limits.
- Offer to turn the fixes into tasks and add tests that stop the same problem returning.
