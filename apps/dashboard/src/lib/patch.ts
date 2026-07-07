// Review-patch download helpers (Issue #165).

// Filename encodes provenance (system / session / snapshot / commit) so a
// saved patch can be traced back to what generated it and what it applies to.
export function buildPatchFilename(opts: {
  systemId: number | null | undefined;
  sessionId: number;
  snapshotId: number;
  commitSha?: string | null;
}): string {
  const parts = [
    "probe-agent",
    opts.systemId != null ? `system${opts.systemId}` : null,
    `session${opts.sessionId}`,
    `snapshot${opts.snapshotId}`,
    opts.commitSha ? opts.commitSha.slice(0, 8) : null,
  ].filter(Boolean);
  return `${parts.join("-")}.patch`;
}

export function downloadTextFile(content: string, filename: string, mime = "text/x-patch") {
  const blob = new Blob([content], { type: `${mime};charset=utf-8` });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  // Give the browser a beat to start the download before revoking.
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}
