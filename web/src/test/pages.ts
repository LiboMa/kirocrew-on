import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

/** Opens a section of the 管理 or 我的设置 page from the top bar and returns its panel (titled ``title``, by default
 * the section's own name). */
export async function openSection(page: "管理" | "我的设置", section: string, title = section): Promise<HTMLElement> {
  await userEvent.click(await screen.findByRole("button", { name: page }));
  const nav = await screen.findByRole("navigation", { name: page });
  await userEvent.click(within(nav).getByRole("link", { name: section }));
  return panel(title);
}

/** The panel on the open page whose title is ``title``. */
export async function panel(title: string): Promise<HTMLElement> {
  const heading = await screen.findByRole("heading", { level: 2, name: title });
  return heading.closest("section") as HTMLElement;
}

/** The workbench's 「会话」 or 「工作流」 in the top bar. */
export async function workbench(tab: "会话" | "工作流"): Promise<HTMLElement> {
  return within(await screen.findByRole("navigation", { name: "工作台" })).getByRole("link", { name: new RegExp("^" + tab) });
}

/** Back to the Sessions: 「返回工作台」 from 管理 / 我的设置, else the top bar's 「会话」. */
export async function openSessions(): Promise<void> {
  const back = screen.queryByRole("button", { name: "返回工作台" });
  await userEvent.click(back ?? await workbench("会话"));
}
