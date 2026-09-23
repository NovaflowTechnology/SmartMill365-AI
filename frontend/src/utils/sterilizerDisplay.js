function cleanValue(value) {
  if (value === null || value === undefined) return "";
  return String(value).trim();
}

function normaliseName(value) {
  return cleanValue(value)
    .toLowerCase()
    .replace(/\.json$/i, "")
    .replace(/\s+/g, "_");
}

function isKnownTagId(value) {
  return Boolean(cleanValue(value));
}

function getRawBenchmarkTagId(benchmark) {
  if (!benchmark) return "";

  return cleanValue(
    benchmark.tag_id ||
      benchmark.tagId ||
      benchmark.sterilizer_id ||
      benchmark.sterilizerId ||
      benchmark.id ||
      benchmark.display_tag_id ||
      benchmark.inferred_tag_id ||
      benchmark.inferredTagId
  );
}

/* =========================================================
   BASIC STERILIZER DISPLAY HELPERS
========================================================= */

export function getSterilizerDisplayName(tagId, field, plantDisplayName = "") {
  const cleanTagId = cleanValue(tagId);
  const cleanPlantName = cleanValue(plantDisplayName);
  const cleanField = cleanValue(field);

  if (!cleanTagId) return "No sterilizer selected";
  const plantIdentity = cleanPlantName && cleanPlantName !== cleanTagId
    ? `${cleanPlantName} (${cleanTagId})`
    : cleanTagId;
  const sterilizerName = getSterilizerName(cleanTagId, cleanField);
  return sterilizerName && sterilizerName !== "-"
    ? `${plantIdentity} [${sterilizerName}]`
    : plantIdentity;
}

export function getTagShortName(tagId) {
  const cleanTagId = cleanValue(tagId);
  return cleanTagId || "-";
}

export function getSterilizerName(tagId, field) {
  void tagId;
  const cleanField = cleanValue(field);

  if (!cleanField) return "-";

  const match = /^stp(\d+)$/i.exec(cleanField);
  return match ? `Sterilizer ${Number(match[1])}` : cleanField;
}

/* =========================================================
   BENCHMARK DISPLAY HELPERS

   Purpose:
   Show:
   SAMYSK_POM_240004, Sterilizer 3

   Instead of:
   SAMYSK_POM_240004, stp3

   Important:
   Older adjusted benchmarks may not store id / sterilizer_id.
   This file can infer the missing tag from the original benchmark name
   or adjusted_from field.
========================================================= */

export function getBenchmarkTagId(benchmark) {
  return getRawBenchmarkTagId(benchmark);
}

export function getBenchmarkField(benchmark) {
  if (!benchmark) return "";

  return cleanValue(
    benchmark.field ||
      benchmark.channel ||
      benchmark.field_name ||
      benchmark.source_field
  );
}

export function getBenchmarkName(benchmark) {
  if (!benchmark) return "";

  return cleanValue(
    benchmark.benchmark_name ||
      benchmark.name ||
      benchmark.display_name ||
      benchmark.file_name
  );
}

export function getBenchmarkFileName(benchmark) {
  if (!benchmark) return "";

  return cleanValue(
    benchmark.file_name ||
      benchmark.filename ||
      benchmark.saved_file_name ||
      benchmark.path
  );
}

function benchmarkHasKnownTag(benchmark) {
  return isKnownTagId(getBenchmarkTagId(benchmark));
}

function findSourceBenchmarkForAdjustedBenchmark(benchmark, benchmarkOptions = []) {
  if (!benchmark || !Array.isArray(benchmarkOptions) || benchmarkOptions.length === 0) {
    return null;
  }

  const currentName = normaliseName(getBenchmarkName(benchmark));
  const currentFileName = normaliseName(getBenchmarkFileName(benchmark));
  const adjustedFrom = normaliseName(benchmark.adjusted_from || benchmark.adjustedFrom);
  const currentField = getBenchmarkField(benchmark);

  const candidates = benchmarkOptions.filter((item) => {
    if (!item || item === benchmark) return false;
    if (!benchmarkHasKnownTag(item)) return false;

    const itemField = getBenchmarkField(item);
    if (currentField && itemField && currentField !== itemField) return false;

    return true;
  });

  if (adjustedFrom) {
    const byAdjustedFrom = candidates.find((item) => {
      const itemName = normaliseName(getBenchmarkName(item));
      const itemFileName = normaliseName(getBenchmarkFileName(item));

      return itemName === adjustedFrom || itemFileName === adjustedFrom;
    });

    if (byAdjustedFrom) return byAdjustedFrom;
  }

  // Example:
  // Testing_Benchmark_adjusted2 should inherit display context
  // from Testing_Benchmark.
  const byNamePrefix = candidates
    .filter((item) => {
      const itemName = normaliseName(getBenchmarkName(item));
      if (!itemName) return false;

      return (
        currentName.startsWith(`${itemName}_adjusted`) ||
        currentFileName.startsWith(`${itemName}_adjusted`)
      );
    })
    .sort(
      (a, b) =>
        normaliseName(getBenchmarkName(b)).length -
        normaliseName(getBenchmarkName(a)).length
    )[0];

  if (byNamePrefix) return byNamePrefix;

  return null;
}

