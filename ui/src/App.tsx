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
type TerminalVerdict = Exclude<Verdict, null>;
type Mode = "single" | "batch";
type BatchStatus = "QUEUED" | "RUNNING" | TerminalVerdict;
type BatchFilter = "ALL" | TerminalVerdict;

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
  receiptUrl?: string;
  cacheHit?: boolean;
};

type RunState = {
  runId: string;
  requestId: string;
  state: string;
  verdict: Verdict;
  findings: Finding[];
  latencyMs?: number | null;
  receiptRef?: string | null;
};

type LabelFields = {
  brandName: string;
  classType: string;
  alcoholContent: string;
  netContents: string;
  origin: string;
};

type Sample = {
  id: string;
  name: string;
  trap: string;
  expected: TerminalVerdict;
  variant: "pass" | "fail" | "review" | "unreadable";
  fields: LabelFields;
  labelLines: string[];
};

type BatchRow = {
  id: string;
  fileName: string;
  status: BatchStatus;
  verdict: Verdict;
  findings: number;
  latencyMs?: number | null;
  receiptRef?: string | null;
  runId?: string | null;
};

type BatchResponse = {
  batchId: string;
  eventsUrl?: string;
  exportUrl?: string;
  rows?: unknown[];
  labels?: unknown[];
  items?: unknown[];
  summary?: Record<string, unknown>;
};

const API_BASE = import.meta.env.VITE_API_BASE ?? "";
const RUN_WATCHDOG_MS = 15_000;
const BATCH_DEMO_SIZE = 50;

const PNG_1X1_BASE64 =
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII=";

const emptyFields: LabelFields = {
  brandName: "",
  classType: "",
  alcoholContent: "",
  netContents: "",
  origin: ""
};

const samples: Sample[] = [
  {
    id: "pass-bourbon",
    name: "Passing bourbon",
    trap: "Clean baseline",
    expected: "PASS",
    variant: "pass",
    fields: {
      brandName: "MOCK",
      classType: "Straight bourbon whiskey",
      alcoholContent: "45% ABV",
      netContents: "750 mL",
      origin: "United States"
    },
    labelLines: [
      "MOCK",
      "STRAIGHT BOURBON WHISKEY",
      "45% ALC BY VOL",
      "750 ML",
      "GOVERNMENT WARNING: (1) ACCORDING TO THE SURGEON GENERAL, WOMEN SHOULD NOT DRINK ALCOHOLIC BEVERAGES DURING PREGNANCY.",
      "UNITED STATES"
    ]
  },
  {
    id: "title-case-warning",
    name: "Title-case warning",
    trap: "Warning canon",
    expected: "FAIL",
    variant: "fail",
    fields: {
      brandName: "RIDGE TEST",
      classType: "Straight bourbon whiskey",
      alcoholContent: "40% ABV",
      netContents: "750 mL",
      origin: "United States"
    },
    labelLines: [
      "RIDGE TEST",
      "STRAIGHT BOURBON WHISKEY",
      "40% ALC BY VOL",
      "750 ML",
      "Government Warning: according to the Surgeon General, women should not drink alcohol during pregnancy."
    ]
  },
  {
    id: "proof-only",
    name: "90-proof label",
    trap: "ABV conversion",
    expected: "PASS",
    variant: "pass",
    fields: {
      brandName: "NINETY LINE",
      classType: "Kentucky straight bourbon whiskey",
      alcoholContent: "45% ABV",
      netContents: "750 mL",
      origin: "United States"
    },
    labelLines: [
      "NINETY LINE",
      "KENTUCKY STRAIGHT BOURBON WHISKEY",
      "90 PROOF",
      "750 ML",
      "GOVERNMENT WARNING: (1) ACCORDING TO THE SURGEON GENERAL, WOMEN SHOULD NOT DRINK ALCOHOLIC BEVERAGES DURING PREGNANCY."
    ]
  },
  {
    id: "glare",
    name: "Glare photo",
    trap: "Readability",
    expected: "UNREADABLE",
    variant: "unreadable",
    fields: {
      brandName: "STONE'S THROW",
      classType: "Distilled spirits specialty",
      alcoholContent: "40% ABV",
      netContents: "75 cL",
      origin: "United States"
    },
    labelLines: ["STONE'S THROW", "DISTILLED SPIRITS SPECIALTY", "40% ALC BY VOL", "75 CL"]
  },
  {
    id: "import-origin",
    name: "Import origin gap",
    trap: "Country of origin",
    expected: "FAIL",
    variant: "fail",
    fields: {
      brandName: "CASA NORTE",
      classType: "Tequila",
      alcoholContent: "40% ABV",
      netContents: "750 mL",
      origin: "Imported"
    },
    labelLines: [
      "CASA NORTE",
      "TEQUILA",
      "40% ALC BY VOL",
      "750 ML",
      "GOVERNMENT WARNING: (1) ACCORDING TO THE SURGEON GENERAL, WOMEN SHOULD NOT DRINK ALCOHOLIC BEVERAGES DURING PREGNANCY."
    ]
  }
];

