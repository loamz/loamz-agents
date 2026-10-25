import { describe, expect, it } from "vitest";
import { resolveMedia } from "./media";

describe("resolveMedia", () => {
  it("serves workspace files through the authenticated endpoint", () => {
    expect(resolveMedia("sandbox:chart.png", "image", "t1")).toContain("/workspace/file?thread_id=t1&path=chart.png");
    expect(resolveMedia("out/chart.png", "image", "t1")).toContain("path=out%2Fchart.png");
    expect(resolveMedia("sandbox:chart.png", "image", null)).toBeNull();
  });

  it("sends web pictures through the picture proxy, never straight to the browser", () => {
    const url = resolveMedia("https://example.com/a.png?x=1", "image", "t1");
    expect(url).toContain("/media/image?url=https%3A%2F%2Fexample.com%2Fa.png%3Fx%3D1");
    expect(url).not.toMatch(/^https:/);
    expect(resolveMedia("https://example.com/v.mp4", "video", "t1")).toBeNull();
  });

  it("allows small raster data images only", () => {
    expect(resolveMedia("data:image/png;base64,AAAA", "image")).toBe("data:image/png;base64,AAAA");
    expect(resolveMedia("data:image/svg+xml;base64,AAAA", "image")).toBeNull();
    expect(resolveMedia("data:text/html;base64,AAAA", "image")).toBeNull();
    expect(resolveMedia("data:image/png;base64," + "A".repeat(2_000_001), "image")).toBeNull();
  });

  it("refuses everything else", () => {
    for (const bad of ["http://example.com/a.png", "javascript:alert(1)", "file:///etc/passwd", "//evil.com/a.png", "ftp://x/y.png", "", "  ", 5, null]) {
      expect(resolveMedia(bad, "image", "t1")).toBeNull();
    }
  });
});
