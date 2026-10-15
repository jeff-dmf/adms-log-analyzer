// Top bar (UI-12): wires the controls that index.html already contains to the action registry.
// Nothing is created here that changes the bar's size: every button exists in the HTML from the first paint and
// this module only sets state (pressed, disabled with a reason, labels) and opens menus built by ui-menu.js.
// A control whose action is missing or whose module failed to load is disabled with a title, never hidden.
import { toMenuItems } from "./actions.js";
import { createMenu } from "./ui-menu.js";
import { S as defaultStrings, format, pluralKey } from "./strings.js";

const MENU_GROUPS = ["analyze", "export", "view"];

// A disabled control shows the reason as its title; enabled again, it gets back the title it had.
function setDisabled(control, disabled, reason) {
  if (control.dataset && control.dataset.baseTitle === undefined) {
    control.dataset.baseTitle = control.title || "";
  }
  control.disabled = disabled;
  control.title = disabled && reason ? reason : (control.dataset && control.dataset.baseTitle) || "";
}

/**
 * refs: caseButton, caseLabel (text node holder), toggles (buttons with data-action), menus ({analyze, export, view}
 *   -> trigger buttons), caseControls (search and go-to-time fields and buttons, disabled without a case),
 *   alertsBadge (count holder inside the alerts toggle) or null.
 * options: actions, ctx, getCaseLabel() -> text of the open case or "", strings, doc.
 * Returns { refresh(), setAlertsCount(n), menus }.
 */
export function mountMenubar({ actions, ctx, refs, getCaseLabel = () => "", strings = defaultStrings, doc = null }) {
  const S = strings;
  const owner = doc || (refs.caseButton && refs.caseButton.ownerDocument);
  const menus = {};
  let alertsCount = 0;

  // The View menu also carries the help items (keyboard shortcuts), after a divider.
  function menuDefs(group) {
    const defs = actions.list({ group });
    if (group !== "view") {
      return defs;
    }
    const help = actions.list({ group: "help" });
    return help.length > 0 ? [...defs, { separator: true }, ...help] : defs;
  }

  // The case menu: the cases (a radio group), then New case, Add files and Close case.
  function caseItems() {
    const items = [];
    for (const item of toMenuItems(actions, actions.list({ group: "case" }), ctx, S)) {
      if (item.id === "case.open") {
        if (item.children && item.children.length > 0) {
          items.push(...item.children, { separator: true, id: "" });
        }
        continue;
      }
      items.push(item);
    }
    return items;
  }

  if (refs.caseButton) {
    menus.case = createMenu({
      trigger: refs.caseButton,
      getItems: caseItems,
      label: S.caseLabel,
      doc: owner,
    });
  }
  for (const group of MENU_GROUPS) {
    const trigger = refs.menus && refs.menus[group];
    if (trigger) {
      menus[group] = createMenu({
        trigger,
        getItems: () => toMenuItems(actions, menuDefs(group), ctx, S),
        label: trigger.getAttribute("aria-label") || trigger.textContent,
        doc: owner,
      });
    }
  }

  for (const control of refs.toggles || []) {
    control.addEventListener("click", () => {
      actions.invoke(control.dataset.action, ctx).then(() => refresh());
    });
  }

  function paintToggle(control) {
    const id = control.dataset.action;
    if (!actions.get(id)) {
      control.setAttribute("aria-pressed", "false");
      setDisabled(control, true, S.actionUnavailable);
      return;
    }
    const now = actions.state(id, ctx);
    control.setAttribute("aria-pressed", String(Boolean(now.pressed)));
    setDisabled(control, !now.enabled, S[now.reasonKey]);
  }

  function paintMenu(group, trigger) {
    const defs = menuDefs(group);
    const states = defs.filter((def) => !def.separator).map((def) => actions.state(def.id, ctx));
    const usable = states.some((now) => now.enabled);
    const reason = states.length > 0 ? S[states[0].reasonKey] : S.actionUnavailable;
    setDisabled(trigger, !usable, reason);
  }

  function paintAlerts() {
    const badge = refs.alertsBadge;
    if (!badge) {
      return;
    }
    badge.hidden = alertsCount <= 0;
    badge.textContent = alertsCount > 0 ? String(alertsCount) : "";
    const button = refs.toggles && refs.toggles.find((c) => c.dataset.action === "toggle.alerts");
    if (button) {
      const label = alertsCount > 0
        ? format(S[pluralKey("actAlertsCount", alertsCount)], { count: alertsCount })
        : S.actAlerts;
      button.setAttribute("aria-label", label);
    }
  }

  function refresh() {
    const hasCase = ctx.caseId() !== null;
    if (refs.caseLabel) {
      refs.caseLabel.textContent = getCaseLabel() || S.caseNone;
    }
    for (const control of refs.toggles || []) {
      paintToggle(control);
    }
    for (const group of MENU_GROUPS) {
      if (refs.menus && refs.menus[group]) {
        paintMenu(group, refs.menus[group]);
      }
    }
    for (const control of refs.caseControls || []) {
      setDisabled(control, !hasCase, S.needCase);
    }
    paintAlerts();
  }

  actions.onChange(refresh);
  refresh();

  return {
    refresh,
    menus,
    setAlertsCount(n) {
      alertsCount = Math.max(0, Number(n) || 0);
      paintAlerts();
    },
  };
}
