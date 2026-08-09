import React, { useEffect, useMemo, useRef, useState } from "react";
import api from "../api";
import {
  enrichBenchmarksWithInferredDisplayContext,
  formatBenchmarkOptionLabel,
  formatBenchmarkShortLabel,
} from "../utils/sterilizerDisplay";
import "../ChatbotWidget.css";

function nowId() {
  return `${Date.now()}_${Math.random().toString(16).slice(2)}`;
}

function BotMessage({ message }) {
  return (
    <div className={`chatbot-message ${message.role}`}>
      <div className="chatbot-message-bubble">
        {String(message.text || "")
          .split("\n")
          .map((line, index) => (
            <React.Fragment key={index}>
              {line}
              {index < String(message.text || "").split("\n").length - 1 && <br />}
            </React.Fragment>
          ))}
      </div>
    </div>
  );
}

const STAGE_OPTIONS = [
  { value: "", label: "No stage / cycle-level" },
  { value: "S1", label: "Stage 1" },
  { value: "S2", label: "Stage 2" },
  { value: "S3", label: "Stage 3" },
];

function getBenchmarkOptionLabel(item, benchmarkOptions) {
  return formatBenchmarkOptionLabel(item, {
    includeFileName: true,
    benchmarkOptions,
  });
}

function getBenchmarkShortLabel(fileName, benchmarkOptions) {
  return formatBenchmarkShortLabel(fileName, benchmarkOptions);
}

