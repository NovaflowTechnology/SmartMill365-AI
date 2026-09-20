import React, { useEffect, useMemo } from "react";
import api from "../api";
import NotificationToast from "../components/NotificationToast";
import TopNav from "../components/TopNav";
import { usePageSessionState } from "../state/pageSessionStore";

const DEFAULT_FORM = {
  rule_id: "",
  stage: "S2",
  priority: "P1",
  attribution: "Boiler",
  pattern: "A+F",
  metric_name: "MAE_s2_peak",
  metric_description: "",
  warn_low: "0.13",
  critical_value: "0.23",
  rec_key: "",
  recommendation_en: "",
  recommendation_bm: "",
  urgency: "High",
  target_metric: "",
  include_for_anomaly_retrieval: true,
};

const DEFAULT_CUSTOM_METRIC_DESCRIPTION =
  "Custom metric. Please make sure this exact metric name exists in the scoring output; otherwise the rule can be saved but it will not trigger during analysis evaluation.";

const METRIC_DISPLAY_LABELS = {
  MAE_s1_peak: "Stage 1 peak difference",
  MAE_s1_ramp: "Stage 1 ramp difference",
  MAE_s1_release: "Stage 1 release difference",
  MAE_s1_full: "Stage 1 full-profile difference",
  RMSE_s1_full: "Stage 1 full-profile instability",
  combined_s1_full: "Stage 1 combined full-profile error",
  osc_ratio_s1_full: "Stage 1 oscillation ratio",

  MAE_s2_peak: "Stage 2 peak difference",
  MAE_s2_ramp: "Stage 2 ramp difference",
  MAE_s2_release: "Stage 2 release difference",
  MAE_s2_full: "Stage 2 full-profile difference",
  RMSE_s2_full: "Stage 2 full-profile instability",
  combined_s2_full: "Stage 2 combined full-profile error",
  osc_ratio_s2_full: "Stage 2 oscillation ratio",

  MAE_s3_hold: "Stage 3 holding-level difference",
  RMSE_s3_hold: "Stage 3 holding instability",
  MAE_s3_ramp: "Stage 3 ramp difference",
  MAE_s3_full: "Stage 3 full-profile difference",
  RMSE_s3_full: "Stage 3 full-profile instability",
  combined_s3_hold: "Stage 3 combined holding error",
  combined_s3_full: "Stage 3 combined full-profile error",
  osc_ratio_s3_hold: "Stage 3 holding oscillation ratio",

  boiler_recur: "Boiler rule trigger rate",
  bpv_recur: "BPV rule trigger rate",
  comp_recur: "Competition trigger rate",
  net_recur: "Network trigger rate",
  local_recur: "Local trigger rate",
  cycle_score: "Overall cycle score",
  exhaust_residual_pressure: "Exhaust residual pressure",
  exhaust_release_time: "Exhaust release time",
};

function isDefaultCustomMetricDescription(value) {
  const text = String(value || "").trim().toLowerCase();
  return (
    text === DEFAULT_CUSTOM_METRIC_DESCRIPTION.toLowerCase() ||
    text.startsWith("custom metric. please make sure this exact metric name exists") ||
    text.startsWith("custom or unlisted metric")
  );
}

function formatValue(value) {
  if (value === null || value === undefined || value === "") return "-";
  return String(value).replace(/\bRCA\b/gi, "analysis");
}

function normaliseCustomRuleId(value) {
  const clean = String(value || "")
    .trim()
    .toUpperCase()
    .replace(/[^A-Z0-9]+/g, "_")
    .replace(/_+/g, "_")
    .replace(/^_+|_+$/g, "");

  if (!clean) return "";
  return clean.startsWith("CUSTOM_") ? clean : `CUSTOM_${clean}`;
}

function normaliseExistingRuleId(value) {
  return String(value || "").trim().toUpperCase();
}

function normaliseMetricName(value) {
  return String(value || "").trim();
}

function metricLookupKey(value) {
  return normaliseMetricName(value)
    .toLowerCase()
    .replace(/&/g, " and ")
    .replace(/[,/]+/g, " ")
    .replace(/[\s-]+/g, "_")
    .replace(/_+/g, "_")
    .replace(/^_+|_+$/g, "");
}

function isValidMetricName(value) {
  // Allow existing/system metric labels such as:
  // MAE_s2_peak, MAE_s1_full, Boiler rule trigger rate, maintenance history.
  // This is still restricted enough to avoid free sentences or unsafe text.
  return /^[A-Za-z][A-Za-z0-9_ ,-]{1,100}$/.test(normaliseMetricName(value));
}

function getMetricAliases(value) {
  const lookup = metricLookupKey(value);
  const aliases = {
    boiler_rule_trigger_rate: "boiler_recur",
    boiler_trigger_rate: "boiler_recur",
    bpv_rule_trigger_rate: "bpv_recur",
    bpv_trigger_rate: "bpv_recur",
    competition_rule_trigger_rate: "comp_recur",
    competition_trigger_rate: "comp_recur",
    comp_trigger_rate: "comp_recur",
    network_rule_trigger_rate: "net_recur",
    network_trigger_rate: "net_recur",
    net_trigger_rate: "net_recur",
    local_rule_trigger_rate: "local_recur",
    local_trigger_rate: "local_recur",
    local_trigger_rate_maintenance_history: "local_recur",
  };
  return [value, aliases[lookup]].filter(Boolean);
}

