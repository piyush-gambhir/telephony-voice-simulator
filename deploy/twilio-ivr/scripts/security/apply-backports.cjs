"use strict";
const { readFileSync, writeFileSync } = require("node:fs");
const { createHash } = require("node:crypto");
const path = require("node:path");
const root = path.resolve(__dirname, "../..");
const entries = require("./backports.json");
const hash = (text) => createHash("sha256").update(text).digest("hex");
function apply() {
  for (const entry of entries) {
    const file = path.join(root, entry.file);
    const packageFile = path.join(root, entry.packageFile);
    if (JSON.parse(readFileSync(packageFile)).version !== entry.version)
      throw Error(
        `Review security backport for changed ${entry.package} version`,
      );
    let text = readFileSync(file, "utf8");
    if (hash(text) === entry.after) continue;
    if (hash(text) !== entry.before)
      throw Error(`Unexpected ${entry.package} source bytes`);
    for (const [before, after] of entry.changes) {
      if (text.split(before).length !== 2)
        throw Error(`Ambiguous ${entry.package} patch`);
      text = text.replace(before, after);
    }
    if (hash(text) !== entry.after)
      throw Error(`Invalid ${entry.package} patch result`);
    writeFileSync(file, text);
  }
}
if (require.main === module) apply();
module.exports = { apply, entries, root };
