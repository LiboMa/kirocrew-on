import { useState } from "react";
import { expect, test } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { FormDialog } from "@/components/FormDialog";

test("closing puts the focus back on the button that opened it (BACKLOG 2026-10-24)", async () => {
  function Row() {
    const [open, setOpen] = useState(false);
    return (<><button onClick={() => setOpen(true)}>打开</button>{open && <FormDialog title="归档" onClose={() => setOpen(false)} onSubmit={() => setOpen(false)} />}</>);
  }
  render(<Row />);
  await userEvent.click(screen.getByRole("button", { name: "打开" }));
  await screen.findByRole("dialog", { name: "归档" });
  await userEvent.keyboard("{Escape}");
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  await waitFor(() => expect(screen.getByRole("button", { name: "打开" })).toHaveFocus());
});