function splitMetricParts(value) {
  return normaliseMetricName(value)
    .split(/[,;]+/)
    .map((part) => part.trim())
    .filter(Boolean);
}

function isCyPercentageRule(rule) {
  const stage = String(rule?.stage || "").toUpperCase();
  const unit = String(rule?.threshold_unit || "").toLowerCase();
  const internalMetric = String(rule?.metric_internal_name || rule?.threshold_metric_name || "").toLowerCase();
  return stage === "CY" || unit === "fraction" || ["boiler_recur", "bpv_recur", "comp_recur", "net_recur", "local_recur"].includes(internalMetric);
}

function formatPlainNumber(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return String(value);
  return Number.isInteger(number) ? String(number) : String(Number(number.toFixed(4)));
}

function formatMetricLabel(rule) {
  const display = normaliseMetricName(rule?.metric_display_name || rule?.metric_name);
  const internal = normaliseMetricName(rule?.metric_internal_name || rule?.threshold_metric_name || rule?.metric_name);
  return display || METRIC_DISPLAY_LABELS[internal] || internal || "-";
}

function formatMetricCode(rule) {
  return normaliseMetricName(rule?.metric_internal_name || rule?.threshold_metric_name || rule?.metric_name);
}

function formatThresholdCondition(value, rule = null, level = "warning") {
  if (value === null || value === undefined || value === "") return "-";

  const text = String(value).trim();
  const hasExplicitCondition = /[A-Za-z≥≤><=]/.test(text);
  const number = Number(value);

  if (!Number.isFinite(number)) {
    return text;
  }

  if (isCyPercentageRule(rule) && number >= 0 && number <= 1) {
    const percent = `${Math.round(number * 100)}%`;
    return level === "critical" ? `> ${percent}` : `≥ ${percent}`;
  }

  if (hasExplicitCondition) {
    return text;
  }

  return `> ${formatPlainNumber(number)}`;
}

function buildCustomRecKey(ruleId) {
  const cleanRuleId = normaliseCustomRuleId(ruleId);
  return cleanRuleId ? `CUSTOM_REC_${cleanRuleId.replace(/^CUSTOM_/, "")}` : "";
}

function RuleBadge({ children, type = "neutral" }) {
  return <span className={`rule-badge rule-badge-${type}`}>{children}</span>;
}

function buildMetricCatalogFromOptions(metricOptions = {}, metricDescriptions = {}) {
  const rows = [];

  Object.entries(metricOptions || {}).forEach(([stage, metrics]) => {
    (metrics || []).forEach((metricName) => {
      rows.push({
        stage,
        metric_name: metricName,
        description: metricDescriptions[metricName] || "",
        known: true,
      });
    });
  });

  return rows;
}

