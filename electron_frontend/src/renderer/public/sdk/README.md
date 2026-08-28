# Live2D SDK assets

The Cubism Core runtime is intentionally not included in this repository.
`pixi-live2d-display` requires `live2dcubismcore.min.js` for Cubism 3/4 model
files. Obtain the file from the official Cubism SDK for Web download:

https://www.live2d.com/download/cubism-sdk/download-web/

After accepting the SDK terms, place the runtime at:

`electron_frontend/src/renderer/public/sdk/live2dcubismcore.min.js`

The Electron bootstrap keeps loading this path opportunistically. Cubism 2
models continue to use the bundled `live2d.min.js` and do not require Core;
Cubism 3/4 models need the user-provided file. Do not redistribute the Core
runtime unless your Live2D SDK license permits it.

`live2d.min.js` is the existing Cubism 2 compatibility runtime.
