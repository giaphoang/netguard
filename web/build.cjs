const { copyFileSync, mkdirSync, readFileSync, rmSync, writeFileSync } = require("node:fs");
const { dirname, join } = require("node:path");
const { buildSync } = require("esbuild");
const { readDemoRecording } = require("./demo-recording.cjs");
const routes = require("./workspaceRoutes.json");

const demo = process.argv.includes("--demo");
const output = join(__dirname, demo ? "dist-demo" : "dist");
const recording = demo ? readDemoRecording() : null;
if (demo) rmSync(output, { recursive: true, force: true });

buildSync({
  absWorkingDir: __dirname,
  entryPoints: ["app.tsx"],
  bundle: true,
  minify: true,
  loader: { ".woff2": "file" },
  assetNames: "assets/[name]-[hash]",
  outfile: join(output, "app.js"),
  define: { __NETGUARD_DEMO__: JSON.stringify(recording) },
  logLevel: "info",
});

const entry = join(__dirname, "index.html");
if (demo) {
  const html = readFileSync(entry, "utf8")
    .replace(/<title>.*<\/title>/, "<title>NetGuard · Ubuntu failover replay</title>")
    .replace(/<meta name="description" content="[^"]*"/, '<meta name="description" content="Replay a recorded Ubuntu network failover and inspect the local AI diagnosis."');
  writeFileSync(join(output, "index.html"), html);
} else {
  for (const route of Object.values(routes)) {
    const directory = join(output, route.slice(1));
    mkdirSync(directory, { recursive: true });
    copyFileSync(entry, join(directory, "index.html"));
  }
}

const assets = join(output, "assets");
for (const font of ["space-grotesk", "jetbrains-mono"]) {
  const directory = dirname(require.resolve(`@fontsource-variable/${font}/package.json`));
  copyFileSync(join(directory, "LICENSE"), join(assets, `${font}-LICENSE.txt`));
}
