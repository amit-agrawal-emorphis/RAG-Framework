// import { useEffect, useMemo, useState } from "react";
// import Login from "./Login";

// const API_BASE = import.meta.env.VITE_DM_API_BASE || "http://127.0.0.1:8001";
// const LOGO_SRC = "/header_logo.png";
// const DELETE_ICON_SRC = "/delete.png";
// const DOWNLOAD_ICON_SRC = "/download.png";
// const STATUS_COLORS = {
//   Completed: "#16a34a",
//   "In progress": "#0ea5e9",
//   Failed: "#dc2626",
//   Uploaded: "#f59e0b"
// };

// function slugifyMachineName(raw) {
//   return (raw || "")
//     .trim()
//     .replace(/\s+/g, "_")
//     .replace(/[^A-Za-z0-9_.-]/g, "_")
//     .replace(/^[._]+|[._]+$/g, "");
// }

// async function parseError(response) {
//   try {
//     const payload = await response.json();
//     if (payload?.detail) return payload.detail;
//     if (payload?.message) return payload.message;
//   } catch (_err) {
//     // ignore parsing fallback
//   }
//   return `Request failed (${response.status})`;
// }

// export default function App() {
//   const [authedUser, setAuthedUser] = useState("");
//   const [rows, setRows] = useState([]);
//   const [message, setMessage] = useState({ type: "info", text: "" });

//   const [showUploadModal, setShowUploadModal] = useState(false);
//   const [machineInput, setMachineInput] = useState("");
//   const [machineSelection, setMachineSelection] = useState("");
//   const machineName = useMemo(() => slugifyMachineName(machineSelection === "__custom__" ? machineInput : machineSelection), [
//     machineSelection,
//     machineInput
//   ]);
//   const [selectedFiles, setSelectedFiles] = useState([]);
//   const machineOptions = useMemo(() => {
//     const setNames = new Set(rows.map((row) => row.machineName).filter(Boolean));
//     return Array.from(setNames).sort((a, b) => a.localeCompare(b));
//   }, [rows]);
//   const groupedRows = useMemo(() => {
//     const grouped = {};
//     rows.forEach((row) => {
//       if (!grouped[row.machineName]) grouped[row.machineName] = [];
//       grouped[row.machineName].push(row);
//     });
//     return Object.entries(grouped)
//       .sort(([a], [b]) => a.localeCompare(b))
//       .map(([machineName, docs]) => ({ machineName, docs }));
//   }, [rows]);
//   const analytics = useMemo(() => {
//     const totalMachines = groupedRows.length;
//     const totalDocuments = rows.length;
//     const completed = rows.filter((row) => row.status === "Completed").length;
//     const inProgress = rows.filter((row) => row.status === "In progress").length;
//     const failed = rows.filter((row) => row.status === "Failed").length;
//     const uploadedOnly = Math.max(0, totalDocuments - completed - inProgress - failed);
//     const completionRate = totalDocuments > 0 ? Math.round((completed / totalDocuments) * 100) : 0;
//     const statusItems = [
//       { label: "Completed", value: completed, color: STATUS_COLORS.Completed },
//       { label: "In progress", value: inProgress, color: STATUS_COLORS["In progress"] },
//       { label: "Failed", value: failed, color: STATUS_COLORS.Failed },
//       { label: "Uploaded", value: uploadedOnly, color: STATUS_COLORS.Uploaded }
//     ];
//     const pieTotal = Math.max(1, statusItems.reduce((sum, item) => sum + item.value, 0));
//     let angleCursor = 0;
//     const pieStops = statusItems
//       .map((item) => {
//         const pct = Math.max(0, (item.value / pieTotal) * 100);
//         const start = angleCursor;
//         const end = angleCursor + pct;
//         angleCursor = end;
//         return `${item.color} ${start.toFixed(2)}% ${end.toFixed(2)}%`;
//       })
//       .join(", ");
//     const machineSeriesRaw = groupedRows.map((group) => {
//       const machineCompleted = group.docs.filter((doc) => doc.status === "Completed").length;
//       const machineRate = group.docs.length ? Math.round((machineCompleted / group.docs.length) * 100) : 0;
//       const unitsPerHour = group.docs.length * 22 + machineCompleted * 12;
//       return {
//         label: group.machineName,
//         unitsPerHour,
//         oee: machineRate
//       };
//     });
//     const machineSeries = machineSeriesRaw.length > 0 ? machineSeriesRaw : [{ label: "No data", unitsPerHour: 0, oee: 0 }];
//     const maxUnits = Math.max(100, ...machineSeries.map((item) => item.unitsPerHour));
//     return {
//       totalMachines,
//       totalDocuments,
//       completed,
//       inProgress,
//       failed,
//       uploadedOnly,
//       completionRate,
//       statusItems,
//       pieStops,
//       machineSeries,
//       maxUnits
//     };
//   }, [groupedRows, rows]);

//   useEffect(() => {
//     if (!authedUser) return;
//     void loadAllDocuments();
//   }, [authedUser]);

//   useEffect(() => {
//     if (!authedUser) return undefined;
//     const timer = setInterval(() => {
//       void loadAllDocuments();
//     }, 3000);
//     return () => clearInterval(timer);
//   }, [authedUser]);

//   async function loadAllDocuments() {
//     if (!authedUser) return;
//     const query = new URLSearchParams({ user_id: authedUser });
//     const response = await fetch(`${API_BASE}/api/documents/all?${query.toString()}`);
//     if (!response.ok) {
//       const detail = await parseError(response);
//       setMessage({ type: "error", text: detail });
//       return;
//     }
//     const payload = await response.json();
//     setRows(payload.rows || []);
//   }

//   function onOpenUploadModal() {
//     setMachineInput("");
//     setMachineSelection("");
//     setSelectedFiles([]);
//     setShowUploadModal(true);
//   }

//   function onCloseUploadModal() {
//     setShowUploadModal(false);
//   }

//   async function onSaveAndNext() {
//     if (!machineName) {
//       setMessage({ type: "warning", text: "Please enter a machine name." });
//       return;
//     }
//     if (selectedFiles.length === 0) {
//       setMessage({ type: "warning", text: "Please select at least one document or video." });
//       return;
//     }

