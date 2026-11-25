// The promo video from script.json and promo.html (macOS: `say` for the voice, Chrome for the frames, ffmpeg to encode).
//
//   node docs/whitepaper/video/render.mjs                    # KiroCrew-ON-宣传片.mp4 and .srt beside this file
//   node docs/whitepaper/video/render.mjs --stills 3,20,60   # only those seconds, as PNGs in ./stills, for checking a frame
//   node docs/whitepaper/video/render.mjs --no-music         # the voice alone
//
// Each scene lasts as long as its narration needs (at least its `min`); the captions split each narration at its
// punctuation and share the narration's time by length. Frames are rendered one by one at a fixed time each, so the
// same script always gives the same video.
import { execFileSync, spawn } from "node:child_process";
import { once } from "node:events";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, "../../..");
const { chromium } = await import(pathToFileURL(join(root, "web/node_modules/playwright-core/index.mjs")).href);

const args = process.argv.slice(2);
const flag = (name) => args.includes(name);
const value = (name) => { const i = args.indexOf(name); return i >= 0 ? args[i + 1] : undefined; };
const FPS = Number(value("--fps") || 30), W = 1920, H = 1080;
const LEAD = 0.45, TAIL = 0.85, HOLD = 1.6;   // seconds of quiet before and after each narration, and at the very end
const out = join(here, "KiroCrew-ON-宣传片.mp4"), srt = join(here, "KiroCrew-ON-宣传片.srt");

const script = JSON.parse(readFileSync(join(here, "script.json"), "utf8"));
const work = mkdtempSync(join(tmpdir(), "kc-promo-"));
const duration = (file) => Number(execFileSync("ffprobe", ["-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", file]).toString());

// 1. the voice, one clip per scene
const clips = script.scenes.map((scene, i) => {
  const file = join(work, `voice-${i}.aiff`);
  execFileSync("say", ["-v", script.voice, "-r", String(script.rate), "-o", file, scene.say]);
  return { file, seconds: duration(file) };
});

// 2. the timeline: scene starts, their lengths, and the captions
let at = 0;
const scenes = script.scenes.map((scene, i) => {
  const dur = Math.max(scene.min, LEAD + clips[i].seconds + TAIL);
  const entry = { id: scene.id, clock: scene.clock, start: at, dur, voiceAt: at + LEAD, voice: clips[i].seconds };
  at += dur;
  return entry;
});
const total = at + HOLD;
const captions = [];
script.scenes.forEach((scene, i) => {
  // sentences, and a long one again at its commas, so a caption reads as one thought (at most about 26 characters)
  const parts = scene.caption.split(/(?<=[。；？！])/).map((p) => p.trim()).filter(Boolean).flatMap((sentence) => {
    if (sentence.length <= 26) return [sentence];
    const lines = [""];
    for (const piece of sentence.split(/(?<=，)/)) {
      if (lines.at(-1) && lines.at(-1).length + piece.length > 26) lines.push("");
      lines[lines.length - 1] += piece;
    }
    return lines;
  });
  const weight = parts.reduce((n, p) => n + p.length, 0);
  let t = scenes[i].voiceAt;
  parts.forEach((part) => {
    const len = scenes[i].voice * part.length / weight;
    captions.push({ start: t, end: t + len, text: part.replace(/[，。；：]$/, "") });
    t += len;
  });
});
for (let i = 0; i < captions.length - 1; i++) {   // close small gaps so a caption stays up until the next one
  if (captions[i + 1].start - captions[i].end < 0.6) captions[i].end = captions[i + 1].start;
}
const timeline = { scenes, captions, total };
console.log(`时长 ${total.toFixed(1)} 秒，${scenes.length} 个场景，${captions.length} 条字幕`);
scenes.forEach((s) => console.log(`  ${s.id.padEnd(13)} ${s.start.toFixed(1).padStart(6)} 秒起，${s.dur.toFixed(1)} 秒`));

// 3. the frames
const executablePath = process.env.KC_CHROME || [
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
].find(existsSync);
const browser = await chromium.launch(executablePath ? { executablePath } : {});
const page = await browser.newPage({ viewport: { width: W, height: H }, deviceScaleFactor: 1 });
await page.goto(pathToFileURL(join(here, "promo.html")).href);
await page.evaluate(async () => { await document.fonts.ready; await Promise.all([...document.images].map((img) => img.decode().catch(() => {}))); });
await page.evaluate((tl) => window.init(tl), timeline);

