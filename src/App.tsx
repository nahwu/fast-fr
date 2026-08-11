import {
  Activity,
  AlertOctagon,
  Archive,
  Bell,
  BellOff,
  Camera as CameraIcon,
  Check,
  CircleUserRound,
  Download,
  FileSpreadsheet,
  Fingerprint,
  Fullscreen,
  Gauge,
  ListChecks,
  LoaderCircle,
  LockKeyhole,
  LogOut,
  Menu,
  Pencil,
  Play,
  Plus,
  RefreshCw,
  Search,
  Settings as SettingsIcon,
  ShieldAlert,
  Square,
  Trash2,
  UploadCloud,
  UserCheck,
  UserCog,
  Users,
  Video,
  X,
} from "lucide-react";
import {
  type DragEvent,
  type FormEvent,
  type ReactNode,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { ApiError, api, formatBytes, localDate, setCsrfToken } from "./api";
import type {
  Camera,
  DashboardData,
  EnrollmentItem,
  EventType,
  ExamSession,
  Identity,
  IdentityCategory,
  RecognitionEvent,
  SystemHealth,
  User,
} from "./types";

type Page = "monitor" | "identities" | "sessions" | "cameras" | "alerts" | "reports" | "settings";
type Notice = { kind: "success" | "error"; message: string } | null;

const EVENT_COPY: Record<EventType, { label: string; short: string }> = {
  match: { label: "MATCH", short: "Admitted" },
  unknown: { label: "NO MATCH", short: "Unknown" },
  not_registered: { label: "NOT REGISTERED", short: "Wrong session" },
  blacklist: { label: "BLACKLIST", short: "Critical" },
};

function Button({
  children,
  variant = "primary",
  className = "",
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "danger" | "ghost";
}) {
  return (
    <button className={`button button-${variant} ${className}`} {...props}>
      {children}
    </button>
  );
}

function Modal({ title, subtitle, onClose, children, wide = false }: { title: string; subtitle?: string; onClose: () => void; children: ReactNode; wide?: boolean }) {
  useEffect(() => {
    const handler = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);
  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <section className={`modal ${wide ? "modal-wide" : ""}`} role="dialog" aria-modal="true" aria-label={title}>
        <header className="modal-header">
          <div><h2>{title}</h2>{subtitle && <p>{subtitle}</p>}</div>
          <button className="icon-button" onClick={onClose} aria-label="Close"><X size={20} /></button>
        </header>
        <div className="modal-body">{children}</div>
      </section>
    </div>
  );
}

function Empty({ icon, title, detail }: { icon: ReactNode; title: string; detail: string }) {
  return <div className="empty-state"><div className="empty-icon">{icon}</div><h3>{title}</h3><p>{detail}</p></div>;
}

function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return <label className="field"><span>{label}</span>{children}{hint && <small>{hint}</small>}</label>;
}

function Spinner({ label = "Loading" }: { label?: string }) {
  return <div className="loading-state"><LoaderCircle className="spin" size={24} /><span>{label}</span></div>;
}