const batchScenarios = [
  { name: "pass_bourbon", verdict: "PASS" as TerminalVerdict, findings: 0, latencyMs: 2180 },
  { name: "abv_mismatch", verdict: "FAIL" as TerminalVerdict, findings: 2, latencyMs: 2310 },
  { name: "brand_case_equivalent", verdict: "PASS" as TerminalVerdict, findings: 0, latencyMs: 2095 },
  { name: "brand_material_mismatch", verdict: "FAIL" as TerminalVerdict, findings: 1, latencyMs: 2265 },
  { name: "import_missing_origin", verdict: "FAIL" as TerminalVerdict, findings: 1, latencyMs: 2340 },
  { name: "net_contents_unit_equiv", verdict: "PASS" as TerminalVerdict, findings: 0, latencyMs: 2050 },
  { name: "proof_only_equivalent", verdict: "PASS" as TerminalVerdict, findings: 0, latencyMs: 2125 },
  { name: "warning_missing", verdict: "FAIL" as TerminalVerdict, findings: 1, latencyMs: 2288 },
  { name: "warning_small_font_signal", verdict: "NEEDS_REVIEW" as TerminalVerdict, findings: 1, latencyMs: 2410 },
  { name: "glare_unreadable", verdict: "UNREADABLE" as TerminalVerdict, findings: 1, latencyMs: 2525 }
];

