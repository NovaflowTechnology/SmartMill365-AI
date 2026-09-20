import React, { useState } from "react";
import "../RcaFeedbackPanel.css";
import { formatPlantDateTime } from "../utils/plantTime";

function formatNumber(value, digits = 3) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return "-";
  }

  return Number(value).toFixed(digits);
}

function formatText(value) {
  if (value === null || value === undefined || value === "") return "-";
  return String(value).replaceAll("_", " ");
}

function toTitleCase(value) {
  const text = formatText(value);
  if (text === "-") return text;

  return text
    .split(" ")
    .map((part) => {
      if (!part) return part;
      return part.charAt(0).toUpperCase() + part.slice(1);
    })
    .join(" ");
}

function getFeedbackPayload(rcaFeedback) {
  if (!rcaFeedback) return null;
  if (!rcaFeedback.feedback) return rcaFeedback;

  // The analysis endpoint returns evidence_context beside feedback. Preserve
  // it when unwrapping the response so Boiler and peer readings reach the UI.
  return {
    ...rcaFeedback.feedback,
    evidence_context:
      rcaFeedback.feedback?.evidence_context || rcaFeedback.evidence_context || {},
  };
}

function getSeverityClass(value) {
  const clean = String(value || "").toLowerCase().trim();

  if (["critical", "poor", "abnormal"].includes(clean)) return "critical";
  if (["warning", "fair", "candidate", "fallback"].includes(clean)) return "warning";

  if (
    ["excellent", "good", "normal", "confirmed", "available", "used", "success"].includes(clean)
  ) {
    return "normal";
  }

  return "neutral";
}

function normaliseList(value) {
  if (!value) return [];

  if (Array.isArray(value)) {
    return value
      .flatMap((item) => normaliseList(item))
      .map((item) => String(item || "").trim())
      .filter(Boolean);
  }

  return String(value)
    .split(/\n+/)
    .map((line) => line.trim())
    .filter(Boolean);
}

function uniqueList(values) {
  const seen = new Set();
  const output = [];

  values.forEach((value) => {
    const clean = String(value || "").trim();
    if (!clean) return;
    const key = clean.toLowerCase();
    if (seen.has(key)) return;
    seen.add(key);
    output.push(clean);
  });

  return output;
}

function joinSentenceParts(parts) {
  return parts
    .map((part) => String(part || "").trim())
    .filter(Boolean)
    .join(" ")
    .replace(/\s+/g, " ")
    .trim();
}

function numberWord(value) {
  const n = Number(value);
  const words = {
    1: "One",
    2: "Two",
    3: "Three",
    4: "Four",
    5: "Five",
    6: "Six",
    7: "Seven",
    8: "Eight",
    9: "Nine",
    10: "Ten",
  };

  return words[n] || String(n || "");
}

function makeOperatorSafeText(value) {
  let text = String(value || "").trim();
  if (!text) return "";

  const replacements = [
    [/\bRCA\b/gi, "analysis"],
    [/\bBOILER\b/g, "Boiler"],
    [/\bbenchmark\b/gi, "normal operating profile"],
    [/\bdeviation\b/gi, "pressure difference from normal"],
    [/\bmeasured difference\b/gi, "pressure difference from normal"],
    [/\bthreshold\b/gi, "limit"],
    [/\bcritical limit\b/gi, "serious pressure limit"],
    [/\bsafe limit\b/gi, "normal operating limit"],
    [/\bmetric value\b/gi, "measured condition"],
    [/\bcombined_error\b/gi, "pressure difference"],
    [/\bnormalisation\b|\bnormalization\b/gi, "normal comparison"],
    [/\bsub-window\b/gi, "phase period"],
    [/\bStage\s*1\b|\bS1\b/g, "first pressurisation phase"],
    [/\bStage\s*2\b|\bS2\b/g, "second pressurisation phase"],
    [/\bStage\s*3\b|\bS3\b/g, "pressure holding phase"],
    [/\bfirst peak\b/gi, "pressure peak in the first pressurisation phase"],
    [/\bsecond peak\b/gi, "pressure peak in the second pressurisation phase"],
    [/\bthird peak\b/gi, "pressure peak in the pressure holding phase"],
    [/\bMAE_[A-Za-z0-9_]+\b/g, "pressure difference from normal"],
    [/\bRMSE_[A-Za-z0-9_]+\b/g, "pressure instability"],
    [/\bMAE\b/g, "pressure difference"],
    [/\bRMSE\b/g, "pressure instability"],
    [/\bPattern\s*[A-F]\b/gi, "pressure behavior"],
    [/\b[A-F]\+[A-F](\+[A-F])*\b/g, "combined pressure behavior"],
    [/\bP[1-5]\b/g, "cause group"],
    [/\battribution\b/gi, "likely cause"],
    [/\brule\s*ID\b/gi, "rule reference"],
    [/\bS[123]-\d{3}\b|\bCY-\d{3}\b|\bEX-\d{3}\b/g, "rule reference"],
  ];

  replacements.forEach(([pattern, replacement]) => {
    text = text.replace(pattern, replacement);
  });

  text = text.replace(/\b0\.\d{3,}\b/g, "");
  text = text.replace(/\s+,/g, ",");
  text = text.replace(/\s+\./g, ".");
  text = text.replace(/\s+/g, " ").trim();
  return removeBrokenSentenceFragments(text);
}

function removeBrokenSentenceFragments(value) {
  const text = String(value || "").trim();
  if (!text) return "";

  const sentences = text
    .split(/(?<=[.!?])\s+/)
    .map((sentence) => sentence.trim())
    .filter(Boolean)
    .filter((sentence) => !isBrokenSentence(sentence));

  return sentences.join(" ").replace(/\s+/g, " ").trim();
}

