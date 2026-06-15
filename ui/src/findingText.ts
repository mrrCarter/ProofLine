type FindingTextInput = {
  ruleId: string;
  status?: string;
  expected?: unknown;
  observed?: unknown;
  explanation?: string;
};

type DataRecord = Record<string, unknown>;

const ruleLabels: Record<string, string> = {
  BRAND_NAME_MATCH: "Brand name",
  CLASS_TYPE_MATCH: "Class / type",
  ALCOHOL_CONTENT_MATCH: "Alcohol content",
  NET_CONTENTS_MATCH: "Net contents",
  NAME_ADDRESS_PRESENT: "Producer name and address",
  COUNTRY_OF_ORIGIN_IF_IMPORT: "Country of origin",
  GOVERNMENT_WARNING_PRESENT: "Government warning",
  GOVERNMENT_WARNING_EXACT_TEXT: "Warning text",
  GOVERNMENT_WARNING_FORMAT_SIGNAL: "Warning format",
  IMAGE_READABILITY: "Image readability"
};

function asRecord(value: unknown): DataRecord {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as DataRecord) : {};
}

function pickString(source: DataRecord, keys: string[], fallback = "not found"): string {
  for (const key of keys) {
    const value = source[key];
    if (typeof value === "string" && value.trim()) return value;
    if (typeof value === "number" && Number.isFinite(value)) return String(value);
    if (typeof value === "boolean") return value ? "yes" : "no";
  }
  return fallback;
}

function pickNumber(source: DataRecord, keys: string[]): number | null {
  for (const key of keys) {
    const value = source[key];
    if (typeof value === "number" && Number.isFinite(value)) return value;
  }
  return null;
}

function formatNumber(value: number | null, digits = 2): string | null {
  if (value === null) return null;
  return Number.isInteger(value) ? String(value) : value.toFixed(digits).replace(/0+$/, "").replace(/\.$/, "");
}

function formatAbv(value: number | null): string | null {
  const formatted = formatNumber(value, 2);
  return formatted ? `${formatted}% ABV` : null;
}

function formatMl(value: number | null): string | null {
  const formatted = formatNumber(value, 1);
  return formatted ? `${formatted} ml` : null;
}

function normalizeStatus(status: string | undefined): "PASS" | "FAIL" | "NEEDS_REVIEW" | "UNREADABLE" | "ERROR" | string {
  return status ?? "NEEDS_REVIEW";
}

function compareTextSentence(label: string, finding: FindingTextInput): string {
  const expected = asRecord(finding.expected);
  const observed = asRecord(finding.observed);
  const expectedText = pickString(expected, ["raw", "text", "normalized"], "the application value");
  const observedText = pickString(observed, ["raw", "text", "normalized"], "no matching label text");
  const score = formatNumber(pickNumber(observed, ["score"]), 2);
  const scoreText = score ? ` with match score ${score}` : "";
  if (normalizeStatus(finding.status) === "PASS") {
    return `${label} matches: the application says "${expectedText}" and the label says "${observedText}"${scoreText}.`;
  }
  return `${label} needs attention: the application says "${expectedText}", but the best label match was "${observedText}"${scoreText}.`;
}

export function ruleLabel(ruleId: string): string {
  return ruleLabels[ruleId] ?? ruleId.replaceAll("_", " ").toLowerCase().replace(/^\w/, (match) => match.toUpperCase());
}

export function formatFindingValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "None";
  if (typeof value === "string") return value;
  if (typeof value === "number") return Number.isInteger(value) ? String(value) : String(Number(value.toFixed(4)));
  if (typeof value === "boolean") return value ? "Yes" : "No";
  return JSON.stringify(value);
}

