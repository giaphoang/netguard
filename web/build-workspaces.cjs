const { copyFileSync, mkdirSync } = require("node:fs");
const { dirname, join } = require("node:path");
const routes = require("./workspaceRoutes.json");

const entry = join(__dirname, "index.html");
for (const route of Object.values(routes)) {
  const directory = join(__dirname, "dist", route.slice(1));
  mkdirSync(directory, { recursive: true });
  copyFileSync(entry, join(directory, "index.html"));
}

const assets = join(__dirname, "dist", "assets");
for (const font of ["space-grotesk", "jetbrains-mono"]) {
  const directory = dirname(require.resolve(`@fontsource-variable/${font}/package.json`));
  copyFileSync(join(directory, "LICENSE"), join(assets, `${font}-LICENSE.txt`));
}