function isBrokenSentence(value) {
  const text = String(value || "").toLowerCase();

  return (
    text.includes("from normal from normal") ||
    text.includes("is ,") ||
    text.includes("is, ") ||
    text.includes("of .") ||
    text.includes("of.") ||
    text.includes("limit of") ||
    text.includes("should be checked using the pressure chart") ||
    text.includes("the likely cause should be checked") ||
    text.includes("no recommended operator action")
  );
}

function isWeakText(value) {
  const text = String(value || "").trim();
  const lower = text.toLowerCase();

  if (!text) return true;
  if (text.length < 70) return true;
  if (isBrokenSentence(text)) return true;
  if (lower.includes("should be checked using the pressure chart")) return true;
  if (lower.includes("not available from the rca result")) return true;
  if (lower.includes("no plain-language explanation")) return true;
  if (lower.includes("rule normal operating limit")) return true;

  return false;
}

function cleanActionLine(value) {
  return makeOperatorSafeText(value)
    .replace(/^[-•]\s*/g, "")
    .replace(/^\d+[.)]\s*/g, "")
    .replace(/^action\s*:\s*/i, "")
    .trim();
}

function isRawTechnicalAction(value) {
  const text = String(value || "").toLowerCase();

  return (
    text.includes("second pressurisation phase pressure peak") ||
    text.includes("below benchmark") ||
    text.includes("below normal reference level") ||
    text.includes("boiler output dropping") ||
    text.includes("during second ramp") ||
    text.includes("stage 2 second peak") ||
    text.includes("s2 window") ||
    text.includes("directly affects") ||
    text.includes("fruitlet loosening")
  );
}

function sectionValueFromPlainLanguage(plainLanguage, key) {
  const sections = plainLanguage?.sections;
  if (!Array.isArray(sections)) return null;

  const matched = sections.find((section) => section?.key === key);
  if (!matched) return null;

  if (key === "what_to_do") {
    return matched.items || matched.body || [];
  }

  return matched.body || matched.items || null;
}

function getPlainLanguagePayload(feedback, humanFeedback) {
  return (
    humanFeedback?.plain_language_feedback ||
    humanFeedback?.plain_language ||
    humanFeedback?.plainLanguage ||
    humanFeedback?.operator_explanation ||
    humanFeedback?.operatorExplanation ||
    feedback?.plain_language_feedback ||
    feedback?.plain_language ||
    feedback?.plainLanguage ||
    {}
  );
}

function getPlainField(plainLanguage, humanFeedback, keys, fallback = "") {
  const sectionKeys = {
    what_happened: "what_happened",
    whatHappened: "what_happened",
    happened: "what_happened",
    most_likely_cause: "most_likely_cause",
    mostLikelyCause: "most_likely_cause",
    likely_cause: "most_likely_cause",
    cause: "most_likely_cause",
    what_to_do: "what_to_do",
    whatToDo: "what_to_do",
    actions: "what_to_do",
    recommended_actions: "what_to_do",
    priority: "priority",
    urgency_plain: "priority",
    urgencyPlain: "priority",
  };

  for (const key of keys) {
    if (plainLanguage?.[key]) return plainLanguage[key];
    const sectionKey = sectionKeys[key];
    if (sectionKey) {
      const sectionValue = sectionValueFromPlainLanguage(plainLanguage, sectionKey);
      if (sectionValue) return sectionValue;
    }
    if (humanFeedback?.[key]) return humanFeedback[key];
  }

  return fallback;
}

function getLlmGeneration(humanFeedback) {
  return (
    humanFeedback?.plain_language_generation ||
    humanFeedback?.plain_language_feedback?.generation ||
    humanFeedback?.plain_language?.generation ||
    humanFeedback?.generation ||
    humanFeedback?.llm_generation ||
    null
  );
}

function getLlmLabel(llmGeneration, plainLanguage) {
  const source = String(
    plainLanguage?.source ||
      plainLanguage?.source_label ||
      plainLanguage?.generation_source ||
      ""
  ).toLowerCase();

  if (llmGeneration?.success || source.includes("sheet10_llm") || source.includes("ai")) {
    return "LLM: Used";
  }

  if (llmGeneration?.attempted || llmGeneration?.enabled || source.includes("fallback")) {
    return "LLM: Fallback";
  }

  return "LLM: Off";
}

function getPlainSourceLabel(llmGeneration, plainLanguage) {
  const label =
    plainLanguage?.source_label ||
    plainLanguage?.sourceLabel ||
    plainLanguage?.display_source ||
    "";

  if (label) return label;

  const source = String(plainLanguage?.source || "").toLowerCase();
  if (llmGeneration?.success || source.includes("sheet10_llm")) {
    return "AI plain-language output";
  }
  if (llmGeneration?.attempted || llmGeneration?.enabled) {
    return "Template fallback output";
  }

  return "Plain-language output";
}

function stageLabel(stage) {
  const clean = String(stage || "").toUpperCase().trim();

  if (clean === "S1") return "first pressurisation phase";
  if (clean === "S2") return "second pressurisation phase";
  if (clean === "S3") return "pressure holding phase";

  return "selected phase";
}

function normaliseRootCause(value) {
  const text = String(value || "").toUpperCase();

  if (text.includes("BOILER")) return "BOILER";
  if (text.includes("COMPETITION")) return "COMPETITION";
  if (text.includes("BPV")) return "BPV";
  if (text.includes("NETWORK")) return "NETWORK";
  if (text.includes("LOCAL")) return "LOCAL";

  return text || "UNKNOWN";
}

