import React, { useEffect, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import api from "../api";
import { PLANT_TIME_ZONE } from "../utils/plantTime";
import NotificationToast from "../components/NotificationToast";
import TopNav from "../components/TopNav";
import {
  runLatestPageTask,
  usePageSessionState,
} from "../state/pageSessionStore";

const DEFAULT_SHIFTS = {
  morning: { label: "Morning Shift", start: "00:00", end: "12:00" },
  night: { label: "Night Shift", start: "12:00", end: "00:00" },
};

const EMPTY_SETTINGS = {
  version: 0,
  timezone: PLANT_TIME_ZONE,
  sites: [],
  active_benchmarks: {},
};

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function benchmarkDataId(benchmark) {
  return benchmark?.tag_id || benchmark?.id || benchmark?.sterilizer_id || "";
}

function settingsFromCatalog(rawSettings, catalog) {
  const saved = clone(rawSettings || EMPTY_SETTINGS);
  const savedSiteMap = new Map(
    (saved.sites || []).map((site) => [site.site_code, site])
  );
  saved.sites = (catalog || []).map((site) => {
    const savedSite = savedSiteMap.get(site.site_code) || {};
    const savedPlantMap = new Map(
      (savedSite.plants || []).map((plant) => [plant.data_id, plant])
    );

    return {
      site_code: site.site_code,
      display_name:
        savedSite.display_name || site.display_name || site.site_code,
      shifts: clone(savedSite.shifts || site.shifts || DEFAULT_SHIFTS),
      plants: (site.plants || []).map((plant) => {
        const savedPlant = savedPlantMap.get(plant.data_id) || {};
        const storedName = String(savedPlant.display_name || "").trim();
        const customDisplayName =
          savedPlant.custom_display_name !== undefined &&
          savedPlant.custom_display_name !== null
            ? savedPlant.custom_display_name
            : storedName && storedName !== plant.data_id
              ? storedName
              : plant.custom_display_name || "";
        const officialName = plant.official_name || savedPlant.official_name || "";
        return {
          data_id: plant.data_id,
          official_name: officialName,
          custom_display_name: customDisplayName,
          display_name:
            customDisplayName || officialName || plant.display_name || plant.data_id,
        };
      }),
    };
  });
  saved.active_benchmarks = saved.active_benchmarks || {};
  return saved;
}

function mergeSettingsDraft(rawSettings, catalog, draftSettings) {
  const refreshed = settingsFromCatalog(rawSettings, catalog);
  const draftSiteMap = new Map(
    (draftSettings?.sites || []).map((site) => [site.site_code, site])
  );

  refreshed.sites = refreshed.sites.map((site) => {
    const draftSite = draftSiteMap.get(site.site_code);
    if (!draftSite) return site;

    const draftPlantMap = new Map(
      (draftSite.plants || []).map((plant) => [plant.data_id, plant])
    );

    return {
      ...site,
      display_name: draftSite.display_name,
      shifts: clone(draftSite.shifts || site.shifts),
      plants: site.plants.map((plant) => {
        const draftPlant = draftPlantMap.get(plant.data_id);
        return {
          ...plant,
          ...(draftPlant
            ? {
                custom_display_name: draftPlant.custom_display_name || "",
                display_name:
                  draftPlant.custom_display_name ||
                  plant.official_name ||
                  plant.data_id,
              }
            : {}),
        };
      }),
    };
  });

  refreshed.timezone = draftSettings?.timezone || refreshed.timezone;
  // Keep the version that the draft was based on. Rebasing it silently would
  // hide a real concurrent edit and allow one browser to overwrite another.
  refreshed.version = draftSettings?.version ?? refreshed.version;
  refreshed.active_benchmarks = clone(
    draftSettings?.active_benchmarks || refreshed.active_benchmarks || {}
  );
  return refreshed;
}

export default function SettingsPage() {
  const location = useLocation();
  const navigate = useNavigate();
  const [settings, setSettings] = usePageSessionState(
    "settings",
    "draft",
    () => clone(EMPTY_SETTINGS)
  );
  const [catalog, setCatalog] = usePageSessionState(
    "settings",
    "catalog",
    [],
    { persist: false }
  );
  const [benchmarks, setBenchmarks] = usePageSessionState(
    "settings",
    "benchmarks",
    [],
    { persist: false }
  );
  const [loading, setLoading] = usePageSessionState(
    "settings",
    "loading",
    true,
    { persist: false }
  );
  const [saving, setSaving] = usePageSessionState(
    "settings",
    "saving",
    false,
    { persist: false }
  );
  const [refreshing, setRefreshing] = usePageSessionState(
    "settings",
    "refreshing",
    false,
    { persist: false }
  );
  const [error, setError] = usePageSessionState(
    "settings",
    "error",
    "",
    { persist: false }
  );
  const [message, setMessage] = useState("");
  const [discoveryError, setDiscoveryError] = usePageSessionState(
    "settings",
    "discoveryError",
    "",
    { persist: false }
  );
  const [recoveryNotice, setRecoveryNotice] = usePageSessionState(
    "settings",
    "recoveryNotice",
    "",
    { persist: false }
  );
  const [settingsConflict, setSettingsConflict] = usePageSessionState(
    "settings",
    "settingsConflict",
    false,
    { persist: false }
  );
  const [selectedSiteCode, setSelectedSiteCode] = usePageSessionState(
    "settings",
    "selectedSiteCode",
    ""
  );
  const [plantSearch, setPlantSearch] = usePageSessionState(
    "settings",
    "plantSearch",
    ""
  );
  const [openPlantIds, setOpenPlantIds] = usePageSessionState(
    "settings",
    "openPlantIds",
    []
  );
  const [hasUnsavedChanges, setHasUnsavedChanges] = usePageSessionState(
    "settings",
    "hasUnsavedChanges",
    false
  );

  const loadConfiguration = async (
    refresh = false,
    { discardDraft = false } = {}
  ) => runLatestPageTask("settings:configuration", async ({ isLatest }) => {
    try {
      refresh ? setRefreshing(true) : setLoading(true);
      setError("");
      const response = await api.get("/api/daily-report/settings", {
        params: { refresh },
      });
      if (!isLatest()) return;
      const data = response.data || {};
      const loadedCatalog = data.catalog || [];
      setCatalog(loadedCatalog);
      const shouldPreserveDraft =
        !discardDraft && hasUnsavedChanges && Boolean(settings.sites.length);
      setSettings(
        shouldPreserveDraft
          ? mergeSettingsDraft(data.settings, loadedCatalog, settings)
          : settingsFromCatalog(data.settings, loadedCatalog)
      );
      setBenchmarks(data.benchmarks || []);
      setDiscoveryError(data.discovery_error || "");
      setRecoveryNotice(data.settings_recovery?.message || "");
      setSettingsConflict(false);
      if (!shouldPreserveDraft) setHasUnsavedChanges(false);
    } catch (err) {
      if (isLatest()) {
        console.error("Settings load error:", err);
        setError("Settings could not be loaded. Please try again.");
      }
    } finally {
      if (isLatest()) {
        setLoading(false);
        setRefreshing(false);
      }
    }
  });

  useEffect(() => {
    if (catalog.length && settings.sites.length) {
      setLoading(false);
      return;
    }
    loadConfiguration(false);
  }, []);

  useEffect(() => {
    const selectedSiteStillExists = settings.sites.some(
      (site) => site.site_code === selectedSiteCode
    );
    if (!selectedSiteStillExists) {
      setSelectedSiteCode(settings.sites[0]?.site_code || "");
      setPlantSearch("");
      setOpenPlantIds([]);
    }
  }, [settings.sites, selectedSiteCode]);

  useEffect(() => {
    const focus = location.state?.settingsFocus;
    if (!focus?.data_id || !settings.sites.length) return;

    const targetSite = settings.sites.find((site) =>
      (site.plants || []).some((plant) => plant.data_id === focus.data_id)
    );
    if (!targetSite) return;

    setSelectedSiteCode(targetSite.site_code);
    setPlantSearch("");
    setOpenPlantIds((current) =>
      current.includes(focus.data_id) ? current : [...current, focus.data_id]
    );
    navigate("/settings", { replace: true, state: null });
  }, [location.state, navigate, settings.sites, setOpenPlantIds, setPlantSearch, setSelectedSiteCode]);

  const updateSite = (siteCode, updater) => {
    setHasUnsavedChanges(true);
    setSettings((current) => ({
      ...current,
      sites: current.sites.map((site) =>
        site.site_code === siteCode ? updater(site) : site
      ),
    }));
  };

  const updateSiteName = (siteCode, displayName) => {
    updateSite(siteCode, (site) => ({ ...site, display_name: displayName }));
  };

  const updateShift = (siteCode, shiftKey, property, value) => {
    updateSite(siteCode, (site) => ({
      ...site,
      shifts: {
        ...site.shifts,
        [shiftKey]: {
          ...site.shifts[shiftKey],
          [property]: value,
        },
      },
    }));
  };

  const updatePlantName = (siteCode, dataId, customDisplayName) => {
    updateSite(siteCode, (site) => ({
      ...site,
      plants: site.plants.map((plant) =>
        plant.data_id === dataId
          ? {
              ...plant,
              custom_display_name: customDisplayName,
              display_name:
                customDisplayName.trim() || plant.official_name || plant.data_id,
            }
          : plant
      ),
    }));
  };

  const updateActiveBenchmark = (dataId, field, fileName) => {
    setHasUnsavedChanges(true);
    setSettings((current) => {
      const active = clone(current.active_benchmarks || {});
      active[dataId] = { ...(active[dataId] || {}) };
      if (fileName) active[dataId][field] = fileName;
      else delete active[dataId][field];
      if (!Object.keys(active[dataId]).length) delete active[dataId];
      return { ...current, active_benchmarks: active };
    });
  };

  const validateSettings = () => {
    for (const site of settings.sites) {
      if (!site.display_name?.trim()) {
        return `Please enter a display name for site ${site.site_code}.`;
      }
      const { morning, night } = site.shifts;
      if (!morning.start || !morning.end || !night.start || !night.end) {
        return `All shift times are required for site ${site.site_code}.`;
      }
      if (morning.start === morning.end || night.start === night.end) {
        return `A shift start and end cannot be the same for site ${site.site_code}.`;
      }
      if (morning.end !== night.start) {
        return `Morning Shift end must match Night Shift start for site ${site.site_code}.`;
      }
      if (night.end !== morning.start) {
        return `Night Shift end must match Morning Shift start for site ${site.site_code}.`;
      }
    }
    return "";
  };

  const handleSave = async () => {
    const validationError = validateSettings();
    if (validationError) {
      setError(validationError);
      setMessage("");
      return;
    }
    try {
      setSaving(true);
      setError("");
      setMessage("");
      const response = await api.put("/api/daily-report/settings", settings);
      setSettings(settingsFromCatalog(response.data.settings, catalog));
      setHasUnsavedChanges(false);
      setSettingsConflict(false);
      setMessage(response.data.message || "Settings saved successfully.");
      if (response.data.audit_status === "audit_failed") {
        setRecoveryNotice(
          "Settings were saved, but the audit-history record could not be written."
        );
      }
    } catch (err) {
      console.error("Settings save error:", err);
      const conflict = err?.response?.status === 409;
      setSettingsConflict(conflict);
      setError(
        conflict
          ? "Settings changed since this draft was loaded. Reload the latest settings before saving again."
          : "Settings could not be saved. Please try again."
      );
    } finally {
      setSaving(false);
    }
  };

  const reloadLatestSettings = async () => {
    const confirmed = window.confirm(
      "Reload the latest saved settings? Your unsaved draft in this tab will be discarded."
    );
    if (!confirmed) return;
    setHasUnsavedChanges(false);
    await loadConfiguration(false, { discardDraft: true });
  };

  const catalogPlantMap = new Map();
  catalog.forEach((site) =>
    (site.plants || []).forEach((plant) =>
      catalogPlantMap.set(plant.data_id, plant)
    )
  );

  const missingAssignmentCount = settings.sites.reduce(
    (siteTotal, site) =>
      siteTotal +
      site.plants.reduce((plantTotal, plant) => {
        const discovered = catalogPlantMap.get(plant.data_id);
        const active = settings.active_benchmarks?.[plant.data_id] || {};
        return (
          plantTotal +
          (discovered?.sterilizers || []).filter((item) => !active[item.field])
            .length
        );
      }, 0),
    0
  );

  const selectedSite =
    settings.sites.find((site) => site.site_code === selectedSiteCode) ||
    settings.sites[0] ||
    null;
  const normalizedPlantSearch = plantSearch.trim().toLowerCase();
  const visiblePlants = (selectedSite?.plants || []).filter((plant) => {
    if (!normalizedPlantSearch) return true;
    return (
      (plant.display_name || "").toLowerCase().includes(normalizedPlantSearch) ||
      (plant.official_name || "").toLowerCase().includes(normalizedPlantSearch) ||
      plant.data_id.toLowerCase().includes(normalizedPlantSearch)
    );
  });

  const selectSite = (siteCode) => {
    setSelectedSiteCode(siteCode);
    setPlantSearch("");
    setOpenPlantIds([]);
  };

  const togglePlant = (dataId) => {
    setOpenPlantIds((current) =>
      current.includes(dataId)
        ? current.filter((item) => item !== dataId)
        : [...current, dataId]
    );
  };

  const expandVisiblePlants = () => {
    setOpenPlantIds(visiblePlants.map((plant) => plant.data_id));
  };

  return (
    <div className="app-shell">
      <TopNav />
      <main className={`app-main settings-page ${hasUnsavedChanges ? "has-unsaved-settings" : ""}`}>
        <header className="dashboard-topbar compact-dashboard-topbar">
          <div className="dashboard-title-group">
            <div className="dashboard-title-row">
              <h1>Settings</h1>
              {missingAssignmentCount > 0 && (
                <span className="settings-warning-pill">
                  {missingAssignmentCount} benchmark assignments required
                </span>
              )}
            </div>
          </div>
          <div className="dashboard-actions settings-actions">
            <Link className="btn-secondary settings-link-button" to="/benchmark">
              Benchmark Management
            </Link>
            <button
              type="button"
              className="btn-secondary"
              onClick={() => loadConfiguration(true)}
              disabled={refreshing || saving}
            >
              {refreshing ? "Refreshing..." : "Refresh Plant List"}
            </button>
          </div>
        </header>

        {error && <div className="error-box">{error}</div>}
        {settingsConflict && (
          <div className="warning-box settings-conflict-box">
            <span>Your draft is preserved, but it is based on an older saved version.</span>
            <button type="button" className="btn-secondary" onClick={reloadLatestSettings}>
              Reload Latest Settings
            </button>
          </div>
        )}
        <NotificationToast
          message={message}
          tone="success"
          autoDismissMs={3000}
          onClose={() => setMessage("")}
        />
        {discoveryError && (
          <div className="warning-box">
            The plant list could not be fully refreshed. Existing saved settings are still available.
          </div>
        )}
        {recoveryNotice && <div className="warning-box">{recoveryNotice}</div>}

        {loading ? (
          <section className="dash-card">
            <div className="empty-state">Loading settings...</div>
          </section>
        ) : !settings.sites.length ? (
          <section className="dash-card">
            <div className="empty-state">
              No plants are available from the connected data source. Select
              “Refresh Plant List” to try again.
            </div>
          </section>
        ) : selectedSite ? (
          <div className="hierarchical-settings-list">
            <section className="dash-card settings-site-card">
              <div className="settings-site-heading unified-site-heading">
                <div className="settings-site-identity unified-site-identity">
                  <label>
                    Site
                    <select
                      value={selectedSiteCode}
                      onChange={(event) => selectSite(event.target.value)}
                    >
                      {settings.sites.map((site) => (
                        <option value={site.site_code} key={site.site_code}>
                          {site.display_name || site.site_code} ({site.site_code})
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    Site Display Name
                    <input
                      value={selectedSite.display_name}
                      onChange={(event) =>
                        updateSiteName(selectedSite.site_code, event.target.value)
                      }
                      placeholder="Enter a site display name"
                    />
                  </label>
                  <div>
                    <span>Automatically Derived Site Code</span>
                    <strong>{selectedSite.site_code}</strong>
                  </div>
                </div>
                <div className="site-selector-summary unified-site-count">
                  <strong>{settings.sites.length}</strong>
                  <span>{settings.sites.length === 1 ? "site" : "sites"}</span>
                </div>
              </div>

              <div className="settings-subsection">
                <div className="settings-subsection-title">
                  <strong>
                    Shift Setting for {selectedSite.display_name || selectedSite.site_code}
                  </strong>
                  <span>These hours apply to every plant under this site.</span>
                </div>
                <div className="shift-setting-grid site-shift-grid">
                  {["morning", "night"].map((shiftKey) => {
                    const shift = selectedSite.shifts[shiftKey];
                    return (
                      <div
                        className={`shift-setting-card ${shiftKey}`}
                        key={shiftKey}
                      >
                        <div className="shift-card-title">
                          {shiftKey === "morning" ? "☀" : "☾"} {shift.label}
                        </div>
                        <div className="shift-time-row">
                          <label>
                            Start Time
                            <input
                              type="time"
                              value={shift.start}
                              onChange={(event) =>
                                updateShift(
                                  selectedSite.site_code,
                                  shiftKey,
                                  "start",
                                  event.target.value
                                )
                              }
                            />
                          </label>
                          <span>to</span>
                          <label>
                            End Time
                            <input
                              type="time"
                              value={shift.end}
                              onChange={(event) =>
                                updateShift(
                                  selectedSite.site_code,
                                  shiftKey,
                                  "end",
                                  event.target.value
                                )
                              }
                            />
                          </label>
                        </div>
                      </div>
                    );
                  })}
                </div>
                <div className="settings-note">
                  Use <strong>00:00</strong> for midnight on the following day.
                  The two site shifts must connect and cover 24 hours.
                </div>
              </div>

              <div className="settings-subsection">
                <div className="settings-subsection-title plant-section-heading">
                  <div>
                    <strong>
                      Plants under {selectedSite.display_name || selectedSite.site_code}
                    </strong>
                  </div>
                  <span>{selectedSite.plants.length} plants</span>
                </div>

                <div className="plant-list-toolbar">
                  <label className="plant-search-field">
                    Search Plant
                    <input
                      type="search"
                      value={plantSearch}
                      onChange={(event) => setPlantSearch(event.target.value)}
                      placeholder="Search by plant name or Data ID"
                    />
                  </label>
                  <div className="plant-accordion-actions">
                    <button
                      type="button"
                      className="btn-secondary"
                      onClick={expandVisiblePlants}
                      disabled={!visiblePlants.length}
                    >
                      Expand All
                    </button>
                    <button
                      type="button"
                      className="btn-secondary"
                      onClick={() => setOpenPlantIds([])}
                      disabled={!openPlantIds.length}
                    >
                      Collapse All
                    </button>
                  </div>
                </div>

                <div className="plant-setting-list">
                  {!visiblePlants.length ? (
                    <div className="plant-search-empty">
                      No plants match “{plantSearch}”.
                    </div>
                  ) : (
                    visiblePlants.map((plant) => {
                      const discovered = catalogPlantMap.get(plant.data_id);
                      const sterilizers = discovered?.sterilizers || [];
                      const active =
                        settings.active_benchmarks?.[plant.data_id] || {};
                      const assignedCount = sterilizers.filter(
                        (sterilizer) => active[sterilizer.field]
                      ).length;
                      const isOpen = openPlantIds.includes(plant.data_id);
                      const benchmarkStatusClass = !sterilizers.length
                        ? "unavailable"
                        : assignedCount === sterilizers.length
                        ? "complete"
                        : "incomplete";
                      const benchmarkStatusText = !sterilizers.length
                        ? "Metadata unavailable"
                        : `${assignedCount}/${sterilizers.length} benchmarks assigned`;

                      return (
                        <article
                          className={`plant-setting-card ${isOpen ? "open" : ""}`}
                          key={plant.data_id}
                        >
                          <button
                            type="button"
                            className="plant-accordion-trigger"
                            onClick={() => togglePlant(plant.data_id)}
                            aria-expanded={isOpen}
                            aria-controls={`plant-settings-${plant.data_id}`}
                          >
                            <span className="plant-accordion-chevron" aria-hidden="true">
                              {isOpen ? "▾" : "▸"}
                            </span>
                            <span className="plant-accordion-identity">
                              <strong>{plant.display_name || plant.data_id}</strong>
                              <code>{plant.data_id}</code>
                            </span>
                            <span className="plant-accordion-summary">
                              <span>
                                {sterilizers.length} {sterilizers.length === 1 ? "sterilizer" : "sterilizers"}
                              </span>
                              <span className={`plant-benchmark-summary ${benchmarkStatusClass}`}>
                                {benchmarkStatusText}
                              </span>
                            </span>
                          </button>

                          {isOpen && (
                            <div
                              className="plant-accordion-content"
                              id={`plant-settings-${plant.data_id}`}
                            >
                              <div className="plant-setting-header">
                                <label>
                                  Custom Plant Alias (Optional)
                                  <input
                                    value={plant.custom_display_name || ""}
                                    onChange={(event) =>
                                      updatePlantName(
                                        selectedSite.site_code,
                                        plant.data_id,
                                        event.target.value
                                      )
                                    }
                                    placeholder="Leave blank to use the official name"
                                  />
                                </label>
                                <div>
                                  <span>Official Plant Name (Read Only)</span>
                                  <strong>{plant.official_name || "Not available"}</strong>
                                </div>
                                <div>
                                  <span>InfluxDB Plant Data ID</span>
                                  <strong>{plant.data_id}</strong>
                                </div>
                              </div>

                              {!sterilizers.length ? (
                                <div className="plant-metadata-warning">
                                  This configured plant is not currently available
                                  in InfluxDB metadata.
                                </div>
                              ) : (
                                <div className="benchmark-assignment-table-wrap">
                                  <table className="benchmark-assignment-table">
                                    <thead>
                                      <tr>
                                        <th>Sterilizer</th>
                                        <th>Database Field</th>
                                        <th>Active Benchmark</th>
                                        <th>Status</th>
                                        <th>Action</th>
                                      </tr>
                                    </thead>
                                    <tbody>
                                      {sterilizers.map((sterilizer) => {
                                        const matchingBenchmarks = benchmarks.filter(
                                          (benchmark) =>
                                            benchmark.validation_status !== "invalid" &&
                                            benchmarkDataId(benchmark) === plant.data_id &&
                                            benchmark.field === sterilizer.field
                                        );
                                        const selectedFile =
                                          active[sterilizer.field] || "";
                                        return (
                                          <tr key={sterilizer.field}>
                                            <td>{sterilizer.sterilizer_name}</td>
                                            <td>
                                              <code>{sterilizer.field}</code>
                                            </td>
                                            <td>
                                              <select
                                                value={selectedFile}
                                                onChange={(event) =>
                                                  updateActiveBenchmark(
                                                    plant.data_id,
                                                    sterilizer.field,
                                                    event.target.value
                                                  )
                                                }
                                              >
                                                <option value="">
                                                  Select active benchmark
                                                </option>
                                                {matchingBenchmarks.map((benchmark) => (
                                                  <option
                                                    key={benchmark.file_name}
                                                    value={benchmark.file_name}
                                                  >
                                                    {benchmark.benchmark_name
                                                      ? `${benchmark.benchmark_name} — ${benchmark.file_name}`
                                                      : benchmark.file_name}
                                                  </option>
                                                ))}
                                              </select>
                                              {!matchingBenchmarks.length && (
                                                <small>
                                                  Create a matching benchmark for this
                                                  plant and sterilizer.
                                                </small>
                                              )}
                                            </td>
                                            <td>
                                              <span
                                                className={`assignment-status ${
                                                  selectedFile ? "ready" : "missing"
                                                }`}
                                              >
                                                {selectedFile ? "Active" : "Required"}
                                              </span>
                                            </td>
                                            <td>
                                              {!selectedFile ? (
                                                <button
                                                  type="button"
                                                  className="btn-secondary benchmark-row-create-button"
                                                  onClick={() =>
                                                    navigate("/benchmark", {
                                                      state: {
                                                        benchmarkPrefill: {
                                                          tag_id: plant.data_id,
                                                          field: sterilizer.field,
                                                          plant_display_name:
                                                            plant.display_name || plant.data_id,
                                                          source_unit: "bar",
                                                        },
                                                      },
                                                    })
                                                  }
                                                >
                                                  Create Benchmark
                                                </button>
                                              ) : (
                                                <span className="table-action-placeholder">—</span>
                                              )}
                                            </td>
                                          </tr>
                                        );
                                      })}
                                    </tbody>
                                  </table>
                                </div>
                              )}
                            </div>
                          )}
                        </article>
                      );
                    })
                  )}
                </div>
              </div>
            </section>
          </div>
        ) : null}

        {hasUnsavedChanges && (
          <div className="settings-save-bar-shell no-print">
            <div className="settings-save-bar" role="region" aria-label="Unsaved settings changes">
              <div className="settings-save-status">
                <span className="settings-save-status-dot" aria-hidden="true" />
                <strong>Unsaved settings changes</strong>
              </div>
              <button
                type="button"
                className="btn-primary"
                onClick={handleSave}
                disabled={saving || loading}
              >
                {saving ? "Saving..." : "Save All Settings"}
              </button>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
