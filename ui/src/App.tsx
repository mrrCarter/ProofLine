import {
  AlertTriangle,
  Camera,
  CheckCircle2,
  Clock3,
  Download,
  FileImage,
  FileText,
  Loader2,
  Play,
  UploadCloud,
  XCircle
} from "lucide-react";
import { ChangeEvent, FormEvent, useMemo, useRef, useState } from "react";

type Verdict = "PASS" | "FAIL" | "NEEDS_REVIEW" | "UNREADABLE" | "ERROR" | null;

type TimelineEvent = {
  id: string;
  event: string;
  data: Record<string, unknown>;
};

type Finding = {
  ruleId: string;
  severity: string;
  status: string;
  expected?: unknown;
  observed?: unknown;
  confidence?: number;
  explanation?: string;
  remediation?: string | null;
  evidence?: {
    text?: string | null;
    bbox?: number[][];
    cropUri?: string | null;
    provider?: string | null;
  } | null;
};

type RunResponse = {
  runId: string;
  requestId: string;
  eventsUrl: string;
};

type RunState = {
  runId: string;
  state: string;
  verdict: Verdict;
  findings: Finding[];
  latencyMs?: number | null;
};

type Sample = {
  id: string;
  name: string;
  brandName: string;
  classType: string;
  alcoholContent: string;
  netContents: string;
  origin: string;
  variant: "pass" | "fail" | "review";
};

const API_BASE = import.meta.env.VITE_API_BASE ?? "";
const RUN_WATCHDOG_MS = 15_000;

const PNG_1X1_BASE64 =
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII=";

const samples: Sample[] = [
  {
    id: "pass",
    name: "Passing bourbon",
    brandName: "MOCK",
    classType: "Straight bourbon whiskey",
    alcoholContent: "45% ABV",
    netContents: "750 mL",
    origin: "United States",
    variant: "pass"
  },
  {
    id: "mismatch",
    name: "Brand mismatch",
    brandName: "Old Forester",
    classType: "Straight bourbon whiskey",
    alcoholContent: "90 Proof",
    netContents: "750 mL",
    origin: "United States",
    variant: "fail"
  },
  {
    id: "review",
    name: "Low-confidence glare",
    brandName: "Stone's Throw",
    classType: "Distilled spirits specialty",
    alcoholContent: "40% ABV",
    netContents: "75 cL",
    origin: "Imported",
    variant: "review"
  }
];

const verdictMeta = {
  PASS: { label: "PASS", icon: CheckCircle2, className: "pass" },
  FAIL: { label: "FAIL", icon: XCircle, className: "fail" },
  NEEDS_REVIEW: { label: "NEEDS REVIEW", icon: AlertTriangle, className: "review" },
  UNREADABLE: { label: "UNREADABLE", icon: Camera, className: "unreadable" },
  ERROR: { label: "ERROR", icon: XCircle, className: "fail" }
};

function base64ToFile(base64: string, filename: string): File {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return new File([bytes], filename, { type: "image/png" });
}

function formatUnknown(value: unknown): string {
  if (value === null || value === undefined || value === "") return "None";
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}

function deriveImported(origin: string): boolean | undefined {
  const normalized = origin.trim().toLowerCase();
  if (!normalized) return undefined;
  if (normalized.includes("import")) return true;
  if (normalized.includes("domestic") || normalized.includes("united states") || normalized === "usa" || normalized === "us") {
    return false;
  }
  return undefined;
}