function getRootCause(feedback, humanFeedback) {
  return normaliseRootCause(
    feedback?.primary_rule?.attribution ||
      humanFeedback?.technical_details?.attribution ||
      humanFeedback?.operator_facts?.attribution ||
      ""
  );
}

function getStatus(feedback, humanFeedback) {
  return String(
    feedback?.primary_rule?.status ||
      humanFeedback?.technical_details?.status ||
      humanFeedback?.operator_facts?.status ||
      ""
  ).toLowerCase();
}

function getSeverity(feedback, humanFeedback) {
  return String(
    feedback?.primary_rule?.severity ||
      humanFeedback?.technical_details?.severity ||
      humanFeedback?.operator_facts?.severity ||
      ""
  ).toLowerCase();
}

function getFocusStage(feedback, humanFeedback) {
  return (
    humanFeedback?.selected_stage ||
    humanFeedback?.technical_details?.selected_stage ||
    feedback?.primary_rule?.stage ||
    humanFeedback?.operator_facts?.focus_stage ||
    null
  );
}

function getEvidenceContext(feedback, humanFeedback) {
  return (
    humanFeedback?.technical_details?.evidence_context ||
    humanFeedback?.operator_facts?.evidence_context ||
    feedback?.evidence_context ||
    feedback?.primary_rule?.evidence_context ||
    {}
  );
}

function getPeerEvidenceInfo(feedback, humanFeedback) {
  const evidence = getEvidenceContext(feedback, humanFeedback);
  const peer = evidence?.peer_sterilizer_evidence || {};
  const comp = evidence?.competition_evidence || {};
  const pressure = evidence?.pressure_time_evidence || {};

  const possibleLists = [
    peer.active_peers,
    peer.overlapping_peers,
    peer.stage_active_peers,
    peer.selected_stage_peers,
    comp.concurrent_ramp_peers,
    pressure.shared_pressure_event_peers,
  ];

  const names = uniqueList(
    possibleLists.flatMap((list) => {
      if (!Array.isArray(list)) return [];
      return list
        .map((item) => {
          if (typeof item === "string") return item;
          return item?.sterilizer_name || item?.name || item?.display_name || item?.field || "";
        })
        .filter(Boolean);
    })
  );

  const activeCount = Number(
    peer.active_peer_count ||
      peer.stage_active_peer_count ||
      peer.selected_stage_peer_count ||
      peer.overlapping_peer_count ||
      names.length ||
      0
  );

  const sharedCount = Number(
    pressure.shared_pressure_event_count ||
      comp.concurrent_ramp_count ||
      activeCount ||
      names.length ||
      0
  );

  return {
    names,
    activeCount: Number.isFinite(activeCount) ? activeCount : 0,
    sharedCount: Number.isFinite(sharedCount) ? sharedCount : 0,
  };
}

function formatPeerNames(names) {
  if (!Array.isArray(names) || names.length === 0) return "";
  if (names.length === 1) return names[0];
  if (names.length === 2) return `${names[0]} and ${names[1]}`;
  return `${names.slice(0, -1).join(", ")}, and ${names[names.length - 1]}`;
}

function formatEvidenceTime(value) {
  if (!value) return "time unavailable";
  return formatPlantDateTime(value, {
    month: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  });
}

function formatEvidenceNumber(value, digits = 2) {
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(digits) : "-";
}

function formatEquipmentName(value, fallback = "Equipment") {
  const text = String(value || fallback).trim();
  const upper = text.toUpperCase();

  if (upper === "BOILER") return "Boiler";
  if (upper === "BPV") return "BPV";
  if (upper === "NETWORK") return "Steam network";
  return text;
}

function peerEvidenceSentence(feedback, humanFeedback) {
  const { names, activeCount, sharedCount } = getPeerEvidenceInfo(feedback, humanFeedback);
  const count = Math.max(activeCount, sharedCount, names.length);

  if (names.length > 0) {
    return `${numberWord(names.length)} other sterilizer${names.length > 1 ? "s" : ""} (${formatPeerNames(names)}) were also active, which can increase total steam demand.`;
  }

  if (count > 0) {
    return `${numberWord(count)} other sterilizer${count > 1 ? "s" : ""} were also active, which can increase total steam demand.`;
  }

  return "The evidence should be reviewed together with the pressure chart and equipment condition.";
}

