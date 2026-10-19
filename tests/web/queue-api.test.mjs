// The message-queue calls of api.js: what is sent, and that unticking the option reaches the server (issue #74).
import assert from "node:assert/strict";
import { test } from "node:test";

const sent = [];
globalThis.fetch = async (url, options) => {
  sent.push({ url, options });
  return { ok: true, status: 200, headers: { get: () => "application/json" }, json: async () => ({}), text: async () => "{}" };
};
const { api } = await import("../../src/dmslog/web/js/api.js");

const bodyOf = () => JSON.parse(sent.at(-1).options.body);

test("addCollection sends message_queue only when it is a boolean: false turns the option off", async () => {
  await api.addCollection("c", "/p.zip", "default", null, true);
  assert.equal(bodyOf().message_queue, true);
  await api.addCollection("c", "/p.zip", "default", null, false);
  assert.equal(bodyOf().message_queue, false);
  await api.addCollection("c", "/p.zip", "default", null, undefined);
  assert.equal("message_queue" in bodyOf(), false);
  await api.addCollection("c", "/p.zip", "default", { from: 1, to: 2 });
  assert.deepEqual(Object.keys(bodyOf()).sort(), ["first", "path", "scope"]);
});

test("the queue routes and their query strings", async () => {
  await api.queueItems("c d", { state: "pending", q: "", limit: 50, after: undefined });
  assert.equal(sent.at(-1).url, "/api/queue/items?case=c+d&state=pending&limit=50");
  await api.queuePreview("c", 7, "stored");
  assert.equal(sent.at(-1).url, "/api/queue/items/7/preview?case=c&form=stored");
  await api.queuePreview("c", 7, "anything");
  assert.match(sent.at(-1).url, /form=wire$/);
  await api.queueRemove("c", null);
  assert.equal(sent.at(-1).url, "/api/cases/c/queue");
  assert.equal(sent.at(-1).options.method, "DELETE");
  await api.queueRemove("c", 3);
  assert.equal(sent.at(-1).url, "/api/cases/c/queue?group=3");
  await api.queueRead("c", 3);
  assert.deepEqual(bodyOf(), { group: 3 });
});
