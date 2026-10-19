// The message-queue box of the dialogs: the case's option fills it, but never over what the user already chose.
import assert from "node:assert/strict";
import { test } from "node:test";
import { installGlobalDom } from "./fixtures/fake-dom.mjs";

const doc = installGlobalDom();
const { createQueueOption } = await import("../../src/dmslog/web/js/case-dialogs.js");

function option() {
  const opt = createQueueOption(doc);
  const box = opt.node.all().find((n) => n.tagName === "INPUT");
  const user = (checked) => {
    box.checked = checked;
    box.dispatch("change");
  };
  return { opt, box, user };
}

test("the box follows the case option until the user touches it", () => {
  const { opt, box } = option();
  opt.baseline(true);
  assert.equal(box.checked, true);
  assert.equal(opt.checked(), true);
});

test("a late answer of the case does not overwrite a choice the user already made", () => {
  const { opt, box, user } = option();
  user(true); // the user ticks it before the answer arrives
  opt.baseline(false); // the late answer says the case has it off
  assert.equal(box.checked, true);
  const second = option();
  second.opt.baseline(true);
  second.user(false); // unticks a case that has it on
  second.opt.baseline(true); // a late answer says it is on
  assert.equal(second.box.checked, false); // the user's choice stands: false goes to the server
  assert.equal(second.opt.checked(), false);
});

test("a fresh dialog session forgets the touch and the baseline", () => {
  const { opt, box, user } = option();
  opt.baseline(true);
  user(false);
  opt.fresh();
  assert.equal(box.checked, false);
  opt.baseline(true); // the new session takes the case's option again
  assert.equal(box.checked, true);
});