function conditionalPressureEvidenceSentence(feedback, humanFeedback) {
  const rootCause = getRootCause(feedback, humanFeedback);
  const evidence = getEvidenceContext(feedback, humanFeedback);

  if (["BOILER", "BPV"].includes(rootCause)) {
    const key = rootCause.toLowerCase();
    const causeName = formatEquipmentName(rootCause);
    const item = evidence?.auxiliary_pressure_evidence?.[key] || {};
    const stats = item?.stage_window_stats || {};
    if (!item || Object.keys(item).length === 0) {
      return `${causeName} pressure data could not be included because no ${causeName} pressure channel is configured for the selected tag.`;
    }
    if (!item?.available || !stats?.available) {
      const unavailableReason = String(
        item.reason || stats.reason || "no readings were returned"
      ).replace(/\bBOILER\b/g, "Boiler");
      return `${formatEquipmentName(item.display_name, causeName)} pressure on ${item.field || "the configured channel"} was unavailable for the affected-stage period because ${unavailableReason}.`;
    }

    const unit = stats.source_unit || stats.benchmark_unit || "pressure units";
    const minimum = stats.raw_min_pressure ?? stats.min_pressure;
    const mean = stats.raw_mean_pressure ?? stats.mean_pressure;
    const maximum = stats.raw_max_pressure ?? stats.max_pressure;
    const start = stats.raw_start_pressure ?? stats.start_pressure;
    const end = stats.raw_end_pressure ?? stats.end_pressure;
    const name = formatEquipmentName(item.display_name, causeName);
    const condition =
      item.pressure_condition || "recorded during the affected stage";

    return `The recorded ${name} pressure on ${item.field || "the configured channel"} was ${condition}. From ${formatEvidenceTime(stats.window_start)} to ${formatEvidenceTime(stats.window_end)}, it had a minimum of ${formatEvidenceNumber(minimum)} ${unit}, mean of ${formatEvidenceNumber(mean)} ${unit}, and maximum of ${formatEvidenceNumber(maximum)} ${unit}; it started at ${formatEvidenceNumber(start)} ${unit} and ended at ${formatEvidenceNumber(end)} ${unit}.`;
  }

  if (rootCause === "COMPETITION") {
    const ramps = evidence?.competition_evidence?.concurrent_ramp_peers || [];
    if (!Array.isArray(ramps) || ramps.length === 0) return "";
    const event = ramps[0] || {};
    return `${event.sterilizer_name || "A peer sterilizer"} began a pressure ramp at ${event.ramp_start_time || "the affected period"}, at ${event.ramp_start_pressure ?? "-"} ${event.pressure_unit || "pressure units"}.`;
  }

  return "";
}

function getPeerPressureEvidence(feedback, humanFeedback) {
  const evidence = getEvidenceContext(feedback, humanFeedback);
  const compactConditions =
    evidence?.peer_sterilizer_evidence?.affected_stage_pressure_conditions || [];
  const legacyConditions = evidence?.pressure_time_evidence?.peer_window_stats || [];
  const conditions = (compactConditions.length ? compactConditions : legacyConditions)
    .filter((item) => item?.available)
    .slice(0, 4);

  if (!conditions.length) {
    return {
      available: false,
      message:
        "Individual competitor sterilizer pressure readings were unavailable for the affected-stage time range.",
      rows: [],
    };
  }

  const first = conditions[0];
  const rows = conditions.map((item) => {
    const unit = item.source_unit || item.benchmark_unit || "pressure units";
    return {
      name: item.sterilizer_name || item.display_name || item.field || "Peer sterilizer",
      condition:
        item.pressure_condition || "active during part of the affected stage",
      unit,
      minimum: item.min_pressure ?? item.raw_min_pressure,
      mean: item.mean_pressure ?? item.raw_mean_pressure,
      maximum: item.max_pressure ?? item.raw_max_pressure,
      start: item.start_pressure ?? item.raw_start_pressure,
      end: item.end_pressure ?? item.raw_end_pressure,
    };
  });

  return {
    available: true,
    windowStart: formatEvidenceTime(first.window_start),
    windowEnd: formatEvidenceTime(first.window_end),
    rows,
  };
}

function rootCauseSentence(rootCause) {
  if (rootCause === "BOILER") {
    return "The most likely cause is that the boiler may not be supplying enough steam during this period.";
  }

  if (rootCause === "COMPETITION") {
    return "The most likely cause is that too many sterilizers may be demanding steam at the same time.";
  }

  if (rootCause === "BPV") {
    return "The most likely cause is that the steam pressure control valve may not be regulating steam properly.";
  }

  if (rootCause === "NETWORK") {
    return "The most likely cause is uneven steam distribution in the shared steam pipe network.";
  }

  if (rootCause === "LOCAL") {
    return "The most likely cause is a local mechanical issue in this sterilizer.";
  }

  return "The likely cause should be verified using the pressure trend and equipment condition.";
}

function sharedConclusionSentence(rootCause, feedback, humanFeedback) {
  const { activeCount, sharedCount, names } = getPeerEvidenceInfo(feedback, humanFeedback);
  const hasPeerEvidence = activeCount > 0 || sharedCount > 0 || names.length > 0;

  if (["BOILER", "COMPETITION", "NETWORK", "BPV"].includes(rootCause) && hasPeerEvidence) {
    return "This suggests a shared steam supply issue among the sterilizers.";
  }

  if (rootCause === "LOCAL") {
    return "This suggests the issue is more likely isolated to this sterilizer.";
  }

  return "This result should be verified before the next operating decision.";
}

function buildPreferredWhatHappened(feedback, humanFeedback) {
  const rootCause = getRootCause(feedback, humanFeedback);
  const stage = stageLabel(getFocusStage(feedback, humanFeedback));
  const peerSentence = peerEvidenceSentence(feedback, humanFeedback);

  return joinSentenceParts([
    `During the ${stage}, the pressure behavior did not follow the expected operating profile for a stable cycle.`,
    rootCauseSentence(rootCause),
    peerSentence,
    conditionalPressureEvidenceSentence(feedback, humanFeedback),
    sharedConclusionSentence(rootCause, feedback, humanFeedback),
  ]);
}

function getKnowledgeBaseRecommendationText(feedback, humanFeedback, existingActions = []) {
  // human_feedback.recommended_actions is the backend's final, status-aware
  // action list.  Do not let an older/empty primary-rule payload or generic
  // next_actions value mask it.  Combine all usable sources and deduplicate so
  // a valid S2-005 recommendation still reaches the action extractor.
  const candidates = [
    ...normaliseList(humanFeedback?.recommended_actions),
    ...normaliseList(existingActions),
    feedback?.primary_rule?.recommendation_en,
    feedback?.recommendation,
    ...normaliseList(feedback?.next_actions),
  ]
    .map((item) => String(item || "").trim())
    .filter(Boolean);

  const unique = [];
  candidates.forEach((item) => {
    if (unique.some((existing) => existing.toLowerCase() === item.toLowerCase())) return;
    unique.push(item);
  });

  return unique.join(" ").trim();
}

