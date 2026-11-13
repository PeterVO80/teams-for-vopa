# Vendored front-end libraries

These files are served by the application itself, so VOPA Teams works on a
network **without internet access** (and no data is sent to third-party CDNs).

| Library | Version | Source | License |
|---|---|---|---|
| Bootstrap | 5.3.3 | npm `bootstrap` (`dist/`) | MIT |
| Bootstrap Icons | 1.11.3 | npm `bootstrap-icons` (`font/`) | MIT |
| SortableJS | 1.15.2 | npm `sortablejs` | MIT |
| Toast UI Editor | 3.2.2 | npm `@toast-ui/editor`, bundled (see below) | MIT |

## Rebuilding the Toast UI Editor bundle

The npm package does not ship a single-file browser build, so it is bundled
(together with its ProseMirror dependencies and the Dutch translation) with
[esbuild](https://esbuild.github.io/) using `toastui-editor/build-entry.js`:

```bash
npm install @toast-ui/editor@3.2.2 esbuild@0.24.0
npx esbuild build-entry.js --bundle --minify --format=iife \
    --legal-comments=eof --outfile=toastui-editor-all.min.js
cp node_modules/@toast-ui/editor/dist/toastui-editor.css .
```
