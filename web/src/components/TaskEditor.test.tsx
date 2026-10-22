// D69: a task's phase (发散 / 设计 / 收敛) can be chosen in the editor; 「自动」 leaves it to the task's kind.
import { describe, expect, test, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { Step } from "@/api/types";
import { TaskEditor } from "@/components/TaskEditor";

const step: Step = { id: "task-1", name: "调研", tool: "kiro", model: "auto", effort: "", prompt: "调研", kind: "research" };

describe("task phase", () => {
  test("choosing a phase writes it, 「自动」 removes it", async () => {
    const onChange = vi.fn();
    const { rerender } = render(<TaskEditor tasks={[step]} onChange={onChange} idPrefix="t" title="任务" />);
    await userEvent.selectOptions(screen.getByLabelText("阶段"), "diverge");
    expect(onChange).toHaveBeenLastCalledWith([{ ...step, phase: "diverge" }]);
    rerender(<TaskEditor tasks={[{ ...step, phase: "diverge" }]} onChange={onChange} idPrefix="t" title="任务" />);
    expect(screen.getByText(/发散/, { selector: "small" })).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("阶段"), "");
    expect(onChange).toHaveBeenLastCalledWith([step]);
  });
});
