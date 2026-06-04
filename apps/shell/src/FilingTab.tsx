import { useCallback, useEffect, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";
import { open } from "@tauri-apps/plugin-dialog";
import { getCurrentWebview } from "@tauri-apps/api/webview";

const BACKEND_URL =
  import.meta.env.VITE_FILER_BACKEND_URL ?? "http://127.0.0.1:8765";

const IS_TAURI =
  typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

type FileStatus = "queued" | "processing" | "ready" | "filed";
type FileKind = "pdf" | "image" | "document" | "spreadsheet" | "other";

type UnfiledFile = {
  file_id: string;
  filename: string;
  absolute_path: string;
  size_bytes: number;
  kind: FileKind;
  status: FileStatus;
  added_at: string;
  suggestion_count: number;
};

type Suggestion = {
  suggestion_id: string;
  folder_name: string;
  folder_path: string;
  absolute_path: string;
  confidence: number;
  rationale: string | null;
};

type SuggestionList = {
  file_id: string;
  filename: string;
  suggestions: Suggestion[];
};

type FolderNode = { name: string; path: string; children: FolderNode[] };
type FolderHierarchy = { root_path: string; children: FolderNode[] };

// Every folder prefix along a path, e.g.
// "Documents/Finance/Invoices" -> ["Documents","Documents/Finance","Documents/Finance/Invoices"]
function ancestorPaths(path: string): string[] {
  const parts = path.split("/");
  const out: string[] = [];
  for (let i = 1; i <= parts.length; i++) out.push(parts.slice(0, i).join("/"));
  return out;
}

export function FilingTab() {
  const [files, setFiles] = useState<UnfiledFile[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [suggestions, setSuggestions] = useState<SuggestionList | null>(null);
  const [hierarchy, setHierarchy] = useState<FolderHierarchy | null>(null);
  const [dragging, setDragging] = useState(false);
  const [accepting, setAccepting] = useState<string | null>(null);

  // Library-tree interaction state.
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [revealedPath, setRevealedPath] = useState<string | null>(null);
  const [dropTarget, setDropTarget] = useState<string | null>(null);
  // Pointer-based drag (Tauri's native drag handler blocks HTML5 DnD in the
  // webview, so we track the drag ourselves with pointer events).
  const dragSrcRef = useRef<{ fileId: string; filename: string } | null>(null);
  const dragStartRef = useRef<{ x: number; y: number } | null>(null);
  const didDragRef = useRef(false);
  const [ghost, setGhost] = useState<{
    filename: string;
    x: number;
    y: number;
  } | null>(null);

  const selectedIdRef = useRef<string | null>(null);
  selectedIdRef.current = selectedId;

  const reveal = useCallback((path: string) => {
    setRevealedPath(path);
    setExpanded(new Set(ancestorPaths(path)));
  }, []);

  const loadFiles = useCallback(async () => {
    try {
      const res = await fetch(`${BACKEND_URL}/filing/files/unfiled`);
      if (!res.ok) return;
      const data = (await res.json()) as UnfiledFile[];
      setFiles(data);
      if (selectedIdRef.current == null) {
        const firstReady = data.find((f) => f.status === "ready");
        if (firstReady) setSelectedId(firstReady.file_id);
      }
    } catch {
      // backend unreachable
    }
  }, []);

  const loadHierarchy = useCallback(async () => {
    try {
      const res = await fetch(`${BACKEND_URL}/filing/folder-hierarchy`);
      if (res.ok) setHierarchy((await res.json()) as FolderHierarchy);
    } catch {
      // ignore
    }
  }, []);

  useEffect(() => {
    loadFiles();
    loadHierarchy();
  }, [loadFiles, loadHierarchy]);

  // Fetch suggestions for the selected file; reveal the top suggestion's path.
  useEffect(() => {
    if (selectedId == null) {
      setSuggestions(null);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(
          `${BACKEND_URL}/filing/files/${selectedId}/suggestions`
        );
        if (!res.ok || cancelled) return;
        const data = (await res.json()) as SuggestionList;
        setSuggestions(data);
        if (data.suggestions.length > 0) reveal(data.suggestions[0].folder_path);
        else setRevealedPath(null);
      } catch {
        if (!cancelled) setSuggestions(null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [selectedId, reveal]);

  // OS drag & drop intake (desktop). Ingest endpoint is pending; a drop just
  // refreshes the queue for now.
  useEffect(() => {
    if (!IS_TAURI) return;
    let unlisten: (() => void) | undefined;
    let cancelled = false;
    getCurrentWebview()
      .onDragDropEvent((event) => {
        const p = event.payload;
        if (p.type === "enter" || p.type === "over") setDragging(true);
        else if (p.type === "leave") setDragging(false);
        else if (p.type === "drop") {
          setDragging(false);
          loadFiles();
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
  }, [loadFiles]);

  const choose = useCallback(async () => {
    if (!IS_TAURI) return;
    const selected = await open({ directory: false, multiple: true });
    if (selected) loadFiles();
  }, [loadFiles]);

  // Remove a filed file from the queue and advance selection.
  const dropFromQueue = useCallback((fileId: string) => {
    setFiles((prev) => {
      const next = prev.filter((f) => f.file_id !== fileId);
      const nextReady = next.find((f) => f.status === "ready");
      setSelectedId(nextReady ? nextReady.file_id : null);
      return next;
    });
  }, []);

  const accept = useCallback(
    async (fileId: string, suggestionId: string) => {
      setAccepting(suggestionId);
      try {
        const res = await fetch(
          `${BACKEND_URL}/filing/files/${fileId}/suggestions/${suggestionId}/accept`,
          { method: "POST" }
        );
        if (res.ok) dropFromQueue(fileId);
      } catch {
        // ignore
      } finally {
        setAccepting(null);
      }
    },
    [dropFromQueue]
  );

  // Resolve the folder path under a screen point (null if not over a folder).
  const folderPathAt = (x: number, y: number): string | null => {
    const el = document.elementFromPoint(x, y);
    const row = el?.closest("[data-folder-path]");
    return row ? row.getAttribute("data-folder-path") : null;
  };

  const onDragMove = useCallback((e: PointerEvent) => {
    const start = dragStartRef.current;
    const src = dragSrcRef.current;
    if (!start || !src) return;
    if (!didDragRef.current) {
      if (Math.hypot(e.clientX - start.x, e.clientY - start.y) < 5) return;
      didDragRef.current = true;
    }
    setGhost({ filename: src.filename, x: e.clientX, y: e.clientY });
    setDropTarget(folderPathAt(e.clientX, e.clientY));
  }, []);

  const onDragEnd = useCallback(
    (e: PointerEvent) => {
      window.removeEventListener("pointermove", onDragMove);
      window.removeEventListener("pointerup", onDragEnd);
      window.removeEventListener("pointercancel", onDragEnd);
      // Re-enable text selection.
      document.body.style.removeProperty("user-select");
      document.body.style.removeProperty("-webkit-user-select");
      const src = dragSrcRef.current;
      const wasDrag = didDragRef.current;
      dragSrcRef.current = null;
      dragStartRef.current = null;
      setGhost(null);
      setDropTarget(null);
      if (!wasDrag || !src) return;
      const target = folderPathAt(e.clientX, e.clientY);
      // TODO: call POST /filing/files/{id}/file to actually file it. For now
      // just confirm the drag wiring with an alert showing source → target.
      if (target) window.alert(`File "${src.filename}" → folder "${target}"`);
    },
    [onDragMove]
  );

  const startFileDrag = useCallback(
    (e: ReactPointerEvent, file: UnfiledFile) => {
      if (e.button !== 0) return;
      dragSrcRef.current = { fileId: file.file_id, filename: file.filename };
      dragStartRef.current = { x: e.clientX, y: e.clientY };
      didDragRef.current = false;
      // Suppress page-wide text selection while the pointer is down.
      document.body.style.setProperty("user-select", "none");
      document.body.style.setProperty("-webkit-user-select", "none");
      window.getSelection()?.removeAllRanges();
      window.addEventListener("pointermove", onDragMove);
      window.addEventListener("pointerup", onDragEnd);
      window.addEventListener("pointercancel", onDragEnd);
    },
    [onDragMove, onDragEnd]
  );

  const toggleExpand = useCallback((path: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }, []);

  const pending = files.filter((f) => f.status !== "ready").length;
  const selectedFile = files.find((f) => f.file_id === selectedId) ?? null;

  return (
    <section className="panel filing">
      <h2>Filing</h2>
      <p className="filing-subtitle">
        Drop an unorganized folder or loose files — Filer processes each one and
        suggests where it belongs.
      </p>

      <div
        className={`drop-zone${dragging ? " dragging" : ""}`}
        onClick={choose}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") choose();
        }}
      >
        <div className="drop-headline">Drop files or a folder here</div>
        <div className="hint">
          {IS_TAURI ? "or click to choose" : "Drag & drop requires the desktop app"}
        </div>
      </div>

      {pending > 0 && (
        <div className="filing-status">
          <span className="spinner" />
          <span>
            Processing {pending} of {files.length} files…
          </span>
        </div>
      )}

      <div className="work-area">
        <div className="work-left">
          <div className="pane file-pane">
            <div className="pane-header">
              <span className="pane-title">Files</span>
              <span className="pane-meta">{files.length} in queue</span>
            </div>
            <ul className="pane-body file-list">
              {files.length === 0 && (
                <li className="pane-empty">No files waiting to be filed.</li>
              )}
              {files.map((f) => (
                <li
                  key={f.file_id}
                  className={`file-row${f.file_id === selectedId ? " selected" : ""}`}
                  title="Click to select · drag onto a folder to file here"
                  onPointerDown={(e) => startFileDrag(e, f)}
                  onClick={() => {
                    if (didDragRef.current) {
                      didDragRef.current = false;
                      return;
                    }
                    setSelectedId(f.file_id);
                  }}
                >
                  <FileIcon kind={f.kind} />
                  <span className="file-name">{f.filename}</span>
                  <FileStatusBadge status={f.status} />
                </li>
              ))}
            </ul>
          </div>

          <div className="pane sug-pane">
            <div className="pane-header">
              <span className="pane-title">Suggestions</span>
              <span className="pane-meta">{selectedFile?.filename ?? "—"}</span>
            </div>
            <div className="pane-body sug-list">
              {!selectedFile && (
                <div className="pane-empty">Select a file to see suggestions.</div>
              )}
              {selectedFile && selectedFile.status !== "ready" && (
                <div className="pane-empty">
                  {selectedFile.status === "processing"
                    ? "Processing… suggestions will appear shortly."
                    : "Queued — not processed yet."}
                </div>
              )}
              {selectedFile &&
                selectedFile.status === "ready" &&
                suggestions?.suggestions.length === 0 && (
                  <div className="pane-empty">No suggestions for this file.</div>
                )}
              {selectedFile &&
                selectedFile.status === "ready" &&
                suggestions?.suggestions.map((s, i) => (
                  <div
                    className={`sug-row${
                      s.folder_path === revealedPath ? " active" : ""
                    }`}
                    key={s.suggestion_id}
                    title="Click to locate · drag onto a folder to file here"
                    onPointerDown={(e) => startFileDrag(e, selectedFile)}
                    onClick={() => {
                      if (didDragRef.current) {
                        didDragRef.current = false;
                        return;
                      }
                      reveal(s.folder_path);
                    }}
                  >
                    <FolderIcon />
                    <div className="sug-info">
                      <div className="sug-folder">{s.folder_name}</div>
                      <div className="sug-path">{s.folder_path}</div>
                      <div className="sug-confidence">
                        <span className="conf-track">
                          <span
                            className={`conf-fill ${confLevel(s.confidence)}`}
                            style={{ width: `${Math.round(s.confidence * 100)}%` }}
                          />
                        </span>
                        <span className="conf-pct">
                          {Math.round(s.confidence * 100)}% match
                        </span>
                      </div>
                    </div>
                    <button
                      className={`accept-btn${i === 0 ? "" : " outline"}`}
                      disabled={accepting === s.suggestion_id}
                      onClick={(e) => {
                        e.stopPropagation();
                        accept(selectedFile.file_id, s.suggestion_id);
                      }}
                    >
                      {accepting === s.suggestion_id ? "Filing…" : "Accept"}
                    </button>
                  </div>
                ))}
            </div>
          </div>
        </div>

        <div className="pane library-pane">
          <div className="pane-header">
            <span className="pane-title">Library</span>
            <span className="pane-meta">{tildeify(hierarchy?.root_path)}</span>
          </div>
          <div className="pane-body tree">
            {hierarchy?.children.map((node) => (
              <TreeNode
                key={node.path}
                node={node}
                depth={0}
                expanded={expanded}
                revealedPath={revealedPath}
                dropTarget={dropTarget}
                onToggle={toggleExpand}
              />
            ))}
          </div>
        </div>
      </div>

      {ghost && (
        <div
          className="drag-ghost"
          style={{ left: ghost.x + 14, top: ghost.y + 12 }}
        >
          {ghost.filename}
        </div>
      )}
    </section>
  );
}

function TreeNode({
  node,
  depth,
  expanded,
  revealedPath,
  dropTarget,
  onToggle,
}: {
  node: FolderNode;
  depth: number;
  expanded: Set<string>;
  revealedPath: string | null;
  dropTarget: string | null;
  onToggle: (path: string) => void;
}) {
  const hasChildren = node.children.length > 0;
  const isOpen = expanded.has(node.path);
  const highlighted = node.path === revealedPath;
  const isDropTarget = node.path === dropTarget;

  return (
    <>
      <div
        className={`tree-row${highlighted ? " highlight" : ""}${
          isDropTarget ? " drop-target" : ""
        }${hasChildren ? " has-children" : ""}`}
        style={{ paddingLeft: 12 + depth * 18 }}
        data-folder-path={node.path}
        onClick={() => hasChildren && onToggle(node.path)}
      >
        {hasChildren ? (
          <ChevronIcon open={isOpen} />
        ) : (
          <span className="tree-chevron spacer" />
        )}
        <FolderIcon small />
        <span className="tree-name">{node.name}</span>
        {highlighted && <SparkleIcon />}
      </div>
      {hasChildren &&
        isOpen &&
        node.children.map((child) => (
          <TreeNode
            key={child.path}
            node={child}
            depth={depth + 1}
            expanded={expanded}
            revealedPath={revealedPath}
            dropTarget={dropTarget}
            onToggle={onToggle}
          />
        ))}
    </>
  );
}

function confLevel(c: number): "high" | "med" | "low" {
  if (c >= 0.85) return "high";
  if (c >= 0.7) return "med";
  return "low";
}

function tildeify(path: string | undefined): string {
  if (!path) return "";
  return path.replace(/^\/Users\/[^/]+/, "~");
}

function FileStatusBadge({ status }: { status: FileStatus }) {
  if (status === "processing") {
    return (
      <span className="file-status processing">
        <span className="spinner tiny" />
        processing
      </span>
    );
  }
  return <span className={`file-status ${status}`}>{status}</span>;
}

function FileIcon({ kind }: { kind: FileKind }) {
  if (kind === "image") {
    return (
      <svg className="file-icon" viewBox="0 0 24 24" aria-hidden="true">
        <rect x="3" y="3" width="18" height="18" rx="2" />
        <circle cx="8.5" cy="8.5" r="1.5" />
        <path d="M21 15l-5-5L5 21" />
      </svg>
    );
  }
  if (kind === "spreadsheet") {
    return (
      <svg className="file-icon" viewBox="0 0 24 24" aria-hidden="true">
        <rect x="3" y="3" width="18" height="18" rx="2" />
        <path d="M3 9h18M3 15h18M9 3v18M15 3v18" />
      </svg>
    );
  }
  // pdf / document / other
  return (
    <svg className="file-icon" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
      <path d="M14 2v6h6" />
      <path d="M8 13h8M8 17h8" />
    </svg>
  );
}

function FolderIcon({ small }: { small?: boolean }) {
  return (
    <svg
      className={small ? "folder-icon small" : "folder-icon"}
      viewBox="0 0 24 24"
      aria-hidden="true"
    >
      <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
    </svg>
  );
}

function ChevronIcon({ open }: { open: boolean }) {
  return (
    <svg
      className={`tree-chevron${open ? " open" : ""}`}
      viewBox="0 0 24 24"
      aria-hidden="true"
    >
      <path d="M9 6l6 6-6 6" />
    </svg>
  );
}

function SparkleIcon() {
  return (
    <svg className="sparkle-icon" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z" />
    </svg>
  );
}
