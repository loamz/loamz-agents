import { requestJson, requestVoid } from "./api/_client";

const DEVICE = "loamz-push-device";

export async function enableNotifications(): Promise<void> {
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) throw new Error("This browser does not support push notifications.");
  if (await Notification.requestPermission() !== "granted") throw new Error("Allow notifications in your browser to receive message alerts.");
  const { public_key } = await requestJson<{ public_key: string }>("/push/key");
  await navigator.serviceWorker.register("/chat/sw.js", { scope: "/chat" });
  const registration = await navigator.serviceWorker.ready;
  const binary = atob(public_key.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - public_key.length % 4) % 4));
  const subscription = await registration.pushManager.getSubscription() ?? await registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: Uint8Array.from(binary, (c) => c.charCodeAt(0)) });
  let device = localStorage.getItem(DEVICE);
  if (!device) { device = crypto.randomUUID(); localStorage.setItem(DEVICE, device); }
  const value = subscription.toJSON();
  await requestVoid("/push/subscriptions", { method: "PUT", body: JSON.stringify({ device, endpoint: value.endpoint, p256dh: value.keys?.p256dh, auth: value.keys?.auth }) });
}

/** Delete the server capability while the old session still authenticates this browser. */
export async function disableNotifications(): Promise<void> {
  const device = localStorage.getItem(DEVICE);
  if (device) await requestVoid(`/push/subscriptions/${encodeURIComponent(device)}`, { method: "DELETE" });
  const registration = await navigator.serviceWorker?.getRegistration("/chat/");
  await (await registration?.pushManager.getSubscription())?.unsubscribe();
  localStorage.removeItem(DEVICE);
}
