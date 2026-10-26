import { describe, expect, test } from "vitest";
import { render, screen } from "@testing-library/react";
import { StatusBadge } from "@/components/StatusBadge";

/** Owner 2026-10-26: what is done is blue, what runs is green, what has a problem is red (failed) or yellow (waits). */
describe("StatusBadge colours", () => {
  const badge = (status: string) => {
    render(<StatusBadge status={status} label={status} />);
    return screen.getByText(status);
  };

  test.each(["completed", "accepted"])("%s is blue", (status) => {
    expect(badge(status)).toHaveClass("text-done");
  });

  test.each(["running", "dispatching", "preparing"])("%s is green and pulses", (status) => {
    const element = badge(status);
    expect(element).toHaveClass("text-success");
    expect(element.querySelector(".animate-pulse")).not.toBeNull();
  });

  test.each(["failed", "blocked"])("%s is red", (status) => {
    expect(badge(status)).toHaveClass("text-destructive");
  });

  test.each(["waiting_worker", "waiting_approval", "dispatch_unknown"])("%s is yellow", (status) => {
    expect(badge(status)).toHaveClass("text-warning");
  });
});