function AuthScreen({ setup, onAuthenticated }: { setup: boolean; onAuthenticated: (user: User, csrf: string) => void }) {
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function submit(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError("");
    try {
      const result = await api<{ user: User; csrfToken: string }>(setup ? "/setup/admin" : "/auth/login", {
        method: "POST",
        body: JSON.stringify(setup ? { username, displayName, password } : { username, password }),
      });
      setCsrfToken(result.csrfToken); onAuthenticated(result.user, result.csrfToken);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Sign in failed"); }
    finally { setBusy(false); }
  }
  return (
    <main className="auth-screen">
      <section className="auth-brand">
        <div className="brand-mark"><Fingerprint size={31} strokeWidth={1.8} /></div>
        <div><span className="eyebrow">OFFLINE EXAM SECURITY</span><h1>Fast-FR</h1></div>
        <p>Supervised facial-recognition checks for one-person-at-a-time exam gantries.</p>
        <div className="auth-proof"><ShieldAlert size={18} /><span>Biometric data stays encrypted on this laptop.</span></div>
      </section>
      <form className="auth-form" onSubmit={submit}>
        <div><span className="eyebrow">{setup ? "FIRST-RUN SETUP" : "OPERATOR ACCESS"}</span><h2>{setup ? "Create the administrator" : "Sign in to the gantry console"}</h2><p>{setup ? "This named account controls cameras, rosters, identities, and security settings." : "Use your named local Fast-FR account."}</p></div>
        {error && <div className="form-error"><AlertOctagon size={17} />{error}</div>}
        <Field label="Username"><input value={username} onChange={(event) => setUsername(event.target.value)} minLength={3} required autoComplete="username" /></Field>
        {setup && <Field label="Display name"><input value={displayName} onChange={(event) => setDisplayName(event.target.value)} required /></Field>}
        <Field label="Password" hint={setup ? "Use at least 12 characters." : undefined}><input type="password" value={password} onChange={(event) => setPassword(event.target.value)} minLength={setup ? 12 : 1} required autoComplete={setup ? "new-password" : "current-password"} /></Field>
        <Button disabled={busy} type="submit">{busy && <LoaderCircle className="spin" size={17} />}{setup ? "Secure this laptop" : "Open console"}</Button>
      </form>
    </main>
  );
}

export default function App() {
  const [booting, setBooting] = useState(true);
  const [setupRequired, setSetupRequired] = useState(false);
  const [user, setUser] = useState<User | null>(null);
  const [page, setPage] = useState<Page>("monitor");
  const [notice, setNotice] = useState<Notice>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  useEffect(() => {
    (async () => {
      try {
        const setup = await api<{ required: boolean }>("/setup/status");
        setSetupRequired(setup.required);
        if (!setup.required) {
          try {
            const auth = await api<{ user: User; csrfToken: string }>("/auth/me");
            setCsrfToken(auth.csrfToken); setUser(auth.user);
          } catch (reason) { if (!(reason instanceof ApiError && reason.status === 401)) throw reason; }
        }
      } catch (reason) { setNotice({ kind: "error", message: reason instanceof Error ? reason.message : "Could not start Fast-FR" }); }
      finally { setBooting(false); }
    })();
  }, []);
  useEffect(() => { if (!notice) return; const timer = window.setTimeout(() => setNotice(null), 4500); return () => window.clearTimeout(timer); }, [notice]);
  async function logout() {
    try { await api<void>("/auth/logout", { method: "POST" }); } finally { setUser(null); setCsrfToken(""); }
  }
  if (booting) return <div className="splash"><Fingerprint size={38} /><LoaderCircle className="spin" size={24} /><span>Starting secure console</span></div>;
  if (!user) return <AuthScreen setup={setupRequired} onAuthenticated={(next) => { setUser(next); setSetupRequired(false); }} />;
  const nav: { id: Page; label: string; icon: ReactNode }[] = [
    { id: "monitor", label: "Live monitor", icon: <Gauge size={19} /> },
    { id: "identities", label: "Identities", icon: <Users size={19} /> },
    { id: "sessions", label: "Exam sessions", icon: <ListChecks size={19} /> },
    { id: "cameras", label: "Cameras", icon: <Video size={19} /> },
    { id: "alerts", label: "Alerts", icon: <Bell size={19} /> },
    { id: "reports", label: "Reports", icon: <FileSpreadsheet size={19} /> },
    { id: "settings", label: "Settings", icon: <SettingsIcon size={19} /> },
  ];
  const notify = (message: string, kind: "success" | "error" = "success") => setNotice({ message, kind });
  return (
    <div className="app-shell">
      <aside className={`sidebar ${menuOpen ? "sidebar-open" : ""}`}>
        <div className="sidebar-brand"><div className="brand-mark small"><Fingerprint size={23} /></div><div><strong>Fast-FR</strong><span>Exam gantry</span></div></div>
        <nav>{nav.map((item) => <button key={item.id} className={page === item.id ? "active" : ""} onClick={() => { setPage(item.id); setMenuOpen(false); }}>{item.icon}<span>{item.label}</span></button>)}</nav>
        <div className="sidebar-user"><CircleUserRound size={22} /><div><strong>{user.displayName}</strong><span>{user.role}</span></div><button onClick={logout} aria-label="Sign out" title="Sign out"><LogOut size={18} /></button></div>
      </aside>
      <div className="main-column">
        <header className="mobile-header"><button className="icon-button" onClick={() => setMenuOpen(!menuOpen)} aria-label="Menu"><Menu /></button><strong>Fast-FR</strong><span>{nav.find((item) => item.id === page)?.label}</span></header>
        <main className={`page page-${page}`}>
          {page === "monitor" && <MonitorPage notify={notify} />}
          {page === "identities" && <IdentitiesPage admin={user.role === "admin"} notify={notify} />}
          {page === "sessions" && <SessionsPage admin={user.role === "admin"} notify={notify} />}
          {page === "cameras" && <CamerasPage admin={user.role === "admin"} notify={notify} />}
          {page === "alerts" && <AlertsPage admin={user.role === "admin"} notify={notify} />}
          {page === "reports" && <ReportsPage notify={notify} />}
          {page === "settings" && <SettingsPage admin={user.role === "admin"} notify={notify} />}
        </main>
      </div>
      {notice && <div className={`toast toast-${notice.kind}`}>{notice.kind === "success" ? <Check size={18} /> : <AlertOctagon size={18} />}{notice.message}</div>}
      {menuOpen && <button type="button" className="sidebar-scrim" onClick={() => setMenuOpen(false)} aria-label="Close navigation" />}
    </div>
  );
}

function PageHeader({ eyebrow, title, detail, actions }: { eyebrow: string; title: string; detail: string; actions?: ReactNode }) {
  return <header className="page-header"><div><span className="eyebrow">{eyebrow}</span><h1>{title}</h1><p>{detail}</p></div>{actions && <div className="page-actions">{actions}</div>}</header>;
}

function MonitorPage({ notify }: { notify: (message: string, kind?: "success" | "error") => void }) {
  const [data, setData] = useState<DashboardData | null>(null);
  const [muted, setMuted] = useState(false);
  const [acknowledgedId, setAcknowledgedId] = useState<number | null>(null);
  const [clock, setClock] = useState(new Date());
  const audioContext = useRef<AudioContext | null>(null);
  const load = useCallback(async () => { try { setData(await api<DashboardData>("/dashboard")); } catch (reason) { notify(reason instanceof Error ? reason.message : "Could not load monitor", "error"); } }, [notify]);
  useEffect(() => { void load(); const timer = window.setInterval(load, 5000); const clockTimer = window.setInterval(() => setClock(new Date()), 1000); return () => { window.clearInterval(timer); window.clearInterval(clockTimer); }; }, [load]);
  const sound = useCallback((critical: boolean) => {
    if (muted) return;
    try {
      const context = audioContext.current || new AudioContext(); audioContext.current = context;
      const oscillator = context.createOscillator(); const gain = context.createGain();
      oscillator.frequency.value = critical ? 780 : 520; gain.gain.setValueAtTime(0.12, context.currentTime); gain.gain.exponentialRampToValueAtTime(0.001, context.currentTime + (critical ? 0.7 : 0.25));
      oscillator.connect(gain); gain.connect(context.destination); oscillator.start(); oscillator.stop(context.currentTime + (critical ? 0.7 : 0.25));
    } catch { /* Sound is optional. */ }
  }, [muted]);
  useEffect(() => {
    const protocol = location.protocol === "https:" ? "wss:" : "ws:";
    const socket = new WebSocket(`${protocol}//${location.host}/api/ws/events`);
    socket.onmessage = (message) => {
      const event = JSON.parse(message.data);
      if (event.type === "recognition.event") {
        const next = event.payload as RecognitionEvent;
        setData((current) => current ? { ...current, latestEvent: next, recentEvents: [next, ...current.recentEvents.filter((item) => item.id !== next.id)].slice(0, 20) } : current);
        sound(next.eventType !== "match");
      }
      if (event.type === "camera.health") setData((current) => current ? { ...current, cameras: current.cameras.map((camera) => camera.id === event.payload.cameraId ? { ...camera, health: event.payload } : camera) } : current);
    };
    return () => socket.close();
  }, [sound]);
  if (!data) return <Spinner label="Connecting to gantry services" />;
  const result = data.latestEvent?.id === acknowledgedId ? null : data.latestEvent;
  const type = result?.eventType;
  const statusClass = type ? `result-${type}` : "result-ready";
  return (
    <div className="monitor-layout">
      <header className="monitor-topbar">
        <div className="monitor-brand"><Activity size={20} /><strong>LIVE GANTRY</strong><span>{data.activeSessions.length} active session{data.activeSessions.length === 1 ? "" : "s"}</span></div>
        <div className="monitor-controls"><span className="clock">{clock.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}</span>{result && result.eventType !== "match" && <Button variant="secondary" onClick={() => setAcknowledgedId(result.id)}><Check size={17} />Acknowledge</Button>}<button className="icon-button" onClick={() => setMuted(!muted)} aria-label={muted ? "Unmute alerts" : "Mute alerts"}>{muted ? <BellOff size={20} /> : <Bell size={20} />}</button><button className="icon-button" onClick={() => document.documentElement.requestFullscreen()} aria-label="Full screen"><Fullscreen size={20} /></button></div>
      </header>
      <section className={`decision-stage ${statusClass}`}>
        <div className="decision-image">{result ? <><img src={result.snapshotUrl} alt="Captured face" />{result.identityFaceUrl && <div className="decision-enrolled"><img src={result.identityFaceUrl} alt="Enrolled identity" /><span>ENROLLED</span></div>}</> : <div className="face-guide"><Fingerprint size={72} strokeWidth={1} /><span>Waiting for a face</span></div>}</div>
        <div className="decision-copy">
          <span className="decision-kicker">{result ? EVENT_COPY[result.eventType].short : "System ready"}</span>
          <h1>{result ? EVENT_COPY[result.eventType].label : "READY"}</h1>
          <div className="identity-name">{result?.identityName || (result ? "Unknown person" : "Approach the gantry")}</div>
          {result?.candidateNo && <div className="candidate-number">Candidate {result.candidateNo}</div>}
          <div className="decision-meta"><span><CameraIcon size={16} />{result?.cameraName || "All configured cameras"}</span><span><ListChecks size={16} />{result?.sessionName || "No decision yet"}</span><span><Activity size={16} />{result?.decisionLatencyMs ? `${Math.round(result.decisionLatencyMs)} ms` : "Monitoring"}</span></div>
          {result && <div className="decision-time">{localDate(result.capturedAt)}{result.confidence != null && ` · Confidence ${Math.round(result.confidence * 100)}%`}</div>}
        </div>
      </section>
      <section className="camera-strip">
        {data.cameras.length ? data.cameras.map((camera) => <article className="camera-tile" key={camera.id}><div className="camera-frame"><img src={camera.previewUrl} alt={`${camera.name} live view`} /><span className={`live-dot ${camera.health?.connected ? "online" : "offline"}`} /> <div className="camera-overlay"><strong>{camera.name}</strong><span>{camera.sessionName || "No session"}</span></div></div><div className="camera-state"><span>{camera.health?.message || (camera.enabled ? "Connecting" : "Disabled")}</span><span>{camera.health?.fps ? `${camera.health.fps} fps` : "—"}</span></div></article>) : <Empty icon={<Video />} title="No cameras configured" detail="An administrator can add up to four RTSP gantry cameras." />}
      </section>
      <section className="recent-strip"><div className="section-label">RECENT DECISIONS</div><div className="recent-events">{data.recentEvents.slice(0, 6).map((event) => <article key={event.id} className={`recent-event event-${event.eventType}`}><img src={event.snapshotUrl} alt="" /><div><strong>{EVENT_COPY[event.eventType].label}</strong><span>{event.identityName || "Unknown person"}</span></div><time>{new Date(event.capturedAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</time></article>)}{!data.recentEvents.length && <span className="muted-copy">Decisions will appear here.</span>}</div></section>
    </div>
  );
}

function IdentitiesPage({ admin, notify }: { admin: boolean; notify: (message: string, kind?: "success" | "error") => void }) {
  const [rows, setRows] = useState<Identity[]>([]); const [sessionsList, setSessionsList] = useState<ExamSession[]>([]); const [q, setQ] = useState(""); const [category, setCategory] = useState<"all" | IdentityCategory>("all"); const [loading, setLoading] = useState(true); const [uploadOpen, setUploadOpen] = useState(false); const [editing, setEditing] = useState<Identity | null>(null);
  const load = useCallback(async () => { setLoading(true); try { const [identities, sessions] = await Promise.all([api<Identity[]>(`/identities?${category !== "all" ? `category=${category}&` : ""}${q ? `q=${encodeURIComponent(q)}` : ""}`), api<ExamSession[]>("/sessions")]); setRows(identities); setSessionsList(sessions); } catch (reason) { notify(reason instanceof Error ? reason.message : "Could not load identities", "error"); } finally { setLoading(false); } }, [category, q, notify]);
  useEffect(() => { const timer = window.setTimeout(load, 180); return () => window.clearTimeout(timer); }, [load]);
  async function archive(row: Identity) { if (!confirm(`Archive ${row.displayName}?`)) return; try { await api(`/identities/${row.id}`, { method: "DELETE" }); notify("Identity archived"); void load(); } catch (reason) { notify(reason instanceof Error ? reason.message : "Archive failed", "error"); } }
  return <>
    <PageHeader eyebrow="BIOMETRIC DIRECTORY" title="Identities" detail="Enroll, classify, and maintain exam candidates and blacklist identities." actions={admin && <Button onClick={() => setUploadOpen(true)}><UploadCloud size={17} />Bulk enroll</Button>} />
    <div className="toolbar"><div className="search-box"><Search size={18} /><input placeholder="Search name or candidate number" value={q} onChange={(event) => setQ(event.target.value)} /></div><div className="segmented"><button className={category === "all" ? "active" : ""} onClick={() => setCategory("all")}>All</button><button className={category === "whitelist" ? "active" : ""} onClick={() => setCategory("whitelist")}>Whitelist</button><button className={category === "blacklist" ? "active" : ""} onClick={() => setCategory("blacklist")}>Blacklist</button></div><span className="toolbar-count">{rows.length} identities</span></div>
    {loading ? <Spinner /> : rows.length ? <div className="identity-grid">{rows.map((row) => <article className={`identity-card identity-${row.category}`} key={row.id}><div className="identity-photo">{row.faceUrl ? <img src={row.faceUrl} alt={row.displayName} /> : <CircleUserRound size={42} />}<span className={`category-mark ${row.category}`} title={row.category}>{row.category === "blacklist" ? <ShieldAlert size={15} /> : <UserCheck size={15} />}</span></div><div className="identity-info"><span className="identity-type">{row.category}</span><h3>{row.displayName}</h3><p>{row.candidateNo ? `Candidate ${row.candidateNo}` : "No candidate number"}</p><small>{row.faceSampleCount} face sample{row.faceSampleCount === 1 ? "" : "s"}</small></div>{admin && <div className="card-actions"><button className="icon-button" onClick={() => setEditing(row)} aria-label="Edit"><Pencil size={17} /></button><button className="icon-button danger-icon" onClick={() => archive(row)} aria-label="Archive"><Archive size={17} /></button></div>}</article>)}</div> : <Empty icon={<Users />} title="No identities found" detail={q ? "Try another search." : "Bulk enroll passport photos to create the recognition directory."} />}
    {uploadOpen && <EnrollmentModal sessions={sessionsList} onClose={() => setUploadOpen(false)} onSaved={() => { setUploadOpen(false); notify("Enrollment completed"); void load(); }} notify={notify} />}
    {editing && <IdentityEditModal identity={editing} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); notify("Identity updated"); void load(); }} notify={notify} />}
  </>;
}

