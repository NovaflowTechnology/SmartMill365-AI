import React, { useEffect, useMemo, useState } from "react";
import api from "../api";
import TopNav from "../components/TopNav";

const DEFAULT_FORM = {
  rule_id: "",
  stage: "S2",
  priority: "P1",
  attribution: "Boiler",
  pattern: "A+F",
  metric_name: "MAE_s2_peak",
  warn_low: "0.13",
  critical_value: "0.23",
  rec_key: "",
  recommendation_en: "",
  recommendation_bm: "",
  urgency: "High",
  target_metric: "",
  include_for_anomaly_retrieval: true,
};

function formatValue(value) {
  if (value === null || value === undefined || value === "") return "-";
  return String(value);
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

function buildCustomRecKey(ruleId) {
  const cleanRuleId = normaliseCustomRuleId(ruleId);
  return cleanRuleId ? `CUSTOM_REC_${cleanRuleId.replace(/^CUSTOM_/, "")}` : "";
}

function RuleBadge({ children, type = "neutral" }) {
  return <span className={`rule-badge rule-badge-${type}`}>{children}</span>;
}

export default function RcaRuleManagementPage() {
  const [rules, setRules] = useState([]);
  const [metadata, setMetadata] = useState({});
  const [form, setForm] = useState(DEFAULT_FORM);
  const [editingRuleId, setEditingRuleId] = useState("");
  const [editingSource, setEditingSource] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [reindexing, setReindexing] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [conflictWarning, setConflictWarning] = useState(null);
  const [sourceFilter, setSourceFilter] = useState("all");
  const [stageFilter, setStageFilter] = useState("all");

  const metricOptions = metadata.metric_options || {};
  const stageOptions = metadata.stage_options || ["S1", "S2", "S3", "CY", "EX"];
  const priorityOptions = metadata.priority_options || ["P1", "P2", "P3", "P4", "P5"];
  const attributionOptions = metadata.attribution_options || ["Boiler", "BPV", "Competition", "Network", "Local"];
  const urgencyOptions = metadata.urgency_options || ["Low", "Medium", "High", "Critical"];
  const currentMetricOptions = metricOptions[form.stage] || [];

  const filteredRules = useMemo(() => {
    return rules.filter((rule) => {
      if (sourceFilter !== "all" && rule.source !== sourceFilter) return false;
      if (stageFilter !== "all" && rule.stage !== stageFilter) return false;
      return true;
    });
  }, [rules, sourceFilter, stageFilter]);

  async function loadRules() {
    try {
      setLoading(true);
      setError("");

      const response = await api.get("/api/rca-rules");
      const data = response.data || {};
      setRules(Array.isArray(data.rules) ? data.rules : []);
      setMetadata(data);
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || "Unable to load RCA rules.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadRules();
  }, []);

  useEffect(() => {
    if (!success) return undefined;

    const timer = window.setTimeout(() => {
      setSuccess("");
    }, 3000);

    return () => window.clearTimeout(timer);
  }, [success]);

  function updateFormField(key, value) {
    setConflictWarning(null);

    setForm((prev) => {
      const next = { ...prev, [key]: value };

      if (key === "stage") {
        const newMetricOptions = metricOptions[value] || [];
        next.metric_name = newMetricOptions[0] || "";
        if (!prev.target_metric) {
          next.target_metric = newMetricOptions[0] || "";
        }
      }

      if (key === "rule_id" && !editingRuleId) {
        next.rec_key = buildCustomRecKey(value);
      }

      if (key === "metric_name" && !prev.target_metric) {
        next.target_metric = value;
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
    setConflictWarning(null);
  }

  function validateForm() {
    if (!form.rule_id.trim()) return "Rule ID is required.";
    if (!form.stage) return "Stage is required.";
    if (!form.priority) return "Priority is required.";
    if (!form.attribution) return "Attribution is required.";
    if (!form.pattern.trim()) return "Pattern is required.";
    if (!form.metric_name) return "Related scoring metric is required.";
    if (!form.recommendation_en.trim()) return "Recommendation EN is required.";

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
      warn_low: Number(form.warn_low),
      critical_value: Number(form.critical_value),
      target_metric: form.target_metric?.trim() || form.metric_name,
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

      const payload = buildPayload(shouldAllowConflict);
      const response = editingRuleId
        ? await api.put(`/api/rca-rules/${encodeURIComponent(editingRuleId)}`, payload)
        : await api.post("/api/rca-rules", payload);

      const qdrantStatus = response.data?.qdrant_reindex?.status;
      const qdrantError = response.data?.qdrant_reindex?.error;
      const verify = response.data?.verification || null;
      const sourceLabel = response.data?.rule?.source === "original" ? "Original RCA rule" : "Custom RCA rule";

      setConflictWarning(null);

      if (qdrantStatus === "index_failed") {
        setSuccess(
          `${response.data?.message || "Rule saved."} The rule is saved and directly usable from JSON, but Qdrant indexing returned a warning: ${qdrantError || "unknown error"}.`
        );
      } else if (verify?.ready_for_immediate_rca) {
        setSuccess(`${response.data?.message || `${sourceLabel} saved successfully.`} It is ready for immediate RCA use.`);
      } else {
        setSuccess(response.data?.message || `${sourceLabel} saved successfully. It is ready for immediate RCA use.`);
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
        typeof detail === "string"
          ? detail
          : detail?.message || err.message || "Unable to save rule."
      );
    } finally {
      setSaving(false);
    }
  }

  function editRule(rule) {
    setEditingRuleId(rule.rule_id);
    setEditingSource(rule.source || "custom");
    setForm({
      rule_id: rule.rule_id || "",
      stage: rule.stage || "S2",
      priority: rule.priority || "P1",
      attribution: rule.attribution || "Boiler",
      pattern: rule.pattern || "A+F",
      metric_name: rule.metric_name || "MAE_s2_peak",
      warn_low: rule.warn_low ?? "0.13",
      critical_value: rule.critical_value ?? "0.23",
      rec_key: rule.rec_key || "",
      recommendation_en: rule.recommendation_en || "",
      recommendation_bm: rule.recommendation_bm || "",
      urgency: rule.urgency || "High",
      target_metric: rule.target_metric || rule.metric_name || "",
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
      `Delete ${sourceLabel} rule ${rule.rule_id}? This will remove it from the active RCA knowledge base and delete its Qdrant point. This action cannot be undone from the UI.`
    );

    if (!confirmed) return;

    try {
      setSaving(true);
      setError("");
      setSuccess("");
      setConflictWarning(null);

      const response = await api.delete(`/api/rca-rules/${encodeURIComponent(rule.rule_id)}`);
      setSuccess(response.data?.message || "Rule deleted successfully.");
      await loadRules();

      if (editingRuleId === rule.rule_id) resetForm();
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || "Unable to delete rule.");
    } finally {
      setSaving(false);
    }
  }

  async function reindexRules() {
    try {
      setReindexing(true);
      setError("");
      setSuccess("");
      setConflictWarning(null);

      const response = await api.post("/api/rca-rules/reindex-all");
      if (response.data?.status === "index_failed") {
        setError(response.data?.error || "Rule Qdrant reindex failed.");
      } else {
        setSuccess(`Rule reindex completed. Indexed chunks: ${response.data?.indexed_chunks ?? 0}.`);
      }
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || "Unable to reindex rules.");
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

  return (
    <div className="dashboard-root">
      <TopNav />

      <main className="main-view rca-rule-page">
        <header className="main-header rca-rule-header">
          <div>
            <h1 className="page-main-title">RCA Rule Management</h1>
            <p className="rca-rule-page-subtitle">
              Add, edit, and delete RCA rules and one-to-one recommendations. Original Excel-derived rules and custom rules are both editable here.
            </p>
          </div>

          <button
            type="button"
            className="btn-secondary"
            onClick={reindexRules}
            disabled={reindexing || saving}
          >
            {reindexing ? "Reindexing..." : "Reindex Rules"}
          </button>
        </header>

        {error && <div className="error-banner">{error}</div>}
        {success && <div className="success-banner">{success}</div>}

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
                If you save anyway, both rules may be triggered and compete during RCA generation.
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
              <p>
                {editingRuleId
                  ? "Changes are saved into the active RCA knowledge-base JSON and reindexed immediately."
                  : "New rules are saved as custom JSON rules, converted to chunks, and available immediately."}
              </p>
            </div>

            {editingRuleId && (
              <button type="button" className="btn-secondary" onClick={resetForm}>
                Cancel Edit
              </button>
            )}
          </div>

          {editingSource === "original" && (
            <div className="rule-edit-warning">
              You are editing an original Excel-derived rule. This updates the active parsed/chunked knowledge-base JSON used by RCA. It does not modify the source Excel workbook.
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

            <label>
              Related Scoring Metric
              <select value={form.metric_name} onChange={(e) => updateFormField("metric_name", e.target.value)}>
                {currentMetricOptions.map((item) => (
                  <option key={item} value={item}>{item}</option>
                ))}
              </select>
              <small>No formula input is needed. The system compares this metric against the thresholds.</small>
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
              Recommendation EN
              <textarea
                value={form.recommendation_en}
                onChange={(e) => updateFormField("recommendation_en", e.target.value)}
                placeholder="Example: Stage 2 second peak below normal level — boiler output may be insufficient. Check boiler load and steam demand balance."
                rows={4}
              />
            </label>

            <label className="rca-rule-wide-field">
              Recommendation BM Optional
              <textarea
                value={form.recommendation_bm}
                onChange={(e) => updateFormField("recommendation_bm", e.target.value)}
                placeholder="Optional Malay recommendation text"
                rows={3}
              />
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
              <h2>RCA Rules</h2>
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
            <table className="rca-rule-table">
              <thead>
                <tr>
                  <th>Source</th>
                  <th>Rule ID</th>
                  <th>Stage</th>
                  <th>Priority</th>
                  <th>Attribution</th>
                  <th>Pattern</th>
                  <th>Metric</th>
                  <th>Warning</th>
                  <th>Critical</th>
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
                    <td>{formatValue(rule.metric_name)}</td>
                    <td>{formatValue(rule.warn_low)}</td>
                    <td>{formatValue(rule.critical_value)}</td>
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
                    <td colSpan={11} className="rca-rule-empty-cell">
                      {loading ? "Loading RCA rules..." : "No RCA rules found for the selected filters."}
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
