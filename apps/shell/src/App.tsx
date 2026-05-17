import { useEffect, useState } from "react";
import "./App.css";

type Tab = "indexing" | "filing";

const BACKEND_URL =
  import.meta.env.VITE_FILER_BACKEND_URL ?? "http://127.0.0.1:8765";

type BackendStatus = "checking" | "ready" | "unreachable";

export default function App() {
  const [tab, setTab] = useState<Tab>("indexing");
  const [backend, setBackend] = useState<BackendStatus>("checking");

  useEffect(() => {
    let cancelled = false;
    const probe = async () => {
      try {
        const res = await fetch(`${BACKEND_URL}/health`);
        if (!cancelled) setBackend(res.ok ? "ready" : "unreachable");
      } catch {
        if (!cancelled) setBackend("unreachable");
      }
    };
    probe();
    const id = setInterval(probe, 5000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  return (
    <div className="app">
      <header className="tab-bar">
        <nav>
          <button
            className={tab === "indexing" ? "tab active" : "tab"}
            onClick={() => setTab("indexing")}
          >
            Indexing
          </button>
          <button
            className={tab === "filing" ? "tab active" : "tab"}
            onClick={() => setTab("filing")}
          >
            Filing
          </button>
        </nav>
        <span className={`backend-status ${backend}`}>
          backend: {backend}
        </span>
      </header>

      <main className="content">
        {tab === "indexing" && <IndexingTab />}
        {tab === "filing" && <FilingTab />}
      </main>
    </div>
  );
}

function IndexingTab() {
  return (
    <section className="panel">
      <h2>Indexing</h2>
      <p>Drop a root folder or choose one to begin indexing. (Coming soon.)</p>
    </section>
  );
}

function FilingTab() {
  return (
    <section className="panel">
      <h2>Filing</h2>
      <p>Inbox files and folder tree will live here. (Coming soon.)</p>
    </section>
  );
}
