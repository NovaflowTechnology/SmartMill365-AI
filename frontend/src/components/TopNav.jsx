import { Link, useLocation } from "react-router-dom";

const NAV_ITEMS = [
  {
    to: "/live-monitor",
    label: "Live Monitoring",
    shortLabel: "Live",
    icon: "LM",
  },
  {
    to: "/compare",
    label: "AI Comparison",
    shortLabel: "AI",
    icon: "AI",
  },
  {
    to: "/benchmark",
    label: "Benchmark Management",
    shortLabel: "BM",
    icon: "BM",
  },
  {
    to: "/rca-rules",
    label: "RCA Rule Management",
    shortLabel: "Rules",
    icon: "RL",
  },
];

export default function TopNav() {
  const location = useLocation();

  return (
    <aside className="app-rail" aria-label="Main navigation">
      <div className="app-rail-logo">NF</div>

      <nav className="app-rail-nav">
        {NAV_ITEMS.map((item) => {
          const active = location.pathname === item.to;

          return (
            <Link
              key={item.to}
              to={item.to}
              title={item.label}
              aria-label={item.label}
              className={`app-rail-link ${active ? "active" : ""}`}
            >
              <span>{item.icon}</span>
            </Link>
          );
        })}
      </nav>
    </aside>
  );
}