export function enrichBenchmarkWithInferredDisplayContext(
  benchmark,
  benchmarkOptions = []
) {
  if (!benchmark) return benchmark;

  const tagId = getBenchmarkTagId(benchmark);
  const field = getBenchmarkField(benchmark);

  if (isKnownTagId(tagId)) {
    return {
      ...benchmark,
      tag_id: benchmark.tag_id || tagId,
      id: benchmark.id || tagId,
      sterilizer_id: benchmark.sterilizer_id || tagId,
      field,
      display_context_source:
        benchmark.display_context_source || "benchmark_metadata",
    };
  }

  const sourceBenchmark = findSourceBenchmarkForAdjustedBenchmark(
    benchmark,
    benchmarkOptions
  );

  if (!sourceBenchmark) {
    return benchmark;
  }

  const sourceTagId = getBenchmarkTagId(sourceBenchmark);
  const sourceField = getBenchmarkField(sourceBenchmark);

  return {
    ...benchmark,
    tag_id: benchmark.tag_id || sourceTagId,
    id: benchmark.id || sourceTagId,
    sterilizer_id: benchmark.sterilizer_id || sourceTagId,
    field: field || sourceField,
    inferred_tag_id: sourceTagId,
    inferred_from_benchmark:
      getBenchmarkName(sourceBenchmark) || getBenchmarkFileName(sourceBenchmark),
    display_context_source: "inferred_from_original_benchmark",
  };
}

export function enrichBenchmarksWithInferredDisplayContext(benchmarks = []) {
  if (!Array.isArray(benchmarks)) return [];

  return benchmarks.map((item) =>
    enrichBenchmarkWithInferredDisplayContext(item, benchmarks)
  );
}

export function getBenchmarkTagName(benchmark) {
  return getTagShortName(getBenchmarkTagId(benchmark));
}

export function getBenchmarkSterilizerName(benchmark) {
  return getSterilizerName(
    getBenchmarkTagId(benchmark),
    getBenchmarkField(benchmark)
  );
}

export function getBenchmarkDisplayParts(benchmark, benchmarkOptions = []) {
  const enriched = enrichBenchmarkWithInferredDisplayContext(
    benchmark,
    benchmarkOptions
  );

  const tagId = getBenchmarkTagId(enriched);
  const field = getBenchmarkField(enriched);
  const tagName = getTagShortName(tagId);
  const sterilizerName = getSterilizerName(tagId, field);

  return {
    benchmarkName: getBenchmarkName(enriched),
    fileName: getBenchmarkFileName(enriched),
    tagId,
    field,

    // Keep both names so old/new components both work.
    tagName,
    tagShortName: tagName,

    sterilizerName,
    displayContextSource:
      enriched?.display_context_source || "benchmark_metadata",
    inferredFromBenchmark: enriched?.inferred_from_benchmark || "",
  };
}

export function formatBenchmarkOptionLabel(benchmark, options = {}) {
  const { includeFileName = false, benchmarkOptions = [] } = options;

  const { benchmarkName, fileName, tagName, sterilizerName } =
    getBenchmarkDisplayParts(benchmark, benchmarkOptions);

  const name = benchmarkName || fileName || "Unnamed benchmark";

  const contextParts = [];

  if (tagName && tagName !== "-") {
    contextParts.push(tagName);
  }

  if (sterilizerName && sterilizerName !== "-") {
    contextParts.push(sterilizerName);
  }

  const context = contextParts.length ? ` (${contextParts.join(", ")})` : "";

  const filePart =
    includeFileName && fileName && fileName !== name ? ` — ${fileName}` : "";

  return `${name}${context}${filePart}`;
}

export function formatBenchmarkShortLabel(fileName, benchmarkOptions = []) {
  if (!fileName) return "";

  const matchedBenchmark = benchmarkOptions.find(
    (item) => getBenchmarkFileName(item) === fileName
  );

  if (!matchedBenchmark) return fileName;

  return formatBenchmarkOptionLabel(matchedBenchmark, {
    includeFileName: false,
    benchmarkOptions,
  });
}

export function formatBenchmarkDropdownLabel(benchmark, options = {}) {
  return formatBenchmarkOptionLabel(benchmark, options);
}

export function formatBenchmarkDisplayLabel(benchmark, options = {}) {
  return formatBenchmarkOptionLabel(benchmark, options);
}

export function formatBenchmarkTableTag(benchmark, benchmarkOptions = []) {
  return getBenchmarkDisplayParts(benchmark, benchmarkOptions).tagName;
}

export function formatBenchmarkTableSterilizer(benchmark, benchmarkOptions = []) {
  return getBenchmarkDisplayParts(benchmark, benchmarkOptions).sterilizerName;
}

export function formatBenchmarkTableSterilizerFull(
  benchmark,
  benchmarkOptions = []
) {
  const display = getBenchmarkDisplayParts(benchmark, benchmarkOptions);

  if (!display.tagName || display.tagName === "-") {
    return display.sterilizerName || "-";
  }

  if (!display.sterilizerName || display.sterilizerName === "-") {
    return display.tagName;
  }

  return `${display.tagName} [${display.sterilizerName}]`;
}

export default {
  getSterilizerDisplayName,
  getTagShortName,
  getSterilizerName,

  getBenchmarkTagId,
  getBenchmarkField,
  getBenchmarkName,
  getBenchmarkFileName,
  getBenchmarkTagName,
  getBenchmarkSterilizerName,
  getBenchmarkDisplayParts,
  enrichBenchmarkWithInferredDisplayContext,
  enrichBenchmarksWithInferredDisplayContext,
  formatBenchmarkOptionLabel,
  formatBenchmarkShortLabel,
  formatBenchmarkDropdownLabel,
  formatBenchmarkDisplayLabel,
  formatBenchmarkTableTag,
  formatBenchmarkTableSterilizer,
  formatBenchmarkTableSterilizerFull,
};