type ReviewItem = EnrollmentItem & { selectedFace: number; displayName: string; candidateNo: string; category: IdentityCategory; sessionId: string };

function EnrollmentModal({ sessions, onClose, onSaved, notify }: { sessions: ExamSession[]; onClose: () => void; onSaved: () => void; notify: (message: string, kind?: "success" | "error") => void }) {
  const [items, setItems] = useState<ReviewItem[]>([]); const [busy, setBusy] = useState(false); const input = useRef<HTMLInputElement>(null);
  async function analyze(files: FileList | File[]) { if (!files.length) return; setBusy(true); const form = new FormData(); Array.from(files).forEach((file) => form.append("files", file)); try { const result = await api<{ items: EnrollmentItem[] }>("/enrollments/analyze", { method: "POST", body: form }); setItems(result.items.map((item) => ({ ...item, selectedFace: Math.max(0, item.faces.findIndex((face) => face.eligible)), displayName: item.suggestedName || "", candidateNo: "", category: "whitelist", sessionId: "" }))); } catch (reason) { notify(reason instanceof Error ? reason.message : "Analysis failed", "error"); } finally { setBusy(false); } }
  function update(index: number, change: Partial<ReviewItem>) { setItems((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, ...change } : item)); }
  async function commit() { const ready = items.filter((item) => item.token && item.faces[item.selectedFace]?.eligible && item.displayName.trim()); if (!ready.length) { notify("Select at least one clear face", "error"); return; } setBusy(true); try { await api("/enrollments/commit", { method: "POST", body: JSON.stringify({ items: ready.map((item) => ({ token: item.token, faceIndex: item.selectedFace, displayName: item.displayName, candidateNo: item.candidateNo || null, category: item.category, sessionId: item.category === "whitelist" && item.sessionId ? Number(item.sessionId) : null })) }) }); onSaved(); } catch (reason) { notify(reason instanceof Error ? reason.message : "Enrollment failed", "error"); } finally { setBusy(false); } }
  return <Modal title="Bulk face enrollment" subtitle="JPG, PNG, or PDF · one identity per file" onClose={onClose} wide>
    {!items.length ? <div className={`drop-zone ${busy ? "busy" : ""}`} role="button" tabIndex={0} onKeyDown={(event) => { if ((event.key === "Enter" || event.key === " ") && !busy) input.current?.click(); }} onDragOver={(event) => event.preventDefault()} onDrop={(event: DragEvent) => { event.preventDefault(); void analyze(event.dataTransfer.files); }} onClick={() => !busy && input.current?.click()}><input ref={input} type="file" hidden multiple accept=".jpg,.jpeg,.png,.pdf" onChange={(event) => event.target.files && void analyze(event.target.files)} />{busy ? <LoaderCircle className="spin" size={42} /> : <UploadCloud size={46} />}<h3>{busy ? "Detecting and aligning faces" : "Drop passport files here"}</h3><p>{busy ? "Files are processed in memory and originals are discarded." : "or click to choose up to 100 files, 25 MB each"}</p></div> : <><div className="enrollment-summary"><span><strong>{items.length}</strong> files reviewed</span><Button variant="secondary" onClick={() => setItems([])}>Choose different files</Button></div><div className="review-list">{items.map((item, index) => <article className={`review-item ${!item.faces.length ? "rejected" : ""}`} key={`${item.filename}-${index}`}><div className="face-options">{item.faces.map((face) => <button key={face.index} className={item.selectedFace === face.index ? "selected" : ""} onClick={() => update(index, { selectedFace: face.index })}><img src={face.preview} alt={`Detected face on page ${face.page}`} /><span>{face.eligible ? `${Math.round(face.quality * 100)}%` : "Low quality"}</span></button>)}{!item.faces.length && <div className="no-face"><AlertOctagon /><span>{item.message || "No face detected"}</span></div>}</div><div className="review-fields"><div className="review-file">{item.filename}</div>{item.faces[item.selectedFace]?.possibleDuplicate && <div className="warning-box"><AlertOctagon size={17} />Possible duplicate of {item.faces[item.selectedFace].possibleDuplicate} ({Math.round((item.faces[item.selectedFace].duplicateScore || 0) * 100)}% similarity). Verify before enrolling.</div>}<div className="form-grid"><Field label="Identity name"><input value={item.displayName} onChange={(event) => update(index, { displayName: event.target.value })} /></Field><Field label="Candidate number"><input value={item.candidateNo} onChange={(event) => update(index, { candidateNo: event.target.value })} /></Field><Field label="List"><select value={item.category} onChange={(event) => update(index, { category: event.target.value as IdentityCategory })}><option value="whitelist">Whitelist</option><option value="blacklist">Blacklist</option></select></Field>{item.category === "whitelist" && <Field label="Add to session"><select value={item.sessionId} onChange={(event) => update(index, { sessionId: event.target.value })}><option value="">Not yet</option>{sessions.map((session) => <option key={session.id} value={session.id}>{session.name}</option>)}</select></Field>}</div></div></article>)}</div><div className="modal-actions"><Button variant="secondary" onClick={onClose}>Cancel</Button><Button onClick={commit} disabled={busy}>{busy && <LoaderCircle className="spin" size={17} />}Enroll selected faces</Button></div></>}
  </Modal>;
}