export default function RcaRuleManagementPage() {
  const [rules, setRules] = usePageSessionState(
    "rules",
    "rules",
    [],
    { persist: false }
  );
  const [metadata, setMetadata] = usePageSessionState(
    "rules",
    "metadata",
    {},
    { persist: false }
  );
  const [form, setForm] = usePageSessionState("rules", "form", DEFAULT_FORM);
  const [editingRuleId, setEditingRuleId] = usePageSessionState(
    "rules",
    "editingRuleId",
    ""
  );
  const [editingSource, setEditingSource] = usePageSessionState(
    "rules",
    "editingSource",
    ""
  );
  const [loading, setLoading] = usePageSessionState(
    "rules",
    "loading",
    false,
    { persist: false }
  );
  const [saving, setSaving] = usePageSessionState(
    "rules",
    "saving",
    false,
    { persist: false }
  );
  const [reindexing, setReindexing] = usePageSessionState(
    "rules",
    "reindexing",
    false,
    { persist: false }
  );
  const [error, setError] = usePageSessionState(
    "rules",
    "error",
    "",
    { persist: false }
  );
  const [success, setSuccess] = usePageSessionState(
    "rules",
    "success",
    "",
    { persist: false }
  );
  const [indexWarning, setIndexWarning] = usePageSessionState(
    "rules",
    "indexWarning",
    "",
    { persist: false }
  );
  const [conflictWarning, setConflictWarning] = usePageSessionState(
    "rules",
    "conflictWarning",
    null
  );
  const [sourceFilter, setSourceFilter] = usePageSessionState(
    "rules",
    "sourceFilter",
    "all"
  );
  const [stageFilter, setStageFilter] = usePageSessionState(
    "rules",
    "stageFilter",
    "all"
  );
  const [metricGuideOpen, setMetricGuideOpen] = usePageSessionState(
    "rules",
    "metricGuideOpen",
    false
  );
  const [metricSearch, setMetricSearch] = usePageSessionState(
    "rules",
    "metricSearch",
    ""
  );

  const metricOptions = metadata.metric_options || {};
  const metricDescriptions = metadata.metric_descriptions || {};
  const metricCatalogFromBackend = metadata.metric_catalog || [];
  const customMetricDefaultDescription =
    metadata.custom_metric_default_description || DEFAULT_CUSTOM_METRIC_DESCRIPTION;
  const stageOptions = metadata.stage_options || ["S1", "S2", "S3", "CY", "EX"];
  const priorityOptions = metadata.priority_options || ["P1", "P2", "P3", "P4", "P5"];
  const attributionOptions = metadata.attribution_options || [
    "Boiler",
    "BPV",
    "Competition",
    "Network",
    "Local",
  ];
  const urgencyOptions = metadata.urgency_options || ["Low", "Medium", "High", "Critical"];

  const metricCatalog = useMemo(() => {
    const baseCatalog = metricCatalogFromBackend.length
      ? metricCatalogFromBackend
      : buildMetricCatalogFromOptions(metricOptions, metricDescriptions);

    const map = new Map();

    baseCatalog.forEach((item) => {
      const metricName = normaliseMetricName(item.metric_name);
      if (!metricName) return;
      const key = `${item.stage || "-"}::${metricName}`;
      map.set(key, {
        stage: item.stage || "-",
        metric_name: metricName,
        metric_internal_name: item.metric_internal_name || metricName,
        metric_display_name: item.metric_display_name || metricName,
        description: item.description || metricDescriptions[metricName] || "",
        known: item.known !== false,
      });
    });

    rules.forEach((rule) => {
      const metricName = normaliseMetricName(rule.metric_name);
      if (!metricName) return;
      const key = `${rule.stage || "-"}::${metricName}`;
      if (!map.has(key)) {
        map.set(key, {
          stage: rule.stage || "-",
          metric_name: rule.metric_display_name || metricName,
          metric_internal_name: rule.metric_internal_name || metricName,
          metric_display_name: rule.metric_display_name || metricName,
          description: rule.metric_description || metricDescriptions[metricName] || "",
          known: Boolean(rule.metric_is_known || metricDescriptions[metricName]),
        });
      }
    });

    return Array.from(map.values()).sort((a, b) => {
      const stageCompare = String(a.stage).localeCompare(String(b.stage));
      if (stageCompare !== 0) return stageCompare;
      return String(a.metric_name).localeCompare(String(b.metric_name));
    });
  }, [metricCatalogFromBackend, metricOptions, metricDescriptions, rules, customMetricDefaultDescription]);

  const allMetricSuggestions = useMemo(() => {
    const map = new Map();

    metricCatalog.forEach((item) => {
      if (!map.has(item.metric_name)) {
        map.set(item.metric_name, item);
      }
    });

    rules.forEach((rule) => {
      const metricName = normaliseMetricName(rule.metric_name);
      if (metricName && !map.has(metricName)) {
        map.set(metricName, {
          stage: rule.stage || "-",
          metric_name: metricName,
          description: rule.metric_description || "",
          known: Boolean(rule.metric_is_known),
        });
      }
    });

    return Array.from(map.values()).sort((a, b) =>
      String(a.metric_name).localeCompare(String(b.metric_name))
    );
  }, [metricCatalog, rules, customMetricDefaultDescription]);

  const filteredRules = useMemo(() => {
    return rules.filter((rule) => {
      if (sourceFilter !== "all" && rule.source !== sourceFilter) return false;
      if (stageFilter !== "all" && rule.stage !== stageFilter) return false;
      return true;
    });
  }, [rules, sourceFilter, stageFilter]);

  const filteredMetricGuideRows = useMemo(() => {
    const search = metricSearch.trim().toLowerCase();

    return metricCatalog.filter((item) => {
      const sameStage = stageFilter === "all" || item.stage === stageFilter;
      if (!sameStage) return false;

      if (!search) return true;
      return (
        String(item.metric_name || "").toLowerCase().includes(search) ||
        String(item.stage || "").toLowerCase().includes(search) ||
        String(item.description || "").toLowerCase().includes(search)
      );
    });
  }, [metricCatalog, metricSearch, stageFilter]);

  function getKnownMetricDescription(metricName) {
    const cleanName = normaliseMetricName(metricName);
    if (!cleanName) return "";

    const candidates = getMetricAliases(cleanName);

    for (const candidate of candidates) {
      const cleanLower = String(candidate).toLowerCase();
      const cleanLookup = metricLookupKey(candidate);

      if (metricDescriptions[candidate] && !isDefaultCustomMetricDescription(metricDescriptions[candidate])) {
        return metricDescriptions[candidate];
      }

      for (const [knownName, description] of Object.entries(metricDescriptions || {})) {
        if (isDefaultCustomMetricDescription(description)) continue;
        if (String(knownName).toLowerCase() === cleanLower) return description;
        if (metricLookupKey(knownName) === cleanLookup) return description;
      }

      const backendCatalogMatch = (metricCatalogFromBackend || []).find(
        (item) =>
          String(item.metric_name || "").toLowerCase() === cleanLower ||
          String(item.metric_internal_name || "").toLowerCase() === cleanLower ||
          metricLookupKey(item.metric_name) === cleanLookup ||
          metricLookupKey(item.metric_internal_name) === cleanLookup
      );

      if (backendCatalogMatch?.description && !isDefaultCustomMetricDescription(backendCatalogMatch.description)) {
        return backendCatalogMatch.description;
      }
    }

    const parts = splitMetricParts(cleanName);
    if (parts.length > 1) {
      const descriptions = parts
        .map((part) => {
          const description = getKnownMetricDescription(part);
          return description ? `${part}: ${description}` : "";
        })
        .filter(Boolean);
      if (descriptions.length) return descriptions.join(" ");
    }

    return "";
  }


  function getMetricDescription(metricName, explicitDescription = "") {
    const cleanName = normaliseMetricName(metricName);
    if (!cleanName) return "";

    const knownDescription = getKnownMetricDescription(cleanName);
    const explicit = String(explicitDescription || "").trim();

    // Keep a real user-written description, but do not let the old generic
    // fallback message hide the built-in description for known metrics.
    if (explicit && !isDefaultCustomMetricDescription(explicit)) return explicit;
    if (knownDescription) return knownDescription;

    const catalogMatch = metricCatalog.find(
      (item) =>
        String(item.metric_name || "").toLowerCase() === cleanName.toLowerCase() &&
        !isDefaultCustomMetricDescription(item.description)
    );
    if (catalogMatch?.description) return catalogMatch.description;

    return "";
  }

  function getRuleMetricDescription(rule) {
    return getMetricDescription(rule.metric_name, rule.metric_description);
  }

  async function loadRules() {
    try {
      setLoading(true);
      setError("");

      const response = await api.get("/api/rca-rules");
      const data = response.data || {};
      setRules(Array.isArray(data.rules) ? data.rules : []);
      setMetadata(data);
    } catch (err) {
      const status = Number(err?.response?.status || 0);
      setError(
        status > 0 && status < 500 && typeof err?.response?.data?.detail === "string"
          ? err.response.data.detail
          : "Analysis rules could not be loaded. Please try again."
      );
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadRules();
  }, []);

  function updateFormField(key, value) {
    setConflictWarning(null);

    setForm((prev) => {
      const next = { ...prev, [key]: value };

      if (key === "rule_id" && !editingRuleId) {
        next.rec_key = buildCustomRecKey(value);
      }

      if (key === "metric_name") {
        const previousKnownDescription = getKnownMetricDescription(prev.metric_name);
        const nextKnownDescription = getKnownMetricDescription(value);
        const previousDescription = String(prev.metric_description || "").trim();

        const shouldAutoReplaceDescription =
          !previousDescription ||
          previousDescription === previousKnownDescription ||
          previousDescription === customMetricDefaultDescription;

        if (shouldAutoReplaceDescription) {
          next.metric_description = nextKnownDescription;
        }

        if (!prev.target_metric || prev.target_metric === prev.metric_name) {
          next.target_metric = value;
        }
      }

      return next;
    });
  }

  function resetForm() {
    setEditingRuleId("");
    setEditingSource("");
    setForm(DEFAULT_FORM);
    setError("");
    setSuccess("");
    setIndexWarning("");
    setConflictWarning(null);
  }

  function validateForm() {
    if (!form.rule_id.trim()) return "Rule ID is required.";
    if (!form.stage) return "Stage is required.";
    if (!form.priority) return "Priority is required.";
    if (!form.attribution) return "Attribution is required.";
    if (!form.pattern.trim()) return "Pattern is required.";
    if (!form.metric_name.trim()) return "Related scoring metric is required.";
    if (!isValidMetricName(form.metric_name)) {
      return "Metric name must start with a letter and only contain letters, numbers, underscores, spaces, or hyphens. Example: MAE_s2_peak, MAE_s1_full, Boiler rule trigger rate, or custom_pressure_drop_score.";
    }
    if (!form.recommendation_en.trim()) return "Recommendation EN is required.";
    if (!form.recommendation_bm.trim()) {
      return "Recommendation BM is required. Enter the Malay recommendation action before saving the rule.";
    }

    const warnLow = Number(form.warn_low);
    const criticalValue = Number(form.critical_value);

    if (!Number.isFinite(warnLow) || warnLow < 0) {
      return "Warning threshold must be a number greater than or equal to 0.";
    }

    if (!Number.isFinite(criticalValue) || criticalValue < 0) {
      return "Critical threshold must be a number greater than or equal to 0.";
    }

    if (criticalValue <= warnLow) {
      return "Critical threshold must be greater than warning threshold.";
    }

    return "";
  }

  function buildPayload(allowConflict = false) {
    const shouldAllowConflict = allowConflict === true;
    const isEditing = Boolean(editingRuleId);
    const metricName = normaliseMetricName(form.metric_name);

    return {
      ...form,
      allow_conflict: shouldAllowConflict,
      source: isEditing ? editingSource : "custom",
      rule_id: isEditing ? normaliseExistingRuleId(form.rule_id) : normaliseCustomRuleId(form.rule_id),
      rec_key: form.rec_key?.trim()
        ? String(form.rec_key).trim().toUpperCase()
        : isEditing
          ? String(form.rec_key || "").trim().toUpperCase()
          : buildCustomRecKey(form.rule_id),
      metric_name: metricName,
      metric_display_name: metricName,
      metric_internal_name: form.metric_internal_name || "",
      metric_description: getMetricDescription(metricName, form.metric_description),
      warn_low: Number(form.warn_low),
      critical_value: Number(form.critical_value),
      target_metric: form.target_metric?.trim() || metricName,
    };
  }

  async function saveRule(allowConflict = false) {
    const shouldAllowConflict = allowConflict === true;
    const validationError = validateForm();

    if (validationError) {
      setError(validationError);
      setSuccess("");
      return;
    }

    try {
      setSaving(true);
      setError("");
      setSuccess("");
      setIndexWarning("");

      const payload = buildPayload(shouldAllowConflict);
      const response = editingRuleId
        ? await api.put(`/api/rca-rules/${encodeURIComponent(editingRuleId)}`, payload)
        : await api.post("/api/rca-rules", payload);

      const qdrantStatus = response.data?.qdrant_reindex?.status;
      const auditStatus = response.data?.qdrant_reindex?.audit_status;
      const verify = response.data?.verification || null;
      const sourceLabel = response.data?.rule?.source === "original" ? "Original analysis rule" : "Custom analysis rule";

      setConflictWarning(null);

      if (qdrantStatus === "index_failed") {
        setSuccess(`${sourceLabel} saved to the main rule storage.`);
        setIndexWarning(
          "The search index was not updated, so search-based rule retrieval may use older data. Use Retry Search Index to try again."
        );
      } else if (verify?.ready_for_immediate_rca) {
        setSuccess(`${response.data?.message || `${sourceLabel} saved successfully.`} It is ready for immediate analysis use.`);
      } else {
        setSuccess(response.data?.message || `${sourceLabel} saved successfully. It is ready for immediate analysis use.`);
      }
      if (auditStatus === "audit_failed") {
        setIndexWarning((current) =>
          `${current ? `${current} ` : ""}The change was saved, but its audit-history record could not be written.`
        );
      }

      setEditingRuleId("");
      setEditingSource("");
      setForm(DEFAULT_FORM);
      await loadRules();
    } catch (err) {
      const status = err?.response?.status;
      const detail = err?.response?.data?.detail;

      if (status === 409 && detail?.conflict_detected) {
        setConflictWarning(detail);
        setError("");
        setSuccess("");
        return;
      }

      setError(
        Number(status || 0) > 0 && Number(status || 0) < 500
          ? typeof detail === "string"
            ? detail
            : detail?.message || "The rule could not be saved. Check the rule details and try again."
          : "The rule could not be saved. Please try again."
      );
    } finally {
      setSaving(false);
    }
  }

  function editRule(rule) {
    const metricName = rule.metric_internal_name || rule.threshold_metric_name || rule.metric_name || "MAE_s2_peak";
    setEditingRuleId(rule.rule_id);
    setEditingSource(rule.source || "custom");
    setForm({
      rule_id: rule.rule_id || "",
      stage: rule.stage || "S2",
      priority: rule.priority || "P1",
      attribution: rule.attribution || "Boiler",
      pattern: rule.pattern || "A+F",
      metric_name: metricName,
      metric_internal_name: rule.metric_internal_name || rule.threshold_metric_name || "",
      metric_description: getMetricDescription(metricName, rule.metric_description),
      warn_low: rule.warn_low ?? "0.13",
      critical_value: rule.critical_value ?? "0.23",
      rec_key: rule.rec_key || "",
      recommendation_en: rule.recommendation_en || "",
      recommendation_bm: rule.recommendation_bm || "",
      urgency: rule.urgency || "High",
      target_metric: rule.target_metric || metricName || "",
      include_for_anomaly_retrieval: rule.include_for_anomaly_retrieval !== false,
    });
    setError("");
    setSuccess("");
    setConflictWarning(null);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  async function deleteRule(rule) {
    const sourceLabel = rule.source === "original" ? "original Excel-derived" : "custom";
    const confirmed = window.confirm(
      `Delete ${sourceLabel} rule ${rule.rule_id}? This will remove it from the active analysis knowledge base and its search index. This action cannot be undone from the UI.`
    );

    if (!confirmed) return;

    try {
      setSaving(true);
      setError("");
      setSuccess("");
      setIndexWarning("");
      setConflictWarning(null);

      const response = await api.delete(`/api/rca-rules/${encodeURIComponent(rule.rule_id)}`);
      setSuccess(response.data?.message || "Rule deleted successfully.");
      const deleteResult = response.data?.delete_result || {};
      if (deleteResult.qdrant_delete?.status === "delete_from_qdrant_failed") {
        setIndexWarning(
          "The rule was removed from the main storage, but the search index could not be cleaned. Use Retry Search Index to synchronize it."
        );
      } else if (deleteResult.audit_status === "audit_failed") {
        setIndexWarning(
          "The rule was removed, but its audit-history record could not be written."
        );
      }
      await loadRules();

      if (editingRuleId === rule.rule_id) resetForm();
    } catch (err) {
      const status = Number(err?.response?.status || 0);
      setError(
        status > 0 && status < 500 && typeof err?.response?.data?.detail === "string"
          ? err.response.data.detail
          : "The rule could not be deleted. Please try again."
      );
    } finally {
      setSaving(false);
    }
  }

  async function reindexRules() {
    try {
      setReindexing(true);
      setError("");
      setSuccess("");
      setIndexWarning("");
      setConflictWarning(null);

      const response = await api.post("/api/rca-rules/reindex-all");
      if (response.data?.status === "index_failed") {
        setError("The rule search index could not be updated. Please try again.");
      } else {
        setSuccess("Search index updated successfully.");
        setIndexWarning("");
      }
    } catch (err) {
      const status = Number(err?.response?.status || 0);
      setError(
        status > 0 && status < 500 && typeof err?.response?.data?.detail === "string"
          ? err.response.data.detail
          : "The rule search index could not be updated. Please try again."
      );
    } finally {
      setReindexing(false);
    }
  }

  function formatConflictLine(conflict) {
    const source = conflict.source === "custom" ? "custom" : "original";
    const candidatePattern = conflict.candidate_pattern
      ? `; new pattern ${conflict.candidate_pattern}`
      : "";

    return `${conflict.rule_id} (${source}) already uses ${conflict.stage} / ${conflict.metric_name} / existing pattern ${conflict.pattern}${candidatePattern}; same root cause: ${conflict.attribution}; warning ${conflict.warning_threshold}, critical ${conflict.critical_threshold}.`;
  }

  const editingSourceLabel = editingSource === "original" ? "Original Rule" : "Custom Rule";
  const formMetricDescription = getMetricDescription(form.metric_name, form.metric_description);
  const formMetricDescriptionPreview =
    formMetricDescription ||
    "No description entered. You may add one below to explain what this metric measures.";
  const formMetricIsKnown = Boolean(getKnownMetricDescription(form.metric_name));

  return (
    <div className="app-shell">
      <TopNav />

      <main className="app-main rca-rule-page">
        <header className="main-header rca-rule-header">
          <div>
            <h1 className="page-main-title">Analysis Rule Management</h1>
            <p className="rca-rule-page-subtitle">
              Add, edit, and delete analysis rules and one-to-one recommendations. Original Excel-derived rules and custom rules are both editable here.
            </p>
          </div>

          <button
            type="button"
            className="btn-secondary"
            onClick={reindexRules}
            disabled={reindexing || saving}
          >
            {reindexing ? "Retrying..." : "Retry Search Index"}
          </button>
        </header>

        {error && <div className="error-banner">{error}</div>}
        <NotificationToast
          message={success}
          tone="success"
          autoDismissMs={3000}
          onClose={() => setSuccess("")}
        />
        {indexWarning && <div className="warning-box">{indexWarning}</div>}

        {conflictWarning && (
          <div className="rule-conflict-banner">
            <div>
              <h3>Rule conflict warning</h3>
              <p>
                {conflictWarning.message ||
                  "Another rule already uses the same stage feature and same root cause."}
              </p>
              <ul>
                {(conflictWarning.conflicts || []).slice(0, 5).map((conflict, index) => (
                  <li key={`${conflict.rule_id}-${index}`}>{formatConflictLine(conflict)}</li>
                ))}
              </ul>
              <p className="rule-conflict-note">
                Conflict warning is only shown for strong duplicates: same stage, same metric, overlapping pattern, and same root cause.
                If you save anyway, both rules may be triggered and compete during analysis generation.
              </p>
            </div>

            <div className="rule-conflict-actions">
              <button
                type="button"
                className="btn-secondary"
                onClick={() => setConflictWarning(null)}
                disabled={saving}
              >
                Cancel
              </button>
              <button
                type="button"
                className="btn-warning"
                onClick={() => saveRule(true)}
                disabled={saving}
              >
                {saving ? "Saving..." : "Save Anyway"}
              </button>
            </div>
          </div>
        )}

        <section className="dash-card rca-rule-form-card">
          <div className="rca-rule-form-header">
            <div>
              <h2>{editingRuleId ? `Edit ${editingSourceLabel}` : "Add New Custom Rule"}</h2>
            </div>

            {editingRuleId && (
              <button type="button" className="btn-secondary" onClick={resetForm}>
                Cancel Edit
              </button>
            )}
          </div>

          {editingSource === "original" && (
            <div className="rule-edit-warning">
              You are editing an original Excel-derived rule. This updates the active parsed/chunked knowledge-base JSON used by analysis. It does not modify the source Excel workbook.
            </div>
          )}

          <div className="metric-helper-card">
            <div>
              <h3>Metric Guide</h3>
            </div>
            <button
              type="button"
              className="btn-secondary"
              onClick={() => setMetricGuideOpen((open) => !open)}
            >
              {metricGuideOpen ? "Hide Metric Guide" : "View Metric Guide"}
            </button>
          </div>

          {metricGuideOpen && (
            <div className="metric-guide-panel">
              <div className="metric-guide-toolbar">
                <input
                  value={metricSearch}
                  onChange={(e) => setMetricSearch(e.target.value)}
                  placeholder="Search metric, stage, or description"
                  autoComplete="off"
                  name="rca_metric_guide_search_no_autofill"
                />
                <span>{filteredMetricGuideRows.length} metric(s)</span>
              </div>

              <div className="metric-guide-table-wrap">
                <table className="metric-guide-table">
                  <thead>
                    <tr>
                      <th>Stage</th>
                      <th>Metric Name</th>
                      <th>Description</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredMetricGuideRows.map((item) => (
                      <tr key={`${item.stage}-${item.metric_name}`}>
                        <td>{item.stage}</td>
                        <td className="metric-guide-name">{item.metric_name}</td>
                        <td>{formatValue(item.description)}</td>
                      </tr>
                    ))}
                    {!filteredMetricGuideRows.length && (
                      <tr>
                        <td colSpan={3} className="rca-rule-empty-cell">
                          No metric matched your search.
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          <div className="rca-rule-form-grid">
            <label>
              Rule ID
              <input
                value={form.rule_id}
                onChange={(e) => updateFormField("rule_id", e.target.value)}
                placeholder="Example: CUSTOM_S2_BOILER_LOW_PEAK"
                disabled={Boolean(editingRuleId)}
              />
              <small>
                {editingRuleId
                  ? "Rule ID cannot be changed while editing an existing rule."
                  : "New custom Rule IDs are automatically prefixed with CUSTOM_."}
              </small>
            </label>

            <label>
              Rec Key
              <input
                value={form.rec_key}
                onChange={(e) => updateFormField("rec_key", e.target.value)}
                placeholder="Auto-generated if empty"
              />
            </label>

            <label>
              Stage
              <select value={form.stage} onChange={(e) => updateFormField("stage", e.target.value)}>
                {stageOptions.map((item) => (
                  <option key={item} value={item}>{item}</option>
                ))}
              </select>
            </label>

            <label>
              Priority
              <select value={form.priority} onChange={(e) => updateFormField("priority", e.target.value)}>
                {priorityOptions.map((item) => (
                  <option key={item} value={item}>{item}</option>
                ))}
              </select>
            </label>

            <label>
              Attribution / Root Cause
              <select value={form.attribution} onChange={(e) => updateFormField("attribution", e.target.value)}>
                {attributionOptions.map((item) => (
                  <option key={item} value={item}>{item}</option>
                ))}
              </select>
            </label>

            <label>
              Pattern
              <select value={form.pattern} onChange={(e) => updateFormField("pattern", e.target.value)}>
                {[
                  "A", "B", "C", "D", "E", "F",
                  "A+F", "B+F", "C+F", "A+B", "A+C", "B+C",
                ].map((item) => (
                  <option key={item} value={item}>{item}</option>
                ))}
              </select>
            </label>

            <label className="rca-rule-wide-field">
              Related Scoring Metric
              <input
                list="rca-metric-suggestions"
                value={form.metric_name}
                onChange={(e) => updateFormField("metric_name", e.target.value)}
                placeholder="Example: MAE_s2_peak or custom_pressure_drop_score"
                autoComplete="off"
                name="rca_metric_name_no_autofill"
              />
              <datalist id="rca-metric-suggestions">
                {allMetricSuggestions.map((item) => (
                  <option
                    key={`${item.stage}-${item.metric_name}`}
                    value={item.metric_name}
                    label={`${item.stage} - ${item.description}`}
                  />
                ))}
              </datalist>
              <small>
                Type a metric name directly. Suggestions are provided, but the field is not limited to the list.
              </small>
              <div className={`metric-description-preview ${formMetricIsKnown ? "known" : "custom"}`}>
                <strong>{formMetricIsKnown ? "Known metric" : "Custom or unlisted metric"}</strong>
                <span>{formMetricDescriptionPreview}</span>
              </div>
            </label>

            <label>
              Urgency
              <select value={form.urgency} onChange={(e) => updateFormField("urgency", e.target.value)}>
                {urgencyOptions.map((item) => (
                  <option key={item} value={item}>{item}</option>
                ))}
              </select>
            </label>

            <label>
              Warning Threshold
              <input
                type="number"
                step="0.0001"
                value={form.warn_low}
                onChange={(e) => updateFormField("warn_low", e.target.value)}
              />
            </label>

            <label>
              Critical Threshold
              <input
                type="number"
                step="0.0001"
                value={form.critical_value}
                onChange={(e) => updateFormField("critical_value", e.target.value)}
              />
            </label>

            <label>
              Target Metric
              <input
                value={form.target_metric}
                onChange={(e) => updateFormField("target_metric", e.target.value)}
                placeholder="Auto uses selected scoring metric"
              />
            </label>

            <label className="rca-rule-checkbox-label">
              <input
                type="checkbox"
                checked={form.include_for_anomaly_retrieval}
                onChange={(e) => updateFormField("include_for_anomaly_retrieval", e.target.checked)}
              />
              Include for anomaly retrieval
            </label>

            <label className="rca-rule-wide-field">
              Metric Description (Optional)
              <textarea
                value={form.metric_description}
                onChange={(e) => updateFormField("metric_description", e.target.value)}
                placeholder="Example: Measures the pressure drop between the holding stage and exhaust stage."
                rows={3}
              />
              <small>
                For a new metric, explain what it measures and whether a higher or lower value is abnormal. You may leave this blank. If provided, it is stored with the rule and included in analysis retrieval.
              </small>
            </label>

            <label className="rca-rule-wide-field">
              Recommendation EN (Required)
              <textarea
                value={form.recommendation_en}
                onChange={(e) => updateFormField("recommendation_en", e.target.value)}
                placeholder="Example: Stage 2 second peak below normal level — boiler output may be insufficient. Check boiler load and steam demand balance."
                rows={4}
                required
              />
            </label>

            <label className="rca-rule-wide-field">
              Recommendation BM (Required)
              <textarea
                value={form.recommendation_bm}
                onChange={(e) => updateFormField("recommendation_bm", e.target.value)}
                placeholder="Contoh: Periksa beban dandang dan keseimbangan permintaan stim. Laraskan jadual permulaan pensteril jika persaingan stim disahkan."
                rows={4}
                required
              />
              <small>
                Compulsory. This Malay action is shown when the user selects Bahasa Melayu in Analysis Feedback.
              </small>
            </label>
          </div>

          <div className="rca-rule-form-actions">
            <button type="button" className="btn-primary" onClick={() => saveRule(false)} disabled={saving}>
              {saving ? "Saving..." : editingRuleId ? "Update Rule" : "Create Custom Rule"}
            </button>

            <button type="button" className="btn-secondary" onClick={resetForm} disabled={saving}>
              Reset
            </button>
          </div>
        </section>

        <section className="dash-card rca-rule-list-card">
          <div className="rca-rule-list-header">
            <div>
              <h2>Analysis Rules</h2>
              <p>
                Original: {metadata.original_count ?? 0} | Custom: {metadata.custom_count ?? 0}
              </p>
            </div>

            <div className="rca-rule-filters">
              <select value={sourceFilter} onChange={(e) => setSourceFilter(e.target.value)}>
                <option value="all">All sources</option>
                <option value="original">Original only</option>
                <option value="custom">Custom only</option>
              </select>

              <select value={stageFilter} onChange={(e) => setStageFilter(e.target.value)}>
                <option value="all">All stages</option>
                {stageOptions.map((item) => (
                  <option key={item} value={item}>{item}</option>
                ))}
              </select>

              <button type="button" className="btn-secondary" onClick={loadRules} disabled={loading}>
                {loading ? "Loading..." : "Refresh"}
              </button>
            </div>
          </div>

          <div className="rca-rule-table-wrap">
            <table className="rca-rule-table rca-rule-table-with-metric-description">
              <thead>
                <tr>
                  <th>Source</th>
                  <th>Rule ID</th>
                  <th>Stage</th>
                  <th>Priority</th>
                  <th>Attribution</th>
                  <th>Pattern</th>
                  <th>Metric</th>
                  <th>Metric Description</th>
                  <th>Warning Condition</th>
                  <th>Critical Condition</th>
                  <th>Recommendation</th>
                  <th>Actions</th>
                </tr>
              </thead>

              <tbody>
                {filteredRules.map((rule) => (
                  <tr key={`${rule.source}-${rule.rule_id}`}>
                    <td>
                      <RuleBadge type={rule.source === "custom" ? "custom" : "original"}>
                        {rule.source === "custom" ? "Custom" : "Original"}
                      </RuleBadge>
                    </td>
                    <td className="rca-rule-id-cell">{formatValue(rule.rule_id)}</td>
                    <td>{formatValue(rule.stage)}</td>
                    <td>{formatValue(rule.priority)}</td>
                    <td>{formatValue(rule.attribution)}</td>
                    <td>{formatValue(rule.pattern)}</td>
                    <td className="rca-rule-metric-name-cell">
                      <strong>{formatMetricLabel(rule)}</strong>
                      {formatMetricCode(rule) && formatMetricCode(rule) !== formatMetricLabel(rule) && (
                        <small>Metric code: {formatMetricCode(rule)}</small>
                      )}
                    </td>
                    <td className="rca-rule-metric-description-cell">
                      {formatValue(getRuleMetricDescription(rule))}
                      {rule.supporting_context && <small>Supporting context: {rule.supporting_context}</small>}
                    </td>
                    <td>{formatThresholdCondition(rule.warn_low, rule, "warning")}</td>
                    <td>{formatThresholdCondition(rule.critical_value, rule, "critical")}</td>
                    <td className="rca-rule-recommendation-cell">
                      <strong>{formatValue(rule.rec_key)}</strong>
                      <p>{formatValue(rule.recommendation_en)}</p>
                    </td>
                    <td>
                      <div className="rca-rule-row-actions">
                        <button type="button" className="btn-secondary" onClick={() => editRule(rule)}>
                          Edit
                        </button>
                        <button type="button" className="btn-danger" onClick={() => deleteRule(rule)}>
                          Delete
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}

                {!filteredRules.length && (
                  <tr>
                    <td colSpan={12} className="rca-rule-empty-cell">
                      {loading ? "Loading analysis rules..." : "No analysis rules found for the selected filters."}
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </section>
      </main>
    </div>
  );
}
