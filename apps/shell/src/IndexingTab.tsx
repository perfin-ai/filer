import { useCallback, useEffect, useState } from "react";
import { open } from "@tauri-apps/plugin-dialog";
import { getCurrentWebview } from "@tauri-apps/api/webview";

const BACKEND_URL =
  import.meta.env.VITE_FILER_BACKEND_URL ?? "http://127.0.0.1:8765";

const IS_TAURI =
  typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

type StartResult =
  | { kind: "idle" }
  | { kind: "starting" }
  | { kind: "started"; jobId: string; rootPath: string }
  | { kind: "error"; message: string };

export function IndexingTab() {
  const [folder, setFolder] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const [result, setResult] = useState<StartResult>({ kind: "idle" });

  useEffect(() => {
    if (!IS_TAURI) return;
    let unlisten: (() => void) | undefined;
    let cancelled = false;
    getCurrentWebview()
      .onDragDropEvent((event) => {
        const p = event.payload;
        if (p.type === "enter" || p.type === "over") {
          setDragging(true);
        } else if (p.type === "leave") {
          setDragging(false);
        } else if (p.type === "drop") {
          setDragging(false);
          const first = p.paths?.[0];
          if (first) {
            setFolder(first);
            setResult({ kind: "idle" });
          }
        }
      })
      .then((un) => {
        if (cancelled) un();
        else unlisten = un;
      });
    return () => {
      cancelled = true;
      unlisten?.();
    };
  }, []);

  const choose = useCallback(async () => {
    if (!IS_TAURI) return;
    const selected = await open({ directory: true, multiple: false });
    if (typeof selected === "string") {
      setFolder(selected);
      setResult({ kind: "idle" });
    }
  }, []);

  const start = useCallback(async () => {
    if (!folder) return;
    setResult({ kind: "starting" });
    try {
      const res = await fetch(`${BACKEND_URL}/index/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ root_path: folder }),
      });
      if (!res.ok) {
        let message = `HTTP ${res.status}`;
        try {
          const body = await res.json();
          if (body?.detail) message = body.detail;
        } catch {
          /* ignore */
        }
        setResult({ kind: "error", message });
        return;
      }
      const data = await res.json();
      setResult({
        kind: "started",
        jobId: data.job_id,
        rootPath: data.root_path,
      });
    } catch (err) {
      setResult({ kind: "error", message: String(err) });
    }
  }, [folder]);

  return (
    <section className="panel indexing">
      <h2>Indexing</h2>
      <div
        className={`drop-zone${dragging ? " dragging" : ""}${folder ? " has-folder" : ""}`}
        onClick={choose}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") choose();
        }}
      >
        {folder ? (
          <>
            <div className="folder-path">{folder}</div>
            <div className="hint">Click to choose a different folder</div>
          </>
        ) : (
          <>
            <div className="drop-headline">Drop a folder here</div>
            <div className="hint">
              {IS_TAURI ? "or click to choose" : "Folder picker requires the desktop app"}
            </div>
          </>
        )}
      </div>

      <div className="actions">
        <button
          className="primary"
          disabled={!folder || result.kind === "starting"}
          onClick={start}
        >
          {result.kind === "starting" ? "Starting…" : "Start indexing"}
        </button>
      </div>

      {result.kind === "started" && (
        <p className="status ok">
          Started job <code>{result.jobId}</code> for <code>{result.rootPath}</code>
        </p>
      )}
      {result.kind === "error" && (
        <p className="status err">Failed to start: {result.message}</p>
      )}
    </section>
  );
}
