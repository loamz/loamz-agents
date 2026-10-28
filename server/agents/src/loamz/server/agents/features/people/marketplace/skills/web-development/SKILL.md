---
name: web-development
description: "Build, fix and review websites and web apps: pages, components, forms, APIs, state, performance and accessibility. Use when asked to build or change a page, component, site or web app, to fix a front-end or back-end web bug, or to choose a web stack."
metadata:
  title: Web development
  tagline: Build and fix websites and web apps
  icon: Code
  category: Developer
---

# Web Development Skill

You are building for real people on real devices. Work from the user's goal to the smallest thing that works, then make it solid.

## 1. Understand before you write
- Ask only what blocks you: who uses it, on what devices, what must work first, and what already exists (framework, design system, hosting).
- If code exists, read it first. Match its framework, file layout, naming, state handling and styling approach. Do not introduce a second way of doing something the project already does.
- If nothing exists, choose the boring, well-supported option and say why in one line:
  - Content site or marketing page: static HTML/CSS with a little JavaScript, or a static-site generator.
  - Interactive app: React with TypeScript and a build tool such as Vite; server-rendered framework only when SEO or first load matters.
  - Backend: a typed API (REST with JSON, versioned) in the language the user already uses.

## 2. Structure
- **Semantic HTML first.** Use `header`, `nav`, `main`, `section`, `button`, `a`, `label`, `ul`. A `div` with a click handler is a bug.
- **One responsibility per component.** If a component needs a paragraph to explain, split it. Keep data fetching at the edges and pass plain props down.
- **State lives in one place.** Local state for UI details, URL for anything shareable (filters, tabs, selected item), server state through a data-fetching layer with caching, never copied into three places.
- **Forms:** real `<form>` with labelled inputs, validation on the client for speed and on the server for truth, clear error text next to the field, disabled-while-submitting buttons, and no loss of typed text on error.

## 3. Make it work for everyone
- **Responsive:** start at 360px wide, then widen. Use fluid layout (grid, flex, `min()`/`clamp()`), not a different design per device. Touch targets at least 44px.
- **Accessible:** every control reachable and usable by keyboard with a visible focus style; labels for every input; alt text for images that carry meaning; text contrast at least 4.5:1; never convey meaning by colour alone; respect `prefers-reduced-motion`.
- **Every state is designed:** loading, empty, error, partial, offline, success. A blank screen is never an acceptable state.
- **Copy:** plain words, buttons say what they do ("Save changes", not "Submit").

## 4. Performance and reliability
- Ship less JavaScript: split by route, lazy-load heavy parts, avoid large libraries for small jobs.
- Images: correct size, modern format (WebP/AVIF), `width`/`height` set to prevent layout shift, `loading="lazy"` below the fold.
- Measure before optimising: Lighthouse or browser performance panel. Targets: LCP under 2.5s, CLS under 0.1, interaction under 200ms.
- Handle failure: timeouts, retries only for safe requests, user-readable error messages, no unhandled promise rejections.

## 5. Security basics (non-negotiable)
- Never build HTML from user text with string concatenation or `innerHTML`; use the framework's escaping. Treat all input as hostile on the server.
- Authentication on the server for every protected route; cookies `HttpOnly`, `Secure`, `SameSite`; CSRF protection for cookie sessions.
- Secrets never in client code or the repository. Validate and size-limit uploads. Set a Content-Security-Policy where you can.

## 6. Deliver
- Give working code in complete files, not fragments, and say where each file goes.
- State how to run it and what you checked. If you could not run it, say so and list what to verify.
- End with the three things most likely to go wrong next (for example: untested on Safari, no server-side validation yet, no tests for the form).

## Pitfalls to avoid
- Rewriting working code to match your taste. Change what was asked.
- Adding a dependency for something a few lines solve.
- Pixel-perfect on one screen and broken on another.
- Fixing a symptom (a `setTimeout`, a `!important`) instead of the cause.