function IdentityEditModal({ identity, onClose, onSaved, notify }: { identity: Identity; onClose: () => void; onSaved: () => void; notify: (message: string, kind?: "success" | "error") => void }) {
  const [name, setName] = useState(identity.displayName); const [candidateNo, setCandidateNo] = useState(identity.candidateNo || ""); const [category, setCategory] = useState(identity.category); const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent) { event.preventDefault(); setBusy(true); try { await api(`/identities/${identity.id}`, { method: "PATCH", body: JSON.stringify({ displayName: name, candidateNo: candidateNo || null, category }) }); onSaved(); } catch (reason) { notify(reason instanceof Error ? reason.message : "Update failed", "error"); } finally { setBusy(false); } }
  async function purge() { if (!confirm(`Permanently delete biometric samples for ${identity.displayName}? Historical event snapshots keep their recorded name and category. This cannot be undone.`)) return; setBusy(true); try { await api(`/identities/${identity.id}?permanent=true`, { method: "DELETE" }); notify("Identity biometric samples permanently deleted"); onSaved(); } catch (reason) { notify(reason instanceof Error ? reason.message : "Permanent deletion failed", "error"); } finally { setBusy(false); } }
  return <Modal title="Edit identity" onClose={onClose}><form onSubmit={submit} className="form-stack"><Field label="Display name"><input value={name} onChange={(event) => setName(event.target.value)} required /></Field><Field label="Candidate number"><input value={candidateNo} onChange={(event) => setCandidateNo(event.target.value)} /></Field><Field label="Classification"><select value={category} onChange={(event) => setCategory(event.target.value as IdentityCategory)}><option value="whitelist">Whitelist candidate</option><option value="blacklist">Blacklist identity</option></select></Field>{category === "blacklist" && identity.category !== "blacklist" && <div className="warning-box"><ShieldAlert size={18} />Changing to blacklist removes this identity from every exam roster.</div>}<div className="modal-actions"><Button type="button" variant="danger" disabled={busy} onClick={purge}>Delete biometrics</Button><Button type="button" variant="secondary" onClick={onClose}>Cancel</Button><Button disabled={busy} type="submit">Save identity</Button></div></form></Modal>;
}

function SessionsPage({ admin, notify }: { admin: boolean; notify: (message: string, kind?: "success" | "error") => void }) {
  const [rows, setRows] = useState<ExamSession[]>([]); const [loading, setLoading] = useState(true); const [editing, setEditing] = useState<ExamSession | "new" | null>(null); const [roster, setRoster] = useState<ExamSession | null>(null);
  const load = useCallback(async () => { setLoading(true); try { setRows(await api<ExamSession[]>("/sessions")); } catch (reason) { notify(reason instanceof Error ? reason.message : "Could not load sessions", "error"); } finally { setLoading(false); } }, [notify]);
  useEffect(() => { void load(); }, [load]);
  async function transition(row: ExamSession, action: "start" | "close") { try { await api(`/sessions/${row.id}/${action}`, { method: "POST" }); notify(`Session ${action === "start" ? "started" : "closed"}`); void load(); } catch (reason) { notify(reason instanceof Error ? reason.message : "Session update failed", "error"); } }
  return <><PageHeader eyebrow="EXAM OPERATIONS" title="Exam sessions" detail="Schedule exams, attach candidate rosters, and assign active sessions to gantry cameras." actions={admin && <Button onClick={() => setEditing("new")}><Plus size={17} />New session</Button>} />{loading ? <Spinner /> : rows.length ? <div className="session-list">{rows.map((row) => <article className={`session-card session-${row.status}`} key={row.id}><div className="session-date"><strong>{new Date(row.startsAt).getDate()}</strong><span>{new Date(row.startsAt).toLocaleString([], { month: "short" }).toUpperCase()}</span></div><div className="session-main"><div className="session-heading"><span className={`status-badge ${row.status}`}>{row.status}</span><span className="session-code">{row.code}</span></div><h3>{row.name}</h3><p>{localDate(row.startsAt)} → {localDate(row.endsAt)}</p><div className="session-stats"><span><Users size={16} />{row.memberCount} roster</span><span><UserCheck size={16} />{row.presentCount} present</span><span><Video size={16} />{row.cameraCount} cameras</span></div></div><div className="session-actions">{admin && <><Button variant="secondary" onClick={() => setRoster(row)}><Users size={16} />Roster</Button><button className="icon-button" onClick={() => setEditing(row)} aria-label="Edit"><Pencil size={17} /></button></>}{row.status === "draft" && <Button onClick={() => transition(row, "start")}><Play size={16} />Start</Button>}{row.status === "active" && <Button variant="danger" onClick={() => transition(row, "close")}><Square size={15} />Close</Button>}<a className="button button-secondary" href={`/api/reports/sessions/${row.id}.xlsx`}><Download size={16} />XLSX</a></div></article>)}</div> : <Empty icon={<ListChecks />} title="No exam sessions" detail="Create a dated session, then attach its candidate roster." />}{editing && <SessionModal existing={editing === "new" ? null : editing} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); notify("Session saved"); void load(); }} notify={notify} />}{roster && <RosterModal session={roster} onClose={() => setRoster(null)} onSaved={() => { setRoster(null); notify("Roster updated"); void load(); }} notify={notify} />}</>;
}