//     const formData = new FormData();
//     formData.append("user_id", authedUser);
//     formData.append("machine_name", machineName);
//     selectedFiles.forEach((file) => formData.append("files", file));

//     const response = await fetch(`${API_BASE}/api/documents/upload`, {
//       method: "POST",
//       body: formData
//     });

//     if (!response.ok) {
//       setMessage({ type: "error", text: await parseError(response) });
//       return;
//     }

//     const payload = await response.json();
//     if (payload.savedCount > 0) {
//       const text =
//         payload.skippedCount > 0
//           ? `Uploaded ${payload.savedCount} file(s). Ignored ${payload.skippedCount} duplicates.`
//           : `Uploaded ${payload.savedCount} file(s).`;
//       setMessage({ type: "success", text });
//       const ingestQuery = new URLSearchParams({
//         user_id: authedUser,
//         machine_name: machineName
//       });
//       await fetch(`${API_BASE}/api/ingest/start?${ingestQuery.toString()}`, {
//         method: "POST"
//       });
//       setMessage({ type: "info", text: `Upload successful. Ingestion started for ${machineName}.` });
//     } else if (payload.skippedCount > 0) {
//       setMessage({ type: "info", text: `Ignored ${payload.skippedCount} duplicate/invalid file(s).` });
//     } else {
//       setMessage({ type: "info", text: "No files selected for upload." });
//     }
//     onCloseUploadModal();
//     await loadAllDocuments();
//   }

//   async function onDeleteRow(path, machineNameForRow, fileName) {
//     const query = new URLSearchParams();
//     query.append("user_id", authedUser);
//     query.append("machine_name", machineNameForRow);
//     query.append("file_name", fileName);
//     const response = await fetch(`${API_BASE}/api/documents/file?${query.toString()}`, { method: "DELETE" });
//     if (!response.ok) {
//       setMessage({ type: "error", text: await parseError(response) });
//       return;
//     }

//     const payload = await response.json();
//     setMessage({ type: "success", text: `Deleted ${payload.deletedCount} file(s).` });
//     setRows((prev) => prev.filter((row) => row.absPath !== path));
//     await loadAllDocuments();
//   }

//   async function onDeleteMachine(machineNameForDelete) {
//     const selectedPaths = rows.filter((row) => row.machineName === machineNameForDelete);
//     if (selectedPaths.length === 0) return;
//     const query = new URLSearchParams();
//     query.append("user_id", authedUser);
//     query.append("machine_name", machineNameForDelete);
//     const response = await fetch(`${API_BASE}/api/documents/machine?${query.toString()}`, { method: "DELETE" });
//     if (!response.ok) {
//       setMessage({ type: "error", text: await parseError(response) });
//       return;
//     }
//     const payload = await response.json();
//     setMessage({ type: "success", text: `Deleted ${payload.deletedCount} file(s) for ${machineNameForDelete}.` });
//     setRows((prev) => prev.filter((row) => row.machineName !== machineNameForDelete));
//     await loadAllDocuments();
//   }

//   async function onDownloadMachine(machineNameForDownload) {
//     if (!machineNameForDownload) return;
//     const group = groupedRows.find((g) => g.machineName === machineNameForDownload);
//     const allCompleted = group?.docs.every((doc) => doc.status === "Completed");
//     if (!allCompleted) {
//       setMessage({ type: "warning", text: `Ingestion is not completed yet for ${machineNameForDownload}.` });
//       return;
//     }
//     const downloadQuery = new URLSearchParams({
//       user_id: authedUser,
//       machine_name: machineNameForDownload
//     });
//     const url = `${API_BASE}/api/export-zip?${downloadQuery.toString()}`;
//     window.open(url, "_blank", "noopener,noreferrer");
//     setMessage({ type: "success", text: `ZIP downloaded for ${machineNameForDownload}.` });
//   }

//   function renderProductionEfficiencyChart(extraClass = "") {
//     return (
//       <article className={`chart-card ${extraClass}`.trim()}>
//         <div className="chart-card-head">
//           <h4>Production Efficiency vs. OEE</h4>
//         </div>
//         <svg className="combo-chart" viewBox="0 0 560 250" role="img" aria-label="Production efficiency and OEE chart">
//           <line x1="56" y1="24" x2="56" y2="210" className="axis-line" />
//           <line x1="56" y1="210" x2="536" y2="210" className="axis-line" />
//           {analytics.machineSeries.map((item, index) => {
//             const barSlot = 480 / analytics.machineSeries.length;
//             const x = 62 + barSlot * index + barSlot * 0.1;
//             const barWidth = Math.max(18, barSlot * 0.45);
//             const barHeight = (item.unitsPerHour / analytics.maxUnits) * 160;
//             const y = 210 - barHeight;
//             const oeeY = 210 - (item.oee / 100) * 160;
//             return (
//               <g key={item.label}>
//                 <rect x={x} y={y} width={barWidth} height={barHeight} className="bar-rect" rx="4" />
//                 <circle cx={x + barWidth / 2} cy={oeeY} r="4" className="line-point" />
//                 <text x={x + barWidth / 2} y="228" textAnchor="middle" className="x-label">
//                   {item.label.length > 9 ? `${item.label.slice(0, 9)}…` : item.label}
//                 </text>
//               </g>
//             );
//           })}
//           <polyline
//             fill="none"
//             className="line-path"
//             points={analytics.machineSeries
//               .map((item, index) => {
//                 const barSlot = 480 / analytics.machineSeries.length;
//                 const x = 62 + barSlot * index + Math.max(18, barSlot * 0.45) / 2 + barSlot * 0.1;
//                 const y = 210 - (item.oee / 100) * 160;
//                 return `${x},${y}`;
//               })
//               .join(" ")}
//           />
//         </svg>
//         <div className="chart-legend-inline">
//           <span className="legend-chip">
//             <span className="chip-box bar-chip"></span> Units per hour
//           </span>
//           <span className="legend-chip">
//             <span className="chip-line"></span> OEE Percentage
//           </span>
//         </div>
//       </article>
//     );
//   }

