import { describe, test, expect } from "vitest";
import { formatBytes, formatDuration, formatSpeed, truncate } from "@/lib/utils";

describe("formatBytes", () => {
  test("formats zero", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(null)).toBe("0 B");
    expect(formatBytes(undefined)).toBe("0 B");
  });

  test("formats bytes", () => {
    expect(formatBytes(500)).toBe("500 B");
  });

  test("formats kilobytes", () => {
    expect(formatBytes(1024)).toBe("1 KB");
  });

  test("formats megabytes", () => {
    expect(formatBytes(1048576)).toMatch(/^1(\.00)? MB$/);
  });

  test("formats gigabytes", () => {
    expect(formatBytes(1073741824)).toMatch(/^1(\.00)? GB$/);
  });
});

describe("formatDuration", () => {
  test("handles null", () => {
    expect(formatDuration(null)).toBe("—");
    expect(formatDuration(undefined)).toBe("—");
  });

  test("formats seconds", () => {
    expect(formatDuration(30)).toBe("30s");
  });

  test("formats minutes", () => {
    expect(formatDuration(90)).toBe("1m 30s");
  });

  test("formats hours", () => {
    expect(formatDuration(3661)).toBe("1h 1m");
  });
});

describe("formatSpeed", () => {
  test("handles null", () => {
    expect(formatSpeed(null)).toBe("—");
    expect(formatSpeed(0)).toBe("—");
  });

  test("formats speed", () => {
    const result = formatSpeed(1048576);
    expect(result).toContain("MB/s");
  });
});

describe("truncate", () => {
  test("short strings unchanged", () => {
    expect(truncate("hello")).toBe("hello");
  });

  test("long strings truncated", () => {
    const long = "a".repeat(100);
    const result = truncate(long, 20);
    expect(result.length).toBe(20);
    expect(result.endsWith("...")).toBe(true);
  });
});
