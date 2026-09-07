import { describe, expect, it } from "vitest";
import {
  formatGameTime,
  formatSlateDate,
  localDateOf,
  localToday,
  relativeDayLabel,
} from "./time";

// 2026-09-04T23:10:00Z === 7:10 PM EDT === 4:10 PM PDT (daylight saving active)
const SUMMER_FIRST_PITCH = "2026-09-04T23:10:00Z";
// 2026-11-10T00:10:00Z === 7:10 PM EST === 4:10 PM PST (standard time)
const WINTER_FIRST_PITCH = "2026-11-10T00:10:00Z";

describe("local game time display", () => {
  it("renders Eastern Time with the zone abbreviation", () => {
    expect(formatGameTime(SUMMER_FIRST_PITCH, "America/New_York")).toBe("7:10 PM EDT");
  });

  it("renders Pacific Time for the same absolute instant", () => {
    expect(formatGameTime(SUMMER_FIRST_PITCH, "America/Los_Angeles")).toBe("4:10 PM PDT");
  });

  it("handles daylight saving automatically (no fixed offset)", () => {
    expect(formatGameTime(WINTER_FIRST_PITCH, "America/New_York")).toBe("7:10 PM EST");
    expect(formatGameTime(WINTER_FIRST_PITCH, "America/Los_Angeles")).toBe("4:10 PM PST");
  });

  it("returns a placeholder for a missing timestamp", () => {
    expect(formatGameTime(null)).toBe("—");
  });
});

describe("UTC midnight crossing", () => {
  // 00:30 UTC on Sept 4 is still 8:30 PM on Sept 3 in Eastern Time.
  const AFTER_UTC_MIDNIGHT = "2026-09-04T00:30:00Z";

  it("keeps an Eastern user on the previous local calendar day", () => {
    expect(localDateOf(AFTER_UTC_MIDNIGHT, "America/New_York")).toBe("2026-09-03");
    expect(localDateOf(AFTER_UTC_MIDNIGHT, "UTC")).toBe("2026-09-04");
  });

  it("labels the Sept 3 slate as Today for that user", () => {
    const now = new Date(AFTER_UTC_MIDNIGHT);
    expect(relativeDayLabel("2026-09-03", now, "America/New_York")).toBe("Today");
    expect(relativeDayLabel("2026-09-04", now, "America/New_York")).toBe("Tomorrow");
  });

  it("labels correctly for a Pacific user too", () => {
    const now = new Date(AFTER_UTC_MIDNIGHT);
    expect(localToday(now, "America/Los_Angeles")).toBe("2026-09-03");
    expect(relativeDayLabel("2026-09-03", now, "America/Los_Angeles")).toBe("Today");
  });

  it("labels a Tokyo user's already-next local day correctly", () => {
    const now = new Date(AFTER_UTC_MIDNIGHT);
    expect(localToday(now, "Asia/Tokyo")).toBe("2026-09-04");
    expect(relativeDayLabel("2026-09-04", now, "Asia/Tokyo")).toBe("Today");
  });
});

describe("slate date label", () => {
  it("never shifts the calendar date by timezone", () => {
    expect(formatSlateDate("2026-09-03")).toBe("Thursday, Sep 3");
  });
});
