// All user-visible UI strings live here, accessed by key (one catalog per locale in the future).
// Log text is never placed in this file: it is shown literally, only through text nodes.

export const S = {
  appName: "dmslog",

  // Panels and toolbar
  panelSources: "Sources",
  panelGrid: "Log entries",
  panelDetail: "Entry detail",
  searchLabel: "Search",
  searchPlaceholder: "Search text (words, \"phrase\", prefix*)",
  searchButton: "Search",
  clearButton: "Clear",
  themeToggle: "Toggle theme",
  themeLight: "Theme: light",
  themeDark: "Theme: dark",
  columnsButton: "Columns",
  gotoLabel: "Go to time",
  gotoPlaceholder: "YYYY-MM-DD HH:MM:SS",
  gotoButton: "Go",
  loading: "Loading...",

  // Case and collections
  caseLabel: "Case",
  caseNone: "(no case)",
  newCaseLabel: "New case name",
  newCaseButton: "Create case",
  pathLabel: "ERS zip, folder or file",
  pathPlaceholder: "Path to an ERS zip, folder or file",
  scopeLabel: "Scope",
  scopeDefault: "Default logs",
  scopeAll: "All logs",
  openButton: "Open",
  needCase: "Create or select a case first.",
  needPath: "Enter the path of a zip, folder or file.",
  collectionStarted: "Collection started.",
  progressLabel: "Indexing progress",
  progressLine: "{source}: {records} records",
  progressDone: "Indexing finished.",
  progressFailed: "Indexing failed",
  conflict_different_capture: "This case already has another capture of the same ERS. Create a new case for it.",
  conflict_different_ers: "This case already holds a different ERS. Create a new case for it.",
  conflict_generic: "This collection conflicts with the one already in the case.",

  // Sources
  sourcesEmpty: "No sources yet. Open an ERS to start.",
  sourcesAll: "All sources",
  sourceMeta: "{family} - pid {pid} - {records} records",
  sourceSensitive: "sensitive",

  // Filters
  filterSource: "Source",
  filterSeverity: "Severity",
  filterLevel: "Level",
  filterFrom: "From (log time)",
  filterTo: "To (log time)",
  filterTimePlaceholder: "YYYY-MM-DD HH:MM:SS",
  filterAny: "Any",
  filterApply: "Apply",
  filterReset: "Reset",
  filterBadTime: "Invalid time. Use YYYY-MM-DD HH:MM:SS (log time).",
  severityNames: ["", "Critical", "Error", "Warning", "Info", "Noise"],
  levelNames: ["", "FATAL", "EXIT", "ERROR", "ALWAYS", "DEBUGLOG", "WARNING", "INFO", "VERBOSE"],
  severityAtLeast: "{name} or worse",
  levelAtLeast: "{name} or worse",

  // Grid
  columnTs: "Time",
  columnPid: "PID",
  columnTid: "TID",
  columnLevel: "Level",
  columnSource: "Source",
  columnMessage: "Message",
  gridEmpty: "No entries match.",
  gridCount: "{count} entries",
  gridCountApprox: "about {count} entries",
  gridPosition: "Showing from {time}",
  extraLines: "+{count} lines",
  rowLabel: "Entry {id}",

  // Detail
  detailEmpty: "Select an entry to see its full text.",
  detailMeta: "{time} - pid {pid} - tid {tid} - {level} - {source}",
  contextButton: "Context",
  contextSame: "Same",
  contextSameSource: "Same source",
  contextSameTid: "Same thread",
  contextSameMerge: "All sources",
  contextClose: "Close context",
  contextTitle: "Context around the entry",
  copyButton: "Copy text",
  copied: "Copied.",

  // Errors
  errorGeneric: "Request failed",
  errorForbidden: "Session not valid. Reopen the link printed by the dmslog server.",
  errorNetwork: "Could not reach the server.",
  errorSearch: "Search failed",
};

export function format(template, values) {
  return String(template).replace(/\{(\w+)\}/g, (match, key) =>
    Object.prototype.hasOwnProperty.call(values, key) ? String(values[key]) : match);
}