function getMalayRecommendationText(feedback, humanFeedback) {
  return String(
    feedback?.primary_rule?.recommendation_bm ||
      feedback?.recommendation_bm ||
      humanFeedback?.recommendation_bm ||
      ""
  ).trim();
}

function splitKnowledgeBaseRecommendationSentences(value) {
  let source = String(value || "").trim();
  if (!source) return [];

  // Remove target metric text because it is not an operator action.
  source = source.replace(/\bTarget\s*:\s*[^.]*\.?/gi, " ");
  source = source.replace(/\bSasaran\s*:\s*[^.]*\.?/gi, " ");

  const chunks = [];
  source.split(/[\n\r]+/).forEach((part) => {
    const cleanPart = part.trim();
    if (!cleanPart) return;

    cleanPart
      .split(/\s+[—–-]\s+/)
      .map((item) => item.trim())
      .filter(Boolean)
      .forEach((item) => chunks.push(item));
  });

  const sentences = [];
  chunks.forEach((chunk) => {
    chunk
      .split(/(?<=[.!?])\s+/)
      .map((item) => item.trim().replace(/[.;:-]+$/g, ""))
      .filter(Boolean)
      .forEach((item) => sentences.push(item));
  });

  return sentences;
}

function isKnowledgeBaseActionClause(value) {
  const text = String(value || "").trim();
  if (!text) return false;

  return /\b(check|inspect|monitor|confirm|verify|stagger|reduce|increase|recalibrate|calibrate|re-tune|retune|tune|adjust|implement|enforce|avoid|limit|compare|clean|drain|replace|repair|maintain|schedule)\b/i.test(text);
}

function splitCompoundKnowledgeBaseAction(value, feedback, humanFeedback) {
  let text = makeOperatorSafeText(value);
  if (!text) return [];

  const firstAction = text.search(/\b(check|inspect|monitor|confirm|verify|stagger|reduce|increase|recalibrate|calibrate|re-tune|retune|tune|adjust|implement|enforce|avoid|limit|compare|clean|drain|replace|repair|maintain|schedule)\b/i);
  if (firstAction > 0) {
    text = text.slice(firstAction).trim();
  }

  const stage = stageLabel(getFocusStage(feedback, humanFeedback));
  text = text.replace(/\bduring the second ramp\b/gi, `during the ${stage}`);
  text = text.replace(/\bduring second ramp\b/gi, `during the ${stage}`);
  text = text.replace(/\bduring first pressurisation phase window\b/gi, "during the first pressurisation phase");
  text = text.replace(/\bduring second pressurisation phase window\b/gi, "during the second pressurisation phase");
  text = text.replace(/\bduring pressure holding phase window\b/gi, "during the pressure holding phase");
  text = text.replace(/\bduring the first pressurisation phase window\b/gi, "during the first pressurisation phase");
  text = text.replace(/\bduring the second pressurisation phase window\b/gi, "during the second pressurisation phase");
  text = text.replace(/\bduring the pressure holding phase window\b/gi, "during the pressure holding phase");

  // Split only simple "Check A and B" structures into separate points.
  // This still uses only the KB sentence content; it does not add new actions.
  if (/^check\s+/i.test(text) && /\s+and\s+/i.test(text)) {
    const body = text.replace(/^check\s+/i, "").replace(/[.]$/g, "").trim();
    const parts = body
      .split(/\s*,\s*|\s+and\s+/i)
      .map((item) => item.trim().replace(/[.]$/g, ""))
      .filter(Boolean);

    if (parts.length >= 2 && parts.length <= 4) {
      return parts.map((part) => `Check ${part}.`);
    }
  }

  return [text.endsWith(".") ? text : `${text}.`];
}

function extractKnowledgeBaseActionItems(feedback, humanFeedback, existingActions = []) {
  const source = getKnowledgeBaseRecommendationText(feedback, humanFeedback, existingActions);
  if (!source) return [];

  const actions = [];
  splitKnowledgeBaseRecommendationSentences(source).forEach((sentence) => {
    if (!isKnowledgeBaseActionClause(sentence)) return;

    splitCompoundKnowledgeBaseAction(sentence, feedback, humanFeedback).forEach((action) => {
      const clean = cleanActionLine(action);
      if (!clean || isBrokenSentence(clean)) return;
      if (actions.some((item) => item.toLowerCase() === clean.toLowerCase())) return;
      actions.push(clean);
    });
  });

  return actions.slice(0, 4);
}

function buildPreferredActions(feedback, humanFeedback, existingActions = []) {
  const kbActions = extractKnowledgeBaseActionItems(feedback, humanFeedback, existingActions);

  if (kbActions.length > 0) {
    return kbActions;
  }

  return ["No knowledge-base recommendation was returned for this analysis rule."];
}

function cleanMalayActionLine(value) {
  let text = String(value || "")
    .replace(/^[-•]\s*/g, "")
    .replace(/^\d+[.)]\s*/g, "")
    .replace(/^tindakan\s*:\s*/i, "")
    .replace(/\bRCA\b/gi, "analisis")
    .replace(/\s+/g, " ")
    .trim();

  if (!text) return "";
  return /[.!?]$/.test(text) ? text : `${text}.`;
}