export function findingSentence(finding: FindingTextInput): string {
  const expected = asRecord(finding.expected);
  const observed = asRecord(finding.observed);
  const status = normalizeStatus(finding.status);

  switch (finding.ruleId) {
    case "BRAND_NAME_MATCH":
      return compareTextSentence("Brand name", finding);

    case "CLASS_TYPE_MATCH":
      return compareTextSentence("Class/type", finding);

    case "ALCOHOL_CONTENT_MATCH": {
      const expectedRaw = pickString(expected, ["raw"], "the application alcohol content");
      const observedRaw = pickString(observed, ["raw"], "no alcohol content on the label");
      const expectedAbv = formatAbv(pickNumber(expected, ["abv"]));
      const observedAbv = formatAbv(pickNumber(observed, ["abv"]));
      const expectedText = expectedAbv ? `${expectedRaw} = ${expectedAbv}` : expectedRaw;
      const observedText = observedAbv ? `${observedRaw} = ${observedAbv}` : observedRaw;
      if (status === "PASS") return `Alcohol content matches: label says ${observedText}, matching the application's ${expectedText}.`;
      return `Alcohol content differs: label says ${observedText}, while the application says ${expectedText}.`;
    }

    case "NET_CONTENTS_MATCH": {
      const expectedRaw = pickString(expected, ["raw"], "the application net contents");
      const observedRaw = pickString(observed, ["raw"], "no net contents on the label");
      const expectedMl = formatMl(pickNumber(expected, ["ml"]));
      const observedMl = formatMl(pickNumber(observed, ["ml"]));
      const expectedText = expectedMl ? `${expectedRaw} (${expectedMl})` : expectedRaw;
      const observedText = observedMl ? `${observedRaw} (${observedMl})` : observedRaw;
      if (status === "PASS") return `Net contents match after unit conversion: label says ${observedText}, matching ${expectedText}.`;
      return `Net contents differ: label says ${observedText}, while the application says ${expectedText}.`;
    }

    case "NAME_ADDRESS_PRESENT": {
      const missing = Array.isArray(observed.missing) ? observed.missing.join(", ") : "";
      if (status === "PASS") return "Producer or bottler name and address signals are present on the label.";
      return missing ? `Producer or bottler address needs attention: missing ${missing}.` : "Producer or bottler address needs review against the application.";
    }

    case "COUNTRY_OF_ORIGIN_IF_IMPORT": {
      const imported = expected.imported;
      const country = pickString(expected, ["country"], "");
      if (imported === false) return "Country of origin is not required because the application marks the product domestic.";
      if (!country) return "Imported product needs a country of origin in the application before the label can be checked.";
      if (status === "PASS") return `Country of origin is present: the application says ${country}, and the label includes it.`;
      return `Country of origin is missing: the application says ${country}, but the label text did not show it.`;
    }

    case "GOVERNMENT_WARNING_PRESENT": {
      if (status === "PASS") return "Government warning anchor is present on the label.";
      return "Government warning anchor was not detected on the label.";
    }

    case "GOVERNMENT_WARNING_EXACT_TEXT": {
      const prefix = pickString(observed, ["prefix"], "no warning prefix");
      if (status === "PASS") return "Government warning text matches the pinned statement after whitespace normalization.";
      if (status === "NEEDS_REVIEW") return `Warning prefix was read as "${prefix}", but OCR confidence is low enough to require review.`;
      return `Government warning text does not match the pinned statement; the prefix was read as "${prefix}".`;
    }

    case "GOVERNMENT_WARNING_FORMAT_SIGNAL": {
      const signal = pickString(observed, ["boldSignal"], "indeterminate");
      const source = pickString(observed, ["signalSource"], "pipeline");
      const caveat = observed.passWithCaveat === true;
      if (status === "PASS" && caveat) return "Warning format passes with caveat: exact text, uppercase prefix, and high OCR confidence were verified.";
      if (status === "PASS") return `Warning format passes: the ${source} bold signal is ${signal}.`;
      return `Warning format needs review: the computed bold signal is ${signal}.`;
    }

    case "IMAGE_READABILITY": {
      const scoreNum = pickNumber(observed, ["readabilityScore"]);
      const floorNum = pickNumber(expected, ["minimumReadabilityScore"]);
      const score = formatNumber(scoreNum, 2) ?? "unknown";
      const floor = formatNumber(floorNum, 2) ?? "required";
      if (status === "PASS") return `Image readability is sufficient: score ${score} meets the ${floor} minimum.`;
      // Non-PASS can also mean a required warning region was not readable.
      const knownScores = scoreNum !== null && floorNum !== null;
      if (knownScores && scoreNum >= floorNum) {
        return `Image is globally readable (score ${score} meets the ${floor} minimum), but the required government warning could not be read for an automated decision; reshoot or crop the warning panel.`;
      }
      if (status === "NEEDS_REVIEW") {
        return `Image readability is below the ${floor} minimum (score ${score}), but required anchors were detected, so human review is required.`;
      }
      return `Image is unreadable for automated review: score ${score} is below the ${floor} minimum.`;
    }

    default:
      if (finding.explanation) return finding.explanation;
      return `${ruleLabel(finding.ruleId)} result: expected ${formatFindingValue(finding.expected)}, observed ${formatFindingValue(finding.observed)}.`;
  }
}
