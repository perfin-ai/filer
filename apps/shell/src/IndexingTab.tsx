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

type HistoryEntry = {
  root_path: string;
  last_indexed_at: string | null;
  last_status: JobStatus;
  last_job_id: string;
  file_count: number;
};

const TERMINAL: ReadonlySet<JobStatus> = new Set([
  "success",
  "failure",
  "cancelled",
]);

function formatDateTime(iso: string | null): string {
  if (!iso) return "never";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "unknown";
  const date = d.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
  const time = d.toLocaleTimeString(undefined, {
    hour: "numeric",
    minute: "2-digit",
  });
  return `${date} ${time}`;
}

export function IndexingTab() {
  const [folder, setFolder] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const [job, setJob] = useState<JobState | null>(null);
  const [startError, setStartError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const [busyPath, setBusyPath] = useState<string | null>(null);
  const esRef = useRef<EventSource | null>(null);

  const loadHistory = useCallback(async () => {
    try {
      const res = await fetch(`${BACKEND_URL}/index/history`);
      if (res.ok) setHistory((await res.json()) as HistoryEntry[]);
    } catch {
      // backend unreachable; leave history as-is
    }
  }, []);

  useEffect(() => {
    loadHistory();
  }, [loadHistory]);

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

  const subscribe = useCallback(
    (jobId: string) => {
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
            loadHistory();
          }
        } catch {
          // ignore parse errors
        }
      });
      es.onerror = () => {
        // Browser closes the connection on terminal status; nothing to do.
      };
    },
    [loadHistory]
  );

  const startForPath = useCallback(
    async (rootPath: string) => {
      setStarting(true);
      setStartError(null);
      setJob(null);
      try {
        const res = await fetch(`${BACKEND_URL}/index/start`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ root_path: rootPath }),
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
    },
    [subscribe]
  );

  const start = useCallback(() => {
    if (folder) startForPath(folder);
  }, [folder, startForPath]);

  const inProgress = job && !TERMINAL.has(job.status);

  const reindex = useCallback(
    (rootPath: string) => {
      if (inProgress || starting) return;
      startForPath(rootPath);
    },
    [inProgress, starting, startForPath]
  );

  const remove = useCallback(
    async (rootPath: string) => {
      if (!window.confirm(`Remove the index for\n${rootPath}?`)) return;
      setBusyPath(rootPath);
      try {
        const res = await fetch(
          `${BACKEND_URL}/index/history?root_path=${encodeURIComponent(rootPath)}`,
          { method: "DELETE" }
        );
        if (res.ok) await loadHistory();
      } catch {
        // ignore; row stays
      } finally {
        setBusyPath(null);
      }
    },
    [loadHistory]
  );

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
        {(starting || inProgress) && (
          <span className="spinner" role="status" aria-label="Indexing in progress" />
        )}
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

      <div className="history">
        <h3>Indexing history</h3>
        {history.length === 0 ? (
          <p className="history-empty">No folders indexed yet.</p>
        ) : (
          <ul className="history-scroll">
            {history.map((entry) => {
              const rowBusy = busyPath === entry.root_path;
              const verb =
                entry.last_status === "success" ? "Indexed" : entry.last_status;
              return (
                <li className="history-row" key={entry.root_path}>
                  <div className="history-info">
                    <div className="history-folder">{entry.root_path}</div>
                    <div className="history-meta">
                      {verb} {formatDateTime(entry.last_indexed_at)} ·{" "}
                      {entry.file_count.toLocaleString()} files
                    </div>
                  </div>
                  <div className="history-actions">
                    <button
                      className="icon-btn"
                      title="Re-index this folder"
                      aria-label="Re-index"
                      disabled={!!inProgress || starting || rowBusy}
                      onClick={() => reindex(entry.root_path)}
                    >
                      <ReindexIcon />
                    </button>
                    <button
                      className="icon-btn danger"
                      title="Remove this index"
                      aria-label="Remove index"
                      disabled={rowBusy}
                      onClick={() => remove(entry.root_path)}
                    >
                      <TrashIcon />
                    </button>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </div>
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

function ReindexIcon() {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M3 12a9 9 0 0 1 9-9 9 9 0 0 1 6.7 3L21 8" />
      <path d="M21 3v5h-5" />
      <path d="M21 12a9 9 0 0 1-9 9 9 9 0 0 1-6.7-3L3 16" />
      <path d="M8 16H3v5" />
    </svg>
  );
}

function TrashIcon() {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M3 6h18" />
      <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6" />
      <path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
      <line x1="10" y1="11" x2="10" y2="17" />
      <line x1="14" y1="11" x2="14" y2="17" />
    </svg>
  );
}
