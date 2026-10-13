// n/N/t/T lookups: a view change (or a newer lookup) aborts the pending request and makes its
// answer stale, so it cannot move the selection in the new view.
import test from "node:test";
import assert from "node:assert/strict";
import { createLatestRequest } from "../../src/dmslog/web/js/filters.js";

function fakeApi() {
  const pending = [];
  return {
    pending,
    page: (signal) => new Promise((resolve, reject) => {
      const call = { resolve, signal };
      signal.addEventListener("abort", () => reject(Object.assign(new Error("aborted"), { name: "AbortError" })));
      pending.push(call);
    }),
  };
}

async function lookup(latest, api, reveal) {
  const { seq, signal } = latest.begin();
  try {
    const page = await api.page(signal);
    if (!latest.isCurrent(seq)) {
      return;
    }
    reveal(page.rows[0]);
  } catch (err) {
    if (err.name !== "AbortError") {
      throw err;
    }
  }
}

test("a view change aborts the pending lookup and its answer never reveals", async () => {
  const latest = createLatestRequest();
  const api = fakeApi();
  const revealed = [];
  const running = lookup(latest, api, (row) => revealed.push(row));
  latest.invalidate(); // applyView
  assert.equal(api.pending[0].signal.aborted, true);
  api.pending[0].resolve({ rows: [{ id: 1 }] }); // late answer
  await running;
  assert.deepEqual(revealed, []);
});

test("a newer lookup supersedes the older one", async () => {
  const latest = createLatestRequest();
  const api = fakeApi();
  const revealed = [];
  const first = lookup(latest, api, (row) => revealed.push(row));
  const second = lookup(latest, api, (row) => revealed.push(row));
  api.pending[1].resolve({ rows: [{ id: 2 }] });
  await Promise.all([first, second]);
  assert.deepEqual(revealed, [{ id: 2 }]);
});