export default function ChatbotWidget() {
  const [open, setOpen] = useState(false);
  const [showForm, setShowForm] = useState(false);
  const [messageText, setMessageText] = useState("");
  const [loading, setLoading] = useState(false);
  const [chatContext, setChatContext] = useState({});
  const [messages, setMessages] = useState([
    {
      id: nowId(),
      role: "bot",
      text:
        "Hi, I can help analyse sterilizer cycles. You can ask: Check SKPG Sterilizer 3 from 24 June 2026 1pm to 3pm.",
    },
  ]);

  const [benchmarkOptions, setBenchmarkOptions] = useState([]);
  const [benchmarkLoading, setBenchmarkLoading] = useState(false);
  const [benchmarkError, setBenchmarkError] = useState("");

  const [formParams, setFormParams] = useState({
    tag_id: "",
    field: "",
    start_time: "",
    stop_time: "",
    stage: "",
    cycle_no: "",
    benchmark_file_name: "",
  });

  const inputRef = useRef(null);

  const hasContext = useMemo(() => {
    return Boolean(chatContext?.cycle_results?.length);
  }, [chatContext]);

  const displayBenchmarkOptions = useMemo(() => {
    return enrichBenchmarksWithInferredDisplayContext(benchmarkOptions);
  }, [benchmarkOptions]);

  const selectedBenchmarkLabel = useMemo(() => {
    return getBenchmarkShortLabel(formParams.benchmark_file_name, displayBenchmarkOptions);
  }, [formParams.benchmark_file_name, displayBenchmarkOptions]);

  useEffect(() => {
    if (!open) return;

    let cancelled = false;

    async function loadBenchmarks() {
      setBenchmarkLoading(true);
      setBenchmarkError("");

      try {
        const response = await api.get("/api/benchmarks");
        const items = response.data?.benchmarks || [];

        if (!cancelled) {
          setBenchmarkOptions(Array.isArray(items) ? items : []);
        }
      } catch (error) {
        if (!cancelled) {
          const detail =
            error?.response?.data?.detail ||
            error?.message ||
            "Unable to load benchmark list.";
          setBenchmarkError(detail);
          setBenchmarkOptions([]);
        }
      } finally {
        if (!cancelled) {
          setBenchmarkLoading(false);
        }
      }
    }

    loadBenchmarks();

    return () => {
      cancelled = true;
    };
  }, [open]);

  function updateFormField(key, value) {
    setFormParams((prev) => ({ ...prev, [key]: value }));
  }

  function buildFormParamsForRequest() {
    const clean = {};
    for (const [key, value] of Object.entries(formParams)) {
      if (value !== null && value !== undefined && String(value).trim() !== "") {
        clean[key] = key === "cycle_no" ? Number(value) : String(value).trim();
      }
    }
    return clean;
  }

  async function sendMessage(customText = null) {
    const text = String(customText ?? messageText).trim();
    if (!text || loading) return;

    const userMsg = { id: nowId(), role: "user", text };
    setMessages((prev) => [...prev, userMsg]);
    setMessageText("");
    setLoading(true);

    try {
      const response = await api.post("/api/chatbot/message", {
        message: text,
        context: chatContext || {},
        form_params: buildFormParamsForRequest(),
      });

      const reply = response.data?.reply || "I could not generate a response.";
      const nextContext = response.data?.context || chatContext || {};

      setChatContext(nextContext);
      setMessages((prev) => [
        ...prev,
        {
          id: nowId(),
          role: "bot",
          text: reply,
        },
      ]);
    } catch (error) {
      const detail =
        error?.response?.data?.detail ||
        error?.message ||
        "The chatbot request failed.";

      setMessages((prev) => [
        ...prev,
        {
          id: nowId(),
          role: "bot error",
          text: `Sorry, I could not analyse that request. ${detail}`,
        },
      ]);
    } finally {
      setLoading(false);
      setTimeout(() => inputRef.current?.focus(), 50);
    }
  }

  function clearChat() {
    setChatContext({});
    setMessages([
      {
        id: nowId(),
        role: "bot",
        text:
          "Chat cleared. Please provide a sterilizer and time range, for example: Check SKPG Sterilizer 3 from 24 June 2026 1pm to 3pm.",
      },
    ]);
  }

  return (
    <>
      <button
        type="button"
        className="chatbot-floating-button"
        onClick={() => setOpen((prev) => !prev)}
        aria-label="Open AI chatbot"
      >
        {open ? "×" : "AI"}
      </button>

      {open && (
        <div className="chatbot-panel">
          <div className="chatbot-header">
            <div>
              <h3>AI Sterilizer Assistant</h3>
              <p>Ask about cycle normality, stage RCA, and recommended actions.</p>
            </div>
            <button type="button" onClick={() => setOpen(false)}>
              Close
            </button>
          </div>

          <div className="chatbot-tools-row">
            <button type="button" onClick={() => setShowForm((prev) => !prev)}>
              {showForm ? "Hide Parameters" : "Optional Parameters"}
            </button>
            <button type="button" onClick={clearChat}>
              Clear
            </button>
            {hasContext && <span className="chatbot-context-pill">Context saved</span>}
            {formParams.benchmark_file_name && (
              <span className="chatbot-benchmark-pill" title={selectedBenchmarkLabel}>
                Benchmark selected
              </span>
            )}
          </div>

          {showForm && (
            <div className="chatbot-param-form">
              <div className="chatbot-form-row">
                <label>
                  Tag ID
                  <input
                    value={formParams.tag_id}
                    onChange={(e) => updateFormField("tag_id", e.target.value)}
                    placeholder="SAMYSK_PSTR_240004"
                  />
                </label>
                <label>
                  Field
                  <input
                    value={formParams.field}
                    onChange={(e) => updateFormField("field", e.target.value)}
                    placeholder="ch4"
                  />
                </label>
              </div>

              <div className="chatbot-form-row">
                <label>
                  Start Time
                  <input
                    value={formParams.start_time}
                    onChange={(e) => updateFormField("start_time", e.target.value)}
                    placeholder="2026-06-24 13:00"
                  />
                </label>
                <label>
                  Stop Time
                  <input
                    value={formParams.stop_time}
                    onChange={(e) => updateFormField("stop_time", e.target.value)}
                    placeholder="2026-06-24 15:00"
                  />
                </label>
              </div>

              <div className="chatbot-form-row">
                <label>
                  Stage
                  <select
                    value={formParams.stage}
                    onChange={(e) => updateFormField("stage", e.target.value)}
                  >
                    {STAGE_OPTIONS.map((item) => (
                      <option key={item.value} value={item.value}>
                        {item.label}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  Cycle No.
                  <input
                    value={formParams.cycle_no}
                    onChange={(e) => updateFormField("cycle_no", e.target.value)}
                    placeholder="2"
                  />
                </label>
              </div>

              <label className="chatbot-benchmark-field">
                Benchmark to use
                <select
                  value={formParams.benchmark_file_name}
                  onChange={(e) => updateFormField("benchmark_file_name", e.target.value)}
                  disabled={benchmarkLoading}
                >
                  <option value="">Auto default / best matching benchmark</option>
                  {displayBenchmarkOptions.map((item) => {
                    const fileName = item.file_name || "";
                    if (!fileName) return null;

                    return (
                      <option key={fileName} value={fileName}>
                        {getBenchmarkOptionLabel(item, displayBenchmarkOptions)}
                      </option>
                    );
                  })}
                </select>

                <small className="chatbot-form-helper">
                  {benchmarkLoading
                    ? "Loading benchmarks..."
                    : benchmarkError
                      ? `Unable to load benchmarks: ${benchmarkError}`
                      : formParams.benchmark_file_name
                        ? `Selected: ${selectedBenchmarkLabel}`
                        : "Leave as Auto if you want the system to choose the best matching benchmark."}
                </small>
              </label>
            </div>
          )}

          <div className="chatbot-messages">
            {messages.map((message) => (
              <BotMessage key={message.id} message={message} />
            ))}
            {loading && (
              <div className="chatbot-message bot">
                <div className="chatbot-message-bubble chatbot-thinking">
                  Analysing pressure-time data and RCA evidence...
                </div>
              </div>
            )}
          </div>


          <div className="chatbot-input-row">
            <textarea
              ref={inputRef}
              value={messageText}
              onChange={(e) => setMessageText(e.target.value)}
              placeholder="Ask: Check SKPG Sterilizer 3 from 24 June 2026 1pm to 3pm"
              rows={2}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  sendMessage();
                }
              }}
            />
            <button type="button" onClick={() => sendMessage()} disabled={loading}>
              Send
            </button>
          </div>
        </div>
      )}
    </>
  );
}
