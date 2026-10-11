// Browser regression runner: each tests/*.browser.js is a Playwright run-code function. Every file gets a fresh
// ephemeral HTTP/SQLite fixture (tests/mvp_browser_fixture.py on 8921); nothing reaches a native Worker.
// Usage: npm --prefix web run test:browser [-- tests/react-console.browser.js ...]   (build the console first)
import { spawn } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";
import { chromium } from "playwright-core";

const root = path.resolve(import.meta.dirname, "../..");
const DEFAULT = ["react-console", "mvp-intent", "mvp-wizard", "mvp-recommend", "mvp-recovery", "mvp-crew-entry", "mvp-team"]
  .map((name) => `tests/${name}.browser.js`);
const files = process.argv.slice(2).length ? process.argv.slice(2) : DEFAULT;
const python = process.env.PYTHON ?? path.join(root, ".venv/bin/python");

async function startFixture() {
  const child = spawn(python, ["tests/mvp_browser_fixture.py"], { cwd: root, env: { ...process.env, PYTHONPATH: root }, stdio: "ignore" });
  for (let i = 0; i < 100; i++) {
    try {
      if ((await fetch("http://127.0.0.1:8921/health")).ok) return child;
    } catch {
      // not listening yet
    }
    await new Promise((resolve) => setTimeout(resolve, 150));
  }
  child.kill();
  throw new Error("fixture did not start on 8921 (is the port already in use?)");
}

const page = { context: () => ({ browser: () => ({ browserType: () => chromium }) }) };
let failed = 0;
for (const file of files) {
  const fixture = await startFixture();
  const started = Date.now();
  try {
    const test = new Function("return (" + readFileSync(path.join(root, file), "utf8") + ")")();
    const result = await test(page);
    const count = result?.checks?.length ?? result?.passed ?? 0;
    console.log(`✓ ${file}  ${count} checks  ${((Date.now() - started) / 1000).toFixed(1)}s`);
  } catch (error) {
    failed++;
    console.log(`✗ ${file}  ${error.message.split("\n")[0]}`);
  } finally {
    fixture.kill();
    await new Promise((resolve) => fixture.once("exit", resolve));
  }
}
console.log(failed ? `browser: ${failed} failed` : "browser: OK");
process.exit(failed ? 1 : 0);
