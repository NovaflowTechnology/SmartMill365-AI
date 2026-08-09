import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import BenchmarkPage from "./pages/BenchmarkPage";
import RcaRuleManagementPage from "./pages/RcaRuleManagementPage";
import ComparisonPage from "./pages/ComparisonPage";
import LiveMonitoringPage from "./pages/LiveMonitoringPage";
import ChatbotWidget from "./components/ChatbotWidget";

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Navigate to="/live-monitor" replace />} />
        <Route path="/live-monitor" element={<LiveMonitoringPage />} />
        <Route path="/compare" element={<ComparisonPage />} />
        <Route path="/benchmark" element={<BenchmarkPage />} />
        <Route path="/rca-rules" element={<RcaRuleManagementPage />} />
      </Routes>

      <ChatbotWidget />
    </BrowserRouter>
  );
}