function toLocalInput(value: string) { const date = new Date(value); const offset = date.getTimezoneOffset(); return new Date(date.getTime() - offset * 60000).toISOString().slice(0, 16); }
function SessionModal({ existing, onClose, onSaved, notify }: { existing: ExamSession | null; onClose: () => void; onSaved: () => void; notify: (message: string, kind?: "success" | "error") => void }) {
  const defaultStart = new Date(Date.now() + 86400000); defaultStart.setHours(9, 0, 0, 0); const defaultEnd = new Date(defaultStart.getTime() + 3 * 3600000);
  const [name, setName] = useState(existing?.name || ""); const [code, setCode] = useState(existing?.code || ""); const [startsAt, setStartsAt] = useState(existing ? toLocalInput(existing.startsAt) : toLocalInput(defaultStart.toISOString())); const [endsAt, setEndsAt] = useState(existing ? toLocalInput(existing.endsAt) : toLocalInput(defaultEnd.toISOString())); const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent) { event.preventDefault(); setBusy(true); try { await api(existing ? `/sessions/${existing.id}` : "/sessions", { method: existing ? "PATCH" : "POST", body: JSON.stringify({ name, code, startsAt: new Date(startsAt).toISOString(), endsAt: new Date(endsAt).toISOString() }) }); onSaved(); } catch (reason) { notify(reason instanceof Error ? reason.message : "Could not save session", "error"); } finally { setBusy(false); } }
  return <Modal title={existing ? "Edit exam session" : "Create exam session"} onClose={onClose}><form onSubmit={submit} className="form-stack"><Field label="Session name"><input value={name} onChange={(event) => setName(event.target.value)} required /></Field><Field label="Session code"><input value={code} onChange={(event) => setCode(event.target.value)} required /></Field><div className="form-grid"><Field label="Starts"><input type="datetime-local" value={startsAt} onChange={(event) => setStartsAt(event.target.value)} required /></Field><Field label="Ends"><input type="datetime-local" value={endsAt} onChange={(event) => setEndsAt(event.target.value)} required /></Field></div><div className="modal-actions"><Button type="button" variant="secondary" onClick={onClose}>Cancel</Button><Button disabled={busy} type="submit">Save session</Button></div></form></Modal>;
}

function RosterModal({ session, onClose, onSaved, notify }: { session: ExamSession; onClose: () => void; onSaved: () => void; notify: (message: string, kind?: "success" | "error") => void }) {
  const [identities, setIdentities] = useState<Identity[]>([]); const [selected, setSelected] = useState<Set<number>>(new Set()); const [q, setQ] = useState(""); const [busy, setBusy] = useState(true);
  useEffect(() => { Promise.all([api<Identity[]>("/identities?category=whitelist"), api<Identity[]>(`/sessions/${session.id}/members`)]).then(([all, members]) => { setIdentities(all); setSelected(new Set(members.map((row) => row.id))); }).catch((reason) => notify(reason.message, "error")).finally(() => setBusy(false)); }, [session.id, notify]);
  async function save() { setBusy(true); try { await api(`/sessions/${session.id}/members`, { method: "PUT", body: JSON.stringify({ identityIds: Array.from(selected) }) }); onSaved(); } catch (reason) { notify(reason instanceof Error ? reason.message : "Roster update failed", "error"); } finally { setBusy(false); } }
  const visible = identities.filter((row) => `${row.displayName} ${row.candidateNo || ""}`.toLowerCase().includes(q.toLowerCase()));
  return <Modal title={`Roster · ${session.name}`} subtitle={`${selected.size} candidates selected`} onClose={onClose} wide>{busy && !identities.length ? <Spinner /> : <><div className="toolbar"><div className="search-box"><Search size={17} /><input placeholder="Find candidate" value={q} onChange={(event) => setQ(event.target.value)} /></div><Button variant="secondary" onClick={() => setSelected(new Set(identities.map((row) => row.id)))}>Select all</Button><Button variant="ghost" onClick={() => setSelected(new Set())}>Clear</Button></div><div className="roster-list">{visible.map((row) => <label key={row.id} className={selected.has(row.id) ? "selected" : ""}><input type="checkbox" checked={selected.has(row.id)} onChange={() => setSelected((current) => { const next = new Set(current); if (next.has(row.id)) next.delete(row.id); else next.add(row.id); return next; })} />{row.faceUrl ? <img src={row.faceUrl} alt="" /> : <CircleUserRound />}<span><strong>{row.displayName}</strong><small>{row.candidateNo || "No candidate number"}</small></span><Check size={18} /></label>)}</div><div className="modal-actions"><Button variant="secondary" onClick={onClose}>Cancel</Button><Button disabled={busy} onClick={save}>Save {selected.size} candidates</Button></div></>}</Modal>;
}