function isMalayActionClause(value) {
  return /\b(periksa|semak|pantau|sahkan|pastikan|uji|laras|laraskan|selaras|selaraskan|tingkatkan|kurangkan|kalibrasi|tentukur|jadualkan|elakkan|hadkan|bandingkan|bersihkan|salirkan|gantikan|ganti|baiki|selenggara|laksanakan|perbetulkan)\b/i.test(
    String(value || "")
  );
}

function buildMalayActions(feedback, humanFeedback) {
  const source = getMalayRecommendationText(feedback, humanFeedback);
  if (!source) {
    return ["Cadangan Bahasa Melayu tidak tersedia untuk peraturan analisis ini."];
  }

  const sentences = splitKnowledgeBaseRecommendationSentences(source)
    .map((sentence) => cleanMalayActionLine(sentence))
    .filter(Boolean);
  const actionSentences = sentences.filter((sentence) => isMalayActionClause(sentence));
  const preferred = actionSentences.length > 0 ? actionSentences : sentences;

  return uniqueList(preferred).slice(0, 4);
}

function translatePriorityToMalay(priority) {
  const text = String(priority || "").trim().toLowerCase();

  if (text.includes("verify immediately")) {
    return "Sahkan dengan segera; ambil tindakan hanya selepas disahkan";
  }
  if (text.includes("verify first")) {
    return "Sahkan dahulu; kemudian ambil tindakan jika disahkan";
  }
  if (text.includes("act immediately")) return "Ambil tindakan segera";
  if (text.includes("inspect soon")) return "Periksa secepat mungkin";
  if (text.includes("maintenance")) return "Jadualkan untuk penyelenggaraan seterusnya";
  return "Semakan diperlukan";
}

function buildPriorityText(feedback, humanFeedback, plainPriority) {
  const status = getStatus(feedback, humanFeedback);
  const severity = getSeverity(feedback, humanFeedback);
  const existing = makeOperatorSafeText(normaliseList(plainPriority).join(" "));

  // Candidate describes confidence in the cause, while critical describes the
  // size of the deviation.  A critical candidate needs urgent verification,
  // not an unverified corrective action.
  if (status === "candidate" && severity === "critical") {
    return "Verify immediately; act only if confirmed";
  }
  if (status === "candidate") return "Verify first, then act if confirmed";
  if (status === "confirmed" && severity === "critical") return "Act immediately";
  if (severity === "critical") return "Act immediately";
  if (severity === "warning") return "Inspect soon";

  return existing || "Review required";
}

function buildTwoSectionContent(feedback, humanFeedback, actionLanguage = "en") {
  const plainLanguage = getPlainLanguagePayload(feedback, humanFeedback);

  const whatHappened = getPlainField(
    plainLanguage,
    humanFeedback,
    ["what_happened", "whatHappened", "happened"],
    ""
  );

  const mostLikelyCause = getPlainField(
    plainLanguage,
    humanFeedback,
    ["most_likely_cause", "mostLikelyCause", "likely_cause", "cause"],
    ""
  );

  const whatToDo = getPlainField(
    plainLanguage,
    humanFeedback,
    ["what_to_do", "whatToDo", "actions", "recommended_actions"],
    []
  );

  const priority = getPlainField(
    plainLanguage,
    humanFeedback,
    ["priority", "urgency_plain", "urgencyPlain"],
    ""
  );

  const combinedPlainText = makeOperatorSafeText(
    joinSentenceParts([
      normaliseList(whatHappened).join(" "),
      normaliseList(mostLikelyCause).join(" "),
    ])
  );

  const rootCause = getRootCause(feedback, humanFeedback);
  const evidenceRichCause = ["BOILER", "BPV", "COMPETITION", "NETWORK"].includes(
    rootCause
  );
  const useGeneratedWhatHappened =
    evidenceRichCause || isWeakText(combinedPlainText) || combinedPlainText.length > 650;
  let finalWhatHappened = useGeneratedWhatHappened
    ? buildPreferredWhatHappened(feedback, humanFeedback)
    : combinedPlainText;

  const rawActions = normaliseList(whatToDo).length
    ? normaliseList(whatToDo)
    : normaliseList(humanFeedback?.recommended_actions || feedback?.next_actions || []);

  const hasTechnicalActions = rawActions.some((action) => isRawTechnicalAction(action) || isBrokenSentence(action));
  const finalActions = hasTechnicalActions || rawActions.length <= 1
    ? buildPreferredActions(feedback, humanFeedback, rawActions)
    : buildPreferredActions(feedback, humanFeedback, rawActions);

  const finalPriority = buildPriorityText(feedback, humanFeedback, priority);
  const peerPressureEvidence = evidenceRichCause
    ? getPeerPressureEvidence(feedback, humanFeedback)
    : null;

  return {
    whatHappened: finalWhatHappened,
    peerPressureEvidence,
    actions:
      actionLanguage === "bm"
        ? buildMalayActions(feedback, humanFeedback)
        : finalActions,
    priority:
      actionLanguage === "bm"
        ? translatePriorityToMalay(finalPriority)
        : finalPriority,
  };
}

function Badge({ children, className = "" }) {
  return <span className={`rca-pill ${className}`}>{children}</span>;
}

function OperatorSection({ title, headerAction = null, children, className = "" }) {
  return (
    <section className={`rca-operator-box ${className}`}>
      <div className="rca-operator-box-header">
        <h4>{title}</h4>
        {headerAction}
      </div>
      <div className="rca-operator-box-content">{children}</div>
    </section>
  );
}

