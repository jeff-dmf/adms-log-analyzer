// Field error (UI-13): the message sits right under the field, tied to it with aria-invalid and aria-describedby,
// and uses .ui-field-error (components.css). text null (or "") clears the error. Text only through textContent.

const notes = new WeakMap(); // input -> its error paragraph

// form.reset() plus the removal of every error left on its fields (paragraph, aria-invalid, aria-describedby).
export function resetFormWithErrors(form, inputs) {
  form.reset();
  for (const input of inputs) {
    setFieldError(input, null);
  }
}

export function setFieldError(input, text) {
  const doc = input.ownerDocument;
  const errorId = (input.id || "field") + "-error";
  let note = notes.get(input) || null;
  const wanted = text !== null && text !== undefined && text !== "";
  if (!wanted) {
    if (note) {
      note.remove();
      notes.delete(input);
    }
    input.removeAttribute("aria-invalid");
    const rest = (input.getAttribute("aria-describedby") || "").split(/\s+/).filter((id) => id && id !== errorId);
    if (rest.length > 0) {
      input.setAttribute("aria-describedby", rest.join(" "));
    } else {
      input.removeAttribute("aria-describedby");
    }
    return null;
  }
  if (!note) {
    note = doc.createElement("p");
    note.className = "ui-field-error";
    note.id = errorId;
    notes.set(input, note);
    if (input.parentNode) {
      input.parentNode.insertBefore(note, input.nextSibling);
    }
  }
  note.textContent = String(text);
  input.setAttribute("aria-invalid", "true");
  const ids = (input.getAttribute("aria-describedby") || "").split(/\s+/).filter(Boolean);
  if (!ids.includes(errorId)) {
    ids.push(errorId);
  }
  input.setAttribute("aria-describedby", ids.join(" "));
  return note;
}
