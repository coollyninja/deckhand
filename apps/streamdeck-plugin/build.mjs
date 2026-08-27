// esbuild config for the Deckhand Stream Deck plugin.
//
// Two things matter here and both were real bugs:
//  1. TARGET must match the Node the Stream Deck app ships (20.x), not the host's.
//  2. The bundle is ESM, but `ws` (via @elgato/streamdeck) is CommonJS and does a
//     dynamic require() of node builtins. esbuild's ESM output has no `require`,
//     so it throws "Dynamic require of \"events\" is not supported" and the plugin
//     crashes on launch. The banner injects a real `require` via createRequire.
import * as esbuild from "esbuild";

await esbuild.build({
  entryPoints: ["src/plugin.ts"],
  bundle: true,
  platform: "node",
  format: "esm",
  target: "node20",
  outfile: "com.coollyninja.deckhand.sdPlugin/bin/plugin.js",
  sourcemap: true,
  banner: {
    js: [
      "import { createRequire as __deckhandCreateRequire } from 'node:module';",
      "const require = __deckhandCreateRequire(import.meta.url);",
    ].join("\n"),
  },
});
console.log("built com.coollyninja.deckhand.sdPlugin/bin/plugin.js");
