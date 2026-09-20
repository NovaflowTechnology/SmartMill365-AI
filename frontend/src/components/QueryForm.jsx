import { useEffect, useMemo, useState } from "react";
import api from "../api";
import PlantDateTimePicker from "./PlantDateTimePicker";

function buildTagOptionLabel(tag) {
  if (!tag) return "";

  const dataId = tag.tag_id || "";
  const tagName =
    tag.display_name || tag.tag_name || tag.tagName || dataId;
  const plantIdentity = tagName && tagName !== dataId
    ? `${tagName} (${dataId})`
    : dataId;
  const count = Number(tag.sterilizer_count ?? tag.sterilizerCount ?? 0);

  if (count > 0) {
    return `${plantIdentity} — ${count} sterilizer${count === 1 ? "" : "s"}`;
  }

  return plantIdentity;
}

function buildFieldOptionLabel(item) {
  if (!item) return "";

  const field = item.field || "";
  const sterilizerName = item.sterilizer_name || item.sterilizerName || "";
  const fieldMatch = /^stp(\d+)$/i.exec(field);

  return sterilizerName || (fieldMatch ? `Sterilizer ${Number(fieldMatch[1])}` : "Sterilizer");
}

export default function QueryForm({
  formData,
  setFormData,
  onDetect,
  loading,
  submitLabel = "Detect Cycles",
}) {
  const [tags, setTags] = useState([]);
  const [tagsLoading, setTagsLoading] = useState(false);
  const [tagsError, setTagsError] = useState("");

  const [sterilizers, setSterilizers] = useState([]);
  const [sterilizersLoading, setSterilizersLoading] = useState(false);
  const [sterilizersError, setSterilizersError] = useState("");

  const selectedTag = useMemo(() => {
    return tags.find((item) => item.tag_id === formData.tag_id) || null;
  }, [tags, formData.tag_id]);

  useEffect(() => {
    if (!selectedTag) return;
    const displayName =
      selectedTag.display_name || selectedTag.tag_name || selectedTag.tag_id || "";
    if (formData.plant_display_name === displayName) return;
    setFormData((previous) => ({
      ...previous,
      plant_display_name: displayName,
    }));
  }, [selectedTag, formData.plant_display_name, setFormData]);

  const updateField = (key, value) => {
    setFormData((prev) => ({ ...prev, [key]: value }));
  };

  const updateManyFields = (updates) => {
    setFormData((prev) => ({ ...prev, ...updates }));
  };

  useEffect(() => {
    let isMounted = true;

    async function fetchTags() {
      try {
        setTagsLoading(true);
        setTagsError("");

        const response = await api.get("/api/tags", {
          params: { unit: formData.source_unit || "bar" },
        });
        const tagsList = Array.isArray(response.data?.tags) ? response.data.tags : [];

        if (!isMounted) return;
        setTags(tagsList);
      } catch (err) {
        if (!isMounted) return;
        setTags([]);
        setTagsError("Data ID list could not be loaded.");
      } finally {
        if (isMounted) setTagsLoading(false);
      }
    }

    fetchTags();

    return () => {
      isMounted = false;
    };
  }, [formData.source_unit]);

  useEffect(() => {
    let isMounted = true;

    async function fetchSterilizersForTag() {
      const tagId = formData.tag_id;

      if (!tagId) {
        setSterilizers([]);
        setSterilizersError("");
        return;
      }

      try {
        setSterilizersLoading(true);
        setSterilizersError("");

        const response = await api.get(`/api/tag-sterilizers/${encodeURIComponent(tagId)}`, {
          params: { unit: formData.source_unit || "bar" },
        });
        const sterilizerList = Array.isArray(response.data?.sterilizers)
          ? response.data.sterilizers
          : [];

        if (!isMounted) return;

        setSterilizers(sterilizerList);

        if (sterilizerList.length > 0) {
          const currentField = String(formData.field || "").trim();
          const currentFieldExists = sterilizerList.some((item) => item.field === currentField);

          if (!currentField || !currentFieldExists) {
            updateField("field", sterilizerList[0].field);
          }
        }
      } catch (err) {
        if (!isMounted) return;
        setSterilizers([]);
        setSterilizersError("Sterilizer list could not be loaded.");
      } finally {
        if (isMounted) setSterilizersLoading(false);
      }
    }

    fetchSterilizersForTag();

    return () => {
      isMounted = false;
    };
  }, [formData.tag_id, formData.source_unit]);

  function handleTagChange(tagId) {
    const tag = tags.find((item) => item.tag_id === tagId);
    updateManyFields({
      tag_id: tagId,
      field: "",
      plant_display_name:
        tag?.display_name || tag?.tag_name || tagId || "",
    });
  }

  function handleUnitChange(unit) {
    updateManyFields({
      source_unit: unit,
      tag_id: "",
      field: "",
      plant_display_name: "",
    });
  }

  return (
    <div className="query-form-container">
      <div className="query-section">
        <div className="field-group">
          <label>Pressure Unit</label>
          <select
            value={formData.source_unit || "bar"}
            onChange={(e) => handleUnitChange(e.target.value)}
          >
            <option value="bar">bar</option>
            <option value="psi">psi</option>
          </select>
        </div>

        <div className="field-group">
          <label>Data ID</label>

          {tags.length > 0 || tagsLoading ? (
            <select
              value={formData.tag_id || ""}
              onChange={(e) => handleTagChange(e.target.value)}
              disabled={tagsLoading}
            >
              <option value="">-- Choose Data ID --</option>
              {tags.map((item) => (
                <option key={item.tag_id} value={item.tag_id}>
                  {buildTagOptionLabel(item)}
                </option>
              ))}
            </select>
          ) : (
            <input
              placeholder="e.g. SAMYSK_POM_240004"
              value={formData.tag_id}
              onChange={(e) =>
                updateManyFields({
                  tag_id: e.target.value,
                  plant_display_name: e.target.value,
                })
              }
            />
          )}

          {formData.tag_id && (
            <small className="form-helper-text">
              Data ID: {formData.tag_id}
            </small>
          )}

          {tagsError && (
            <small className="form-helper-text">
              Data ID list could not be loaded. You may type the ID manually.
            </small>
          )}
        </div>

        <div className="field-group">
          <label>Select Sterilizer</label>

          {sterilizers.length > 0 ? (
            <select
              value={formData.field || ""}
              onChange={(e) => updateField("field", e.target.value)}
              disabled={sterilizersLoading}
            >
              {sterilizers.map((item) => (
                <option key={item.field} value={item.field}>
                  {buildFieldOptionLabel(item)}
                </option>
              ))}
            </select>
          ) : (
            <input
              value={formData.field}
              onChange={(e) => updateField("field", e.target.value)}
              placeholder="Enter configured sterilizer channel"
            />
          )}

          {sterilizersError && (
            <small className="form-helper-text">
              Sterilizer list could not be loaded. You may enter the configured sterilizer channel manually.
            </small>
          )}
        </div>

        {selectedTag?.ignored_auxiliary_fields?.includes("blr") && (
          <small className="warning-text">
            An unconfirmed auxiliary channel is excluded. Boiler analysis uses only the confirmed boiler channel.
          </small>
        )}

        <div className="field-group">
          <label>Start Time</label>
          <PlantDateTimePicker
            id="query-start-time"
            value={formData.start_time}
            onChange={(value) => updateField("start_time", value)}
          />
        </div>

        <div className="field-group">
          <label>Stop Time</label>
          <PlantDateTimePicker
            id="query-stop-time"
            value={formData.stop_time}
            onChange={(value) => updateField("stop_time", value)}
          />
        </div>

        <div className="field-group">
          <label>Smooth Window</label>
          <input
            type="number"
            min="1"
            value={formData.smooth_window}
            onChange={(e) => updateField("smooth_window", Number(e.target.value))}
          />
        </div>
      </div>

      <button
        type="button"
        className="btn-primary-full"
        onClick={onDetect}
        disabled={loading}
      >
        {loading ? "Processing..." : submitLabel}
      </button>
    </div>
  );
}
