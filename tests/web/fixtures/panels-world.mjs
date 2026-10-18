// A fake server for the Panels tests: every request is held until the test answers it, honours the abort signal, and
// is recorded. Timers and the clock are fake too, so a test can see exactly which timers are alive.
export const flush = () => new Promise((resolve) => setImmediate(resolve));

export function widget(id, extra = {}) {
  return {
    id, title: "Widget " + id,
    query: { rows: "entries", group_by: ["tpl"], measures: ["count"], limit: 100 },
    chart: "table", options: {}, ...extra,
  };
}

export function panel(id, name, widgets = [widget("w1")], rev = 1) {
  return { id, rev, name, widgets };
}

export function runAnswer(p, extra = {}) {
  return {
    panel: p.id, rev: p.rev, complete: true, budget_ms: 15000, elapsed_ms: 120,
    results: p.widgets.map((w) => ({
      id: w.id, status: "ok", error: null,
      result: {
        columns: [{ id: "tpl", role: "group" }, { id: "count", role: "measure" }],
        rows: [{ values: [7, 42], labels: ["Type seven", null], drill: null }],
        groups: 1, truncated: false, total: 42, exact: true, method: "exact", notes: [],
      },
    })),
    ...extra,
  };
}

export class ApiFail extends Error {
  constructor(status, code) {
    super(code);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

export function makeWorld({ caseId = "A", panels = [panel("aaaaaaaaaaaa", "Alpha")] } = {}) {
  const w = {
    caseId,
    clock: 1_000_000,
    timers: new Map(),
    nextTimer: 1,
    streams: [],
    held: { list: [], run: [], create: [], update: [], remove: [] },
    states: 0,
    server: { panels: panels.map((p) => JSON.parse(JSON.stringify(p))) },
  };
  // `w.rejectOnAbort = false` models a response that was already on its way when the request was aborted: the
  // promise still resolves later, and only the panel's own checks can tell that nobody wants it any more.
  w.rejectOnAbort = true;
  const hold = (kind, args, signal) => new Promise((resolve, reject) => {
    const item = { args, resolve, reject, aborted: false };
    w.held[kind].push(item);
    if (signal) {
      signal.addEventListener("abort", () => {
        item.aborted = true;
        if (w.rejectOnAbort) {
          reject(Object.assign(new Error("aborted"), { name: "AbortError" }));
        }
      });
    }
  });
  w.api = {
    progressUrl: (id) => "/api/cases/" + id + "/progress",
    keyTypes: async () => ({ types: [{ type: "msgid" }] }),
    panels: {
      list: (signal) => hold("list", [], signal),
      create: (body) => hold("create", [body]),
      update: (id, body, ifRev) => hold("update", [id, body, ifRev]),
      remove: (id) => hold("remove", [id]),
      run: (caseId_, id, widgets, signal) => hold("run", [caseId_, id, widgets], signal),
    },
  };
  w.options = {
    api: w.api,
    getCase: () => w.caseId,
    now: () => w.clock,
    setTimer: (fn, ms) => {
      const id = w.nextTimer++;
      w.timers.set(id, { fn, due: w.clock + ms, ms });
      return id;
    },
    clearTimer: (id) => w.timers.delete(id),
    connect: (opts) => {
      const stream = { ...opts, closed: false, close() { stream.closed = true; } };
      w.streams.push(stream);
      return stream;
    },
  };
  w.live = () => w.streams.filter((s) => !s.closed);
  w.advance = (ms) => {
    w.clock += ms;
    for (const [id, t] of Array.from(w.timers)) {
      if (t.due <= w.clock) {
        w.timers.delete(id);
        t.fn();
      }
    }
  };
  w.answerList = (index = w.held.list.length - 1, data) => {
    w.held.list[index].resolve(data ?? { panels: w.server.panels, problems: [], invalid: false, limits: { panels: 20, widgets: 16, rows: 4000, title_chars: 80, name_chars: 80 } });
  };
  return w;
}
