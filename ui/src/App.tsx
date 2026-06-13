import {
  AlertTriangle,
  Camera,
  CheckCircle2,
  Clock3,
  Download,
  FileImage,
  FileText,
  Loader2,
  Maximize2,
  Play,
  UploadCloud,
  X,
  XCircle
} from "lucide-react";
import { ChangeEvent, FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { findingSentence, formatFindingValue, ruleLabel } from "./findingText";

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
  commodity: string;
  brandName: string;
  classType: string;
  alcoholContent: string;
  netContents: string;
  origin: string;
  countryOfOrigin: string;
  producerName: string;
  producerCity: string;
  producerState: string;
};

type FieldSuggestion = {
  key: keyof LabelFields;
  label: string;
  value: string;
  confidence?: number;
  source?: string;
};

type Sample = {
  id: string;
  name: string;
  trap: string;
  expected: TerminalVerdict;
  variant: "pass" | "fail" | "review";
  fields: LabelFields;
  imagePath: string;
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

type EvidencePreview = {
  src: string;
  label: string;
};

const API_BASE = import.meta.env.VITE_API_BASE ?? "";
const REQUEST_TIMEOUT_MS = 15_000;
const RUN_WATCHDOG_MS = 15_000;
const MAX_BATCH_FILES = 10;

const PNG_1X1_BASE64 =
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII=";

const emptyFields: LabelFields = {
  commodity: "",
  brandName: "",
  classType: "",
  alcoholContent: "",
  netContents: "",
  origin: "",
  countryOfOrigin: "",
  producerName: "",
  producerCity: "",
  producerState: ""
};

const fieldLabels: Record<keyof LabelFields, string> = {
  commodity: "Product type",
  brandName: "Brand name",
  classType: "Class / type",
  alcoholContent: "Alcohol content",
  netContents: "Net contents",
  origin: "Origin status",
  countryOfOrigin: "Country of origin",
  producerName: "Producer / bottler",
  producerCity: "Producer city",
  producerState: "Producer state"
};

const fieldAliases: Record<keyof LabelFields, string[]> = {
  commodity: ["commodity", "labelType", "productType", "productCategory"],
  brandName: ["brandName", "brand", "brand_name", "applicantBrandName"],
  classType: ["classType", "class", "class_type", "productClass", "productTypeText"],
  alcoholContent: ["alcoholContent", "alcohol", "abv", "alcoholByVolume", "alcByVol"],
  netContents: ["netContents", "netContent", "net_contents", "volume", "containerSize"],
  origin: ["origin", "originType", "importStatus", "isImported", "imported"],
  countryOfOrigin: ["countryOfOrigin", "country", "originCountry", "country_of_origin"],
  producerName: ["producerName", "bottlerName", "applicantName", "importerName", "name"],
  producerCity: ["producerCity", "bottlerCity", "importerCity", "city"],
  producerState: ["producerState", "bottlerState", "importerState", "state"]
};

const samples: Sample[] = [
  {
    id: "title-case-warning",
    name: "Title-case warning",
    trap: "Warning canon",
    expected: "FAIL",
    variant: "fail",
    fields: {
      ...emptyFields,
      commodity: "spirits",
      brandName: "Old Forester",
      classType: "Bourbon Whisky",
      alcoholContent: "43% ABV",
      netContents: "750 mL",
      origin: "United States"
    },
    imagePath: "/fixtures/warning_title_case.png"
  },
  {
    id: "abv-mismatch",
    name: "ABV mismatch",
    trap: "Alcohol field",
    expected: "FAIL",
    variant: "fail",
    fields: {
      ...emptyFields,
      commodity: "spirits",
      brandName: "Old Forester",
      classType: "Bourbon Whisky",
      alcoholContent: "45% ABV",
      netContents: "750 mL",
      origin: "United States"
    },
    imagePath: "/fixtures/abv_mismatch.png"
  },
  {
    id: "import-origin",
    name: "Import origin gap",
    trap: "Country of origin",
    expected: "NEEDS_REVIEW",
    variant: "review",
    fields: {
      ...emptyFields,
      commodity: "spirits",
      brandName: "Highland Sample",
      classType: "Single Malt Whisky",
      alcoholContent: "46% ABV",
      netContents: "700 mL",
      origin: "Imported",
      countryOfOrigin: "Scotland"
    },
    imagePath: "/fixtures/import_missing_origin.png"
  }
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
  const commodity = fields.commodity.trim().toLowerCase();
  return {
    ...fields,
    commodity,
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

function labelForStatus(status: string): string {
  return status === "NEEDS_REVIEW" ? "NEEDS REVIEW" : status;
}

const batchTriageOrder: Record<string, number> = {
  FAIL: 0,
  NEEDS_REVIEW: 1,
  ERROR: 2,
  UNREADABLE: 3,
  RUNNING: 4,
  QUEUED: 5,
  PASS: 6
};

function batchTriageRank(row: BatchRow): number {
  return batchTriageOrder[row.verdict ?? row.status] ?? 99;
}

function stringifyError(caught: unknown, fallback: string): string {
  return caught instanceof Error ? caught.message : fallback;
}

function readLatencyMs(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function canPreviewFile(file: File): boolean {
  const name = file.name.toLowerCase();
  if (file.type === "image/heic" || file.type === "image/heif" || /\.(heic|heif)$/.test(name)) return false;
  return file.type.startsWith("image/") || /\.(png|jpe?g|webp|gif)$/.test(name);
}

async function fetchWithTimeout(input: RequestInfo | URL, init: RequestInit = {}, timeoutMs = REQUEST_TIMEOUT_MS): Promise<Response> {
  const controller = new AbortController();
  const timeoutId = window.setTimeout(() => {
    controller.abort();
  }, timeoutMs);

  try {
    return await fetch(input, { ...init, signal: controller.signal });
  } catch (caught) {
    if (caught instanceof DOMException && caught.name === "AbortError") {
      throw new Error("Request timed out. Please try again.");
    }
    throw caught;
  } finally {
    window.clearTimeout(timeoutId);
  }
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

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function normalizeFieldName(value: string): string {
  return value.replace(/[^a-z0-9]+/gi, "").toLowerCase();
}

function fieldKeyFromName(value: unknown): keyof LabelFields | null {
  if (typeof value !== "string") return null;
  const normalized = normalizeFieldName(value);
  for (const key of Object.keys(fieldAliases) as Array<keyof LabelFields>) {
    if (normalizeFieldName(key) === normalized) return key;
    if (fieldAliases[key].some((alias) => normalizeFieldName(alias) === normalized)) return key;
  }
  return null;
}

function suggestionValue(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value.trim();
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  if (typeof value === "boolean") return value ? "Imported" : "Domestic";
  const source = asRecord(value);
  for (const key of ["value", "text", "raw", "normalized", "display"]) {
    if (!(key in source)) continue;
    const next = suggestionValue(source[key]);
    if (next) return next;
  }
  return "";
}

function suggestionConfidence(value: unknown): number | undefined {
  const source = asRecord(value);
  const confidence = source.confidence ?? source.score;
  return typeof confidence === "number" && Number.isFinite(confidence) ? Math.max(0, Math.min(1, confidence)) : undefined;
}

function suggestionSource(value: unknown): string | undefined {
  const source = asRecord(value);
  const sourceValue = source.source ?? source.provider ?? source.ruleId;
  return typeof sourceValue === "string" && sourceValue.trim() ? sourceValue : undefined;
}

function normalizeSuggestionItem(key: keyof LabelFields, value: unknown): FieldSuggestion | null {
  const nextValue = suggestionValue(value);
  if (!nextValue) return null;
  return {
    key,
    label: fieldLabels[key],
    value: nextValue,
    confidence: suggestionConfidence(value),
    source: suggestionSource(value)
  };
}

function normalizeFieldSuggestions(payload: unknown): FieldSuggestion[] {
  const source = asRecord(payload);
  const rawSuggestions = source.suggestedFields ?? source.fields ?? source.extractedFields ?? source.suggestions ?? payload;
  const suggestions = new Map<keyof LabelFields, FieldSuggestion>();

  if (Array.isArray(rawSuggestions)) {
    rawSuggestions.forEach((item) => {
      const itemRecord = asRecord(item);
      const key = fieldKeyFromName(itemRecord.key ?? itemRecord.field ?? itemRecord.name ?? itemRecord.id);
      if (!key) return;
      const suggestion = normalizeSuggestionItem(key, itemRecord.value ?? itemRecord.text ?? itemRecord.raw ?? item);
      if (suggestion) {
        suggestion.confidence = suggestion.confidence ?? suggestionConfidence(item);
        suggestion.source = suggestion.source ?? suggestionSource(item);
        suggestions.set(key, suggestion);
      }
    });
    return [...suggestions.values()];
  }

  const suggestionRecord = asRecord(rawSuggestions);
  for (const key of Object.keys(fieldAliases) as Array<keyof LabelFields>) {
    const aliases = [key, ...fieldAliases[key]];
    for (const alias of aliases) {
      if (!(alias in suggestionRecord)) continue;
      const suggestion = normalizeSuggestionItem(key, suggestionRecord[alias]);
      if (suggestion) suggestions.set(key, suggestion);
      break;
    }
  }
  return [...suggestions.values()];
}

function normalizeBatchRow(value: unknown, index: number): BatchRow {
  const source = value && typeof value === "object" ? (value as Record<string, unknown>) : {};
  const verdict = readString(source.verdict, "") as Verdict;
  const statusValue = readString(source.status ?? source.state, verdict ?? "QUEUED") as BatchStatus;
  const runId = readString(source.runId, "") || null;
  return {
    id: readString(source.id ?? source.itemId, runId ?? `row-${index + 1}`),
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

function completedRunEventData(run: RunState, cacheHit = false): Record<string, unknown> {
  return {
    runId: run.runId,
    status: run.verdict ?? run.state,
    verdict: run.verdict ?? run.state,
    latencyMs: run.latencyMs ?? null,
    receiptRef: run.receiptRef ?? `/api/receipts/${run.runId}`,
    ...(cacheHit ? { cacheHit: true } : {})
  };
}

async function labelImageFile(sample: Sample): Promise<File> {
  const response = await fetch(sample.imagePath);
  if (!response.ok) return base64ToFile(PNG_1X1_BASE64, `${sample.id}-label.png`);
  const blob = await response.blob();
  return new File([blob], `${sample.id}.png`, { type: blob.type || "image/png" });
}

function App() {
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const batchInputRef = useRef<HTMLInputElement | null>(null);
  const extractionRequestRef = useRef(0);
  const [mode, setMode] = useState<Mode>("single");
  const [file, setFile] = useState<File | null>(null);
  const [batchFiles, setBatchFiles] = useState<File[]>([]);
  const [fields, setFields] = useState<LabelFields>(emptyFields);
  const [fieldSuggestions, setFieldSuggestions] = useState<FieldSuggestion[]>([]);
  const [isExtractingFields, setIsExtractingFields] = useState(false);
  const [extractError, setExtractError] = useState<string | null>(null);
  const [timeline, setTimeline] = useState<TimelineEvent[]>([]);
  const [runState, setRunState] = useState<RunState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isRunning, setIsRunning] = useState(false);
  const [isDragging, setIsDragging] = useState(false);
  const [isPreviewOpen, setIsPreviewOpen] = useState(false);
  const [evidencePreview, setEvidencePreview] = useState<EvidencePreview | null>(null);
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
    if (!batchFiles.length) return "No batch selected";
    if (batchFiles.length === 1) {
      const [selected] = batchFiles;
      const sizeKb = Math.max(1, Math.round(selected.size / 1024));
      return `${selected.name} - ${sizeKb} KB`;
    }
    return `${batchFiles.length}/${MAX_BATCH_FILES} labels selected`;
  }, [batchFiles]);

  const terminalVerdict = runState?.verdict ?? null;
  const completedRunEvent = useMemo(() => [...timeline].reverse().find((item) => item.event === "run.completed") ?? null, [timeline]);
  const displayedRunLatencyMs = readLatencyMs(runState?.latencyMs) ?? readLatencyMs(completedRunEvent?.data.latencyMs);
  const runWasCached = completedRunEvent?.data.cacheHit === true;
  const singleRunChipLabel = isRunning
    ? displayedRunLatencyMs != null
      ? `${displayedRunLatencyMs} ms`
      : "Running"
    : runWasCached && displayedRunLatencyMs === 0
      ? "Cached"
      : displayedRunLatencyMs != null
        ? `${displayedRunLatencyMs} ms`
        : "Ready";

  const filePreviewUrl = useMemo(() => {
    if (!file || !canPreviewFile(file)) return null;
    return URL.createObjectURL(file);
  }, [file]);

  useEffect(() => {
    return () => {
      if (filePreviewUrl) URL.revokeObjectURL(filePreviewUrl);
    };
  }, [filePreviewUrl]);

  useEffect(() => {
    if (!isPreviewOpen && !evidencePreview) return;
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setIsPreviewOpen(false);
        setEvidencePreview(null);
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [evidencePreview, isPreviewOpen]);

  useEffect(() => {
    if (!filePreviewUrl && isPreviewOpen) setIsPreviewOpen(false);
  }, [filePreviewUrl, isPreviewOpen]);

  const filteredBatchRows = useMemo(() => {
    const visibleRows = batchFilter === "ALL" ? batchRows : batchRows.filter((row) => row.verdict === batchFilter || row.status === batchFilter);
    return visibleRows
      .map((row, index) => ({ row, index }))
      .sort((left, right) => batchTriageRank(left.row) - batchTriageRank(right.row) || left.index - right.index)
      .map(({ row }) => row);
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
  const batchProgressMax = Math.max(batchCounts.total, 1);

  function updateField(key: keyof LabelFields, value: string) {
    setFields((current) => ({ ...current, [key]: value }));
  }

  function resetSingleRunState() {
    setTimeline([]);
    setRunState(null);
    setError(null);
    setIsPreviewOpen(false);
    setEvidencePreview(null);
  }

  function resetBatchRunState() {
    setBatchRows([]);
    setBatchTimeline([]);
    setBatchError(null);
    setBatchId(null);
    setBatchExportUrl(null);
  }

  function selectLabelFile(nextFile: File | null, extractFields = true) {
    extractionRequestRef.current += 1;
    setFile(nextFile);
    resetSingleRunState();
    setFieldSuggestions([]);
    setExtractError(null);
    setIsExtractingFields(false);
    if (nextFile && extractFields) void extractSuggestedFields(nextFile, extractionRequestRef.current);
  }

  async function extractSuggestedFields(nextFile: File, requestId: number) {
    setIsExtractingFields(true);
    setExtractError(null);

    const form = new FormData();
    form.append("image", nextFile);

    try {
      const response = await fetchWithTimeout(`${API_BASE}/api/extract`, { method: "POST", body: form });
      if (requestId !== extractionRequestRef.current) return;
      if (response.status === 404 || response.status === 405) {
        throw new Error("Field extraction is not available in this build.");
      }
      if (!response.ok) {
        const payload = await response.json().catch(() => null);
        throw new Error(payload?.error?.message ?? "Field extraction failed.");
      }

      const payload = await response.json();
      const suggestions = normalizeFieldSuggestions(payload);
      setFieldSuggestions(suggestions);
      if (!suggestions.length) setExtractError("No fields were extracted from this image.");
    } catch (caught) {
      if (requestId === extractionRequestRef.current) setExtractError(stringifyError(caught, "Field extraction failed."));
    } finally {
      if (requestId === extractionRequestRef.current) setIsExtractingFields(false);
    }
  }

  function applyFieldSuggestions() {
    setFields((current) => {
      const next = { ...current };
      fieldSuggestions.forEach((suggestion) => {
        next[suggestion.key] = suggestion.value;
      });
      return next;
    });
  }

  async function applySample(sample: Sample) {
    setPreparingSampleId(sample.id);
    setMode("single");
    setFields(sample.fields);
    setFieldSuggestions([]);
    setExtractError(null);
    resetSingleRunState();
    try {
      selectLabelFile(await labelImageFile(sample));
    } finally {
      setPreparingSampleId(null);
    }
  }

  function onFileChange(event: ChangeEvent<HTMLInputElement>) {
    const nextFile = event.target.files?.[0] ?? null;
    selectLabelFile(nextFile);
    event.target.value = "";
  }

  function clearSelectedLabel() {
    selectLabelFile(null, false);
    if (fileInputRef.current) fileInputRef.current.value = "";
  }

  function addBatchFiles(nextFiles: FileList | File[]) {
    const incoming = Array.from(nextFiles);
    if (!incoming.length) return;

    setBatchRows([]);
    setBatchTimeline([]);
    setBatchId(null);
    setBatchExportUrl(null);

    const seen = new Set(batchFiles.map((item) => `${item.name}:${item.size}:${item.lastModified}`));
    const uniqueIncoming = incoming.filter((item) => {
      const key = `${item.name}:${item.size}:${item.lastModified}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
    const accepted = [...batchFiles, ...uniqueIncoming].slice(0, MAX_BATCH_FILES);
    setBatchFiles(accepted);
    setBatchError(batchFiles.length + uniqueIncoming.length > MAX_BATCH_FILES ? `Batch is capped at ${MAX_BATCH_FILES} labels for this prototype.` : null);
  }

  function removeBatchFile(index: number) {
    setBatchFiles((current) => current.filter((_, itemIndex) => itemIndex !== index));
    resetBatchRunState();
  }

  function onBatchFileChange(event: ChangeEvent<HTMLInputElement>) {
    if (event.target.files) addBatchFiles(event.target.files);
    event.target.value = "";
  }

  function onDrop(event: React.DragEvent<HTMLLabelElement>) {
    event.preventDefault();
    setIsDragging(false);
    const nextFile = event.dataTransfer.files?.[0] ?? null;
    if (nextFile) selectLabelFile(nextFile);
  }

  function onBatchDrop(event: React.DragEvent<HTMLLabelElement>) {
    event.preventDefault();
    setIsDraggingBatch(false);
    if (event.dataTransfer.files?.length) addBatchFiles(event.dataTransfer.files);
  }

  async function refreshRun(runId: string) {
    const response = await fetchWithTimeout(`${API_BASE}/api/runs/${runId}`);
    if (!response.ok) return null;
    const payload = (await response.json()) as RunState;
    setRunState(payload);
    return payload;
  }

  function upsertCompletedRunEvent(run: RunState, cacheHit = false) {
    setTimeline((current) => {
      const data = completedRunEventData(run, cacheHit);
      const index = current.findIndex((item) => item.event === "run.completed");
      if (index === -1) {
        return [...current, { id: `run.completed-${current.length}`, event: "run.completed", data }];
      }
      return current.map((item, itemIndex) => (itemIndex === index ? { ...item, data: { ...item.data, ...data } } : item));
    });
  }

  async function refreshBatch(nextBatchId: string) {
    const response = await fetchWithTimeout(`${API_BASE}/api/batches/${nextBatchId}`);
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
    setEvidencePreview(null);

    const form = new FormData();
    form.append("image", file);
    form.append("application_data", JSON.stringify(applicationPayload(fields)));

    try {
      const response = await fetchWithTimeout(`${API_BASE}/api/runs`, {
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

      if (payload.cacheHit) {
        const cachedRun = await refreshRun(payload.runId);
        setTimeline([
          { id: "run.created-0", event: "run.created", data: { runId: payload.runId, cacheHit: true } },
          {
            id: "run.completed-1",
            event: "run.completed",
            data: cachedRun
              ? completedRunEventData(cachedRun, true)
              : {
                  runId: payload.runId,
                  status: "complete",
                  verdict: "complete",
                  latencyMs: 0,
                  receiptRef: payload.receiptUrl ?? `/api/receipts/${payload.runId}`,
                  cacheHit: true
                }
          }
        ]);
        setIsRunning(false);
        return;
      }

      const source = new EventSource(`${API_BASE}${payload.eventsUrl}`);
      source.onmessage = () => undefined;
      let isSettled = false;
      let watchdogId: number | undefined;

      async function settleRun(message?: string) {
        if (isSettled) return;
        isSettled = true;
        if (watchdogId !== undefined) window.clearTimeout(watchdogId);
        source.close();
        const latestRun = await refreshRun(payload.runId);
        if (latestRun?.verdict) upsertCompletedRunEvent(latestRun);
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
      const response = await fetchWithTimeout(receiptRef.startsWith("http") ? receiptRef : `${API_BASE}${receiptRef}`);
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
      "batch.item.queued",
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
    if (!batchFiles.length) {
      setBatchError("Select up to 10 label images or a batch artifact first.");
      return;
    }

    setBatchError(null);
    setBatchRows([]);
    setBatchTimeline([]);
    setBatchId(null);
    setBatchExportUrl(null);
    setIsBatchRunning(true);

    const form = new FormData();
    batchFiles.forEach((selectedFile) => form.append("files", selectedFile));
    form.append("application_data", JSON.stringify(applicationPayload(fields)));

    try {
      const response = await fetchWithTimeout(`${API_BASE}/api/batches`, {
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
    setMode("batch");
    setBatchFiles([]);
    setBatchError(null);
    setBatchTimeline([]);
    setBatchRows([]);
    setBatchId(null);
    setBatchExportUrl(null);
    setIsBatchRunning(true);

    try {
      const response = await fetchWithTimeout(`${API_BASE}/api/batches/demo`, { method: "POST" });

      if (!response.ok) {
        const payload = await response.json().catch(() => null);
        throw new Error(payload?.error?.message ?? "Server demo batch is not available.");
      }

      const payload = (await response.json()) as BatchResponse;
      setBatchId(payload.batchId);
      setBatchExportUrl(payload.exportUrl ?? `/api/batches/${payload.batchId}/export.csv`);
      const rows = normalizeBatchRows(payload);
      if (rows.length) setBatchRows(rows);
      else await refreshBatch(payload.batchId);
      if (payload.eventsUrl) {
        connectBatchEvents(payload.batchId, payload.eventsUrl);
        return;
      }
    } catch (caught) {
      setBatchError(stringifyError(caught, "Server demo batch is not available."));
    }

    setIsBatchRunning(false);
  }

  async function exportBatchCsv() {
    try {
      if (batchExportUrl) {
        const response = await fetchWithTimeout(batchExportUrl.startsWith("http") ? batchExportUrl : `${API_BASE}${batchExportUrl}`);
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
            {mode === "batch" ? `${batchProgress}%` : singleRunChipLabel}
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
                  capture="environment"
                  onChange={onFileChange}
                />
                <span className="drop-icon">
                  <UploadCloud size={34} aria-hidden="true" />
                </span>
                <span className="drop-title">Drop label image</span>
                <span className="drop-file">{selectedFileLabel}</span>
              </label>

              {file ? (
                <div className="upload-preview" aria-live="polite">
                  <button
                    className="preview-thumb"
                    type="button"
                    disabled={!filePreviewUrl}
                    aria-label={filePreviewUrl ? `Expand uploaded label ${file.name}` : `Preview unavailable for ${file.name}`}
                    onClick={() => setIsPreviewOpen(true)}
                  >
                    {filePreviewUrl ? <img src={filePreviewUrl} alt="" /> : <FileImage size={30} aria-hidden="true" />}
                    {filePreviewUrl ? (
                      <span className="preview-expand" aria-hidden="true">
                        <Maximize2 size={14} />
                        Expand
                      </span>
                    ) : null}
                  </button>
                  <div className="preview-details">
                    <p>Selected label</p>
                    <strong>{file.name}</strong>
                    <span>{filePreviewUrl ? "Click the thumbnail to inspect before verifying." : "Preview unavailable for this file type; filename is retained for the receipt."}</span>
                    <div className="preview-actions">
                      <button className="secondary-button compact" type="button" disabled={isRunning} onClick={() => fileInputRef.current?.click()}>
                        <UploadCloud size={16} aria-hidden="true" />
                        Replace image
                      </button>
                      <button className="mini-icon-button" type="button" disabled={isRunning} aria-label={`Remove ${file.name}`} title="Remove image" onClick={clearSelectedLabel}>
                        <X size={16} aria-hidden="true" />
                      </button>
                    </div>
                  </div>
                </div>
              ) : null}

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
                      <img src={sample.imagePath} alt="" loading="lazy" decoding="async" />
                    </span>
                    <span className="sample-name">{sample.name}</span>
                    <span className="sample-meta">{sample.trap}</span>
                    <span className={`sample-verdict ${classForStatus(sample.expected)}`}>{verdictMeta[sample.expected].label}</span>
                  </button>
                ))}
              </div>
            </section>

            <section className="fields-band" aria-label="Application fields">
              <SuggestedFieldsPanel
                suggestions={fieldSuggestions}
                isExtracting={isExtractingFields}
                error={extractError}
                onApply={applyFieldSuggestions}
                onDismiss={() => {
                  setFieldSuggestions([]);
                  setExtractError(null);
                }}
              />
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

          {terminalVerdict === "UNREADABLE" && runState?.findings.length ? <RetakeGuidance findings={runState.findings} /> : null}

          <section className="results-grid">
            <div className="findings-panel">
              <div className="section-heading">
                <FileText size={20} aria-hidden="true" />
                <h2>Findings</h2>
              </div>
              {runState?.findings.length ? (
                <div className="finding-list">
                  {runState.findings.map((finding) => {
                    const label = ruleLabel(finding.ruleId);
                    const cropUri = finding.evidence?.cropUri ?? null;
                    return (
                      <article className={`finding-item ${finding.status.toLowerCase()}`} key={finding.ruleId}>
                        <div className="finding-summary">
                          <div>
                            <h3>{label}</h3>
                            <p>{findingSentence(finding)}</p>
                          </div>
                          <span className={`status-pill ${classForStatus(finding.status as TerminalVerdict)}`}>{labelForStatus(finding.status)}</span>
                        </div>
                        {finding.remediation && finding.status !== "PASS" ? <p className="remediation-note">{finding.remediation}</p> : null}
                        {cropUri ? (
                          <button
                            className="evidence-crop-button"
                            type="button"
                            onClick={() => setEvidencePreview({ src: cropUri, label: `${label} evidence crop` })}
                          >
                            <img src={cropUri} alt="" />
                            <span>
                              <Maximize2 size={15} aria-hidden="true" />
                              Zoom evidence crop
                            </span>
                          </button>
                        ) : null}
                        <details className="finding-details">
                          <summary>Technical details</summary>
                          <dl className="finding-metrics">
                            <div>
                              <dt>Expected</dt>
                              <dd>{formatFindingValue(finding.expected)}</dd>
                            </div>
                            <div>
                              <dt>Observed</dt>
                              <dd>{formatFindingValue(finding.observed)}</dd>
                            </div>
                            <div>
                              <dt>Confidence</dt>
                              <dd>{finding.confidence != null ? `${Math.round(finding.confidence * 100)}%` : "None"}</dd>
                            </div>
                          </dl>
                          {finding.evidence?.text ? <p className="evidence-text">{finding.evidence.text}</p> : null}
                          <pre className="finding-raw">{JSON.stringify(finding, null, 2)}</pre>
                        </details>
                      </article>
                    );
                  })}
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
                <input ref={batchInputRef} type="file" accept=".zip,.csv,image/png,image/jpeg,image/webp,application/zip" multiple onChange={onBatchFileChange} />
                <span className="drop-icon">
                  <UploadCloud size={34} aria-hidden="true" />
                </span>
                <span className="drop-title">Drop labels or batch artifact</span>
                <span className="drop-file">{selectedBatchLabel}</span>
              </label>
              {batchFiles.length ? (
                <div className="batch-file-list" aria-label="Queued labels">
                  {batchFiles.map((selectedFile, index) => {
                    const sizeKb = Math.max(1, Math.round(selectedFile.size / 1024));
                    return (
                      <div className="batch-file-item" key={`${selectedFile.name}-${selectedFile.size}-${selectedFile.lastModified}`}>
                        <span>
                          <strong>{selectedFile.name}</strong>
                          <small>{sizeKb} KB</small>
                        </span>
                        <button
                          className="mini-icon-button"
                          type="button"
                          aria-label={`Remove ${selectedFile.name}`}
                          title="Remove"
                          disabled={isBatchRunning}
                          onClick={() => removeBatchFile(index)}
                        >
                          <X size={16} aria-hidden="true" />
                        </button>
                      </div>
                    );
                  })}
                </div>
              ) : null}
              <div className="batch-actions">
                <button className="secondary-button" type="button" disabled={isBatchRunning} onClick={() => batchInputRef.current?.click()}>
                  <UploadCloud size={20} aria-hidden="true" />
                  Add images
                </button>
                <button className="verify-button" type="submit" disabled={isBatchRunning || !batchFiles.length}>
                  {isBatchRunning ? <Loader2 className="spin" size={22} aria-hidden="true" /> : <Play size={22} aria-hidden="true" />}
                  Start batch
                </button>
                <button className="secondary-button" type="button" onClick={() => void runDemoBatch()} disabled={isBatchRunning}>
                  Run server demo
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
                {batchCounts.completed}/{batchCounts.total}
              </strong>
            </div>
            <progress value={batchCounts.completed} max={batchProgressMax} />
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

              <div className="batch-table-wrap" tabIndex={0} aria-label="Batch results table">
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

      {isPreviewOpen && filePreviewUrl ? (
        <div className="preview-dialog-backdrop" role="presentation" onClick={() => setIsPreviewOpen(false)}>
          <div className="preview-dialog" role="dialog" aria-modal="true" aria-label="Uploaded label preview" onClick={(event) => event.stopPropagation()}>
            <div className="preview-dialog-bar">
              <strong>{file?.name ?? "Uploaded label"}</strong>
              <button className="mini-icon-button" type="button" aria-label="Close preview" onClick={() => setIsPreviewOpen(false)}>
                <X size={18} aria-hidden="true" />
              </button>
            </div>
            <img src={filePreviewUrl} alt={`Uploaded label preview for ${file?.name ?? "selected label"}`} />
          </div>
        </div>
      ) : null}

      {evidencePreview ? (
        <div className="preview-dialog-backdrop" role="presentation" onClick={() => setEvidencePreview(null)}>
          <div className="preview-dialog" role="dialog" aria-modal="true" aria-label={evidencePreview.label} onClick={(event) => event.stopPropagation()}>
            <div className="preview-dialog-bar">
              <strong>{evidencePreview.label}</strong>
              <button className="mini-icon-button" type="button" aria-label="Close evidence preview" onClick={() => setEvidencePreview(null)}>
                <X size={18} aria-hidden="true" />
              </button>
            </div>
            <img src={evidencePreview.src} alt={evidencePreview.label} />
          </div>
        </div>
      ) : null}
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
        Product type
        <select value={fields.commodity} onChange={(event) => updateField("commodity", event.target.value)}>
          <option value="">Select type</option>
          <option value="spirits">Spirits</option>
          <option value="wine">Wine</option>
          <option value="malt">Malt beverage</option>
        </select>
      </label>
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
      <label>
        Country of origin
        <input value={fields.countryOfOrigin} onChange={(event) => updateField("countryOfOrigin", event.target.value)} autoComplete="off" />
      </label>
      <label className="wide">
        Producer / bottler
        <input value={fields.producerName} onChange={(event) => updateField("producerName", event.target.value)} autoComplete="off" />
      </label>
      <label>
        Producer city
        <input value={fields.producerCity} onChange={(event) => updateField("producerCity", event.target.value)} autoComplete="off" />
      </label>
      <label>
        Producer state
        <input value={fields.producerState} onChange={(event) => updateField("producerState", event.target.value)} autoComplete="off" />
      </label>
    </div>
  );
}

function SuggestedFieldsPanel({
  suggestions,
  isExtracting,
  error,
  onApply,
  onDismiss
}: {
  suggestions: FieldSuggestion[];
  isExtracting: boolean;
  error: string | null;
  onApply: () => void;
  onDismiss: () => void;
}) {
  if (!isExtracting && !suggestions.length && !error) return null;
  return (
    <div className="suggestion-panel" aria-live="polite">
      <div className="suggestion-panel-header">
        <div>
          <strong>Extracted fields</strong>
          <span>{isExtracting ? "Reading label text" : suggestions.length ? `${suggestions.length} suggestions` : "No suggestions"}</span>
        </div>
        <button className="mini-icon-button" type="button" aria-label="Dismiss extracted fields" onClick={onDismiss}>
          <X size={18} aria-hidden="true" />
        </button>
      </div>
      {isExtracting ? (
        <div className="suggestion-status">
          <Loader2 className="spin" size={18} aria-hidden="true" />
          <span>Extracting</span>
        </div>
      ) : null}
      {error ? (
        <div className="suggestion-status warning">
          <AlertTriangle size={18} aria-hidden="true" />
          <span>{error}</span>
        </div>
      ) : null}
      {suggestions.length ? (
        <>
          <div className="suggestion-list">
            {suggestions.map((suggestion) => (
              <div className="suggestion-item" key={suggestion.key}>
                <span>{suggestion.label}</span>
                <strong>{suggestion.value}</strong>
                {suggestion.confidence !== undefined ? <small>{Math.round(suggestion.confidence * 100)}%</small> : null}
              </div>
            ))}
          </div>
          <p className="suggestion-help">Save extracted values to the application fields, then adjust anything before verifying.</p>
          <button className="secondary-button compact" type="button" onClick={onApply}>
            <CheckCircle2 size={18} aria-hidden="true" />
            Save extracted fields
          </button>
        </>
      ) : null}
    </div>
  );
}

function RetakeGuidance({ findings }: { findings: Finding[] }) {
  const readFields = findings.filter((finding) => finding.status === "PASS").map((finding) => ruleLabel(finding.ruleId));
  const retakeFields = findings
    .filter((finding) => finding.status === "UNREADABLE" || finding.ruleId === "IMAGE_READABILITY")
    .map((finding) => ruleLabel(finding.ruleId));

  return (
    <section className="retake-guidance" aria-label="Retake guidance">
      <div>
        <strong>What the verifier could read</strong>
        {readFields.length ? (
          <ul>
            {readFields.map((label) => (
              <li key={label}>{label}</li>
            ))}
          </ul>
        ) : (
          <p>No required fields were read confidently.</p>
        )}
      </div>
      <div>
        <strong>What needs a clearer photo</strong>
        {retakeFields.length ? (
          <ul>
            {retakeFields.map((label) => (
              <li key={label}>{label}</li>
            ))}
          </ul>
        ) : (
          <p>Review the findings below before deciding whether to retake.</p>
        )}
      </div>
    </section>
  );
}

type TimelineTone = "pass" | "fail" | "review" | "pending" | "unreadable";

function toneForStatus(status: unknown): TimelineTone {
  if (status === "PASS") return "pass";
  if (status === "FAIL" || status === "ERROR") return "fail";
  if (status === "NEEDS_REVIEW") return "review";
  if (status === "UNREADABLE") return "unreadable";
  return "pending";
}

function timelineTone(item: TimelineEvent): TimelineTone {
  if (item.event.includes("failed")) return "fail";
  if (item.event === "run.completed") return toneForStatus(item.data.verdict ?? item.data.status);
  if (item.event === "rule.evaluated") return toneForStatus(item.data.status);
  if (item.event.includes("completed")) return "pass";
  if (item.event === "run.created" || item.event === "field.extracted" || item.event === "batch.created" || item.event === "batch.item.queued") return "pass";
  if (item.event.includes("escalated") || item.event.includes("opinion")) return "review";
  return "pending";
}

function timelineLabel(eventName: string) {
  const labels: Record<string, string> = {
    "run.created": "Run received",
    "preprocess.completed": "Image prepared",
    "ocr.completed": "OCR completed",
    "field.extracted": "Field extracted",
    "rule.evaluated": "Rule evaluated",
    "run.escalated": "Review requested",
    "agent.spawned": "Reviewer spawned",
    "agent.opinion": "Reviewer opinion",
    "run.completed": "Run completed",
    "batch.created": "Batch received",
    "batch.item.queued": "Label queued",
    "batch.item.started": "Label started",
    "batch.item.completed": "Label completed",
    "batch.item.failed": "Label failed",
    "batch.completed": "Batch completed",
    "batch.failed": "Batch failed"
  };
  return labels[eventName] ?? eventName;
}

function timelineSummary(item: TimelineEvent) {
  if (item.event === "ocr.completed") {
    const confidence = typeof item.data.confidence === "number" ? `, ${Math.round(item.data.confidence * 100)}% confidence` : "";
    const latency = typeof item.data.latencyMs === "number" ? `, ${item.data.latencyMs} ms` : "";
    return `${String(item.data.provider ?? "OCR")}${confidence}${latency}`;
  }
  if (item.event === "field.extracted") {
    const field = String(item.data.field ?? "field");
    const value = item.data.value == null || item.data.value === "" ? "no value" : String(item.data.value);
    return `${field}: ${value}`;
  }
  if (item.event === "rule.evaluated") {
    const rule = item.data.ruleId ? ruleLabel(String(item.data.ruleId)) : "Rule";
    return `${rule}: ${String(item.data.status ?? "pending")}`;
  }
  if (item.event === "run.completed") {
    const verdict = String(item.data.verdict ?? item.data.status ?? "complete");
    if (item.data.cacheHit === true && readLatencyMs(item.data.latencyMs) === 0) return `${verdict} from cached result`;
    const latencyMs = readLatencyMs(item.data.latencyMs);
    const latency = latencyMs != null ? ` in ${latencyMs} ms` : "";
    return `${verdict}${latency}`;
  }
  if (item.data.runId) return `Run ${String(item.data.runId).slice(0, 8)}`;
  if (item.data.fileName) return String(item.data.fileName);
  return "Waiting for the next event";
}

function TimelinePanel({ items, title = "Verification Steps" }: { items: TimelineEvent[]; title?: string }) {
  return (
    <div className="timeline-panel">
      <div className="section-heading">
        <Clock3 size={20} aria-hidden="true" />
        <h2>{title}</h2>
      </div>
      <ol className="timeline-list">
        {items.length ? (
          items.map((item) => {
            const tone = timelineTone(item);
            const Icon = tone === "fail" ? XCircle : tone === "pending" ? Loader2 : tone === "review" || tone === "unreadable" ? AlertTriangle : CheckCircle2;
            return (
              <li className={`timeline-item ${tone}`} key={item.id}>
                <span className="timeline-icon" aria-hidden="true">
                  <Icon className={tone === "pending" ? "spin" : undefined} size={15} />
                </span>
                <div className="timeline-copy">
                  <strong>{timelineLabel(item.event)}</strong>
                  <span>{timelineSummary(item)}</span>
                  <details>
                    <summary>Event data</summary>
                    <code>{JSON.stringify(item.data, null, 2)}</code>
                  </details>
                </div>
              </li>
            );
          })
        ) : (
          <li className="timeline-item pending">
            <span className="timeline-icon" aria-hidden="true">
              <Clock3 size={15} />
            </span>
            <div className="timeline-copy">
              <strong>Ready</strong>
              <span>Events will appear as the verifier reads the label.</span>
            </div>
          </li>
        )}
      </ol>
    </div>
  );
}

export default App;
