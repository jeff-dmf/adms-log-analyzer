# uPlot (vendored)

- Project: https://github.com/leeoniya/uPlot (MIT, see LICENSE)
- Version: 1.6.32 (tag `1.6.32`, released 2025-03-14)
- Source: `dist/uPlot.esm.js`, `dist/uPlot.min.css` and `LICENSE` from that tag, copied unmodified.
- `uPlot.esm.min.js` is what the page loads: `dist/uPlot.esm.js` minified once with esbuild 0.25.10 (`esbuild uPlot.esm.js --minify --format=esm --target=es2020 --outfile=uPlot.esm.min.js`) plus a one-line header; 145 KB become 52 KB. `uPlot.esm.js` stays as the unmodified source of record and is not served to the browser.

| File | SHA-256 |
|---|---|
| uPlot.esm.js | 5dd9b3281aa64b461b42d9945f6adb2649d346502b12281a9ae0d46599a80eba |
| uPlot.esm.min.js | d3c2bf8a4c37c51cca833b6f2aea87d6467920787f624a821afb331c8fb4097a |
| uPlot.min.css | df630c6a8d6f8eeaff264b50f73ce5b114f646ffd9a0bb74f049b0a00135fa04 |
| LICENSE | 8f989229699b4fe2f1a0432d0e9edc338a8a911e250e2d1b01ecd770a5f5b1bd |

Loaded as an ES module (the `.min.js` file) by `js/timeline.js`, `js/dashboard.js`, `js/aggregate-panel.js` and `js/panels-panel.js` (no CDN, no build step). To update: replace the files from a
newer tag, update this table and run the test suite.
