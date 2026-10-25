import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(() => {
  vi.resetModules();
  delete (globalThis as { trustedTypes?: unknown }).trustedTypes;
});

describe("serviceWorkerUrl", () => {
  it("is the plain address where the browser has no Trusted Types", async () => {
    const { serviceWorkerUrl } = await import("./trusted");
    expect(serviceWorkerUrl()).toBe("/chat/sw.js");
  });

  it("goes through a policy that allows only the messenger's worker", async () => {
    const created: string[] = [];
    (globalThis as { trustedTypes?: unknown }).trustedTypes = {
      createPolicy: (name: string, rules: { createScriptURL(url: string): string }) => ({
        createScriptURL: (url: string) => (created.push(name), { trusted: rules.createScriptURL(url) }),
      }),
    };
    const { serviceWorkerUrl } = await import("./trusted");
    expect(serviceWorkerUrl()).toEqual({ trusted: "/chat/sw.js" });
    expect(created).toEqual(["loamz-service-worker"]);
    expect(() => serviceWorkerUrl("/chat/evil.js")).toThrow();
    expect(() => serviceWorkerUrl("https://evil.example/x.js")).toThrow();
  });

  it("falls back when the policy cannot be created (a CSP list that does not name it)", async () => {
    (globalThis as { trustedTypes?: unknown }).trustedTypes = {
      createPolicy: () => {
        throw new TypeError("Policy with name loamz-service-worker disallowed");
      },
    };
    const { serviceWorkerUrl } = await import("./trusted");
    expect(serviceWorkerUrl()).toBe("/chat/sw.js");
  });
});