function OperatorPlainOutput({ feedback, humanFeedback }) {
  const [actionLanguage, setActionLanguage] = useState("en");
  const plainLanguage = getPlainLanguagePayload(feedback, humanFeedback);
  const llmGeneration = getLlmGeneration(humanFeedback);
  const sourceLabel = getPlainSourceLabel(llmGeneration, plainLanguage);
  const hasMalayRecommendation = Boolean(
    getMalayRecommendationText(feedback, humanFeedback)
  );

  const effectiveActionLanguage = hasMalayRecommendation ? actionLanguage : "en";
  const content = buildTwoSectionContent(
    feedback,
    humanFeedback,
    effectiveActionLanguage
  );

  return (
    <div className="rca-operator-block">
      <div className="rca-operator-meta-row">
        <p>
          Sheet 10 explanation first; engineering evidence is available below if
          needed.
        </p>

        <Badge className="rca-pill-green">{sourceLabel}</Badge>
      </div>

      <div className="rca-two-section-grid">
        <OperatorSection title="What happened" className="rca-what-happened-box">
          <p>{content.whatHappened}</p>

          {content.peerPressureEvidence && (
            <PeerPressureEvidence evidence={content.peerPressureEvidence} />
          )}
        </OperatorSection>

        <OperatorSection
          title="What to do"
          className="rca-what-to-do-box"
          headerAction={
            <div
              className="rca-language-switch"
              role="group"
              aria-label="Select action language"
            >
              <button
                type="button"
                className={actionLanguage === "en" ? "active" : ""}
                onClick={() => setActionLanguage("en")}
                aria-pressed={actionLanguage === "en"}
              >
                English
              </button>
              <button
                type="button"
                className={actionLanguage === "bm" ? "active" : ""}
                onClick={() => setActionLanguage("bm")}
                aria-pressed={actionLanguage === "bm"}
                disabled={!hasMalayRecommendation}
                title={
                  hasMalayRecommendation
                    ? "Show the Malay knowledge-base recommendation"
                    : "This older rule has no Malay recommendation"
                }
              >
                Bahasa Melayu
              </button>
            </div>
          }
        >

          <div className="rca-priority-inline">
            <strong>{actionLanguage === "bm" ? "Keutamaan:" : "Priority:"}</strong>{" "}
            {content.priority}
          </div>

          <ul className="rca-action-list">
            {content.actions.map((action, index) => (
              <li key={`${action}-${index}`}>{action}</li>
            ))}
          </ul>
        </OperatorSection>
      </div>
    </div>
  );
}