const stills = value("--stills");
if (stills) {
  const dir = join(here, "stills");
  mkdirSync(dir, { recursive: true });
  for (const s of stills.split(",").map(Number)) {
    await page.evaluate((t) => window.seek(t), s);
    await page.screenshot({ path: join(dir, `t${String(s).padStart(5, "0")}.png`) });
  }
  console.log(`静帧在 ${dir}`);
  await browser.close();
  rmSync(work, { recursive: true, force: true });
  process.exit(0);
}

// 4. the sound: each clip at its scene's start, over a quiet pad that eases between two chords
const pad = "0.032*((0.5+0.5*cos(2*PI*t/16))*(sin(2*PI*220*t)+0.8*sin(2*PI*277.18*t)+0.7*sin(2*PI*329.63*t)+0.35*sin(2*PI*440*t))"
  + "+(0.5-0.5*cos(2*PI*t/16))*(sin(2*PI*185*t)+0.8*sin(2*PI*220*t)+0.7*sin(2*PI*277.18*t)+0.35*sin(2*PI*369.99*t)))"
  + "*(0.85+0.15*sin(2*PI*0.25*t))";
const inputs = clips.flatMap((c) => ["-i", c.file]);
const delays = scenes.map((s, i) => `[${i}:a]aresample=48000,adelay=${Math.round(s.voiceAt * 1000)}:all=1[v${i}]`);
const voices = `${scenes.map((_, i) => `[v${i}]`).join("")}amix=inputs=${scenes.length}:normalize=0:duration=longest,apad=whole_dur=${total.toFixed(2)}[voice]`;
const music = flag("--no-music") ? "" :
  `;aevalsrc='${pad}':s=48000:d=${total.toFixed(2)},lowpass=f=1400,afade=t=in:d=2.5,afade=t=out:st=${(total - 3).toFixed(2)}:d=3[pad];[voice][pad]amix=inputs=2:normalize=0:duration=first[mix]`;
const last = flag("--no-music") ? "[voice]" : "[mix]";
const audio = join(work, "audio.wav");
execFileSync("ffmpeg", ["-y", "-v", "error", ...inputs, "-filter_complex",
  `${delays.join(";")};${voices}${music};${last}loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000[out]`, "-map", "[out]", "-t", total.toFixed(2), audio]);

// 5. encode: frames piped in as JPEGs, the sound beside them
const frames = Math.ceil(total * FPS);
const ffmpeg = spawn("ffmpeg", ["-y", "-v", "error", "-f", "image2pipe", "-framerate", String(FPS), "-c:v", "mjpeg", "-i", "-", "-i", audio,
  "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p", "-r", String(FPS), "-c:a", "aac", "-b:a", "160k",
  "-shortest", "-movflags", "+faststart", out], { stdio: ["pipe", "inherit", "inherit"] });
const started = Date.now();
for (let f = 0; f < frames; f++) {
  await page.evaluate((t) => window.seek(t), f / FPS);
  const jpeg = await page.screenshot({ type: "jpeg", quality: 92 });
  if (!ffmpeg.stdin.write(jpeg)) await once(ffmpeg.stdin, "drain");
  if (f % (FPS * 10) === 0) console.log(`帧 ${f}/${frames}（${((Date.now() - started) / 1000).toFixed(0)} 秒）`);
}
ffmpeg.stdin.end();
const [code] = await once(ffmpeg, "close");
await browser.close();
rmSync(work, { recursive: true, force: true });
if (code !== 0) throw new Error(`ffmpeg 退出码 ${code}`);

// 6. the captions as SubRip, for players and uploads that show their own
const stamp = (t) => { const ms = Math.round(t * 1000), h = Math.floor(ms / 3600000), m = Math.floor(ms / 60000) % 60, s = Math.floor(ms / 1000) % 60;
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")},${String(ms % 1000).padStart(3, "0")}`; };
writeFileSync(srt, captions.map((c, i) => `${i + 1}\n${stamp(c.start)} --> ${stamp(c.end)}\n${c.text}\n`).join("\n"));
console.log(`完成：${out}（${total.toFixed(1)} 秒）与 ${srt}`);