function CamerasPage({ admin, notify }: { admin: boolean; notify: (message: string, kind?: "success" | "error") => void }) {
  const [rows, setRows] = useState<Camera[]>([]); const [sessionsList, setSessionsList] = useState<ExamSession[]>([]); const [editing, setEditing] = useState<Camera | "new" | null>(null); const [loading, setLoading] = useState(true); const [testing, setTesting] = useState<number | null>(null);
  const load = useCallback(async () => { setLoading(true); try { const [cameras, sessions] = await Promise.all([api<Camera[]>("/cameras"), api<ExamSession[]>("/sessions")]); setRows(cameras); setSessionsList(sessions); } catch (reason) { notify(reason instanceof Error ? reason.message : "Could not load cameras", "error"); } finally { setLoading(false); } }, [notify]);
  useEffect(() => { void load(); }, [load]);
  async function test(row: Camera) { setTesting(row.id); try { const result = await api<{ ok: boolean; message: string; latencyMs?: number }>(`/cameras/${row.id}/test`, { method: "POST", body: "{}" }); notify(result.ok ? `${row.name}: ${result.message} (${result.latencyMs} ms)` : `${row.name}: ${result.message}`, result.ok ? "success" : "error"); } catch (reason) { notify(reason instanceof Error ? reason.message : "Connection test failed", "error"); } finally { setTesting(null); } }
  async function remove(row: Camera) { if (!confirm(`Remove ${row.name}? Cameras with event history will be disabled instead.`)) return; try { await api(`/cameras/${row.id}`, { method: "DELETE" }); notify("Camera removed"); void load(); } catch (reason) { notify(reason instanceof Error ? reason.message : "Remove failed", "error"); } }
  return <><PageHeader eyebrow="RTSP INPUTS" title="Gantry cameras" detail="Configure up to four one-person-at-a-time streams and assign each to an exam session." actions={admin && <Button onClick={() => setEditing("new")} disabled={rows.filter((row) => row.enabled).length >= 4}><Plus size={17} />Add camera</Button>} />{loading ? <Spinner /> : rows.length ? <div className="camera-admin-grid">{rows.map((row) => <article className="camera-admin-card" key={row.id}><div className="camera-admin-preview"><img src={row.previewUrl} alt={`${row.name} preview`} /><span className={`connection-badge ${row.health?.connected ? "online" : "offline"}`}>{row.health?.connected ? "ONLINE" : row.enabled ? "OFFLINE" : "DISABLED"}</span></div><div className="camera-admin-copy"><div><h3>{row.name}</h3><p>{row.sessionName || "No session assigned"}</p></div><dl><div><dt>Status</dt><dd>{row.health?.message || "Not running"}</dd></div><div><dt>Rotation</dt><dd>{row.rotation}°</dd></div><div><dt>Stream</dt><dd>{row.health?.fps ? `${row.health.fps} fps` : "—"}</dd></div></dl></div>{admin && <div className="camera-admin-actions"><Button variant="secondary" disabled={testing === row.id} onClick={() => test(row)}>{testing === row.id ? <LoaderCircle className="spin" size={16} /> : <RefreshCw size={16} />}Test</Button><button className="icon-button" onClick={() => setEditing(row)} aria-label="Edit"><Pencil size={17} /></button><button className="icon-button danger-icon" onClick={() => remove(row)} aria-label="Remove"><Trash2 size={17} /></button></div>}</article>)}</div> : <Empty icon={<CameraIcon />} title="No gantry cameras" detail="Add an H.264 RTSP stream to begin live checks." />}{editing && <CameraModal existing={editing === "new" ? null : editing} sessions={sessionsList} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); notify("Camera saved"); void load(); }} notify={notify} />}</>;
}

function CameraModal({ existing, sessions, onClose, onSaved, notify }: { existing: Camera | null; sessions: ExamSession[]; onClose: () => void; onSaved: () => void; notify: (message: string, kind?: "success" | "error") => void }) {
  const [name, setName] = useState(existing?.name || ""); const [rtspUrl, setRtspUrl] = useState(""); const [enabled, setEnabled] = useState(existing?.enabled ?? true); const [rotation, setRotation] = useState(String(existing?.rotation ?? 0)); const [sessionId, setSessionId] = useState(existing?.sessionId ? String(existing.sessionId) : ""); const [roi, setRoi] = useState(JSON.stringify(existing?.roi?.length ? existing.roi : [[0.2, 0.05], [0.8, 0.05], [0.8, 0.95], [0.2, 0.95]])); const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent) { event.preventDefault(); setBusy(true); try { const parsed = JSON.parse(roi); const payload: Record<string, unknown> = { name, enabled, rotation: Number(rotation), roi: parsed, sessionId: sessionId ? Number(sessionId) : null, clearSession: !sessionId }; if (rtspUrl) payload.rtspUrl = rtspUrl; if (!existing && !rtspUrl) throw new Error("RTSP URL is required"); await api(existing ? `/cameras/${existing.id}` : "/cameras", { method: existing ? "PATCH" : "POST", body: JSON.stringify(payload) }); onSaved(); } catch (reason) { notify(reason instanceof Error ? reason.message : "Could not save camera", "error"); } finally { setBusy(false); } }
  return <Modal title={existing ? "Edit gantry camera" : "Add gantry camera"} onClose={onClose}><form onSubmit={submit} className="form-stack"><Field label="Camera name"><input value={name} onChange={(event) => setName(event.target.value)} required /></Field><Field label="RTSP URL" hint={existing ? "Leave blank to keep the stored encrypted URL." : "Use an H.264 RTSP stream for the tested performance target."}><input type="password" value={rtspUrl} onChange={(event) => setRtspUrl(event.target.value)} required={!existing} placeholder={existing ? "Stored securely" : "rtsp://user:password@camera/stream"} /></Field><div className="form-grid"><Field label="Rotation"><select value={rotation} onChange={(event) => setRotation(event.target.value)}><option value="0">0°</option><option value="90">90° clockwise</option><option value="180">180°</option><option value="270">270° clockwise</option></select></Field><Field label="Exam session"><select value={sessionId} onChange={(event) => setSessionId(event.target.value)}><option value="">No session</option>{sessions.map((row) => <option value={row.id} key={row.id}>{row.name} · {row.status}</option>)}</select></Field></div><Field label="Recognition ROI (normalized polygon)" hint="Each point is [x, y] between 0 and 1."><textarea value={roi} onChange={(event) => setRoi(event.target.value)} rows={3} /></Field><label className="toggle-row"><input type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} /><span>Enable this camera</span></label><div className="modal-actions"><Button type="button" variant="secondary" onClick={onClose}>Cancel</Button><Button disabled={busy} type="submit">Save camera</Button></div></form></Modal>;
}

