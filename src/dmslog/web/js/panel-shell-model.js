// Pure rules of the panel frame (panel-shell.js): who closes a panel, and when Escape closes a dialog.

// The function a Close button calls. `onRequestClose` is the dock's way to close the tab; without it the module falls
// back to `hide` (compatible default). A request never throws into the click handler.
export function requestCloser(onRequestClose, hide) {
  const target = typeof onRequestClose === "function" ? onRequestClose : hide;
  return () => {
    try {
      target();
    } catch (err) {
      // A closing handler that fails leaves the panel open; the dock reports its own errors.
    }
  };
}

// Escape inside a dialog without native <dialog> support: close, unless a field already handled the key.
export function dialogKeyAction(event) {
  if (!event || event.key !== "Escape" || event.defaultPrevented) {
    return null;
  }
  return "close";
}
