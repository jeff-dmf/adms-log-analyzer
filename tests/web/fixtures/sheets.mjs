// The stylesheets of the page: index.html links only css/bundle.css, which tools/build_web_css.py builds from the sheets
// listed (in cascade order) in css/bundle.order.txt. Tests that care about a sheet or about the cascade order read that list.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const WEB = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../../src/dmslog/web");

// Sheet file names in cascade order ("tokens.css" ... "responsive.css"); the vendored uPlot sheet is "../vendor/uplot/uPlot.min.css".
export const SHEET_ORDER = fs.readFileSync(path.join(WEB, "css/bundle.order.txt"), "utf8")
  .replace(/\r\n/g, "\n").split("\n").map((l) => l.trim()).filter((l) => l && !l.startsWith("#"));

export const inBundle = (name) => SHEET_ORDER.includes(name);
// Position of a sheet in the cascade (-1 when it is not part of the page).
export const sheetIndex = (name) => SHEET_ORDER.indexOf(name);
// True when the page links the bundle (the only stylesheet it loads).
export const pageLinksBundle = (html) => /<link rel="stylesheet" href="\/css\/bundle\.css">/.test(html)
  && (html.match(/<link rel="stylesheet"/g) || []).length === 1;