function AlertsPage({ admin, notify }: { admin: boolean; notify: (message: string, kind?: "success" | "error") => void }) {
  const [rows, setRows] = useState<RecognitionEvent[]>([]); const [filter, setFilter] = useState<"all" | EventType>("all"); const [unresolved, setUnresolved] = useState(false); const [loading, setLoading] = useState(true); const [resolving, setResolving] = useState<RecognitionEvent | null>(null);
  const load = useCallback(async () => { setLoading(true); const query = new URLSearchParams({ limit: "300" }); if (filter !== "all") query.set("eventType", filter); if (unresolved) query.set("unresolvedOnly", "true"); try { setRows(await api<RecognitionEvent[]>(`/events?${query}`)); } catch (reason) { notify(reason instanceof Error ? reason.message : "Could not load alerts", "error"); } finally { setLoading(false); } }, [filter, unresolved, notify]);
  useEffect(() => { void load(); }, [load]);
  async function remove(row: RecognitionEvent) { if (!confirm("Permanently delete this event and its captured face?")) return; try { await api(`/events/${row.id}`, { method: "DELETE" }); notify("Event deleted"); void load(); } catch (reason) { notify(reason instanceof Error ? reason.message : "Delete failed", "error"); } }
  async function removeFiltered() { if (!confirm(`Permanently delete ${rows.length} filtered events and their captured faces? This cannot be undone.`)) return; try { const result = await api<{ deleted: number }>("/events/bulk-delete", { method: "POST", body: JSON.stringify({ eventTypes: filter === "all" ? [] : [filter], unresolvedOnly: unresolved, confirmation: "DELETE" }) }); notify(`${result.deleted} events permanently deleted`); void load(); } catch (reason) { notify(reason instanceof Error ? reason.message : "Bulk deletion failed", "error"); } }
  return <><PageHeader eyebrow="REVIEW QUEUE" title="Recognition alerts" detail="Review unknown, unregistered, and blacklist events while preserving the original decision trail." /><div className="toolbar"><div className="segmented event-filters">{(["all", "unknown", "not_registered", "blacklist", "match"] as const).map((value) => <button key={value} className={filter === value ? "active" : ""} onClick={() => setFilter(value)}>{value === "all" ? "All" : EVENT_COPY[value].short}</button>)}</div><label className="toggle-row compact"><input type="checkbox" checked={unresolved} onChange={(event) => setUnresolved(event.target.checked)} /><span>Unresolved only</span></label><span className="toolbar-count">{rows.length} events</span>{admin && rows.length > 0 && (filter !== "all" || unresolved) && <Button variant="danger" onClick={removeFiltered}><Trash2 size={16} />Delete filtered</Button>}</div>{loading ? <Spinner /> : rows.length ? <div className="alerts-list">{rows.map((row) => <article className={`alert-row event-${row.eventType}`} key={row.id}><img src={row.snapshotUrl} alt="Captured face" /><div className="alert-status"><strong>{EVENT_COPY[row.eventType].label}</strong><span>{row.identityName || "Unknown person"}</span>{row.candidateNo && <small>Candidate {row.candidateNo}</small>}</div><div className="alert-context"><span><CameraIcon size={15} />{row.cameraName}</span><span><ListChecks size={15} />{row.sessionName}</span><span><Activity size={15} />{row.decisionLatencyMs ? `${Math.round(row.decisionLatencyMs)} ms` : "—"}</span></div><div className="alert-time"><strong>{new Date(row.capturedAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}</strong><span>{localDate(row.capturedAt, false)}</span>{row.confidence != null && <small>{Math.round(row.confidence * 100)}% confidence</small>}</div><div className="alert-resolution">{row.resolvedIdentityName ? <><UserCheck size={17} /><span>Manual match<br /><strong>{row.resolvedIdentityName}</strong></span></> : row.eventType === "unknown" ? <Button variant="secondary" onClick={() => setResolving(row)}>Assign candidate</Button> : <span className="muted-copy">No action</span>}</div>{admin && <button className="icon-button danger-icon" onClick={() => remove(row)} aria-label="Delete event"><Trash2 size={17} /></button>}</article>)}</div> : <Empty icon={<Bell />} title="No alerts match this filter" detail="Recognition events from active gantries will appear here." />}{resolving && <ResolveModal event={resolving} onClose={() => setResolving(null)} onSaved={() => { setResolving(null); notify("Unknown face assigned as a manual match"); void load(); }} notify={notify} />}</>;
}

function ResolveModal({ event, onClose, onSaved, notify }: { event: RecognitionEvent; onClose: () => void; onSaved: () => void; notify: (message: string, kind?: "success" | "error") => void }) {
  const [members, setMembers] = useState<Identity[]>([]); const [selected, setSelected] = useState(""); const [note, setNote] = useState(""); const [q, setQ] = useState(""); const [busy, setBusy] = useState(true);
  useEffect(() => { if (!event.sessionId) return; api<Identity[]>(`/sessions/${event.sessionId}/members`).then(setMembers).catch((reason) => notify(reason.message, "error")).finally(() => setBusy(false)); }, [event.sessionId, notify]);
  async function save() { if (!selected) return; setBusy(true); try { await api(`/events/${event.id}/resolve`, { method: "POST", body: JSON.stringify({ identityId: Number(selected), note: note || null }) }); onSaved(); } catch (reason) { notify(reason instanceof Error ? reason.message : "Manual match failed", "error"); } finally { setBusy(false); } }
  const visible = members.filter((row) => `${row.displayName} ${row.candidateNo || ""}`.toLowerCase().includes(q.toLowerCase()));
  return <Modal title="Assign unknown face" subtitle="This creates attendance marked as a manual match; it does not retrain the identity." onClose={onClose} wide><div className="resolve-layout"><div className="resolve-capture"><img src={event.snapshotUrl} alt="Unknown captured face" /><strong>{localDate(event.capturedAt)}</strong><span>{event.cameraName} · {event.sessionName}</span></div><div className="resolve-picker"><div className="search-box"><Search size={17} /><input placeholder="Search session roster" value={q} onChange={(change) => setQ(change.target.value)} /></div>{busy && !members.length ? <Spinner /> : <div className="roster-list compact-list">{visible.map((row) => <label className={selected === String(row.id) ? "selected" : ""} key={row.id}><input type="radio" name="candidate" value={row.id} checked={selected === String(row.id)} onChange={(change) => setSelected(change.target.value)} />{row.faceUrl ? <img src={row.faceUrl} alt="" /> : <CircleUserRound />}<span><strong>{row.displayName}</strong><small>{row.candidateNo || "No candidate number"}</small></span><Check size={18} /></label>)}</div>}<Field label="Operator note (optional)"><textarea rows={3} value={note} onChange={(change) => setNote(change.target.value)} placeholder="Reason or verification performed" /></Field></div></div><div className="modal-actions"><Button variant="secondary" onClick={onClose}>Cancel</Button><Button disabled={!selected || busy} onClick={save}>Confirm manual match</Button></div></Modal>;
}

function ReportsPage({ notify }: { notify: (message: string, kind?: "success" | "error") => void }) {
  const [rows, setRows] = useState<ExamSession[]>([]); const [loading, setLoading] = useState(true);
  useEffect(() => { api<ExamSession[]>("/sessions").then(setRows).catch((reason) => notify(reason.message, "error")).finally(() => setLoading(false)); }, [notify]);
  return <><PageHeader eyebrow="ATTENDANCE EXPORTS" title="Session reports" detail="Download a complete XLSX workbook with roster status, manual matches, unknown people, and blacklist alerts." /><div className="privacy-banner"><LockKeyhole size={22} /><div><strong>Exports contain sensitive biometric evidence</strong><span>The downloaded workbook is outside Fast-FR&apos;s encrypted storage. Handle it according to your exam data policy.</span></div></div>{loading ? <Spinner /> : rows.length ? <div className="report-list">{rows.map((row) => <article key={row.id}><div className="report-icon"><FileSpreadsheet size={30} /></div><div><span className={`status-badge ${row.status}`}>{row.status}</span><h3>{row.name}</h3><p>{row.code} · {localDate(row.startsAt, false)}</p></div><div className="report-numbers"><span><strong>{row.presentCount}</strong> present</span><span><strong>{Math.max(0, row.memberCount - row.presentCount)}</strong> absent</span></div><a className="button button-primary" href={`/api/reports/sessions/${row.id}.xlsx`}><Download size={17} />Download XLSX</a></article>)}</div> : <Empty icon={<FileSpreadsheet />} title="No sessions to report" detail="Reports become available as soon as a session is created." />}</>;
}

interface AppSettings { recognitionThreshold: number; telegram: { enabled: boolean; tokenSet: boolean; chatIdSet: boolean; alertTypes: EventType[] }; storageBytes: number; dataDirectory: string; }
interface AuditEntry { id: number; user: string; action: string; entityType: string; entityId: string | null; detail: Record<string, unknown>; createdAt: string; }

function SettingsPage({ admin, notify }: { admin: boolean; notify: (message: string, kind?: "success" | "error") => void }) {
  const [health, setHealth] = useState<SystemHealth | null>(null); const [settingsData, setSettingsData] = useState<AppSettings | null>(null); const [usersList, setUsersList] = useState<User[]>([]); const [audit, setAudit] = useState<AuditEntry[]>([]); const [threshold, setThreshold] = useState("0.45"); const [telegramEnabled, setTelegramEnabled] = useState(false); const [token, setToken] = useState(""); const [chatId, setChatId] = useState(""); const [newUser, setNewUser] = useState(false);
  const load = useCallback(async () => { try { const [nextHealth, nextSettings, nextUsers, nextAudit] = await Promise.all([api<SystemHealth>("/system/health"), api<AppSettings>("/settings"), api<User[]>("/users"), api<AuditEntry[]>("/audit?limit=50")]); setHealth(nextHealth); setSettingsData(nextSettings); setUsersList(nextUsers); setAudit(nextAudit); setThreshold(String(nextSettings.recognitionThreshold)); setTelegramEnabled(nextSettings.telegram.enabled); } catch (reason) { notify(reason instanceof Error ? reason.message : "Could not load settings", "error"); } }, [notify]);
  useEffect(() => { void load(); }, [load]);
  async function saveThreshold() { try { await api("/settings/threshold", { method: "PUT", body: JSON.stringify({ threshold: Number(threshold) }) }); notify("Recognition threshold updated"); void load(); } catch (reason) { notify(reason instanceof Error ? reason.message : "Threshold update failed", "error"); } }
  async function saveTelegram() { try { await api("/settings/telegram", { method: "PUT", body: JSON.stringify({ enabled: telegramEnabled, botToken: token || null, chatId: chatId || null, alertTypes: ["unknown"] }) }); setToken(""); setChatId(""); notify("Telegram settings saved"); void load(); } catch (reason) { notify(reason instanceof Error ? reason.message : "Telegram update failed", "error"); } }
  async function testTelegram() { try { await api("/settings/telegram/test", { method: "POST" }); notify("Telegram test message sent"); } catch (reason) { notify(reason instanceof Error ? reason.message : "Telegram test failed", "error"); } }
  if (!health || !settingsData) return <Spinner />;
  return <><PageHeader eyebrow="SYSTEM CONTROL" title="Settings" detail="Review inference health, tune guarded settings, manage local accounts, and inspect the audit trail." />
    <section className="settings-section"><div className="section-heading"><div><span className="eyebrow">RUNTIME HEALTH</span><h2>Recognition engine</h2></div><span className={`health-state ${health.recognitionReady ? "good" : "bad"}`}>{health.recognitionReady ? "READY" : "DEGRADED"}</span></div><div className="health-grid"><div><Activity /><span>Inference provider</span><strong>{health.inferenceProvider}</strong><small>{health.cudaAvailable ? "Four-camera GPU target enabled" : "CPU fallback; four-camera SLA not guaranteed"}</small></div><div><Fingerprint /><span>Recognition samples</span><strong>{health.modelSamples.toLocaleString()}</strong><small>Encrypted embeddings loaded in memory</small></div><div><Gauge /><span>Decision threshold</span><strong>{health.recognitionThreshold.toFixed(3)}</strong><small>Conservative SFace cosine threshold</small></div><div><LockKeyhole /><span>Local storage</span><strong>{formatBytes(health.storageBytes)}</strong><small>Retained until administrator deletion</small></div></div>{health.modelError && <div className="form-error"><AlertOctagon />{health.modelError}</div>}</section>
    {admin && <section className="settings-grid"><div className="settings-card"><div className="settings-card-title"><Gauge size={20} /><div><h3>Recognition threshold</h3><p>Higher values reduce false accepts but may increase no-match alerts.</p></div></div><Field label="Cosine threshold (0.36–0.60)"><input type="number" min="0.36" max="0.60" step="0.005" value={threshold} onChange={(event) => setThreshold(event.target.value)} /></Field><Button onClick={saveThreshold}>Save threshold</Button></div><div className="settings-card"><div className="settings-card-title"><Bell size={20} /><div><h3>Telegram unknown alerts</h3><p>Delivery is asynchronous and never blocks recognition.</p></div></div><label className="toggle-row"><input type="checkbox" checked={telegramEnabled} onChange={(event) => setTelegramEnabled(event.target.checked)} /><span>Enable unknown/no-match alerts</span></label><Field label="Bot token"><input type="password" value={token} onChange={(event) => setToken(event.target.value)} placeholder={settingsData.telegram.tokenSet ? "Stored securely · leave blank to keep" : "Required to enable"} /></Field><Field label="Chat ID"><input type="password" value={chatId} onChange={(event) => setChatId(event.target.value)} placeholder={settingsData.telegram.chatIdSet ? "Stored securely · leave blank to keep" : "Required to enable"} /></Field><div className="inline-actions"><Button onClick={saveTelegram}>Save Telegram</Button><Button variant="secondary" onClick={testTelegram}>Send test</Button></div></div></section>}
    <section className="settings-section"><div className="section-heading"><div><span className="eyebrow">LOCAL ACCESS</span><h2>Named accounts</h2></div>{admin && <Button onClick={() => setNewUser(true)}><UserCog size={17} />Add user</Button>}</div><div className="users-table">{usersList.map((row) => <div key={row.id}><CircleUserRound size={22} /><span><strong>{row.displayName}</strong><small>@{row.username}</small></span><span className="role-label">{row.role}</span><span className={row.active ? "account-active" : "account-inactive"}>{row.active ? "Active" : "Inactive"}</span></div>)}</div></section>
    <section className="settings-section"><div className="section-heading"><div><span className="eyebrow">APPEND-ONLY HISTORY</span><h2>Recent audit log</h2></div></div><div className="audit-list">{audit.map((row) => <div key={row.id}><time>{localDate(row.createdAt)}</time><strong>{row.user}</strong><code>{row.action}</code><span>{row.entityType}{row.entityId ? ` #${row.entityId}` : ""}</span></div>)}</div></section>
    {newUser && <NewUserModal onClose={() => setNewUser(false)} onSaved={() => { setNewUser(false); notify("Local user created"); void load(); }} notify={notify} />}
  </>;
}

function NewUserModal({ onClose, onSaved, notify }: { onClose: () => void; onSaved: () => void; notify: (message: string, kind?: "success" | "error") => void }) {
  const [username, setUsername] = useState(""); const [displayName, setDisplayName] = useState(""); const [password, setPassword] = useState(""); const [role, setRole] = useState<"admin" | "operator">("operator"); const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent) { event.preventDefault(); setBusy(true); try { await api("/users", { method: "POST", body: JSON.stringify({ username, displayName, password, role }) }); onSaved(); } catch (reason) { notify(reason instanceof Error ? reason.message : "Could not create user", "error"); } finally { setBusy(false); } }
  return <Modal title="Add named local user" onClose={onClose}><form onSubmit={submit} className="form-stack"><Field label="Username"><input value={username} onChange={(event) => setUsername(event.target.value)} minLength={3} required /></Field><Field label="Display name"><input value={displayName} onChange={(event) => setDisplayName(event.target.value)} required /></Field><Field label="Role"><select value={role} onChange={(event) => setRole(event.target.value as "admin" | "operator")}><option value="operator">Operator</option><option value="admin">Administrator</option></select></Field><Field label="Temporary password" hint="At least 12 characters"><input type="password" value={password} onChange={(event) => setPassword(event.target.value)} minLength={12} required /></Field><div className="modal-actions"><Button type="button" variant="secondary" onClick={onClose}>Cancel</Button><Button type="submit" disabled={busy}>Create user</Button></div></form></Modal>;
}
