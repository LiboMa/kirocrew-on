import { describe, expect, test, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AgentFields } from "@/components/AgentFields";

const models = { claude: ["auto", "default", "us.anthropic.claude-opus-5-5", "us.anthropic.claude-sonnet-5-5"], codex: ["auto"] };

describe("the model a task runs on", () => {
  test("on auto the environment's models are all offered, not filtered by what is typed", () => {
    render(<AgentFields idPrefix="t" models={models} value={{ tool: "claude", model: "auto", effort: "" }} onChange={() => {}} />);
    const select = screen.getByLabelText("模型");
    expect(select).toHaveValue("auto");
    expect(within(select).getAllByRole("option").map((o) => o.getAttribute("value")))
      .toEqual(["auto", "default", "us.anthropic.claude-opus-5-5", "us.anthropic.claude-sonnet-5-5"]);
  });

  test("picking a model writes it", async () => {
    const onChange = vi.fn();
    render(<AgentFields idPrefix="t" models={models} value={{ tool: "claude", model: "auto", effort: "high" }} onChange={onChange} />);
    await userEvent.selectOptions(screen.getByLabelText("模型"), "us.anthropic.claude-opus-5-5");
    expect(onChange).toHaveBeenCalledWith({ tool: "claude", model: "us.anthropic.claude-opus-5-5", effort: "high" });
  });

  test("a model this environment has not published is kept and marked", () => {
    render(<AgentFields idPrefix="t" models={models} value={{ tool: "claude", model: "claude-opus-4.8", effort: "" }} onChange={() => {}} />);
    const select = screen.getByLabelText("模型");
    expect(select).toHaveValue("claude-opus-4.8");
    expect(within(select).getByRole("option", { name: /claude-opus-4\.8.*未公布/ })).toBeInTheDocument();
  });

  test("auto says which model it runs where the tool publishes none (D51)", () => {
    render(<AgentFields idPrefix="t" models={models} defaults={{ codex: "openai.gpt-6-astra" }}
      value={{ tool: "codex", model: "auto", effort: "" }} onChange={() => {}} />);
    expect(screen.getByRole("option", { name: "自动（openai.gpt-6-astra）" })).toBeInTheDocument();
  });

  test("with only auto it says why and what to do", () => {
    render(<AgentFields idPrefix="t" models={models} value={{ tool: "codex", model: "auto", effort: "" }} onChange={() => {}} />);
    expect(screen.getByText(/尚未公布 Codex 的模型/)).toBeInTheDocument();
  });
});
