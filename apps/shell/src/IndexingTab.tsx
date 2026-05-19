import { useCallback, useEffect, useRef, useState } from "react";
import { open } from "@tauri-apps/plugin-dialog";
import { getCurrentWebview } from "@tauri-apps/api/webview";

const BACKEND_URL =
  import.meta.env.VITE_FILER_BACKEND_URL ?? "http://127.0.0.1:8765";

const IS_TAURI =
  typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

type JobStatus =
  | "pending"
  | "running"
  | "success"
  | "failure"
  | "cancelled";

type JobState = {
  job_id: string;
  root_path: string;
  status: JobStatus;
  stage: string | null;
  files_seen: number;
  files_indexed: number;
  files_skipped: number;
  error: string | null;
};

const TERMINAL: ReadonlySet<JobStatus> = new Set([
  "success",
  "failure",
  "cancelled",
]);

export function IndexingTab() {
  const [folder, setFolder] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const [job, setJob] = useState<JobState | null>(null);
  const [startError, setStartError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const esRef = useRef<EventSource | null>(null);

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
            setJob(null);
            setStartError(null);
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

  useEffect(() => {
    return () => {
      esRef.current?.close();
      esRef.current = null;
    };
  }, []);

  const choose = useCallback(async () => {
    if (!IS_TAURI) return;
    const selected = await open({ directory: true, multiple: false });
    if (typeof selected === "string") {
      setFolder(selected);
      setJob(null);
      setStartError(null);
    }
  }, []);

  const subscribe = useCallback((jobId: string) => {
    esRef.current?.close();
    const es = new EventSource(`${BACKEND_URL}/index/jobs/${jobId}/events`);
    esRef.current = es;
    es.addEventListener("progress", (ev) => {
      try {
        const data = JSON.parse((ev as MessageEvent).data) as JobState;
        setJob(data);
        if (TERMINAL.has(data.status)) {
          es.close();
          esRef.current = null;
        }
      } catch {
        // ignore parse errors
      }
    });
    es.onerror = () => {
      // Browser closes the connection on terminal status; nothing to do.
    };
  }, []);

  const start = useCallback(async () => {
    if (!folder) return;
    setStarting(true);
    setStartError(null);
    setJob(null);
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
        setStartError(message);
        return;
      }
      const data = (await res.json()) as JobState;
      setJob(data);
      subscribe(data.job_id);
    } catch (err) {
      setStartError(String(err));
    } finally {
      setStarting(false);
    }
  }, [folder, subscribe]);

  const inProgress = job && !TERMINAL.has(job.status);

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
              {IS_TAURI
                ? "or click to choose"
                : "Folder picker requires the desktop app"}
            </div>
          </>
        )}
      </div>

      <div className="actions">
        <button
          className="primary"
          disabled={!folder || starting || !!inProgress}
          onClick={start}
        >
          {starting || inProgress ? "Indexing…" : "Start indexing"}
        </button>
      </div>

      {startError && <p className="status err">Failed to start: {startError}</p>}

      {job && (
        <div className="job-card">
          <div className="job-header">
            <span className={`job-status ${job.status}`}>{job.status}</span>
            {job.stage && <span className="job-stage">{job.stage}</span>}
            <code className="job-id">{job.job_id.slice(0, 8)}</code>
          </div>
          <div className="job-counters">
            <Counter label="seen" value={job.files_seen} />
            <Counter label="indexed" value={job.files_indexed} />
            <Counter label="skipped" value={job.files_skipped} />
          </div>
          {job.status === "failure" && job.error && (
            <p className="status err">Error: {job.error}</p>
          )}
        </div>
      )}
    </section>
  );
}

function Counter({ label, value }: { label: string; value: number }) {
  return (
    <div className="counter">
      <div className="counter-value">{value.toLocaleString()}</div>
      <div className="counter-label">{label}</div>
    </div>
  );
}
