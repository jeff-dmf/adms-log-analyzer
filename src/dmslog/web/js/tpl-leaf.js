// Template leaf of the filter tree: {tpl: [id | code, ...]}. Loaded together with filter-tree.js (UI-14: main.js no
// longer imports the tree, so the tree and its presets and saved views load when the filter tree is first opened).
import { TPL_LEAF, parseTplList } from "./filters.js";
import { registerLeafType } from "./filter-tree-model.js";
import { registerLeafEditor } from "./filter-tree.js";
import { S } from "./strings.js";

try {
  registerLeafType("tpl", TPL_LEAF);
} catch (err) {
  // Already registered (module evaluated twice): keep the existing definition.
}
let tplEditorUid = 0;
registerLeafEditor("tpl", {
  label: () => S.ftTypeTpl,
  render(leaf, ctx) {
    tplEditorUid += 1;
    const wrap = document.createElement("span");
    wrap.className = "ft-fields ft-grow";
    const label = document.createElement("label");
    const input = document.createElement("input");
    input.id = "ft-tpl-" + tplEditorUid;
    label.htmlFor = input.id;
    label.textContent = S.ftTplLabel;
    input.type = "text";
    input.autocomplete = "off";
    input.placeholder = S.ftTplPlaceholder;
    input.value = leaf.data.ids.join(", ");
    input.addEventListener("input", () => {
      const ids = parseTplList(input.value);
      ctx.update({ ids: ids.includes(-1) ? [-1] : ids }, "ids"); // [-1] fails validation
    });
    wrap.append(label, input);
    return wrap;
  },
});
