# Live2D SDK assets

Cubism 3/4 models use the repository's upstream WebUI Core 5 distribution at
`dsakiko_webui/frontend/public/cubism/core5/`. That directory carries the
upstream `LICENSE.md`, `NOTICE.md`, and `RedistributableFiles.txt` beside the
Core. Electron development serves that single source; production builds copy
only those four files into ignored `dist/renderer/sdk/core5/` for `file://`
loading. Do not add another Core copy under this directory.

Cubism 2 uses the single existing WebUI asset at
`dsakiko_webui/frontend/public/live2d.min.js`; Electron does not vendor a
second copy. Development serves that file at `/sdk/live2d.min.js`, and the
production build copies it to `dist/renderer/sdk/live2d.min.js` for `file://`
loading. The runtime remains subject to the applicable Live2D license terms.
