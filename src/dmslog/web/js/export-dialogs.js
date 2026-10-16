// "Export report..." (UI-23): the export dialog and, on top of it, the redaction settings dialog. Both are modal
// <dialog>s added to `host` (any stable element, for example document.body); neither is a dock tab. One module so the
// two load together, on the first use of the action.
import { initExportPanel } from "./export-panel.js";
import { initRedactionPanel } from "./redaction-panel.js";

// Options: getContext() as in initExportPanel, onClose() after the export dialog closed.
// Returns { open(opener), close(), isOpen(), refresh(), reset() }; reset() closes both and forgets the case's result.
export function mountExportDialogs(host, { getContext, onClose = null }) {
  const doc = host.ownerDocument || document;
  let redaction = null;
  const exporting = initExportPanel(host, {
    getContext,
    onClose,
    onOpenRedaction() {
      if (redaction === null) {
        redaction = initRedactionPanel(host);
      }
      redaction.open(doc.activeElement); // closing it returns the focus to the button that opened it
    },
  });
  return {
    open: (opener) => exporting.open(opener),
    close() {
      if (redaction !== null) {
        redaction.close();
      }
      exporting.close();
    },
    isOpen: () => exporting.isOpen(),
    refresh: () => exporting.refresh(),
    reset() {
      if (redaction !== null) {
        redaction.close();
      }
      exporting.reset();
    },
  };
}