function App() {
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [fields, setFields] = useState({
    brandName: "",
    classType: "",
    alcoholContent: "",
    netContents: "",
    origin: ""
  });
  const [timeline, setTimeline] = useState<TimelineEvent[]>([]);
  const [runState, setRunState] = useState<RunState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isRunning, setIsRunning] = useState(false);
  const [isDragging, setIsDragging] = useState(false);

  const selectedFileLabel = useMemo(() => {
    if (!file) return "No file selected";
    const sizeKb = Math.max(1, Math.round(file.size / 1024));
    return `${file.name} · ${sizeKb} KB`;
  }, [file]);

  const terminalVerdict = runState?.verdict ?? null;

  function updateField(key: keyof typeof fields, value: string) {
    setFields((current) => ({ ...current, [key]: value }));
  }

  function applySample(sample: Sample) {
    setFields({
      brandName: sample.brandName,
      classType: sample.classType,
      alcoholContent: sample.alcoholContent,
      netContents: sample.netContents,
      origin: sample.origin
    });
    setFile(base64ToFile(PNG_1X1_BASE64, `${sample.id}-label.png`));
    setTimeline([]);
    setRunState(null);
    setError(null);
  }

  function onFileChange(event: ChangeEvent<HTMLInputElement>) {
    const nextFile = event.target.files?.[0] ?? null;
    setFile(nextFile);
    setTimeline([]);
    setRunState(null);
    setError(null);
  }

  function onDrop(event: React.DragEvent<HTMLLabelElement>) {
    event.preventDefault();
    setIsDragging(false);
    const nextFile = event.dataTransfer.files?.[0] ?? null;
    if (nextFile) {
      setFile(nextFile);
      setTimeline([]);
      setRunState(null);
      setError(null);
    }
  }

  async function refreshRun(runId: string) {
    const response = await fetch(`${API_BASE}/api/runs/${runId}`);
    if (!response.ok) return;
    const payload = (await response.json()) as RunState;
    setRunState(payload);
  }

  async function verify(event: FormEvent) {
    event.preventDefault();
    if (!file) {
      setError("Select a label image first.");
      return;
    }

    setIsRunning(true);
    setError(null);
    setTimeline([]);
    setRunState(null);

    const form = new FormData();
    const originType = fields.origin.trim();
    const imported = deriveImported(originType);
    const applicationData = {
      ...fields,
      originType,
      ...(imported === undefined ? {} : { imported, isImported: imported })
    };
    form.append("image", file);
    form.append("application_data", JSON.stringify(applicationData));

    try {
      const response = await fetch(`${API_BASE}/api/runs`, {
        method: "POST",
        body: form
      });

      if (!response.ok) {
        const payload = await response.json().catch(() => null);
        throw new Error(payload?.error?.message ?? "Verification failed.");
      }

      const payload = (await response.json()) as RunResponse;
      setRunState({
        runId: payload.runId,
        state: "RECEIVED",
        verdict: null,
        findings: [],
        latencyMs: null
      });

      const source = new EventSource(`${API_BASE}${payload.eventsUrl}`);
      source.onmessage = () => undefined;
      let isSettled = false;
      let watchdogId: number | undefined;

      async function settleRun(message?: string) {
        if (isSettled) return;
        isSettled = true;
        if (watchdogId !== undefined) window.clearTimeout(watchdogId);
        source.close();
        await refreshRun(payload.runId);
        if (message) setError(message);
        setIsRunning(false);
      }

      watchdogId = window.setTimeout(() => {
        void settleRun("Verification timed out waiting for completion; showing the latest run state.");
      }, RUN_WATCHDOG_MS);

      const eventNames = [
        "run.created",
        "preprocess.completed",
        "ocr.completed",
        "field.extracted",
        "rule.evaluated",
        "run.completed"
      ];

      eventNames.forEach((eventName) => {
        source.addEventListener(eventName, async (message) => {
          const data = JSON.parse(message.data) as Record<string, unknown>;
          setTimeline((current) => [
            ...current,
            { id: `${eventName}-${current.length}`, event: eventName, data }
          ]);

          if (eventName === "run.completed") {
            await settleRun();
          }
        });
      });

      source.onerror = () => {
        void settleRun();
      };
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Verification failed.");
      setIsRunning(false);
    }
  }

  const VerdictIcon = terminalVerdict ? verdictMeta[terminalVerdict]?.icon : Clock3;
  const verdictClass = terminalVerdict ? verdictMeta[terminalVerdict]?.className : "pending";

  return (
    <main className="app-shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">ProofLine</p>
          <h1>Alcohol label verification</h1>
        </div>
        <div className="run-chip">
          <Clock3 size={18} aria-hidden="true" />
          {runState?.latencyMs != null ? `${runState.latencyMs} ms` : "Ready"}
        </div>
      </header>

      <form className="workspace" onSubmit={verify}>
        <section className="upload-band" aria-label="Label upload">
          <label
            className={`drop-zone ${isDragging ? "dragging" : ""}`}
            onDragOver={(event) => {
              event.preventDefault();
              setIsDragging(true);
            }}
            onDragLeave={() => setIsDragging(false)}
            onDrop={onDrop}
          >
            <input
              ref={fileInputRef}
              type="file"
              accept="image/png,image/jpeg,image/webp,image/heic,image/heif,application/pdf"
              onChange={onFileChange}
            />
            <span className="drop-icon">
              <UploadCloud size={34} aria-hidden="true" />
            </span>
            <span className="drop-title">Drop label image</span>
            <span className="drop-file">{selectedFileLabel}</span>
          </label>

          <div className="sample-strip" aria-label="Try these">
            {samples.map((sample) => (
              <button
                className={`sample-tile ${sample.variant}`}
                key={sample.id}
                type="button"
                onClick={() => applySample(sample)}
              >
                <span className="sample-preview" aria-hidden="true">
                  <span>{sample.brandName}</span>
                </span>
                <span>{sample.name}</span>
              </button>
            ))}
          </div>
        </section>

        <section className="fields-band" aria-label="Application fields">
          <div className="field-grid">
            <label>
              Brand name
              <input
                value={fields.brandName}
                onChange={(event) => updateField("brandName", event.target.value)}
                autoComplete="off"
              />
            </label>
            <label>
              Class / type
              <input
                value={fields.classType}
                onChange={(event) => updateField("classType", event.target.value)}
                autoComplete="off"
              />
            </label>
            <label>
              Alcohol content
              <input
                value={fields.alcoholContent}
                onChange={(event) => updateField("alcoholContent", event.target.value)}
                autoComplete="off"
              />
            </label>
            <label>
              Net contents
              <input
                value={fields.netContents}
                onChange={(event) => updateField("netContents", event.target.value)}
                autoComplete="off"
              />
            </label>
            <label>
              Origin
              <input
                value={fields.origin}
                onChange={(event) => updateField("origin", event.target.value)}
                autoComplete="off"
              />
            </label>
          </div>

          <div className="action-row">
            <button className="verify-button" type="submit" disabled={isRunning}>
              {isRunning ? <Loader2 className="spin" size={22} aria-hidden="true" /> : <Play size={22} aria-hidden="true" />}
              Verify
            </button>
            <button className="icon-button" type="button" disabled={!runState?.runId} aria-label="Download receipt">
              <Download size={20} aria-hidden="true" />
            </button>
          </div>
        </section>
      </form>

      {error ? (
        <div className="error-banner" role="alert">
          <AlertTriangle size={20} aria-hidden="true" />
          {error}
        </div>
      ) : null}

      <section className={`verdict-banner ${verdictClass}`} aria-live="polite">
        <VerdictIcon size={34} aria-hidden="true" />
        <div>
          <p>Verdict</p>
          <strong>{terminalVerdict ? verdictMeta[terminalVerdict].label : isRunning ? "RUNNING" : "WAITING"}</strong>
        </div>
      </section>

      <section className="results-grid">
        <div className="findings-panel">
          <div className="section-heading">
            <FileText size={20} aria-hidden="true" />
            <h2>Findings</h2>
          </div>
          {runState?.findings.length ? (
            <div className="finding-list">
              {runState.findings.map((finding) => (
                <article className={`finding-item ${finding.status.toLowerCase()}`} key={finding.ruleId}>
                  <div>
                    <h3>{finding.ruleId.replaceAll("_", " ")}</h3>
                    <p>{finding.explanation}</p>
                  </div>
                  <dl>
                    <div>
                      <dt>Expected</dt>
                      <dd>{formatUnknown(finding.expected)}</dd>
                    </div>
                    <div>
                      <dt>Observed</dt>
                      <dd>{formatUnknown(finding.observed)}</dd>
                    </div>
                    <div>
                      <dt>Confidence</dt>
                      <dd>{finding.confidence != null ? `${Math.round(finding.confidence * 100)}%` : "None"}</dd>
                    </div>
                  </dl>
                  {finding.evidence?.text ? <p className="evidence-text">{finding.evidence.text}</p> : null}
                </article>
              ))}
            </div>
          ) : (
            <div className="empty-panel">
              <FileImage size={28} aria-hidden="true" />
              <span>No findings yet</span>
            </div>
          )}
        </div>

        <div className="timeline-panel">
          <div className="section-heading">
            <Clock3 size={20} aria-hidden="true" />
            <h2>Orchestrator Timeline</h2>
          </div>
          <ol className="timeline-list">
            {timeline.length ? (
              timeline.map((item) => (
                <li key={item.id}>
                  <span className="timeline-dot" aria-hidden="true" />
                  <div>
                    <strong>{item.event}</strong>
                    <code>{JSON.stringify(item.data)}</code>
                  </div>
                </li>
              ))
            ) : (
              <li className="timeline-empty">
                <span className="timeline-dot" aria-hidden="true" />
                <div>
                  <strong>Ready</strong>
                  <code>{"{}"}</code>
                </div>
              </li>
            )}
          </ol>
        </div>
      </section>
    </main>
  );
}

export default App;
