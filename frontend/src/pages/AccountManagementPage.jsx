import { useEffect, useMemo, useState } from "react";
import TopNav from "../components/TopNav";
import ProfessionalModal from "../components/ProfessionalModal";
import OneTimePasswordModal from "../components/OneTimePasswordModal";
import NotificationToast from "../components/NotificationToast";
import { authErrorMessage, useAuth } from "../auth/AuthContext";
import api from "../api";

const EMPTY_CREATE = { full_name: "", email: "", role: "viewer" };

function displayRole(role) {
  return role ? role.charAt(0).toUpperCase() + role.slice(1) : "-";
}

function isLocked(user) {
  if (!user?.locked_until) return false;
  return new Date(user.locked_until).getTime() > Date.now();
}

function statusLabel(user) {
  if (!user.is_active) return "Disabled";
  if (isLocked(user)) return "Temporarily Locked";
  return "Active";
}

export default function AccountManagementPage() {
  const { user: currentUser, clearSession } = useAuth();
  const [users, setUsers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
  const [createForm, setCreateForm] = useState(EMPTY_CREATE);
  const [editTarget, setEditTarget] = useState(null);
  const [editForm, setEditForm] = useState({ full_name: "", role: "viewer" });
  const [confirm, setConfirm] = useState(null);
  const [busy, setBusy] = useState(false);
  const [oneTime, setOneTime] = useState(null);
  const [toast, setToast] = useState(null);

  const loadUsers = async () => {
    setLoading(true);
    try {
      const response = await api.get("/api/admin/users");
      setUsers(response.data?.users || []);
    } catch (error) {
      setToast({ type: "error", message: authErrorMessage(error, "User accounts could not be loaded.") });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadUsers(); }, []);

  const filtered = useMemo(() => {
    const term = query.trim().toLowerCase();
    if (!term) return users;
    return users.filter((item) => [item.full_name, item.email, item.role, statusLabel(item)].some((value) => String(value || "").toLowerCase().includes(term)));
  }, [users, query]);

  const createAccount = async (event) => {
    event.preventDefault();
    setBusy(true);
    try {
      const response = await api.post("/api/admin/users", createForm);
      setCreateOpen(false);
      setCreateForm(EMPTY_CREATE);
      setOneTime({ account: response.data.user, password: response.data.temporary_password, title: "Account Created" });
      await loadUsers();
    } catch (error) {
      setToast({ type: "error", message: authErrorMessage(error, "The account could not be created.") });
    } finally {
      setBusy(false);
    }
  };

  const openEdit = (target) => {
    setEditTarget(target);
    setEditForm({ full_name: target.full_name, role: target.role });
  };

  const saveEdit = async (event) => {
    event.preventDefault();
    setBusy(true);
    try {
      await api.patch(`/api/admin/users/${editTarget.id}`, editForm);
      setEditTarget(null);
      setToast({ type: "success", message: "Account details updated successfully." });
      await loadUsers();
    } catch (error) {
      setToast({ type: "error", message: authErrorMessage(error, "The account could not be updated.") });
    } finally {
      setBusy(false);
    }
  };

  const executeConfirm = async () => {
    if (!confirm) return;
    const target = confirm.user;
    setBusy(true);
    try {
      if (confirm.type === "disable") {
        await api.post(`/api/admin/users/${target.id}/disable`);
        setToast({ type: "success", message: `${target.full_name} was disabled.` });
      } else if (confirm.type === "enable") {
        await api.post(`/api/admin/users/${target.id}/enable`);
        setToast({ type: "success", message: `${target.full_name} was enabled.` });
      } else if (confirm.type === "unlock") {
        await api.post(`/api/admin/users/${target.id}/unlock`);
        setToast({ type: "success", message: `Temporary login restriction cleared for ${target.full_name}.` });
      } else if (confirm.type === "reset") {
        const response = await api.post(`/api/admin/users/${target.id}/reset-password`);
        setOneTime({ account: response.data.user, password: response.data.temporary_password, title: "Password Reset" });
      }
      setConfirm(null);
      await loadUsers();
    } catch (error) {
      setToast({ type: "error", message: authErrorMessage(error, "The account action could not be completed.") });
    } finally {
      setBusy(false);
    }
  };

  const confirmCopy = confirm?.type === "reset"
    ? { title: "Reset Password?", subtitle: "A new temporary password will replace the user's current password.", button: "Generate Temporary Password", tone: "warning" }
    : confirm?.type === "disable"
      ? { title: "Disable Account?", subtitle: "The user will be blocked on their next protected request.", button: "Disable Account", tone: "danger" }
      : confirm?.type === "enable"
        ? { title: "Enable Account?", subtitle: "The user will be allowed to sign in again.", button: "Enable Account", tone: "info" }
        : { title: "Clear Login Restriction?", subtitle: "Failed-attempt count and the temporary lock will be cleared.", button: "Unlock Account", tone: "info" };

  return (
    <div className="app-shell">
      <TopNav />
      <main className="app-main account-management-page">
        <section className="page-header-card account-header-card">
          <div>
            <span className="section-kicker">Administration</span>
            <h1>Account Management</h1>
            <p>Create system accounts, manage roles, and control account access.</p>
          </div>
          <button type="button" className="btn-primary" onClick={() => setCreateOpen(true)}>+ Create Account</button>
        </section>

        <section className="content-card account-directory-card">
          <div className="account-toolbar">
            <div>
              <h2>User Directory</h2>
              <p>{users.length} account{users.length === 1 ? "" : "s"} configured</p>
            </div>
            <input className="account-search" type="search" placeholder="Search name, email, role, or status..." value={query} onChange={(event) => setQuery(event.target.value)} />
          </div>

          <div className="account-table-wrap">
            <table className="account-table">
              <thead><tr><th>User</th><th>Role</th><th>Status</th><th>Last Security State</th><th>Actions</th></tr></thead>
              <tbody>
                {loading ? <tr><td colSpan="5" className="account-empty">Loading accounts...</td></tr> : null}
                {!loading && filtered.length === 0 ? <tr><td colSpan="5" className="account-empty">No matching accounts found.</td></tr> : null}
                {!loading && filtered.map((item) => {
                  const self = item.id === currentUser?.id;
                  const locked = isLocked(item);
                  return (
                    <tr key={item.id}>
                      <td><div className="account-user-cell"><span className="account-avatar">{(item.full_name || item.email || "U").slice(0, 1).toUpperCase()}</span><div><strong>{item.full_name}</strong><span>{item.email}{self ? " · You" : ""}</span></div></div></td>
                      <td><span className={`role-badge role-badge--${item.role}`}>{displayRole(item.role)}</span></td>
                      <td><span className={`account-status account-status--${!item.is_active ? "disabled" : locked ? "locked" : "active"}`}><i />{statusLabel(item)}</span></td>
                      <td><span className="account-security-note">{item.must_change_password ? "Temporary password / setup required" : locked ? `Locked until ${new Date(item.locked_until).toLocaleString()}` : "Private password configured"}</span></td>
                      <td>
                        <div className="account-row-actions">
                          <button type="button" className="btn-secondary btn-compact" onClick={() => openEdit(item)}>Edit</button>
                          <button type="button" className="btn-secondary btn-compact" onClick={() => setConfirm({ type: "reset", user: item })}>Reset Password</button>
                          {locked ? <button type="button" className="btn-secondary btn-compact" onClick={() => setConfirm({ type: "unlock", user: item })}>Unlock</button> : null}
                          {item.is_active ? (
                            <button type="button" className="btn-danger-subtle btn-compact" disabled={self} title={self ? "You cannot disable your own account." : "Disable account"} onClick={() => setConfirm({ type: "disable", user: item })}>Disable</button>
                          ) : (
                            <button type="button" className="btn-secondary btn-compact" onClick={() => setConfirm({ type: "enable", user: item })}>Enable</button>
                          )}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>
      </main>

      <ProfessionalModal
        open={createOpen}
        title="Create Account"
        subtitle="The system will generate a secure 16-character temporary password automatically."
        onClose={() => !busy && setCreateOpen(false)}
        actions={<><button type="button" className="btn-secondary" onClick={() => setCreateOpen(false)} disabled={busy}>Cancel</button><button form="create-account-form" type="submit" className="btn-primary" disabled={busy}>{busy ? "Creating..." : "Create Account"}</button></>}
      >
        <form id="create-account-form" className="account-form-grid" onSubmit={createAccount}>
          <label><span>Full Name</span><input value={createForm.full_name} onChange={(event) => setCreateForm((v) => ({ ...v, full_name: event.target.value }))} required /></label>
          <label><span>Email</span><input type="email" value={createForm.email} onChange={(event) => setCreateForm((v) => ({ ...v, email: event.target.value }))} required /></label>
          <label><span>Role</span><select value={createForm.role} onChange={(event) => setCreateForm((v) => ({ ...v, role: event.target.value }))}><option value="viewer">Viewer</option><option value="editor">Editor</option><option value="admin">Admin</option></select></label>
        </form>
      </ProfessionalModal>

      <ProfessionalModal
        open={Boolean(editTarget)}
        title="Edit Account"
        subtitle="Email is the login identifier and cannot be changed from this page."
        onClose={() => !busy && setEditTarget(null)}
        actions={<><button type="button" className="btn-secondary" onClick={() => setEditTarget(null)} disabled={busy}>Cancel</button><button form="edit-account-form" type="submit" className="btn-primary" disabled={busy}>{busy ? "Saving..." : "Save Changes"}</button></>}
      >
        {editTarget ? (
          <form id="edit-account-form" className="account-form-grid" onSubmit={saveEdit}>
            <label><span>Full Name</span><input value={editForm.full_name} onChange={(event) => setEditForm((v) => ({ ...v, full_name: event.target.value }))} required /></label>
            <label><span>Email</span><input value={editTarget.email} disabled /></label>
            <label><span>Role</span><select value={editForm.role} disabled={editTarget.id === currentUser?.id} title={editTarget.id === currentUser?.id ? "You cannot change your own role." : undefined} onChange={(event) => setEditForm((v) => ({ ...v, role: event.target.value }))}><option value="viewer">Viewer</option><option value="editor">Editor</option><option value="admin">Admin</option></select></label>
          </form>
        ) : null}
      </ProfessionalModal>

      <ProfessionalModal
        open={Boolean(confirm)}
        title={confirmCopy.title}
        subtitle={confirmCopy.subtitle}
        tone={confirmCopy.tone}
        onClose={() => !busy && setConfirm(null)}
        actions={<><button type="button" className="btn-secondary" onClick={() => setConfirm(null)} disabled={busy}>Cancel</button><button type="button" className={confirm?.type === "disable" ? "btn-danger" : "btn-primary"} onClick={executeConfirm} disabled={busy}>{busy ? "Working..." : confirmCopy.button}</button></>}
      >
        {confirm ? <div className="confirmation-user-card"><strong>{confirm.user.full_name}</strong><span>{confirm.user.email}</span><small>{displayRole(confirm.user.role)} · {statusLabel(confirm.user)}</small></div> : null}
        {confirm?.type === "reset" ? <div className="security-note-box">The current password will stop working. The generated temporary password is shown only once, and the user must create a new private password after signing in.</div> : null}
      </ProfessionalModal>

      <OneTimePasswordModal
        open={Boolean(oneTime)}
        account={oneTime?.account}
        password={oneTime?.password}
        title={oneTime?.title}
        onDone={() => {
          const selfReset = oneTime?.account?.id === currentUser?.id && oneTime?.title === "Password Reset";
          setOneTime(null);
          if (selfReset) clearSession("Your password was reset. Sign in with the temporary password to continue.");
        }}
      />

      {toast ? <NotificationToast tone={toast.type} message={toast.message} onClose={() => setToast(null)} /> : null}
    </div>
  );
}