function PeerPressureEvidence({ evidence }) {
  if (!evidence?.available) {
    return (
      <div className="rca-peer-pressure-unavailable">
        {evidence?.message || "Competitor sterilizer pressure data is unavailable."}
      </div>
    );
  }

  return (
    <div className="rca-peer-pressure-block">
      <div className="rca-peer-pressure-title">
        Competitor sterilizer pressure
      </div>
      <div className="rca-peer-pressure-window">
        <strong>Affected-stage period:</strong> {evidence.windowStart} to{" "}
        {evidence.windowEnd}
      </div>

      <div className="rca-peer-pressure-rows">
        {evidence.rows.map((row, index) => (
          <div className="rca-peer-pressure-row" key={`${row.name}-${index}`}>
            <div className="rca-peer-pressure-row-heading">
              <strong>{row.name}</strong>
              <span>{row.condition}</span>
            </div>

            <div className="rca-peer-pressure-metrics">
              {[
                ["Minimum", row.minimum],
                ["Mean", row.mean],
                ["Maximum", row.maximum],
                ["Start", row.start],
                ["End", row.end],
              ].map(([label, value]) => (
                <div key={label} className="rca-peer-pressure-metric">
                  <span>{label}</span>
                  <strong>
                    {formatEvidenceNumber(value)} {row.unit}
                  </strong>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function DiagnosisValue({ value, severity }) {
  const severityClass = getSeverityClass(severity);
  const text = value === null || value === undefined || value === "" ? "-" : String(value);

  return <span className={`rca-diagnosis-value ${severityClass}`}>{text}</span>;
}

function HumanDiagnosisTable({ rows }) {
  if (!Array.isArray(rows) || rows.length === 0) return null;

  return (
    <div className="rca-section">
      <h4>Diagnosis Summary</h4>

      <div className="rca-diagnosis-table-wrap">
        <table className="rca-diagnosis-table">
          <colgroup>
            <col className="rca-col-field" />
            <col className="rca-col-value" />
            <col className="rca-col-meaning" />
          </colgroup>

          <thead>
            <tr>
              <th>Field</th>
              <th>Value</th>
              <th>Meaning</th>
            </tr>
          </thead>

          <tbody>
            {rows.map((row, index) => (
              <tr key={`${row.field || "row"}-${index}`}>
                <td className="rca-diagnosis-field" data-label="Field">
                  {row.field || "-"}
                </td>

                <td className="rca-diagnosis-value-cell" data-label="Value">
                  <DiagnosisValue value={row.value} severity={row.severity} />
                </td>

                <td className="rca-diagnosis-meaning" data-label="Meaning">
                  {row.explanation || "-"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function TechnicalDetails({ primaryRule, humanTechnicalDetails }) {
  const details = humanTechnicalDetails || {};
  const source = primaryRule || details;

  if (!source || Object.keys(source).length === 0) return null;

  const rows = [
    ["Rule ID", source.rule_id],
    ["Stage", source.stage],
    ["Priority", source.priority],
    ["Attribution", source.attribution],
    ["Pattern", source.pattern],
    ["Metric", source.metric_name],
    ["Metric Value", formatNumber(source.metric_value, 4)],
    ["Warning Threshold", formatNumber(source.warn_low ?? source.warning_threshold, 3)],
    ["Critical Threshold", formatNumber(source.critical_value ?? source.critical_threshold, 3)],
    ["Status", source.status],
    ["Severity", source.severity],
  ];

  return (
    <div className="rca-technical-grid">
      {rows.map(([label, value]) => (
        <div key={label} className="rca-info-item">
          <span>{label}</span>
          <strong>{formatText(value)}</strong>
        </div>
      ))}
    </div>
  );
}

function OtherMatchedRules({ matchedRules }) {
  if (!Array.isArray(matchedRules) || matchedRules.length <= 1) return null;

  return (
    <div className="rca-section">
      <h4>Other Matched Analysis Rules</h4>

      <div className="rca-table-wrap">
        <table className="rca-table">
          <thead>
            <tr>
              <th>Rule</th>
              <th>Stage</th>
              <th>Attribution</th>
              <th>Status</th>
              <th>Severity</th>
              <th>Metric</th>
            </tr>
          </thead>

          <tbody>
            {matchedRules.slice(1, 6).map((rule, index) => (
              <tr key={`${rule.rule_id || "rule"}-${index}`}>
                <td>{rule.rule_id || "-"}</td>
                <td>{rule.stage || "-"}</td>
                <td>{rule.attribution || "-"}</td>
                <td>{formatText(rule.status)}</td>
                <td>{formatText(rule.severity)}</td>
                <td>{rule.metric_name || "-"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Notes({ notes }) {
  const cleanNotes = normaliseList(notes);

  if (cleanNotes.length === 0) return null;

  return (
    <div className="rca-section">
      <h4>System Notes</h4>
      <ul className="rca-notes-list">
        {cleanNotes.map((note, index) => (
          <li key={`${note}-${index}`}>{note}</li>
        ))}
      </ul>
    </div>
  );
}

function AugmentedContext({ augmentedContext }) {
  if (!augmentedContext) return null;

  const rawContextText =
    typeof augmentedContext === "string"
      ? augmentedContext
      : JSON.stringify(augmentedContext, null, 2);
  const contextText = rawContextText.replace(/\bRCA\b/gi, "Analysis");

  if (!contextText || contextText === "{}") return null;

  return (
    <div className="rca-section">
      <h4>Retrieved Analysis Context</h4>
      <pre className="rca-context-pre">{contextText}</pre>
    </div>
  );
}

export default function RcaFeedbackPanel({ rcaFeedback }) {
  const [showTechnical, setShowTechnical] = useState(false);
  const [showContext, setShowContext] = useState(false);

  if (!rcaFeedback) {
    return (
      <div className="rca-card rca-empty">
        <p>Click Generate Analysis to retrieve rules and produce feedback.</p>
      </div>
    );
  }

  const feedback = getFeedbackPayload(rcaFeedback);

  if (!feedback) {
    return (
      <div className="rca-card rca-empty">
        <p>No analysis feedback payload was returned.</p>
      </div>
    );
  }

  const primaryRule = feedback?.primary_rule;
  const matchedRules = feedback?.matched_rules || [];
  const notes = feedback?.notes || [];
  const augmentedContext = feedback?.augmented_context;
  const humanFeedback = feedback?.human_feedback || {};

  const status = primaryRule?.status || "not_available";
  const severity = primaryRule?.severity || "not_available";
  const plainLanguage = getPlainLanguagePayload(feedback, humanFeedback);
  const llmGeneration = getLlmGeneration(humanFeedback);

  const llmLabel = getLlmLabel(llmGeneration, plainLanguage);
  const llmClass = llmLabel.includes("Used")
    ? "normal"
    : llmLabel.includes("Fallback")
      ? "warning"
      : "neutral";

  const diagnosisRows = humanFeedback?.diagnosis_table || [];
  const humanTechnicalDetails = humanFeedback?.technical_details || {};

  return (
    <div className="rca-card rca-flat-card">
      <div className="rca-top-row">
        <div className="rca-badge-group">
          <Badge className={`rca-pill-${getSeverityClass(status)}`}>
            Analysis: {toTitleCase(status)}
          </Badge>

          <Badge className={`rca-pill-${getSeverityClass(severity)}`}>
            Severity: {toTitleCase(severity)}
          </Badge>

          <Badge className={`rca-pill-${llmClass}`}>{llmLabel}</Badge>
        </div>
      </div>

      <OperatorPlainOutput feedback={feedback} humanFeedback={humanFeedback} />

      <div className="rca-technical-toggle-row">
        <button
          type="button"
          className="rca-toggle-btn"
          onClick={() => setShowTechnical((prev) => !prev)}
        >
          {showTechnical ? "Hide Technical Details" : "Show Technical Details"}
        </button>
      </div>

      {showTechnical && (
        <div className="rca-technical-panel">
          <TechnicalDetails
            primaryRule={primaryRule}
            humanTechnicalDetails={humanTechnicalDetails}
          />

          <HumanDiagnosisTable rows={diagnosisRows} />

          <OtherMatchedRules matchedRules={matchedRules} />

          <Notes notes={notes} />

          {augmentedContext && (
            <div className="rca-section">
              <button
                type="button"
                className="rca-toggle-btn secondary"
                onClick={() => setShowContext((prev) => !prev)}
              >
                {showContext ? "Hide Retrieved Context" : "Show Retrieved Context"}
              </button>

              {showContext && <AugmentedContext augmentedContext={augmentedContext} />}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
