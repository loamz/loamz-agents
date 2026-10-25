/**
 * Trusted Types policies for the few places this app sets a script address itself. Everything else in our code builds the page
 * through React, which never sets raw HTML. A policy refuses anything but what it was made for, so a hostile string cannot become
 * a script address here even once the browser enforces Trusted Types.
 */

type Factory = { createPolicy(name: string, rules: { createScriptURL(url: string): string }): { createScriptURL(url: string): unknown } };

const WORKER_PATH = "/chat/sw.js";
let workerPolicy: ReturnType<Factory["createPolicy"]> | null | undefined;

/** The service worker's address, as a Trusted Types script URL where the browser has them (and a plain string where it does not). */
export function serviceWorkerUrl(path: string = WORKER_PATH): string {
  if (path !== WORKER_PATH) throw new Error("Only the messenger's own service worker can be registered");
  if (workerPolicy === undefined) {
    const factory = (globalThis as { trustedTypes?: Factory }).trustedTypes;
    try {
      workerPolicy =
        factory?.createPolicy("loamz-service-worker", {
          createScriptURL(url) {
            if (url !== WORKER_PATH) throw new TypeError("Not the messenger's service worker");
            return url;
          },
        }) ?? null;
    } catch {
      workerPolicy = null; // the browser or a CSP `trusted-types` list does not allow this policy name
    }
  }
  return (workerPolicy ? workerPolicy.createScriptURL(path) : path) as string;
}