//   function renderDocumentList(extraClass = "") {
//     return (
//       <section className={`table-wrap ${extraClass}`.trim()}>
//         <div className="list-heading-row">
//           <h3>Document list</h3>
//           <button className="upload-btn" onClick={onOpenUploadModal}>
//             Upload
//           </button>
//         </div>
//         {groupedRows.length === 0 ? (
//           <p className="muted">No documents or videos uploaded for this machine yet.</p>
//         ) : (
//           <div className="list-scroll">
//             <table>
//               <thead>
//                 <tr>
//                   <th></th>
//                   <th></th>
//                   <th></th>
//                   <th></th>
//                 </tr>
//               </thead>
//               <tbody>
//                 {groupedRows.flatMap((group) => {
//                   const machineRow = (
//                     <tr key={`machine_${group.machineName}`} className="machine-row">
//                       <td>{group.machineName}</td>
//                       <td>
//                         {group.docs.every((doc) => doc.status === "Completed")
//                           ? "Completed"
//                           : group.docs.some((doc) => doc.status === "In progress")
//                             ? "In progress"
//                             : group.docs.some((doc) => doc.status === "Failed")
//                               ? "Failed"
//                               : "Uploaded"}
//                       </td>
//                       <td>
//                         <button
//                           className="icon-btn"
//                           title="Download tenant ZIP"
//                           disabled={!group.docs.every((doc) => doc.status === "Completed")}
//                           onClick={() => onDownloadMachine(group.machineName)}
//                         >
//                           <img src={DOWNLOAD_ICON_SRC} alt="Download" className="action-icon" />
//                         </button>
//                       </td>
//                       <td>
//                         <button className="icon-btn danger" title="Delete machine files" onClick={() => onDeleteMachine(group.machineName)}>
//                           <img src={DELETE_ICON_SRC} alt="Delete" className="action-icon" />
//                         </button>
//                       </td>
//                     </tr>
//                   );
//                   const docRows = group.docs.map((doc) => (
//                     <tr key={doc.absPath}>
//                       <td className="doc-name-cell">{doc.fileName}</td>
//                       <td>{doc.status}</td>
//                       <td>-</td>
//                       <td>
//                         <button
//                           className="icon-btn danger"
//                           title="Delete file"
//                           onClick={() => onDeleteRow(doc.absPath, group.machineName, doc.fileName)}
//                         >
//                           <img src={DELETE_ICON_SRC} alt="Delete" className="action-icon" />
//                         </button>
//                       </td>
//                     </tr>
//                   ));
//                   return [machineRow, ...docRows];
//                 })}
//               </tbody>
//             </table>
//           </div>
//         )}
//       </section>
//     );
//   }

//   if (!authedUser) {
//     return <Login apiBase={API_BASE} onAuthSuccess={(uid) => setAuthedUser(uid)} />;
//   }

//   return (
//     <div className="page">
//       <header className="header with-logo">
//         <img src={LOGO_SRC} alt="Yuktra" className="header-logo" />
//         <h2>Equipment Intelligence</h2>
//         <button
//           type="button"
//           className="logout-btn"
//           onClick={() => {
//             setRows([]);
//             setMessage({ type: "info", text: "" });
//             setAuthedUser("");
//           }}
//         >
//           Logout
//         </button>
//       </header>

//       {message.text && <div className={`alert alert-${message.type}`}>{message.text}</div>}

//       <section className="analytics-panel">
//         <div className="analytics-head">
//           <h3>Operational Analytics</h3>
//           <span className="analytics-subtitle">Live ingestion and document processing overview</span>
//         </div>
//         <div className="analytics-cards">
//           <div className="metric-card">
//             <span className="metric-label">Machines</span>
//             <strong>{analytics.totalMachines}</strong>
//           </div>
//           <div className="metric-card">
//             <span className="metric-label">Documents</span>
//             <strong>{analytics.totalDocuments}</strong>
//           </div>
//           <div className="metric-card">
//             <span className="metric-label">Completed</span>
//             <strong>{analytics.completed}</strong>
//           </div>
//           <div className="metric-card">
//             <span className="metric-label">Success Rate</span>
//             <strong>{analytics.completionRate}%</strong>
//           </div>
//         </div>
//         <div className="analytics-charts-grid">
//           <div className="analytics-left-stack">
//             <article className="chart-card">
//               <div className="chart-card-head">
//                 <h4>Equipment Status Overview</h4>
//               </div>
//               <div className="pie-layout">
//                 <div
//                   className="pie-chart"
//                   style={{
//                     background: `conic-gradient(${analytics.pieStops})`
//                   }}
//                 ></div>
//                 <div className="pie-legend">
//                   {analytics.statusItems.map((item) => {
//                     const total = analytics.totalDocuments || 1;
//                     const pct = Math.round((item.value / total) * 100);
//                     return (
//                       <div className="legend-row" key={item.label}>
//                         <span className="legend-swatch" style={{ backgroundColor: item.color }}></span>
//                         <span className="legend-label">{item.label}</span>
//                         <span className="legend-value">
//                           {item.value} ({pct}%)
//                         </span>
//                       </div>
//                     );
//                   })}
//                 </div>
//               </div>
//             </article>

//             {renderProductionEfficiencyChart("production-chart-card")}
//           </div>

//           {renderDocumentList("document-list-card")}
//         </div>
//       </section>

//       {showUploadModal && (
//         <div className="modal-overlay" onClick={onCloseUploadModal}>
//           <div className="modal-card" onClick={(e) => e.stopPropagation()}>
//             <div className="modal-header">
//               <h3>Upload</h3>
//               <button className="modal-close" onClick={onCloseUploadModal} aria-label="Close upload modal">
//                 x
//               </button>
//             </div>
//             <div className="modal-body">
//               <div className="field modal-field">
//                 <label>Machine/Model Name *</label>
//                 <select value={machineSelection} onChange={(e) => setMachineSelection(e.target.value)}>
//                   <option value="">Select machine name</option>
//                   {machineOptions.map((name) => (
//                     <option key={name} value={name}>
//                       {name}
//                     </option>
//                   ))}
//                   <option value="__custom__">Other (Type manually)</option>
//                 </select>
//               </div>
//               {machineSelection === "__custom__" && (
//                 <div className="field modal-field">
//                   <label>Machine Name *</label>
//                   <input
//                     value={machineInput}
//                     onChange={(e) => setMachineInput(e.target.value)}
//                     placeholder="Enter machine name"
//                   />
//                 </div>
//               )}

