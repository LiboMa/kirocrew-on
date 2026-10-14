export function downloadJson(filename: string, value: unknown) {
  downloadText(filename, JSON.stringify(value, null, 2), "application/json");
}

/** Save text as a file. The object URL is revoked a moment later: Safari starts the download after click() returns. */
export function downloadText(filename: string, text: string, kind: string) {
  const url = URL.createObjectURL(new Blob([text], { type: kind }));
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** Follow a download link (the server answers with Content-Disposition: attachment, so the page stays). */
export function followDownload(url: string) {
  const link = document.createElement("a");
  link.href = url;
  link.rel = "noopener";
  document.body.appendChild(link);
  link.click();
  link.remove();
}
