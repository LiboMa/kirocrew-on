import { useState } from "react";
import { afterEach, describe, expect, test, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { RefreshCw, Trash2 } from "lucide-react";
import { FormDialog } from "@/components/FormDialog";
import { RowActions } from "@/components/ListCard";

const narrow = (matches: boolean) => vi.stubGlobal("matchMedia", (query: string) => ({
  matches: matches && query.includes("max-width"), media: query, addEventListener() {}, removeEventListener() {},
}));

afterEach(() => vi.unstubAllGlobals());

describe("RowActions", () => {
  test("on a wide screen the actions sit in the row; only the rest are in 「⋯」", async () => {
    narrow(false);
    const archive = vi.fn();
    render(<RowActions menu="操作：a" actions={[{ icon: RefreshCw, label: "重新生成", name: "重新生成：a", onSelect: () => {} }]}
      more={[{ icon: Trash2, label: "归档", name: "归档：a", tone: "danger", onSelect: archive }]} />);
    expect(screen.getByRole("button", { name: "重新生成：a" })).toHaveTextContent("重新生成");
    await userEvent.click(screen.getByRole("button", { name: "操作：a" }));
    expect(screen.getAllByRole("menuitem").map((m) => m.textContent)).toEqual(["归档…"]);
    await userEvent.click(screen.getByRole("menuitem", { name: "归档…" }));
    expect(archive).toHaveBeenCalled();
  });

  test("on a phone every action is in 「⋯」 and no buttons sit side by side (PRD §3.5)", async () => {
    narrow(true);
    const regenerate = vi.fn();
    render(<RowActions menu="操作：a" actions={[{ icon: RefreshCw, label: "重新生成", name: "重新生成：a", onSelect: regenerate },
      { icon: Trash2, label: "撤销", name: "撤销：a", tone: "danger", onSelect: () => {} }]} />);
    expect(screen.getAllByRole("button").map((b) => b.getAttribute("aria-label"))).toEqual(["操作：a"]);
    await userEvent.click(screen.getByRole("button", { name: "操作：a" }));
    expect(screen.getAllByRole("menuitem").map((m) => m.textContent)).toEqual(["重新生成…", "撤销…"]);
    await userEvent.click(screen.getByRole("menuitem", { name: "重新生成…" }));
    expect(regenerate).toHaveBeenCalled();
  });

  test("a step that runs at once has no 「…」; one rule sets the dangerous ones apart", async () => {
    narrow(false);
    render(<RowActions menu="操作：a" actions={[]} more={[
      { icon: RefreshCw, label: "刷新模型", name: "刷新模型：a", direct: true, onSelect: () => {} },
      { icon: Trash2, label: "停用", name: "停用：a", tone: "danger", onSelect: () => {} },
      { icon: Trash2, label: "退役", name: "退役：a", tone: "danger", onSelect: () => {} }]} />);
    await userEvent.click(screen.getByRole("button", { name: "操作：a" }));
    expect(screen.getAllByRole("menuitem").map((m) => m.textContent)).toEqual(["刷新模型", "停用…", "退役…"]);
    expect(screen.getAllByRole("separator")).toHaveLength(1);
  });

  test("closing a dialog opened from 「⋯」 puts the focus back on 「⋯」 (BACKLOG 2026-10-24)", async () => {
    narrow(false);
    function Row() {
      const [open, setOpen] = useState(false);
      return (
        <>
          <RowActions menu="操作：a" actions={[]} more={[{ icon: Trash2, label: "归档", name: "归档：a", onSelect: () => setOpen(true) }]} />
          {open && <FormDialog title="归档" onClose={() => setOpen(false)} onSubmit={() => setOpen(false)} />}
        </>
      );
    }
    render(<Row />);
    await userEvent.click(screen.getByRole("button", { name: "操作：a" }));
    await userEvent.click(await screen.findByRole("menuitem", { name: "归档…" }));
    await screen.findByRole("dialog", { name: "归档" });
    await userEvent.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await waitFor(() => expect(screen.getByRole("button", { name: "操作：a" })).toHaveFocus());
  });

  test("no actions, no menu", () => {
    narrow(false);
    const { container } = render(<RowActions menu="操作：a" actions={[]} />);
    expect(container.querySelector("button")).toBeNull();
  });
});