const verdictMeta: Record<TerminalVerdict, { label: string; icon: typeof CheckCircle2; className: string }> = {
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

function applicationPayload(fields: LabelFields): Record<string, unknown> {
  const originType = fields.origin.trim();
  const imported = deriveImported(originType);
  return {
    ...fields,
    originType,
    ...(imported === undefined ? {} : { imported, isImported: imported })
  };
}

function classForStatus(status: BatchStatus | Verdict): string {
  if (status === "PASS") return "pass";
  if (status === "FAIL" || status === "ERROR") return "fail";
  if (status === "NEEDS_REVIEW") return "review";
  if (status === "UNREADABLE") return "unreadable";
  return "pending";
}

function stringifyError(caught: unknown, fallback: string): string {
  return caught instanceof Error ? caught.message : fallback;
}

function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

function csvEscape(value: unknown): string {
  const raw = value === null || value === undefined ? "" : String(value);
  if (!/[",\n]/.test(raw)) return raw;
  return `"${raw.replaceAll('"', '""')}"`;
}

function rowsToCsv(rows: BatchRow[]): string {
  const header = ["fileName", "status", "verdict", "findings", "latencyMs", "receiptRef"];
  const body = rows.map((row) =>
    [
      row.fileName,
      row.status,
      row.verdict ?? "",
      row.findings,
      row.latencyMs ?? "",
      row.receiptRef ?? ""
    ]
      .map(csvEscape)
      .join(",")
  );
  return [header.join(","), ...body].join("\n");
}

function readString(value: unknown, fallback: string): string {
  return typeof value === "string" && value.trim() ? value : fallback;
}

function readNumber(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function normalizeBatchRow(value: unknown, index: number): BatchRow {
  const source = value && typeof value === "object" ? (value as Record<string, unknown>) : {};
  const verdict = readString(source.verdict, "") as Verdict;
  const statusValue = readString(source.status, verdict ?? "QUEUED") as BatchStatus;
  const runId = readString(source.runId, "") || null;
  return {
    id: readString(source.id, runId ?? `row-${index + 1}`),
    fileName: readString(source.fileName ?? source.filename ?? source.name, `label-${String(index + 1).padStart(2, "0")}.png`),
    status: statusValue,
    verdict,
    findings: readNumber(source.findingsCount ?? source.findings, 0),
    latencyMs: typeof source.latencyMs === "number" ? source.latencyMs : null,
    receiptRef: readString(source.receiptRef ?? source.receiptUrl, "") || (runId ? `/api/receipts/${runId}` : null),
    runId
  };
}

function normalizeBatchRows(payload: BatchResponse | Record<string, unknown>): BatchRow[] {
  const rows = payload.rows ?? payload.items ?? payload.labels;
  if (!Array.isArray(rows)) return [];
  return rows.map(normalizeBatchRow);
}

function createQueuedRows(): BatchRow[] {
  return Array.from({ length: BATCH_DEMO_SIZE }, (_, index) => {
    const scenario = batchScenarios[index % batchScenarios.length];
    return {
      id: `demo-${index + 1}`,
      fileName: `${scenario.name}_${String(index + 1).padStart(2, "0")}.png`,
      status: "QUEUED",
      verdict: null,
      findings: 0,
      latencyMs: null,
      receiptRef: null,
      runId: null
    };
  });
}

function createCompletedRows(): BatchRow[] {
  return Array.from({ length: BATCH_DEMO_SIZE }, (_, index) => {
    const scenario = batchScenarios[index % batchScenarios.length];
    return {
      id: `demo-${index + 1}`,
      fileName: `${scenario.name}_${String(index + 1).padStart(2, "0")}.png`,
      status: scenario.verdict,
      verdict: scenario.verdict,
      findings: scenario.findings,
      latencyMs: scenario.latencyMs + (index % 5) * 24,
      receiptRef: null,
      runId: null
    };
  });
}

function delay(ms: number) {
  return new Promise((resolve) => {
    window.setTimeout(resolve, ms);
  });
}

async function labelImageFile(sample: Sample): Promise<File> {
  const canvas = document.createElement("canvas");
  canvas.width = 960;
  canvas.height = 640;
  const context = canvas.getContext("2d");
  if (!context) return base64ToFile(PNG_1X1_BASE64, `${sample.id}-label.png`);

  context.fillStyle = sample.variant === "unreadable" ? "#eef1f2" : "#ffffff";
  context.fillRect(0, 0, canvas.width, canvas.height);
  context.strokeStyle = "#11181d";
  context.lineWidth = 8;
  context.strokeRect(26, 26, canvas.width - 52, canvas.height - 52);

  context.fillStyle = "#11181d";
  context.textAlign = "center";
  context.textBaseline = "middle";

  const [brand, ...rest] = sample.labelLines;
  context.font = "800 64px Arial";
  context.fillText(brand, canvas.width / 2, 108, 820);

  context.font = "700 34px Arial";
  rest.slice(0, 4).forEach((line, index) => {
    context.fillText(line, canvas.width / 2, 194 + index * 58, 820);
  });

  context.font = "600 22px Arial";
  const warning = rest.slice(4).join(" ");
  if (warning) {
    const chunks = warning.match(/.{1,72}(\s|$)/g) ?? [warning];
    chunks.slice(0, 4).forEach((line, index) => {
      context.fillText(line.trim(), canvas.width / 2, 470 + index * 34, 820);
    });
  }

  if (sample.variant === "unreadable") {
    const glare = context.createLinearGradient(180, 80, 820, 560);
    glare.addColorStop(0, "rgba(255,255,255,0.08)");
    glare.addColorStop(0.45, "rgba(255,255,255,0.92)");
    glare.addColorStop(1, "rgba(255,255,255,0.16)");
    context.fillStyle = glare;
    context.beginPath();
    context.moveTo(180, 0);
    context.lineTo(960, 410);
    context.lineTo(790, 640);
    context.lineTo(0, 230);
    context.closePath();
    context.fill();
    context.fillStyle = "rgba(24,32,38,0.18)";
    context.fillRect(0, 0, canvas.width, canvas.height);
  }

  return new Promise((resolve) => {
    canvas.toBlob((blob) => {
      if (!blob) {
        resolve(base64ToFile(PNG_1X1_BASE64, `${sample.id}-label.png`));
        return;
      }
      resolve(new File([blob], `${sample.id}-label.png`, { type: "image/png" }));
    }, "image/png");
  });
}

function App() {
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const batchInputRef = useRef<HTMLInputElement | null>(null);
  const [mode, setMode] = useState<Mode>("single");
  const [file, setFile] = useState<File | null>(null);
  const [batchFile, setBatchFile] = useState<File | null>(null);
  const [fields, setFields] = useState<LabelFields>(emptyFields);
  const [timeline, setTimeline] = useState<TimelineEvent[]>([]);
  const [runState, setRunState] = useState<RunState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isRunning, setIsRunning] = useState(false);
  const [isDragging, setIsDragging] = useState(false);
  const [preparingSampleId, setPreparingSampleId] = useState<string | null>(null);
  const [batchRows, setBatchRows] = useState<BatchRow[]>([]);
  const [batchTimeline, setBatchTimeline] = useState<TimelineEvent[]>([]);
  const [batchFilter, setBatchFilter] = useState<BatchFilter>("ALL");
  const [batchError, setBatchError] = useState<string | null>(null);
  const [batchId, setBatchId] = useState<string | null>(null);
  const [batchExportUrl, setBatchExportUrl] = useState<string | null>(null);
  const [isBatchRunning, setIsBatchRunning] = useState(false);
  const [isDraggingBatch, setIsDraggingBatch] = useState(false);

  const selectedFileLabel = useMemo(() => {
    if (!file) return "No file selected";
    const sizeKb = Math.max(1, Math.round(file.size / 1024));
    return `${file.name} - ${sizeKb} KB`;
  }, [file]);

  const selectedBatchLabel = useMemo(() => {
    if (!batchFile) return "No batch selected";
    const sizeKb = Math.max(1, Math.round(batchFile.size / 1024));
    return `${batchFile.name} - ${sizeKb} KB`;
  }, [batchFile]);

  const terminalVerdict = runState?.verdict ?? null;

  const filteredBatchRows = useMemo(() => {
    if (batchFilter === "ALL") return batchRows;
    return batchRows.filter((row) => row.verdict === batchFilter || row.status === batchFilter);
  }, [batchFilter, batchRows]);

  const batchCounts = useMemo(() => {
    const counts: Record<TerminalVerdict, number> = {
      PASS: 0,
      FAIL: 0,
      NEEDS_REVIEW: 0,
      UNREADABLE: 0,
      ERROR: 0
    };
    let completed = 0;
    batchRows.forEach((row) => {
      if (row.verdict) {
        counts[row.verdict] += 1;
        completed += 1;
      }
    });
    return { ...counts, completed, total: batchRows.length };
  }, [batchRows]);

  const batchProgress = batchCounts.total ? Math.round((batchCounts.completed / batchCounts.total) * 100) : 0;

  function updateField(key: keyof LabelFields, value: string) {
    setFields((current) => ({ ...current, [key]: value }));
  }

  async function applySample(sample: Sample) {
    setPreparingSampleId(sample.id);
    setMode("single");
    setFields(sample.fields);
    setTimeline([]);
    setRunState(null);
    setError(null);
    try {
      setFile(await labelImageFile(sample));
    } finally {
      setPreparingSampleId(null);
    }
  }

  function onFileChange(event: ChangeEvent<HTMLInputElement>) {
    const nextFile = event.target.files?.[0] ?? null;
    setFile(nextFile);
    setTimeline([]);
    setRunState(null);
    setError(null);
  }

  function onBatchFileChange(event: ChangeEvent<HTMLInputElement>) {
    const nextFile = event.target.files?.[0] ?? null;
    setBatchFile(nextFile);
    setBatchRows([]);
    setBatchTimeline([]);
    setBatchError(null);
    setBatchId(null);
    setBatchExportUrl(null);
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

  function onBatchDrop(event: React.DragEvent<HTMLLabelElement>) {
    event.preventDefault();
    setIsDraggingBatch(false);
    const nextFile = event.dataTransfer.files?.[0] ?? null;
    if (nextFile) {
      setBatchFile(nextFile);
      setBatchRows([]);
      setBatchTimeline([]);
      setBatchError(null);
      setBatchId(null);
      setBatchExportUrl(null);
    }
  }

  async function refreshRun(runId: string) {
    const response = await fetch(`${API_BASE}/api/runs/${runId}`);
    if (!response.ok) return null;
    const payload = (await response.json()) as RunState;
    setRunState(payload);
    return payload;
  }

  async function refreshBatch(nextBatchId: string) {
    const response = await fetch(`${API_BASE}/api/batches/${nextBatchId}`);
    if (!response.ok) return null;
    const payload = (await response.json()) as BatchResponse;
    const rows = normalizeBatchRows(payload);
    if (rows.length) setBatchRows(rows);
    if (payload.exportUrl) setBatchExportUrl(payload.exportUrl);
    return payload;
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
    form.append("image", file);
    form.append("application_data", JSON.stringify(applicationPayload(fields)));

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
        requestId: payload.requestId,
        state: "RECEIVED",
        verdict: null,
        findings: [],
        latencyMs: null,
        receiptRef: payload.receiptUrl ?? null
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
        "run.escalated",
        "agent.spawned",
        "agent.opinion",
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
      setError(stringifyError(caught, "Verification failed."));
      setIsRunning(false);
    }
  }

  async function downloadReceipt(row?: BatchRow) {
    const runId = row?.runId ?? runState?.runId;
    const receiptRef = row?.receiptRef ?? runState?.receiptRef ?? (runId ? `/api/receipts/${runId}` : null);
    if (!receiptRef) return;

    try {
      const response = await fetch(receiptRef.startsWith("http") ? receiptRef : `${API_BASE}${receiptRef}`);
      if (!response.ok) throw new Error("Receipt is not available yet.");
      const receipt = await response.json();
      const filename = `proofline-receipt-${runId ?? "batch-row"}.json`;
      downloadBlob(new Blob([JSON.stringify(receipt, null, 2)], { type: "application/json" }), filename);
    } catch (caught) {
      const message = stringifyError(caught, "Receipt download failed.");
      if (row) setBatchError(message);
      else setError(message);
    }
  }

  function connectBatchEvents(nextBatchId: string, eventsUrl: string) {
    const source = new EventSource(`${API_BASE}${eventsUrl}`);
    const eventNames = [
      "batch.created",
      "batch.item.started",
      "batch.item.completed",
      "batch.item.failed",
      "batch.completed",
      "batch.failed"
    ];

    eventNames.forEach((eventName) => {
      source.addEventListener(eventName, async (message) => {
        const data = JSON.parse(message.data) as Record<string, unknown>;
        setBatchTimeline((current) => [
          ...current,
          { id: `${eventName}-${current.length}`, event: eventName, data }
        ]);

        if (eventName === "batch.item.completed" || eventName === "batch.item.failed") {
          const row = normalizeBatchRow(data, batchRows.length);
          setBatchRows((current) => {
            const index = current.findIndex((candidate) => candidate.id === row.id || candidate.fileName === row.fileName);
            if (index === -1) return [...current, row];
            return current.map((candidate, candidateIndex) => (candidateIndex === index ? row : candidate));
          });
        }

        if (eventName === "batch.completed" || eventName === "batch.failed") {
          source.close();
          await refreshBatch(nextBatchId);
          setIsBatchRunning(false);
        }
      });
    });

    source.onerror = () => {
      source.close();
      void refreshBatch(nextBatchId);
      setIsBatchRunning(false);
    };
  }

  async function startBatch(event: FormEvent) {
    event.preventDefault();
    if (!batchFile) {
      setBatchError("Select a batch zip or multi-file artifact first.");
      return;
    }

    setBatchError(null);
    setBatchRows([]);
    setBatchTimeline([]);
    setBatchId(null);
    setBatchExportUrl(null);
    setIsBatchRunning(true);

    const form = new FormData();
    form.append("batch", batchFile);
    form.append("application_data", JSON.stringify(applicationPayload(fields)));

    try {
      const response = await fetch(`${API_BASE}/api/batches`, {
        method: "POST",
        body: form
      });

      if (!response.ok) {
        const payload = await response.json().catch(() => null);
        throw new Error(payload?.error?.message ?? "Batch endpoint is not available yet.");
      }

      const payload = (await response.json()) as BatchResponse;
      setBatchId(payload.batchId);
      setBatchExportUrl(payload.exportUrl ?? `/api/batches/${payload.batchId}/export.csv`);
      const rows = normalizeBatchRows(payload);
      if (rows.length) setBatchRows(rows);
      else await refreshBatch(payload.batchId);
      if (payload.eventsUrl) connectBatchEvents(payload.batchId, payload.eventsUrl);
      else setIsBatchRunning(false);
    } catch (caught) {
      setBatchError(stringifyError(caught, "Batch submission failed."));
      setIsBatchRunning(false);
    }
  }

  async function runDemoBatch() {
    const queuedRows = createQueuedRows();
    const completedRows = createCompletedRows();
    setMode("batch");
    setBatchError(null);
    setBatchTimeline([{ id: "demo-start", event: "batch.created", data: { source: "ui-demo", count: BATCH_DEMO_SIZE } }]);
    setBatchRows(queuedRows);
    setBatchId("ui-demo-50");
    setBatchExportUrl(null);
    setIsBatchRunning(true);

    try {
      const response = await fetch(`${API_BASE}/api/batches/demo`, { method: "POST" });
      if (response.ok) {
        const payload = (await response.json()) as BatchResponse;
        setBatchId(payload.batchId);
        setBatchExportUrl(payload.exportUrl ?? `/api/batches/${payload.batchId}/export.csv`);
        const rows = normalizeBatchRows(payload);
        if (rows.length) setBatchRows(rows);
        if (payload.eventsUrl) {
          connectBatchEvents(payload.batchId, payload.eventsUrl);
          return;
        }
      }
    } catch {
      // Local demo rows keep the UI usable while the API batch slice is being implemented.
    }

    for (let completed = 5; completed <= BATCH_DEMO_SIZE; completed += 5) {
      await delay(110);
      setBatchRows((current) =>
        current.map((row, index) => (index < completed ? completedRows[index] : row.status === "QUEUED" ? { ...row, status: "RUNNING" } : row))
      );
      setBatchTimeline((current) => [
        ...current,
        { id: `demo-progress-${completed}`, event: "batch.item.completed", data: { completed, total: BATCH_DEMO_SIZE } }
      ]);
    }

    setBatchRows(completedRows);
    setBatchTimeline((current) => [
      ...current,
      { id: "demo-complete", event: "batch.completed", data: { completed: BATCH_DEMO_SIZE, source: "ui-demo" } }
    ]);
    setIsBatchRunning(false);
  }

  async function exportBatchCsv() {
    try {
      if (batchExportUrl) {
        const response = await fetch(batchExportUrl.startsWith("http") ? batchExportUrl : `${API_BASE}${batchExportUrl}`);
        if (response.ok) {
          const blob = await response.blob();
          downloadBlob(blob, `proofline-batch-${batchId ?? "export"}.csv`);
          return;
        }
      }
      downloadBlob(new Blob([rowsToCsv(batchRows)], { type: "text/csv" }), `proofline-batch-${batchId ?? "local"}.csv`);
    } catch (caught) {
      setBatchError(stringifyError(caught, "CSV export failed."));
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
        <div className="topbar-actions">
          <div className="mode-tabs" role="tablist" aria-label="Verification mode">
            <button className={mode === "single" ? "active" : ""} type="button" role="tab" aria-selected={mode === "single"} onClick={() => setMode("single")}>
              Single
            </button>
            <button className={mode === "batch" ? "active" : ""} type="button" role="tab" aria-selected={mode === "batch"} onClick={() => setMode("batch")}>
              Batch
            </button>
          </div>
          <div className="run-chip">
            <Clock3 size={18} aria-hidden="true" />
            {mode === "batch" ? `${batchProgress}%` : runState?.latencyMs != null ? `${runState.latencyMs} ms` : "Ready"}
          </div>
        </div>
      </header>

      {mode === "single" ? (
        <>
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
                    onClick={() => void applySample(sample)}
                    disabled={preparingSampleId === sample.id}
                  >
                    <span className="sample-preview" aria-hidden="true">
                      <span>{sample.fields.brandName}</span>
                    </span>
                    <span className="sample-name">{sample.name}</span>
                    <span className="sample-meta">{sample.trap}</span>
                    <span className={`sample-verdict ${classForStatus(sample.expected)}`}>{verdictMeta[sample.expected].label}</span>
                  </button>
                ))}
              </div>
            </section>

            <section className="fields-band" aria-label="Application fields">
              <FieldGrid fields={fields} updateField={updateField} />

              <div className="action-row">
                <button className="verify-button" type="submit" disabled={isRunning}>
                  {isRunning ? <Loader2 className="spin" size={22} aria-hidden="true" /> : <Play size={22} aria-hidden="true" />}
                  Verify
                </button>
                <button
                  className="icon-button"
                  type="button"
                  disabled={!runState?.runId || isRunning}
                  aria-label="Download receipt"
                  title="Download receipt"
                  onClick={() => void downloadReceipt()}
                >
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

            <TimelinePanel items={timeline} />
          </section>
        </>
      ) : (
        <>
          <form className="batch-workspace" onSubmit={startBatch}>
            <section className="upload-band" aria-label="Batch upload">
              <label
                className={`drop-zone batch ${isDraggingBatch ? "dragging" : ""}`}
                onDragOver={(event) => {
                  event.preventDefault();
                  setIsDraggingBatch(true);
                }}
                onDragLeave={() => setIsDraggingBatch(false)}
                onDrop={onBatchDrop}
              >
                <input ref={batchInputRef} type="file" accept=".zip,.csv,image/png,image/jpeg,image/webp,application/zip" onChange={onBatchFileChange} />
                <span className="drop-icon">
                  <UploadCloud size={34} aria-hidden="true" />
                </span>
                <span className="drop-title">Drop batch artifact</span>
                <span className="drop-file">{selectedBatchLabel}</span>
              </label>
              <div className="batch-actions">
                <button className="verify-button" type="submit" disabled={isBatchRunning}>
                  {isBatchRunning ? <Loader2 className="spin" size={22} aria-hidden="true" /> : <Play size={22} aria-hidden="true" />}
                  Start batch
                </button>
                <button className="secondary-button" type="button" onClick={() => void runDemoBatch()} disabled={isBatchRunning}>
                  Run 50-label demo
                </button>
              </div>
            </section>

            <section className="fields-band" aria-label="Batch defaults">
              <FieldGrid fields={fields} updateField={updateField} />
            </section>
          </form>

          {batchError ? (
            <div className="error-banner" role="alert">
              <AlertTriangle size={20} aria-hidden="true" />
              {batchError}
            </div>
          ) : null}

          <section className="batch-summary" aria-live="polite">
            <div>
              <p>Progress</p>
              <strong>
                {batchCounts.completed}/{batchCounts.total || BATCH_DEMO_SIZE}
              </strong>
            </div>
            <progress value={batchCounts.completed} max={batchCounts.total || BATCH_DEMO_SIZE} />
            <div className="summary-pills">
              <span className="pass">PASS {batchCounts.PASS}</span>
              <span className="fail">FAIL {batchCounts.FAIL}</span>
              <span className="review">REVIEW {batchCounts.NEEDS_REVIEW}</span>
              <span className="unreadable">UNREADABLE {batchCounts.UNREADABLE}</span>
            </div>
          </section>

          <section className="batch-grid">
            <div className="batch-table-panel">
              <div className="section-heading table-heading">
                <div>
                  <FileText size={20} aria-hidden="true" />
                  <h2>Batch Results</h2>
                </div>
                <div className="table-tools">
                  <select value={batchFilter} onChange={(event) => setBatchFilter(event.target.value as BatchFilter)} aria-label="Filter batch results">
                    <option value="ALL">All</option>
                    <option value="PASS">PASS</option>
                    <option value="FAIL">FAIL</option>
                    <option value="NEEDS_REVIEW">Needs review</option>
                    <option value="UNREADABLE">Unreadable</option>
                    <option value="ERROR">Error</option>
                  </select>
                  <button className="icon-button" type="button" disabled={!batchRows.length} onClick={() => void exportBatchCsv()} aria-label="Export CSV" title="Export CSV">
                    <Download size={20} aria-hidden="true" />
                  </button>
                </div>
              </div>

              <div className="batch-table-wrap">
                <table className="batch-table">
                  <thead>
                    <tr>
                      <th>Label</th>
                      <th>Status</th>
                      <th>Findings</th>
                      <th>Latency</th>
                      <th>Receipt</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredBatchRows.length ? (
                      filteredBatchRows.map((row) => (
                        <tr key={row.id}>
                          <td>{row.fileName}</td>
                          <td>
                            <span className={`status-pill ${classForStatus(row.verdict ?? row.status)}`}>{row.verdict ? verdictMeta[row.verdict].label : row.status}</span>
                          </td>
                          <td>{row.findings}</td>
                          <td>{row.latencyMs != null ? `${row.latencyMs} ms` : "-"}</td>
                          <td>
                            <button className="mini-icon-button" type="button" disabled={!row.receiptRef && !row.runId} aria-label={`Download receipt for ${row.fileName}`} onClick={() => void downloadReceipt(row)}>
                              <Download size={16} aria-hidden="true" />
                            </button>
                          </td>
                        </tr>
                      ))
                    ) : (
                      <tr>
                        <td colSpan={5}>
                          <span className="table-empty">No batch rows yet</span>
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>

            <TimelinePanel items={batchTimeline} title="Batch Timeline" />
          </section>
        </>
      )}
    </main>
  );
}

function FieldGrid({
  fields,
  updateField
}: {
  fields: LabelFields;
  updateField: (key: keyof LabelFields, value: string) => void;
}) {
  return (
    <div className="field-grid">
      <label>
        Brand name
        <input value={fields.brandName} onChange={(event) => updateField("brandName", event.target.value)} autoComplete="off" />
      </label>
      <label>
        Class / type
        <input value={fields.classType} onChange={(event) => updateField("classType", event.target.value)} autoComplete="off" />
      </label>
      <label>
        Alcohol content
        <input value={fields.alcoholContent} onChange={(event) => updateField("alcoholContent", event.target.value)} autoComplete="off" />
      </label>
      <label>
        Net contents
        <input value={fields.netContents} onChange={(event) => updateField("netContents", event.target.value)} autoComplete="off" />
      </label>
      <label>
        Origin
        <input value={fields.origin} onChange={(event) => updateField("origin", event.target.value)} autoComplete="off" />
      </label>
    </div>
  );
}

function TimelinePanel({ items, title = "Orchestrator Timeline" }: { items: TimelineEvent[]; title?: string }) {
  return (
    <div className="timeline-panel">
      <div className="section-heading">
        <Clock3 size={20} aria-hidden="true" />
        <h2>{title}</h2>
      </div>
      <ol className="timeline-list">
        {items.length ? (
          items.map((item) => (
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
  );
}

export default App;