//               <h3 className="modal-subtitle">Upload File/Folder *</h3>
//               <div className="upload-card modal-upload-card">
//                 <label className="drop-zone">
//                   <input
//                     type="file"
//                     accept=".pdf,.docx,.txt,.md,.markdown,.mp4"
//                     multiple
//                     onChange={(e) => setSelectedFiles(Array.from(e.target.files || []))}
//                   />
//                   <span className="drop-zone-icon">⇪</span>
//                   <span>Drag & drop or upload document/video files</span>
//                 </label>
//               </div>

//               <div className="selected-files">
//                 <h4>Selected File</h4>
//                 <table className="selected-files-table">
//                   <thead>
//                     <tr>
//                       <th>File Name</th>
//                       <th>File Path</th>
//                       <th>Size(in bytes)</th>
//                       <th>Action</th>
//                     </tr>
//                   </thead>
//                   <tbody>
//                     {selectedFiles.length === 0 ? (
//                       <tr>
//                         <td colSpan={4} className="no-data">
//                           No Data Found
//                         </td>
//                       </tr>
//                     ) : (
//                       selectedFiles.map((file) => (
//                         <tr key={`${file.name}_${file.size}`}>
//                           <td>{file.name}</td>
//                           <td>{file.webkitRelativePath || "-"}</td>
//                           <td>{file.size}</td>
//                           <td>
//                             <button
//                               className="icon-btn danger"
//                               onClick={() =>
//                                 setSelectedFiles((prev) =>
//                                   prev.filter((f) => !(f.name === file.name && f.size === file.size))
//                                 )
//                               }
//                             >
//                               <img src={DELETE_ICON_SRC} alt="Delete" className="action-icon" />
//                             </button>
//                           </td>
//                         </tr>
//                       ))
//                     )}
//                   </tbody>
//                 </table>
//               </div>

//               <div className="modal-actions">
//                 <button className="primary" onClick={onSaveAndNext}>
//                   Save & Next
//                 </button>
//               </div>
//             </div>
//           </div>
//         </div>
//       )}
//     </div>
//   );
// }




import { useEffect, useMemo, useState } from "react";
import Login from "./Login";

const API_BASE = import.meta.env.VITE_DM_API_BASE || "";
const LOGO_SRC = "/header_logo.png";
const DELETE_ICON_SRC = "/delete.png";
const DOWNLOAD_ICON_SRC = "/download.png";
const STATUS_COLORS = {
  Completed: "#16a34a",
  "In progress": "#0ea5e9",
  Failed: "#dc2626",
  Uploaded: "#f59e0b"
};

function slugifyMachineName(raw) {
  return (raw || "")
    .trim()
    .replace(/\s+/g, "_")
    .replace(/[^A-Za-z0-9_.-]/g, "_")
    .replace(/^[._]+|[._]+$/g, "");
}

async function parseError(response) {
  try {
    const payload = await response.json();
    if (payload?.detail) return payload.detail;
    if (payload?.message) return payload.message;
  } catch (_err) {
    // ignore parsing fallback
  }
  return `Request failed (${response.status})`;
}

const COMPLETED_DOCS_STORAGE_KEY = "yuktra_completed_docs";
const ROWS_PER_PAGE_OPTIONS = [10, 25, 50, 75];
const docKey = (machineName, fileName) => `${machineName}|||${fileName}`;

function sleep(ms) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

// Build a compact list of page tokens, e.g. [1, 2, 3, 4, 5, "...", 97].
function buildPageItems(current, total) {
  if (total <= 7) return Array.from({ length: total }, (_, i) => i + 1);
  const items = [1];
  const start = Math.max(2, current - 1);
  const end = Math.min(total - 1, current + 1);
  if (start > 2) items.push("ellipsis-start");
  for (let page = start; page <= end; page += 1) items.push(page);
  if (end < total - 1) items.push("ellipsis-end");
  items.push(total);
  return items;
}

function loadCompletedKeys() {
  try {
    const raw = localStorage.getItem(COMPLETED_DOCS_STORAGE_KEY);
    return new Set(raw ? JSON.parse(raw) : []);
  } catch {
    return new Set();
  }
}

