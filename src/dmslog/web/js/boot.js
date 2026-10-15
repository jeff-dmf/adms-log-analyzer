// Entry point of the viewer (UI-14). It stays tiny on purpose: it starts the opening requests (cases, then the
// first case's sources and collections in parallel) and only then loads main.js, whose module graph index.html
// has already asked the browser to fetch with <link rel="modulepreload">. The requests and the modules therefore
// travel together instead of one after the other.
import { startPrefetch } from "./prefetch.js";

try {
  startPrefetch();
} catch (err) {
  // The prefetch is an optimisation: main.js asks for everything itself when it did not run.
}

import("./main.js");
