import React, { useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import api from "../api";
import { useAuth } from "../auth/AuthContext";
import "../ChatbotWidget.css";

function nowId() {
  return `${Date.now()}_${Math.random().toString(16).slice(2)}`;
}

function BotMessage({ message }) {
  const lines = String(message.text || "").split("\n");
  return (
    <div className={`chatbot-message ${message.role}`}>
      <div className="chatbot-message-bubble">
        {lines.map((line, index) => (
          <React.Fragment key={index}>
            {line}
            {index < lines.length - 1 && <br />}
          </React.Fragment>
        ))}
      </div>
    </div>
  );
}

const INITIAL_MESSAGE =
  "Hi, I can help analyze sterilizer cycles using the active benchmark configured in Settings. Ask naturally—even if some details are missing, I will save what you provide and ask for all remaining details together. If no pressure unit is specified, I will use bar.";

export default function ChatbotWidget() {
  const navigate = useNavigate();
  const { user } = useAuth();
  const [open, setOpen] = useState(false);
  const [messageText, setMessageText] = useState("");
  const [loading, setLoading] = useState(false);
  const [chatContext, setChatContext] = useState({});
  const [messages, setMessages] = useState([
    { id: nowId(), role: "bot", text: INITIAL_MESSAGE },
  ]);

  const inputRef = useRef(null);

  const hasContext = useMemo(() => {
    return Boolean(
      chatContext?.cycle_results?.length ||
      chatContext?.pending_request ||
      chatContext?.awaiting_active_benchmark
    );
  }, [chatContext]);

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
      });

      const reply = response.data?.reply || "I could not generate a response.";
      const nextContext = response.data?.context || chatContext || {};

      setChatContext(nextContext);
      setMessages((prev) => [
        ...prev,
        { id: nowId(), role: "bot", text: reply },
      ]);
    } catch (error) {
      console.error("Chatbot request error:", error);
      const status = Number(error?.response?.status || 0);
      const detail = error?.response?.data?.detail;
      const userMessage =
        status > 0 && status < 500 && typeof detail === "string"
          ? detail
          : error?.request
            ? "The chatbot service could not be reached. Please try again."
            : "The chatbot service could not complete the request. Please try again.";

      setMessages((prev) => [
        ...prev,
        {
          id: nowId(),
          role: "bot error",
          text: `Sorry, I could not analyze that request. ${userMessage}`,
        },
      ]);
    } finally {
      setLoading(false);
      setTimeout(() => inputRef.current?.focus(), 50);
    }
  }

  function clearChat() {
    setChatContext({});
    setMessageText("");
    setMessages([
      {
        id: nowId(),
        role: "bot",
        text:
          "Chat cleared. Tell me what you want to analyze; I will ask for all missing plant, sterilizer, or time details together. If no pressure unit is specified, I will use bar.",
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
              <p>Ask about cycle normality, stage analysis, and recommended actions.</p>
            </div>
            <button type="button" onClick={() => setOpen(false)}>
              Close
            </button>
          </div>

          <div className="chatbot-tools-row">
            <button type="button" onClick={clearChat}>
              Clear
            </button>
            {hasContext && <span className="chatbot-context-pill">Context saved</span>}
          </div>

          <div className="chatbot-messages">
            {messages.map((message) => (
              <BotMessage key={message.id} message={message} />
            ))}
            {loading && (
              <div className="chatbot-message bot">
                <div className="chatbot-message-bubble chatbot-thinking">
                  Analyzing pressure-time data and supporting evidence...
                </div>
              </div>
            )}
          </div>

          {chatContext?.awaiting_active_benchmark && (
            <div className="chatbot-settings-actions" role="group" aria-label="Active benchmark actions">
              <div>
                <strong>Analysis request saved</strong>
                <span>{user?.role === "viewer" ? "An Editor or Admin must assign the active benchmark before this analysis can continue." : "Assign the active benchmark in Settings, then re-check it here."}</span>
              </div>
              <div className="chatbot-settings-buttons">
                {user?.role !== "viewer" && (
                  <button type="button" onClick={() => navigate("/settings")} disabled={loading}>
                    Open Settings
                  </button>
                )}
                <button type="button" onClick={() => sendMessage("Done")} disabled={loading}>
                  Done — Re-check
                </button>
              </div>
            </div>
          )}

          <div className="chatbot-input-row">
            <textarea
              ref={inputRef}
              value={messageText}
              onChange={(e) => setMessageText(e.target.value)}
              placeholder="Ask: Check Sterilizer 3 from today 3am until now"
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