export default function App() {
  const [authedUser, setAuthedUser] = useState("");
  const [rows, setRows] = useState([]);
  // Files that have already finished ingesting at least once. The backend reports a
  // single machine-level status for every file, so when a new file re-triggers
  // ingestion the whole machine flips to "In progress". We remember the files that
  // were already completed and keep showing them as completed.
  const [completedKeys, setCompletedKeys] = useState(() => loadCompletedKeys());
  const [message, setMessage] = useState({ type: "info", text: "" });
  const [downloadProgressByMachine, setDownloadProgressByMachine] = useState({});

  const [searchQuery, setSearchQuery] = useState("");
  const [rowsPerPage, setRowsPerPage] = useState(10);
  const [currentPage, setCurrentPage] = useState(1);
  const [showUploadModal, setShowUploadModal] = useState(false);
  const [machineInput, setMachineInput] = useState("");
  const [machineSelection, setMachineSelection] = useState("");
  const machineName = useMemo(() => slugifyMachineName(machineSelection === "__custom__" ? machineInput : machineSelection), [
    machineSelection,
    machineInput
  ]);
  const [selectedFiles, setSelectedFiles] = useState([]);
  const machineOptions = useMemo(() => {
    const setNames = new Set(rows.map((row) => row.machineName).filter(Boolean));
    return Array.from(setNames).sort((a, b) => a.localeCompare(b));
  }, [rows]);
  const effectiveRows = useMemo(() => {
    return rows.map((row) => {
      // Never override live backend "In progress" with stale localStorage completion.
      if (row.status === "In progress") return row;
      const alreadyCompleted =
        row.status === "Completed" || completedKeys.has(docKey(row.machineName, row.fileName));
      if (!alreadyCompleted) return row;
      return { ...row, status: "Completed", ingestionPct: 100 };
    });
  }, [rows, completedKeys]);
  const groupedRows = useMemo(() => {
    const grouped = {};
    effectiveRows.forEach((row) => {
      if (!grouped[row.machineName]) grouped[row.machineName] = [];
      grouped[row.machineName].push(row);
    });
    return Object.entries(grouped)
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([machineName, docs]) => ({ machineName, docs }));
  }, [effectiveRows]);
  const analytics = useMemo(() => {
    const totalMachines = groupedRows.length;
    const totalDocuments = effectiveRows.length;
    const completed = effectiveRows.filter((row) => row.status === "Completed").length;
    const inProgress = effectiveRows.filter((row) => row.status === "In progress").length;
    const failed = effectiveRows.filter((row) => row.status === "Failed").length;
    const uploadedOnly = Math.max(0, totalDocuments - completed - inProgress - failed);
    const completionRate = totalDocuments > 0 ? Math.round((completed / totalDocuments) * 100) : 0;
    const statusItems = [
      { label: "Completed", value: completed, color: STATUS_COLORS.Completed },
      { label: "In progress", value: inProgress, color: STATUS_COLORS["In progress"] },
      { label: "Failed", value: failed, color: STATUS_COLORS.Failed },
      { label: "Uploaded", value: uploadedOnly, color: STATUS_COLORS.Uploaded }
    ];
    const pieTotal = Math.max(1, statusItems.reduce((sum, item) => sum + item.value, 0));
    let angleCursor = 0;
    const pieStops = statusItems
      .map((item) => {
        const pct = Math.max(0, (item.value / pieTotal) * 100);
        const start = angleCursor;
        const end = angleCursor + pct;
        angleCursor = end;
        return `${item.color} ${start.toFixed(2)}% ${end.toFixed(2)}%`;
      })
      .join(", ");
    const machineSeriesRaw = groupedRows.map((group) => {
      const machineCompleted = group.docs.filter((doc) => doc.status === "Completed").length;
      const machineRate = group.docs.length ? Math.round((machineCompleted / group.docs.length) * 100) : 0;
      const unitsPerHour = group.docs.length * 22 + machineCompleted * 12;
      return {
        label: group.machineName,
        unitsPerHour,
        oee: machineRate
      };
    });
    const machineSeries = machineSeriesRaw.length > 0 ? machineSeriesRaw : [{ label: "No data", unitsPerHour: 0, oee: 0 }];
    const maxUnits = Math.max(100, ...machineSeries.map((item) => item.unitsPerHour));
    return {
      totalMachines,
      totalDocuments,
      completed,
      inProgress,
      failed,
      uploadedOnly,
      completionRate,
      statusItems,
      pieStops,
      machineSeries,
      maxUnits
    };
  }, [groupedRows, effectiveRows]);

  useEffect(() => {
    setCurrentPage(1);
  }, [searchQuery, rowsPerPage]);

  useEffect(() => {
    if (!authedUser) return;
    void loadAllDocuments();
  }, [authedUser]);

  useEffect(() => {
    if (!authedUser) return undefined;
    const timer = setInterval(() => {
      void loadAllDocuments();
    }, rows.some((row) => row.status === "In progress") ? 1000 : 3000);
    return () => clearInterval(timer);
  }, [authedUser, rows]);

  async function loadAllDocuments() {
    const response = await fetch(`${API_BASE}/api/documents/all`);
    if (!response.ok) {
      const detail = await parseError(response);
      setMessage({ type: "error", text: detail });
      return;
    }
    const payload = await response.json();
    const nextRows = payload.rows || [];
    setRows(nextRows);
    setCompletedKeys((prev) => {
      const present = new Set(nextRows.map((row) => docKey(row.machineName, row.fileName)));
      const next = new Set();
      // Keep previously-completed files that still exist on the server.
      for (const key of prev) if (present.has(key)) next.add(key);
      // Mark anything currently reported as completed.
      for (const row of nextRows) {
        if (row.status === "Completed") next.add(docKey(row.machineName, row.fileName));
      }
      try {
        localStorage.setItem(COMPLETED_DOCS_STORAGE_KEY, JSON.stringify([...next]));
      } catch {
        // ignore storage errors (e.g. private mode)
      }
      return next;
    });
  }

  function onOpenUploadModal() {
    setMachineInput("");
    setMachineSelection("");
    setSelectedFiles([]);
    setShowUploadModal(true);
  }

  function onCloseUploadModal() {
    setShowUploadModal(false);
  }

  async function onSaveAndNext() {
    if (!machineName) {
      setMessage({ type: "warning", text: "Please enter a machine name." });
      return;
    }
    if (selectedFiles.length === 0) {
      setMessage({ type: "warning", text: "Please select at least one document or video." });
      return;
    }

    const formData = new FormData();
    formData.append("machine_name", machineName);
    selectedFiles.forEach((file) => formData.append("files", file));

    const response = await fetch(`${API_BASE}/api/documents/upload`, {
      method: "POST",
      body: formData
    });

    if (!response.ok) {
      setMessage({ type: "error", text: await parseError(response) });
      return;
    }

    const payload = await response.json();
    if (payload.savedCount > 0) {
      await fetch(`${API_BASE}/api/ingest/start?machine_name=${encodeURIComponent(machineName)}`, {
        method: "POST"
      });
    }
    onCloseUploadModal();
    await loadAllDocuments();
  }

  async function onDeleteRow(path, machineNameForRow, fileName) {
    if (!window.confirm(`Are you sure you want to delete "${fileName}"?`)) return;
    const query = new URLSearchParams();
    query.append("machine_name", machineNameForRow);
    query.append("file_name", fileName);
    const response = await fetch(`${API_BASE}/api/documents/file?${query.toString()}`, { method: "DELETE" });
    if (!response.ok) {
      setMessage({ type: "error", text: await parseError(response) });
      return;
    }

    const payload = await response.json();
    setRows((prev) => prev.filter((row) => row.absPath !== path));
    await loadAllDocuments();
  }

  async function onDeleteMachine(machineNameForDelete) {
    const selectedPaths = rows.filter((row) => row.machineName === machineNameForDelete);
    if (selectedPaths.length === 0) return;
    if (!window.confirm(`Are you sure you want to delete all files for "${machineNameForDelete}"?`)) return;
    const query = new URLSearchParams();
    query.append("machine_name", machineNameForDelete);
    const response = await fetch(`${API_BASE}/api/documents/machine?${query.toString()}`, { method: "DELETE" });
    if (!response.ok) {
      setMessage({ type: "error", text: await parseError(response) });
      return;
    }
    const payload = await response.json();
    setRows((prev) => prev.filter((row) => row.machineName !== machineNameForDelete));
    await loadAllDocuments();
  }

  async function onDownloadMachine(machineNameForDownload) {
    if (!machineNameForDownload) return;
    if (downloadProgressByMachine[machineNameForDownload]) return;

    const group = groupedRows.find((g) => g.machineName === machineNameForDownload);
    const allCompleted = group?.docs.every((doc) => doc.status === "Completed");
    if (!allCompleted) {
      setMessage({ type: "warning", text: `Ingestion is not completed yet for ${machineNameForDownload}.` });
      return;
    }

    const machineQuery = encodeURIComponent(machineNameForDownload);
    const prepareUrl = `${API_BASE}/api/export-zip/prepare?machine_name=${machineQuery}`;
    const statusUrl = `${API_BASE}/api/export-zip/status?machine_name=${machineQuery}`;
    const downloadUrl = `${API_BASE}/api/export-zip?machine_name=${machineQuery}`;

    setDownloadProgressByMachine((prev) => ({
      ...prev,
      [machineNameForDownload]: { phase: "extracting", progress: 0 }
    }));

    const clearDownloadProgress = () => {
      setDownloadProgressByMachine((prev) => {
        const next = { ...prev };
        delete next[machineNameForDownload];
        return next;
      });
    };

    const setDownloadState = (phase, progress, queuePosition = null) => {
      setDownloadProgressByMachine((prev) => ({
        ...prev,
        [machineNameForDownload]: {
          phase,
          progress,
          ...(queuePosition != null ? { queuePosition } : {})
        }
      }));
    };

    try {
      const prepareResponse = await fetch(prepareUrl, { method: "POST" });
      if (!prepareResponse.ok) {
        clearDownloadProgress();
        setMessage({ type: "error", text: await parseError(prepareResponse) });
        return;
      }

      const preparePayload = await prepareResponse.json();
      if (preparePayload.state === "queued") {
        setDownloadState("queued", 0, preparePayload.queuePosition ?? null);
      }

      while (true) {
        const statusResponse = await fetch(statusUrl);
        if (!statusResponse.ok) {
          clearDownloadProgress();
          setMessage({ type: "error", text: await parseError(statusResponse) });
          return;
        }

        const statusPayload = await statusResponse.json();
        if (statusPayload.state === "ready") {
          break;
        }
        if (statusPayload.state === "failed") {
          clearDownloadProgress();
          setMessage({
            type: "error",
            text: statusPayload.error || `Export failed for ${machineNameForDownload}.`
          });
          return;
        }
        if (statusPayload.state === "queued") {
          setDownloadState("queued", 0, statusPayload.queuePosition ?? null);
        } else if (statusPayload.state === "building") {
          setDownloadState("extracting", 0);
        }

        await sleep(2000);
      }

      setDownloadState("preparing", 100);
      await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));

      const link = document.createElement("a");
      link.href = downloadUrl;
      link.download = "Yuktra-YEQ.zip";
      link.rel = "noopener noreferrer";
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);

      window.setTimeout(clearDownloadProgress, 1200);
    } catch (_err) {
      clearDownloadProgress();
      setMessage({ type: "error", text: "Download failed. Please try again." });
    }
  }

  function renderProductionEfficiencyChart(extraClass = "") {
    const chartLeft = 56;
    const chartRight = 536;
    const chartBottom = 210;
    const chartTop = 24;
    const plotHeight = chartBottom - chartTop;
    const plotWidth = chartRight - chartLeft;
    const seriesCount = analytics.machineSeries.length;
    const slotWidth = Math.min(96, plotWidth / Math.max(seriesCount, 1));
    const barWidth = Math.min(52, slotWidth * 0.55);
    const totalSeriesWidth = slotWidth * seriesCount;
    const seriesStart = chartLeft + (plotWidth - totalSeriesWidth) / 2;

    return (
      <article className={`chart-card ${extraClass}`.trim()}>
        <div className="chart-card-head">
          <h4>Production Efficiency vs. OEE</h4>
        </div>
        <div className="combo-chart-wrap">
          <svg
            className="combo-chart"
            viewBox="0 0 560 250"
            preserveAspectRatio="xMidYMid meet"
            role="img"
            aria-label="Production efficiency and OEE chart"
          >
            <line x1={chartLeft} y1={chartTop} x2={chartLeft} y2={chartBottom} className="axis-line" />
            <line x1={chartLeft} y1={chartBottom} x2={chartRight} y2={chartBottom} className="axis-line" />
            {analytics.machineSeries.map((item, index) => {
              const slotX = seriesStart + slotWidth * index;
              const x = slotX + (slotWidth - barWidth) / 2;
              const barHeight = (item.unitsPerHour / analytics.maxUnits) * plotHeight;
              const y = chartBottom - barHeight;
              const oeeY = chartBottom - (item.oee / 100) * plotHeight;
              const labelX = x + barWidth / 2;
              return (
                <g key={item.label}>
                  <rect x={x} y={y} width={barWidth} height={barHeight} className="bar-rect" rx="4" />
                  <circle cx={labelX} cy={oeeY} r="4" className="line-point" />
                  <text x={labelX} y="228" textAnchor="middle" className="x-label">
                    {item.label.length > 12 ? `${item.label.slice(0, 12)}…` : item.label}
                  </text>
                </g>
              );
            })}
            <polyline
              fill="none"
              className="line-path"
              points={analytics.machineSeries
                .map((item, index) => {
                  const slotX = seriesStart + slotWidth * index;
                  const x = slotX + slotWidth / 2;
                  const y = chartBottom - (item.oee / 100) * plotHeight;
                  return `${x},${y}`;
                })
                .join(" ")}
            />
          </svg>
        </div>
        <div className="chart-legend-inline">
          <span className="legend-chip">
            <span className="chip-box bar-chip"></span> Units per hour
          </span>
          <span className="legend-chip">
            <span className="chip-line"></span> OEE Percentage
          </span>
        </div>
      </article>
    );
  }

  function renderDocumentList(extraClass = "") {
    const query = searchQuery.trim().toLowerCase();
    const filteredGroups = query
      ? groupedRows
          .map((group) => {
            const machineMatches = group.machineName.toLowerCase().includes(query);
            if (machineMatches) return group;
            const docs = group.docs.filter((doc) => doc.fileName.toLowerCase().includes(query));
            return docs.length ? { ...group, docs } : null;
          })
          .filter(Boolean)
      : groupedRows;
    const totalPages = Math.max(1, Math.ceil(filteredGroups.length / rowsPerPage));
    const safePage = Math.min(currentPage, totalPages);
    const pageStart = (safePage - 1) * rowsPerPage;
    const pagedGroups = filteredGroups.slice(pageStart, pageStart + rowsPerPage);
    const pageItems = buildPageItems(safePage, totalPages);
    return (
      <section className={`table-wrap ${extraClass}`.trim()}>
        <div className="list-heading-row">
          <h3>Document list</h3>
          <div className="list-heading-actions">
            <input
              type="search"
              className="doc-search-input"
              placeholder="Search machine or document..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
            />
            <button className="upload-btn" onClick={onOpenUploadModal}>
              Upload
            </button>
          </div>
        </div>
        {groupedRows.length === 0 ? (
          <p className="muted">No documents or videos uploaded for this machine yet.</p>
        ) : filteredGroups.length === 0 ? (
          <p className="muted">No machines or documents match "{searchQuery}".</p>
        ) : (
          <>
          <div className="list-scroll">
            <table>
              <thead>
                <tr>
                  <th></th>
                  <th></th>
                  <th className="ingestion-pct-col">Progress %</th>
                  <th></th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {pagedGroups.flatMap((group) => {
                  const allCompleted = group.docs.every((doc) => doc.status === "Completed");
                  const inProgressDocs = group.docs.filter((doc) => doc.status === "In progress");
                  const machineStatus = allCompleted
                    ? "Completed"
                    : inProgressDocs.length > 0
                      ? "In progress"
                      : group.docs.some((doc) => doc.status === "Failed")
                        ? "Failed"
                        : "Uploaded";
                  const machinePct = allCompleted
                    ? 100
                    : inProgressDocs.length > 0
                      ? Math.max(...inProgressDocs.map((doc) => Number(doc.ingestionPct) || 0))
                      : 0;
                  const downloadState = downloadProgressByMachine[group.machineName];
                  const machineRow = (
                    <tr key={`machine_${group.machineName}`} className="machine-row">
                      <td className="machine-name-cell">
                        {downloadState ? (
                          <div className="machine-download-row">
                            <div
                              className={`machine-download-progress${
                                downloadState.phase === "extracting"
                                  ? " is-extracting"
                                  : downloadState.phase === "queued"
                                    ? " is-queued"
                                    : ""
                              }`}
                              role="progressbar"
                              aria-valuemin={0}
                              aria-valuemax={100}
                              aria-valuenow={downloadState.progress}
                              aria-label={`Downloading ${group.machineName}`}
                            >
                              <div
                                className="machine-download-progress-fill"
                                style={{ width: `${downloadState.progress}%` }}
                              />
                              <span className="machine-download-label">{group.machineName}</span>
                            </div>
                            <span className="machine-download-status">
                              {downloadState.phase === "queued"
                                ? `Queued${downloadState.queuePosition ? ` (#${downloadState.queuePosition})` : ""}`
                                : downloadState.progress >= 100
                                  ? "Downloading…"
                                  : "Preparing zip …."}
                            </span>
                          </div>
                        ) : (
                          group.machineName
                        )}
                      </td>
                      <td>{machineStatus}</td>
                      <td className="ingestion-pct-col">{machinePct}%</td>
                      <td>
                        <button
                          className="icon-btn"
                          title="Download tenant ZIP"
                          disabled={!allCompleted || !!downloadState}
                          onClick={() => onDownloadMachine(group.machineName)}
                        >
                          <img src={DOWNLOAD_ICON_SRC} alt="Download" className="action-icon" />
                        </button>
                      </td>
                      <td>
                        <button className="icon-btn danger" title="Delete machine files" onClick={() => onDeleteMachine(group.machineName)}>
                          <img src={DELETE_ICON_SRC} alt="Delete" className="action-icon" />
                        </button>
                      </td>
                    </tr>
                  );
                  const docRows = group.docs.map((doc) => (
                    <tr key={doc.absPath}>
                      <td className="doc-name-cell">{doc.fileName}</td>
                      <td>{doc.status}</td>
                      <td className="ingestion-pct-col" title={doc.ingestError || ""}>
                        {doc.status === "In progress"
                          ? `${Number(doc.ingestionPct) || 0}%`
                          : doc.status === "Completed"
                            ? "100%"
                            : doc.status === "Failed"
                              ? "0%"
                              : "-"}
                      </td>
                      <td>-</td>
                      <td>
                        <button
                          className="icon-btn danger"
                          title="Delete file"
                          onClick={() => onDeleteRow(doc.absPath, group.machineName, doc.fileName)}
                        >
                          <img src={DELETE_ICON_SRC} alt="Delete" className="action-icon" />
                        </button>
                      </td>
                    </tr>
                  ));
                  return [machineRow, ...docRows];
                })}
              </tbody>
            </table>
          </div>
          <div className="list-pagination">
            <div className="rows-per-page">
              <span>Rows per page:</span>
              <select
                className="rows-per-page-select"
                value={rowsPerPage}
                onChange={(e) => setRowsPerPage(Number(e.target.value))}
              >
                {ROWS_PER_PAGE_OPTIONS.map((option) => (
                  <option key={option} value={option}>
                    {option}
                  </option>
                ))}
              </select>
            </div>
            <div className="pagination-controls">
              <button
                type="button"
                className="page-nav"
                disabled={safePage <= 1}
                onClick={() => setCurrentPage(safePage - 1)}
                aria-label="Previous page"
              >
                ‹
              </button>
              {pageItems.map((item) =>
                typeof item === "number" ? (
                  <button
                    type="button"
                    key={item}
                    className={`page-btn${item === safePage ? " active" : ""}`}
                    onClick={() => setCurrentPage(item)}
                  >
                    {item}
                  </button>
                ) : (
                  <span key={item} className="page-ellipsis">
                    …
                  </span>
                )
              )}
              <button
                type="button"
                className="page-nav"
                disabled={safePage >= totalPages}
                onClick={() => setCurrentPage(safePage + 1)}
                aria-label="Next page"
              >
                ›
              </button>
            </div>
          </div>
          </>
        )}
      </section>
    );
  }

  if (!authedUser) {
    return <Login apiBase={API_BASE} onAuthSuccess={(uid) => setAuthedUser(uid)} />;
  }

  return (
    <div className="page">
      <header className="header with-logo">
        <div className="header-brand">
          <img src={LOGO_SRC} alt="Yuktra" className="header-logo" />
          <span className="header-brand-subtitle">Equipment Intelligence</span>
        </div>
        <h2>Control Pane</h2>
        <button type="button" className="logout-btn" onClick={() => setAuthedUser("")}>
          Logout
        </button>
      </header>

      {message.text && <div className={`alert alert-${message.type}`}>{message.text}</div>}

      <section className="analytics-panel">
        <div className="analytics-charts-grid">
          <div className="analytics-left-stack">
            <div className="analytics-head">
              <h3>Operational Analytics</h3>
            </div>
            <div className="analytics-cards">
              <div className="metric-card">
                <span className="metric-label">Machines</span>
                <strong>{analytics.totalMachines}</strong>
              </div>
              <div className="metric-card">
                <span className="metric-label">Documents</span>
                <strong>{analytics.totalDocuments}</strong>
              </div>
              <div className="metric-card">
                <span className="metric-label">Completed</span>
                <strong>{analytics.completed}</strong>
              </div>
              <div className="metric-card">
                <span className="metric-label">Success Rate</span>
                <strong>{analytics.completionRate}%</strong>
              </div>
            </div>
            <article className="chart-card">
              <div className="chart-card-head">
                <h4>Equipment Status Overview</h4>
              </div>
              <div className="pie-layout">
                <div
                  className="pie-chart"
                  style={{
                    background: `conic-gradient(${analytics.pieStops})`
                  }}
                ></div>
                <div className="pie-legend">
                  {analytics.statusItems.map((item) => {
                    const total = analytics.totalDocuments || 1;
                    const pct = Math.round((item.value / total) * 100);
                    return (
                      <div className="legend-row" key={item.label}>
                        <span className="legend-swatch" style={{ backgroundColor: item.color }}></span>
                        <span className="legend-label">{item.label}</span>
                        <span className="legend-value">
                          {item.value} ({pct}%)
                        </span>
                      </div>
                    );
                  })}
                </div>
              </div>
            </article>

            {renderProductionEfficiencyChart("production-chart-card")}
          </div>

          {renderDocumentList("document-list-card")}
        </div>
      </section>

      {showUploadModal && (
        <div className="modal-overlay" onClick={onCloseUploadModal}>
          <div className="modal-card" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h3>Upload</h3>
              <button className="modal-close" onClick={onCloseUploadModal} aria-label="Close upload modal">
                x
              </button>
            </div>
            <div className="modal-body">
              <div className="field modal-field">
                <label>Machine/Model Name *</label>
                <select value={machineSelection} onChange={(e) => setMachineSelection(e.target.value)}>
                  <option value="">Select machine name</option>
                  {machineOptions.map((name) => (
                    <option key={name} value={name}>
                      {name}
                    </option>
                  ))}
                  <option value="__custom__">Other (Type manually)</option>
                </select>
              </div>
              {machineSelection === "__custom__" && (
                <div className="field modal-field">
                  <label>Machine Name *</label>
                  <input
                    value={machineInput}
                    onChange={(e) => setMachineInput(e.target.value)}
                    placeholder="Enter machine name"
                  />
                </div>
              )}

              <h3 className="modal-subtitle">Upload File/Folder *</h3>
              <div className="upload-card modal-upload-card">
                <label className="drop-zone">
                  <input
                    type="file"
                    accept=".pdf,.docx,.txt,.md,.markdown,.mp4"
                    multiple
                    onChange={(e) => setSelectedFiles(Array.from(e.target.files || []))}
                  />
                  <span className="drop-zone-icon">⇪</span>
                  <span>Drag & drop or upload document/video files</span>
                </label>
              </div>

              <div className="selected-files">
                <h4>Selected File</h4>
                <table className="selected-files-table">
                  <thead>
                    <tr>
                      <th>File Name</th>
                      <th>File Path</th>
                      <th>Size (MB)</th>
                      <th>Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {selectedFiles.length === 0 ? (
                      <tr>
                        <td colSpan={4} className="no-data">
                          No Data Found
                        </td>
                      </tr>
                    ) : (
                      selectedFiles.map((file) => (
                        <tr key={`${file.name}_${file.size}`}>
                          <td>{file.name}</td>
                          <td>{file.webkitRelativePath || "-"}</td>
                          <td>{(file.size / (1024 * 1024)).toFixed(2)} MB</td>
                          <td>
                            <button
                              className="icon-btn danger"
                              onClick={() =>
                                setSelectedFiles((prev) =>
                                  prev.filter((f) => !(f.name === file.name && f.size === file.size))
                                )
                              }
                            >
                              <img src={DELETE_ICON_SRC} alt="Delete" className="action-icon" />
                            </button>
                          </td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>

              <div className="modal-actions">
                <button className="primary" onClick={onSaveAndNext}>
                  Save & Next
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
 