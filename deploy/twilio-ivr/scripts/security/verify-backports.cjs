"use strict";
const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const { spawnSync } = require("node:child_process");
const { createHash } = require("node:crypto");
const { apply, entries, root } = require("./apply-backports.cjs");
apply();
test("backports retain real versions and exact reviewed source bytes", () => {
  for (const entry of entries)
    assert.equal(
      createHash("sha256")
        .update(fs.readFileSync(path.join(root, entry.file)))
        .digest("hex"),
      entry.after,
    );
  apply();
});
test("backport installer fails closed on unexpected source or version", () => {
  const os = require("node:os");
  const scratch = fs.mkdtempSync(path.join(os.tmpdir(), "twilio-security-"));
  try {
    fs.mkdirSync(path.join(scratch, "scripts/security"), { recursive: true });
    for (const name of ["apply-backports.cjs", "backports.json"]) {
      fs.copyFileSync(
        path.join(__dirname, name),
        path.join(scratch, "scripts/security", name),
      );
    }
    const entry = entries[0];
    const packageFile = path.join(scratch, entry.packageFile);
    const sourceFile = path.join(scratch, entry.file);
    fs.mkdirSync(path.dirname(sourceFile), { recursive: true });
    fs.writeFileSync(packageFile, JSON.stringify({ version: entry.version }));
    fs.writeFileSync(sourceFile, "unexpected bytes");
    const script = path.join(scratch, "scripts/security/apply-backports.cjs");
    let result = spawnSync(process.execPath, [script], { encoding: "utf8" });
    assert.notEqual(result.status, 0);
    assert.match(result.stderr, /Unexpected file-type source bytes/);
    fs.writeFileSync(packageFile, JSON.stringify({ version: "0.0.0" }));
    result = spawnSync(process.execPath, [script], { encoding: "utf8" });
    assert.notEqual(result.status, 0);
    assert.match(result.stderr, /changed file-type version/);
  } finally {
    fs.rmSync(scratch, { recursive: true, force: true });
  }
});
test("serverless asset MIME detection handles malformed ASF without hanging", () => {
  const file = path.join(
    root,
    "node_modules/@twilio-labs/serverless-api/dist/utils/content-type.js",
  );
  const code = `const {getContentType}=require(${JSON.stringify(file)});getContentType(Buffer.from('3026b2758e66cf11a6d9'+'00'.repeat(45),'hex')).then(x=>{if(x!=='application/vnd.ms-asf')process.exit(2)});`;
  const result = spawnSync(process.execPath, ["-e", code], {
    timeout: 2000,
    encoding: "utf8",
  });
  assert.ifError(result.error);
  assert.equal(result.status, 0, result.stderr);
});
test("actual serverless packaging preserves the protected IVR route and MIME types", async () => {
  const {
    getListOfFunctionsAndAssets,
  } = require("@twilio-labs/serverless-api/dist/utils/fs");
  const {
    getContentType,
  } = require("@twilio-labs/serverless-api/dist/utils/content-type");
  const files = await getListOfFunctionsAndAssets(root);
  assert(
    files.functions.some(
      (file) => file.path === "/ivr" && file.access === "protected",
    ),
  );
  assert.equal(
    await getContentType(
      Buffer.from(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489",
        "hex",
      ),
    ),
    "image/png",
  );
  assert.equal(
    await getContentType(Buffer.from("fixture"), "prompt.mp3"),
    "audio/mpeg",
  );
  assert.equal(
    await getContentType(Buffer.from("fixture"), "ivr.js"),
    "application/javascript",
  );
  assert.equal(typeof require("title")("offline ivr fixture"), "string");
});
