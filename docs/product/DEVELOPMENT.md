# Development

The console is native HTML, CSS and JavaScript served directly by FastAPI. There is no bundler step. Use node --check on app.js, run the Runtime pytest suite, and exercise the installed console with scripts/observatory_browser_smoke.py against an empty test data directory.

Run the server and browser tests in separate terminals. Install Playwright in an isolated tool environment or pass an installed Chromium/Edge executable. See the observatory guide for the exact commands.

Keep new research audit output in the runtime data directory. Do not overwrite frozen experiment artifacts during validation. Use a fresh directory for reproduction runs.
