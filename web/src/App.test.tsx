import { expect, test } from "vitest";
import { render, screen } from "@testing-library/react";
import { App } from "@/App";

test("renders the console shell", () => {
  render(<App />);
  expect(screen.getByText(/KiroCrew ON/)).toBeInTheDocument();
});
