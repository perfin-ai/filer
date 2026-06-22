import { useCallback, useEffect, useRef, useState } from "react";
import type {
  MouseEvent as ReactMouseEvent,
  PointerEvent as ReactPointerEvent,
} from "react";
import { open } from "@tauri-apps/plugin-dialog";
import { openPath } from "@tauri-apps/plugin-opener";
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
  modified_at: string | null;
  suggestion_count: number;
};

type SortKey = "name" | "modified" | "kind";
const SORT_LABELS: Record<SortKey, string> = {
  name: "Name",
  modified: "Modified",
  kind: "Type",
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

type FilePreview = {
  file_id: string;
  filename: string;
  kind: FileKind;
  extension: string | null;
  size_bytes: number;
  modified_at: string | null;
  status: FileStatus;
  parser_used: string | null;
  text: string;
  truncated: boolean;
};

const HOVER_DELAY_MS = 350;
const HOVER_CHARS = 600;
const MODAL_CHARS = 20000;

type FsEntry = {
  name: string;
  path: string;
  is_dir: boolean;
  kind?: FileKind;
};

// The Library tree is rooted here and read lazily from the real filesystem.
const LIBRARY_ROOT = "/Volumes";

type BatchProgress = {
  batch_id: string;
  status: string;
  files_total: number;
  files_processed: number;
};

const BATCH_TERMINAL: ReadonlySet<string> = new Set(["success", "failure"]);

export function FilingTab() {
  const [files, setFiles] = useState<UnfiledFile[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [suggestions, setSuggestions] = useState<SuggestionList | null>(null);
  const [childrenByPath, setChildrenByPath] = useState<
    Record<string, FsEntry[]>
  >({});
  const childrenRef = useRef<Record<string, FsEntry[]>>({});
  const [dragging, setDragging] = useState(false);
  const [accepting, setAccepting] = useState<string | null>(null);
  const [sortKey, setSortKey] = useState<SortKey>("name");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");
  const [batch, setBatch] = useState<BatchProgress | null>(null);
  const batchEsRef = useRef<EventSource | null>(null);
  const [flash, setFlash] = useState<{ kind: "ok" | "err"; text: string } | null>(
    null
  );
  const flashTimer = useRef<number | null>(null);

  // Content-preview state: a lightweight hover popover and a heavy double-click modal.
  const previewCache = useRef<Map<string, FilePreview>>(new Map());
  const [hoverPreview, setHoverPreview] = useState<{
    fileId: string;
    text: string;
    truncated: boolean;
    top: number;
    left: number;
  } | null>(null);
  const hoverTimer = useRef<number | null>(null);
  const hoverReq = useRef<string | null>(null);
  const modalReq = useRef<string | null>(null);
  const [modalFileId, setModalFileId] = useState<string | null>(null);
  const [modalData, setModalData] = useState<FilePreview | null>(null);
  const [modalLoading, setModalLoading] = useState(false);

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
  const treeRef = useRef<HTMLDivElement | null>(null);

  // Lazily fetch a folder's immediate children (memoized via childrenRef).
  const loadChildren = useCallback(
    async (path: string): Promise<FsEntry[]> => {
      if (childrenRef.current[path]) return childrenRef.current[path];
      try {
        const res = await fetch(
          `${BACKEND_URL}/filing/entries?path=${encodeURIComponent(path)}`
        );
        if (!res.ok) return [];
        const data = (await res.json()) as FsEntry[];
        childrenRef.current = { ...childrenRef.current, [path]: data };
        setChildrenByPath(childrenRef.current);
        return data;
      } catch {
        return [];
      }
    },
    []
  );

  // Open the tree down to `target`, collapsing every branch not on its path.
  const revealPath = useCallback(
    async (target: string) => {
      const segs = target.split("/").filter(Boolean); // ["Volumes","home",…]
      const paths: string[] = [];
      let cur = "";
      for (const s of segs) {
        cur += "/" + s;
        paths.push(cur);
      }
      // Folders to expand: from the first level under the root down to the
      // target's parent (root children are always shown; target is the leaf).
      const toExpand = paths.slice(1, paths.length - 1);
      await loadChildren(LIBRARY_ROOT);
      for (const p of toExpand) await loadChildren(p);
      setExpanded(new Set(toExpand));
      setRevealedPath(target);
    },
    [loadChildren]
  );

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

  // Subscribe to a drop batch's progress; refresh the queue as files turn ready.
  const subscribeBatch = useCallback(
    (batchId: string) => {
      batchEsRef.current?.close();
      const es = new EventSource(`${BACKEND_URL}/filing/jobs/${batchId}/events`);
      batchEsRef.current = es;
      es.addEventListener("progress", (ev) => {
        try {
          const data = JSON.parse((ev as MessageEvent).data) as BatchProgress;
          setBatch(data);
          loadFiles();
          if (BATCH_TERMINAL.has(data.status)) {
            es.close();
            batchEsRef.current = null;
          }
        } catch {
          // ignore parse errors
        }
      });
      es.onerror = () => {
        // Browser closes on terminal status; nothing to do.
      };
    },
    [loadFiles]
  );

  // Send dropped/chosen paths to the backend for processing.
  const startIngest = useCallback(
    async (paths: string[]) => {
      if (paths.length === 0) return;
      try {
        const res = await fetch(`${BACKEND_URL}/filing/ingest`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ paths }),
        });
        if (!res.ok) return;
        const data = (await res.json()) as BatchProgress;
        setBatch(data);
        subscribeBatch(data.batch_id);
      } catch {
        // backend unreachable
      }
    },
    [subscribeBatch]
  );

  useEffect(() => {
    loadFiles();
    loadChildren(LIBRARY_ROOT);
  }, [loadFiles, loadChildren]);

  useEffect(() => {
    return () => {
      batchEsRef.current?.close();
      batchEsRef.current = null;
    };
  }, []);

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
        if (data.suggestions.length > 0) revealPath(data.suggestions[0].folder_path);
        else setRevealedPath(null);
      } catch {
        if (!cancelled) setSuggestions(null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [selectedId, revealPath]);

  // Scroll the Library pane so the highlighted folder is centred. Runs after
  // the revealed path's folders have loaded/rendered (childrenByPath dep).
  useEffect(() => {
    if (!revealedPath) return;
    const container = treeRef.current;
    if (!container) return;
    const rows = Array.from(
      container.querySelectorAll<HTMLElement>("[data-folder-path]")
    );
    const target = rows.find((r) => r.dataset.folderPath === revealedPath);
    if (!target) return;
    const cRect = container.getBoundingClientRect();
    const tRect = target.getBoundingClientRect();
    const delta =
      tRect.top - cRect.top - container.clientHeight / 2 + tRect.height / 2;
    container.scrollTop += delta;
  }, [revealedPath, childrenByPath]);

  // OS drag & drop intake (desktop): send dropped folder/file paths to ingest.
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
          if (p.paths?.length) startIngest(p.paths);
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
  }, [startIngest]);

  const choose = useCallback(async () => {
    if (!IS_TAURI) return;
    const selected = await open({ directory: false, multiple: true });
    const paths = Array.isArray(selected) ? selected : selected ? [selected] : [];
    if (paths.length) startIngest(paths);
  }, [startIngest]);

  const dismissFlash = useCallback(() => {
    if (flashTimer.current) window.clearTimeout(flashTimer.current);
    flashTimer.current = null;
    setFlash(null);
  }, []);

  // Show a success/error toast. Errors stay until dismissed; successes auto-hide.
  const showFlash = useCallback((kind: "ok" | "err", text: string) => {
    setFlash({ kind, text });
    if (flashTimer.current) window.clearTimeout(flashTimer.current);
    flashTimer.current =
      kind === "ok" ? window.setTimeout(() => setFlash(null), 4000) : null;
  }, []);

  // Pull `detail` out of an error response (falls back to the status code).
  const errorDetail = async (res: Response): Promise<string> => {
    try {
      const body = await res.json();
      if (body?.detail) return String(body.detail);
    } catch {
      /* ignore */
    }
    return `HTTP ${res.status}`;
  };

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
        if (res.ok) {
          const data = await res.json();
          dropFromQueue(fileId);
          const parts = String(data.moved_to).split("/");
          const name = parts[parts.length - 1];
          const folder = parts[parts.length - 2] ?? "";
          showFlash("ok", `Filed “${name}” → ${folder}`);
        } else {
          showFlash("err", `Couldn’t file: ${await errorDetail(res)}`);
        }
      } catch (e) {
        showFlash("err", `Couldn’t file: ${String(e)}`);
      } finally {
        setAccepting(null);
      }
    },
    [dropFromQueue, showFlash]
  );

  // File a dropped document into a library folder, then drop it from the queue.
  const fileInto = useCallback(
    async (fileId: string, folderPath: string) => {
      try {
        const res = await fetch(`${BACKEND_URL}/filing/files/${fileId}/file`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ folder_path: folderPath }),
        });
        if (res.ok) {
          const data = await res.json();
          dropFromQueue(fileId);
          const parts = String(data.moved_to).split("/");
          const name = parts[parts.length - 1];
          const folder = parts[parts.length - 2] ?? "";
          showFlash("ok", `Filed “${name}” → ${folder}`);
        } else {
          showFlash("err", `Couldn’t file: ${await errorDetail(res)}`);
        }
      } catch (e) {
        showFlash("err", `Couldn’t file: ${String(e)}`);
      }
    },
    [dropFromQueue, showFlash]
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
      // Re-enable text selection and pane scrolling.
      document.body.style.removeProperty("user-select");
      document.body.style.removeProperty("-webkit-user-select");
      document.body.classList.remove("dragging-no-scroll");
      const src = dragSrcRef.current;
      const wasDrag = didDragRef.current;
      dragSrcRef.current = null;
      dragStartRef.current = null;
      setGhost(null);
      setDropTarget(null);
      if (!wasDrag || !src) return;
      const target = folderPathAt(e.clientX, e.clientY);
      if (target) fileInto(src.fileId, target);
    },
    [onDragMove, fileInto]
  );

  const clearHover = useCallback(() => {
    if (hoverTimer.current !== null) {
      window.clearTimeout(hoverTimer.current);
      hoverTimer.current = null;
    }
    hoverReq.current = null;
    setHoverPreview(null);
  }, []);

  const startFileDrag = useCallback(
    (e: ReactPointerEvent, file: UnfiledFile) => {
      if (e.button !== 0) return;
      clearHover();
      dragSrcRef.current = { fileId: file.file_id, filename: file.filename };
      dragStartRef.current = { x: e.clientX, y: e.clientY };
      didDragRef.current = false;
      // Suppress page-wide text selection and freeze pane scrolling while the
      // pointer is down (so dragging over a list doesn't scroll it).
      document.body.style.setProperty("user-select", "none");
      document.body.style.setProperty("-webkit-user-select", "none");
      document.body.classList.add("dragging-no-scroll");
      window.getSelection()?.removeAllRanges();
      window.addEventListener("pointermove", onDragMove);
      window.addEventListener("pointerup", onDragEnd);
      window.addEventListener("pointercancel", onDragEnd);
    },
    [onDragMove, onDragEnd, clearHover]
  );

  // --- Content preview (hover popover + double-click modal) --------------- //
  const loadPreview = useCallback(
    async (fileId: string, limit: number): Promise<FilePreview> => {
      const cached = previewCache.current.get(fileId);
      // Reuse the cache when it already holds enough text (or the whole file).
      if (cached && (cached.text.length >= limit || !cached.truncated)) {
        return cached;
      }
      const res = await fetch(
        `${BACKEND_URL}/filing/files/${fileId}/preview?limit=${limit}`
      );
      if (!res.ok) throw new Error(`preview failed (${res.status})`);
      const data = (await res.json()) as FilePreview;
      previewCache.current.set(fileId, data);
      return data;
    },
    []
  );

  const onRowEnter = useCallback(
    (e: ReactMouseEvent<HTMLElement>, file: UnfiledFile) => {
      // Only ready files have cached text; never trigger OCR from a hover.
      if (file.status !== "ready" || modalFileId) return;
      // Trigger is the icon, but anchor the popover to the whole row.
      const row =
        (e.currentTarget.closest(".file-row") as HTMLElement | null) ??
        e.currentTarget;
      const rect = row.getBoundingClientRect();
      const top = Math.min(rect.top, window.innerHeight - 220);
      const left = rect.right + 8;
      if (hoverTimer.current !== null) window.clearTimeout(hoverTimer.current);
      hoverReq.current = file.file_id;
      hoverTimer.current = window.setTimeout(() => {
        loadPreview(file.file_id, HOVER_CHARS)
          .then((data) => {
            if (hoverReq.current !== file.file_id) return; // stale
            setHoverPreview({
              fileId: file.file_id,
              text: data.text.slice(0, HOVER_CHARS),
              truncated: data.truncated || data.text.length > HOVER_CHARS,
              top,
              left,
            });
          })
          .catch(() => {});
      }, HOVER_DELAY_MS);
    },
    [loadPreview, modalFileId]
  );

  const openModal = useCallback(
    (file: UnfiledFile) => {
      clearHover();
      setSelectedId(file.file_id);
      setModalFileId(file.file_id);
      setModalData(null);
      setModalLoading(true);
      modalReq.current = file.file_id;
      loadPreview(file.file_id, MODAL_CHARS)
        .then((data) => {
          if (modalReq.current !== file.file_id) return; // closed/switched
          setModalData(data);
        })
        .catch(() => {
          if (modalReq.current === file.file_id) {
            setFlash({ kind: "err", text: "Couldn't load preview." });
          }
        })
        .finally(() => {
          if (modalReq.current === file.file_id) setModalLoading(false);
        });
    },
    [clearHover, loadPreview]
  );

  const closeModal = useCallback(() => {
    modalReq.current = null;
    setModalFileId(null);
    setModalData(null);
    setModalLoading(false);
  }, []);

  const openOriginal = useCallback(async (file: UnfiledFile) => {
    if (IS_TAURI) {
      try {
        await openPath(file.absolute_path);
      } catch (e) {
        console.error("openPath failed", e);
        setFlash({ kind: "err", text: "Couldn't open the file." });
      }
    } else {
      window.open(`${BACKEND_URL}/filing/files/${file.file_id}/raw`, "_blank");
    }
  }, []);

  // Close the preview modal on Escape.
  useEffect(() => {
    if (!modalFileId) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") closeModal();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [modalFileId, closeModal]);

  const toggleExpand = useCallback(
    (path: string) => {
      setExpanded((prev) => {
        const next = new Set(prev);
        if (next.has(path)) next.delete(path);
        else next.add(path);
        return next;
      });
      if (!childrenRef.current[path]) loadChildren(path);
    },
    [loadChildren]
  );

  const selectedFile = files.find((f) => f.file_id === selectedId) ?? null;
  const modalFile = files.find((f) => f.file_id === modalFileId) ?? null;

  // Re-clicking the active key flips direction; switching key picks a default.
  const changeSort = (key: SortKey) => {
    if (key === sortKey) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir(key === "modified" ? "desc" : "asc");
    }
  };

  // Sort only the ready files; keep queued/processing grouped below them.
  const compareReady = (a: UnfiledFile, b: UnfiledFile): number => {
    if (sortKey === "modified") {
      const av = a.modified_at ? Date.parse(a.modified_at) : null;
      const bv = b.modified_at ? Date.parse(b.modified_at) : null;
      // Files without a known modified date always sort last.
      if (av === null || bv === null) {
        if (av === bv) return 0;
        return av === null ? 1 : -1;
      }
      return sortDir === "asc" ? av - bv : bv - av;
    }
    let r: number;
    if (sortKey === "name") {
      r = a.filename.localeCompare(b.filename, undefined, { sensitivity: "base" });
    } else {
      r = a.kind.localeCompare(b.kind) || a.filename.localeCompare(b.filename);
    }
    return sortDir === "asc" ? r : -r;
  };
  const readyFiles = files.filter((f) => f.status === "ready").sort(compareReady);
  const otherFiles = files.filter((f) => f.status !== "ready");
  const orderedFiles = [...readyFiles, ...otherFiles];

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
        <div className="drop-headline">Drop a folder or files here</div>
        <div className="hint">
          {IS_TAURI ? "or click to choose" : "Drag & drop requires the desktop app"}
        </div>
      </div>

      {batch && !BATCH_TERMINAL.has(batch.status) && (
        <div className="filing-status">
          <span className="spinner" />
          <span>
            {batch.files_total > 0
              ? `Processing ${batch.files_processed} of ${batch.files_total} files…`
              : "Processing dropped files…"}
          </span>
        </div>
      )}

      <div className="work-area">
        <div className="work-left">
          <div className="pane file-pane">
            <div className="pane-header">
              <span className="pane-title">
                Files <span className="pane-count">({files.length})</span>
              </span>
              <div className="sort-controls" title="Sort ready files">
                {(Object.keys(SORT_LABELS) as SortKey[]).map((k) => (
                  <button
                    key={k}
                    className={`sort-btn${sortKey === k ? " active" : ""}`}
                    onClick={() => changeSort(k)}
                  >
                    {SORT_LABELS[k]}
                    {sortKey === k ? (sortDir === "asc" ? " ↑" : " ↓") : ""}
                  </button>
                ))}
              </div>
            </div>
            <ul className="pane-body file-list">
              {files.length === 0 && (
                <li className="pane-empty">No files waiting to be filed.</li>
              )}
              {orderedFiles.map((f) => (
                <li
                  key={f.file_id}
                  className={`file-row${f.file_id === selectedId ? " selected" : ""}`}
                  title="Click to select · hover the icon to peek · double-click to preview · drag onto a folder to file"
                  onPointerDown={(e) => startFileDrag(e, f)}
                  onClick={() => {
                    if (didDragRef.current) {
                      didDragRef.current = false;
                      return;
                    }
                    setSelectedId(f.file_id);
                  }}
                  onDoubleClick={() => openModal(f)}
                >
                  <span
                    className="file-icon-hit"
                    onMouseEnter={(e) => onRowEnter(e, f)}
                    onMouseLeave={clearHover}
                  >
                    <FileIcon kind={f.kind} />
                  </span>
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
                    title="Click to select · drag onto a folder to file this"
                    onPointerDown={(e) => startFileDrag(e, selectedFile)}
                    onClick={() => {
                      if (didDragRef.current) {
                        didDragRef.current = false;
                        return;
                      }
                      revealPath(s.folder_path);
                    }}
                  >
                    <FolderIcon />
                    <div className="sug-info">
                      <div className="sug-folder">{s.folder_name}</div>
                      <div className="sug-path" title={s.folder_path}>
                        {s.folder_path}
                      </div>
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
            <span className="pane-meta">{LIBRARY_ROOT}</span>
          </div>
          <div className="pane-body tree" ref={treeRef}>
            {childrenByPath[LIBRARY_ROOT] === undefined ? (
              <div className="pane-empty">Loading…</div>
            ) : childrenByPath[LIBRARY_ROOT].length === 0 ? (
              <div className="pane-empty">No folders found under {LIBRARY_ROOT}.</div>
            ) : (
              childrenByPath[LIBRARY_ROOT].map((node) => (
                <TreeNode
                  key={node.path}
                  node={node}
                  depth={0}
                  expanded={expanded}
                  revealedPath={revealedPath}
                  dropTarget={dropTarget}
                  childrenByPath={childrenByPath}
                  onToggle={toggleExpand}
                />
              ))
            )}
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

      {flash && (
        <div className={`flash-toast ${flash.kind}`} role="status">
          <span className="flash-text">{flash.text}</span>
          <button
            className="flash-close"
            aria-label="Dismiss"
            onClick={dismissFlash}
          >
            ×
          </button>
        </div>
      )}

      {hoverPreview && !modalFileId && (
        <div
          className="preview-pop"
          style={{ top: hoverPreview.top, left: hoverPreview.left }}
        >
          <pre className="preview-pop-text">
            {hoverPreview.text.trim() || "(no extractable text)"}
            {hoverPreview.truncated ? "\n…" : ""}
          </pre>
        </div>
      )}

      {modalFileId && (
        <div className="preview-backdrop" onClick={closeModal}>
          <div
            className="preview-modal"
            role="dialog"
            aria-modal="true"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="preview-modal-header">
              <div className="preview-modal-titles">
                <span className="preview-modal-name">
                  {modalFile?.filename ?? modalData?.filename ?? "Preview"}
                </span>
                <span className="preview-modal-meta">
                  {[
                    modalData?.kind,
                    modalFile ? formatBytes(modalFile.size_bytes) : null,
                    modalData?.parser_used,
                    modalData?.truncated ? "truncated" : null,
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                </span>
              </div>
              <button
                className="preview-modal-close"
                aria-label="Close"
                onClick={closeModal}
              >
                ×
              </button>
            </div>

            <div className="preview-modal-body">
              {modalLoading && !modalData ? (
                <div className="preview-loading">
                  <span className="spinner" /> Extracting…
                </div>
              ) : (
                <>
                  {modalData?.kind === "image" && (
                    <img
                      className="preview-image"
                      src={`${BACKEND_URL}/filing/files/${modalFileId}/raw`}
                      alt={modalData?.filename ?? ""}
                    />
                  )}
                  <pre className="preview-modal-text">
                    {modalData?.text.trim() || "(no extractable text)"}
                    {modalData?.truncated ? "\n\n… (truncated)" : ""}
                  </pre>
                </>
              )}
            </div>

            <div className="preview-modal-footer">
              {modalFile && (
                <button
                  className="preview-btn"
                  onClick={() => openOriginal(modalFile)}
                >
                  Open original
                </button>
              )}
              <button className="preview-btn ghost" onClick={closeModal}>
                Close
              </button>
            </div>
          </div>
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
  childrenByPath,
  onToggle,
}: {
  node: FsEntry;
  depth: number;
  expanded: Set<string>;
  revealedPath: string | null;
  dropTarget: string | null;
  childrenByPath: Record<string, FsEntry[]>;
  onToggle: (path: string) => void;
}) {
  const isDir = node.is_dir;
  const isOpen = isDir && expanded.has(node.path);
  const highlighted = node.path === revealedPath;
  const isDropTarget = node.path === dropTarget;
  const kids = childrenByPath[node.path];

  return (
    <>
      <div
        className={`tree-row${isDir ? " has-children" : ""}${
          highlighted ? " highlight" : ""
        }${isDropTarget ? " drop-target" : ""}`}
        style={{ paddingLeft: 12 + depth * 18 }}
        // Only folders are drop targets.
        {...(isDir ? { "data-folder-path": node.path } : {})}
        title={node.path}
        onClick={isDir ? () => onToggle(node.path) : undefined}
      >
        {isDir ? (
          <ChevronIcon open={isOpen} />
        ) : (
          <span className="tree-chevron spacer" />
        )}
        {isDir ? <FolderIcon small /> : <FileIcon kind={node.kind ?? "other"} />}
        <span className="tree-name">{node.name}</span>
        {highlighted && <SparkleIcon />}
      </div>
      {isOpen &&
        kids?.map((child) => (
          <TreeNode
            key={child.path}
            node={child}
            depth={depth + 1}
            expanded={expanded}
            revealedPath={revealedPath}
            dropTarget={dropTarget}
            childrenByPath={childrenByPath}
            onToggle={onToggle}
          />
        ))}
    </>
  );
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(v < 10 ? 1 : 0)} ${units[i]}`;
}

function confLevel(c: number): "high" | "med" | "low" {
  if (c >= 0.85) return "high";
  if (c >= 0.7) return "med";
  return "low";
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
