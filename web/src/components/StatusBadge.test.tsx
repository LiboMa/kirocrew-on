import { describe, expect, test } from "vitest";
import { render, screen } from "@testing-library/react";
import { StatusBadge } from "@/components/StatusBadge";

/** Owner 2026-10-27 (Session and task statuses): done is green, in progress is blue; a problem is red (failed) or yellow (waits). */
describe("StatusBadge colours", () => {
  const badge = (status: string) => {
    render(<StatusBadge status={status} label={status} />);
    return screen.getByText(status);
  };

  test.each(["completed", "accepted"])("%s is green", (status) => {
    expect(badge(status)).toHaveClass("text-success");
  });

  test.each(["running", "dispatching", "preparing"])("%s is blue and pulses", (status) => {
    const element = badge(status);
    expect(element).toHaveClass("text-info");
    expect(element.querySelector(".animate-pulse")).not.toBeNull();
  });

  test.each(["failed", "blocked"])("%s is red", (status) => {
    expect(badge(status)).toHaveClass("text-destructive");
  });

  test.each(["waiting_worker", "waiting_approval", "dispatch_unknown"])("%s is yellow", (status) => {
    expect(badge(status)).toHaveClass("text-warning");
  });
});
