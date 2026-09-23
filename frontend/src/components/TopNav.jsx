import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Link, useLocation } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";

const SIDEBAR_COLLAPSED_STORAGE_KEY = "nf-sidebar-collapsed";

function readStoredCollapsedState() {
  try { return window.localStorage.getItem(SIDEBAR_COLLAPSED_STORAGE_KEY) === "true"; }
  catch { return false; }
}

function SidebarToggleIcon({ collapsed }) {
  return <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16"/>{collapsed ? <path d="m13 9 3 3-3 3"/> : <path d="m16 9-3 3 3 3"/>}</svg>;
}

function UserAvatar({ initials, name }) {
  const avatarRef = useRef(null);
  const [tooltipPosition, setTooltipPosition] = useState(null);

  const showTooltip = () => {
    const rect = avatarRef.current?.getBoundingClientRect();
    if (!rect) return;
    setTooltipPosition({
      left: rect.right + 10,
      top: rect.top + rect.height / 2,
    });
  };

  const hideTooltip = () => setTooltipPosition(null);

  return (
    <>
      <div
        ref={avatarRef}
        className="app-rail-user-avatar"
        tabIndex={0}
        aria-label={`Logged in as ${name}`}
        onMouseEnter={showTooltip}
        onMouseLeave={hideTooltip}
        onFocus={showTooltip}
        onBlur={hideTooltip}
      >
        {initials || "U"}
      </div>
      {tooltipPosition && createPortal(
        <div
          className="app-rail-user-tooltip"
          role="tooltip"
          style={{ left: tooltipPosition.left, top: tooltipPosition.top }}
        >
          {name}
        </div>,
        document.body,
      )}
    </>
  );
}

function NavIcon({ name }) {
  const p = { width: 20, height: 20, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.8, strokeLinecap: "round", strokeLinejoin: "round", "aria-hidden": true };
  if (name === "monitor") return <svg {...p}><path d="M3 3v18h18"/><path d="m6 15 3-4 3 2 4-6 3 3"/></svg>;
  if (name === "analysis") return <svg {...p}><circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/><path d="M8 12.5 10.2 10l2 1.4L14.5 8"/></svg>;
  if (name === "report") return <svg {...p}><path d="M6 2h9l4 4v16H6z"/><path d="M14 2v5h5"/><path d="M9 13h6M9 17h6M9 9h2"/></svg>;
  if (name === "settings") return <svg {...p}><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2.8 2.8-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6v.2h-4V21a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1L4.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9A1.7 1.7 0 0 0 3 14H2.8v-4H3a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9L4.2 7 7 4.2l.1.1A1.7 1.7 0 0 0 9 4.6a1.7 1.7 0 0 0 1-1.6v-.2h4V3a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.3l.1-.1L19.8 7l-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.6 1h.2v4H21a1.7 1.7 0 0 0-1.6 1Z"/></svg>;
  if (name === "users") return <svg {...p}><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.9M16 3.1a4 4 0 0 1 0 7.8"/></svg>;
  if (name === "logout") return <svg {...p}><path d="M10 17l5-5-5-5M15 12H3"/><path d="M14 3h5a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-5"/></svg>;
  return <svg {...p}><path d="M4 4h16v16H4z"/><path d="M8 9h8M8 13h5M8 17h3"/><path d="m15 15 1.5 1.5L20 13"/></svg>;
}

const OPERATION_ITEMS = [
  { to: "/live-monitor", label: "Live Monitoring", icon: "monitor", roles: ["admin", "editor", "viewer"] },
  { to: "/compare", label: "AI Comparison", icon: "analysis", roles: ["admin", "editor", "viewer"] },
  { to: "/daily-report", label: "Daily Report", icon: "report", roles: ["admin", "editor", "viewer"] },
];
const CONFIG_ITEMS = [
  { to: "/settings", label: "Settings", icon: "settings", roles: ["admin", "editor"], activePaths: ["/settings", "/benchmark"] },
  { to: "/rca-rules", label: "Analysis Rule Management", icon: "rules", roles: ["admin", "editor"] },
  { to: "/account-management", label: "Account Management", icon: "users", roles: ["admin"] },
];

function NavGroup({ title, items, role, location }) {
  const visible = items.filter((item) => item.roles.includes(role));
  if (!visible.length) return null;
  return <><div className="app-rail-section-label">{title}</div><nav className="app-rail-nav">{visible.map((item) => {
    const active = (item.activePaths || [item.to]).includes(location.pathname);
    return <Link key={item.to} to={item.to} title={item.label} aria-label={item.label} aria-current={active ? "page" : undefined} className={`app-rail-link ${active ? "active" : ""}`}><span className="app-rail-link-icon"><NavIcon name={item.icon}/></span><span className="app-rail-link-label">{item.label}</span></Link>;
  })}</nav></>;
}

export default function TopNav() {
  const location = useLocation();
  const railRef = useRef(null);
  const [collapsed, setCollapsed] = useState(readStoredCollapsedState);
  const { user, logout } = useAuth();

  useLayoutEffect(() => {
    const shell = railRef.current?.closest(".app-shell");
    shell?.classList.toggle("app-shell--rail-collapsed", collapsed);
    return () => shell?.classList.remove("app-shell--rail-collapsed");
  }, [collapsed]);

  useEffect(() => {
    try { window.localStorage.setItem(SIDEBAR_COLLAPSED_STORAGE_KEY, String(collapsed)); } catch {}
    window.dispatchEvent(new Event("resize"));
    const timer = window.setTimeout(() => window.dispatchEvent(new Event("resize")), 240);
    return () => window.clearTimeout(timer);
  }, [collapsed]);

  const role = user?.role || "viewer";
  const displayName = user?.full_name?.trim() || user?.email || "User";
  const initials = displayName.split(/\s+/).filter(Boolean).slice(0,2).map((part) => part[0]?.toUpperCase()).join("");

  return (
    <aside ref={railRef} className={`app-rail ${collapsed ? "app-rail--collapsed" : ""}`} aria-label="Main navigation">
      <div className="app-rail-brand"><div className="app-rail-logo">NF</div><div className="app-rail-brand-copy"><strong>Sterilizer Intelligence</strong><span>Industrial Analytics Platform</span></div></div>
      <div className="app-rail-controls"><button type="button" className="app-rail-toggle" onClick={() => setCollapsed((value) => !value)} aria-label={collapsed ? "Expand navigation" : "Collapse navigation"} aria-expanded={!collapsed} title={collapsed ? "Expand navigation" : "Collapse navigation"}><SidebarToggleIcon collapsed={collapsed}/></button></div>
      <NavGroup title="Operations" items={OPERATION_ITEMS} role={role} location={location}/>
      <NavGroup title="Configuration" items={CONFIG_ITEMS} role={role} location={location}/>
      <div className="app-rail-user">
        <UserAvatar initials={initials} name={displayName} />
        <div className="app-rail-user-copy"><strong>{displayName}</strong><span>{role.charAt(0).toUpperCase()+role.slice(1)}</span></div>
        <button type="button" className="app-rail-logout" onClick={logout} aria-label="Sign out" title="Sign out"><NavIcon name="logout"/></button>
      </div>
    </aside>
  );
}
