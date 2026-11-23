const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { parseInstallLink } = require("../install-link");
const checkResources = require("../build/check-resources");

test("parseInstallLink rejects credentials, ports, and control characters", () => {
  assert.deepEqual(
    parseInstallLink("frame-control://install?url=https://example.com/app.apk"),
    { kind: "url", target: "https://example.com/app.apk" }
  );
  for (const bad of [
    "frame-control://user:pass@install?url=https://example.com/app.apk",
    "frame-control://install:8080?url=https://example.com/app.apk",
    "frame-control://install?url=https://example.com/app.apk%0Aevil",
    "frame-control://install?url=https://example.com/app.apk%0Devil",
    "frame-control://install?url=https://example.com/app.apk%09tab",
    "frame-control://install?url=https://example.com/app.apk\n",
  ]) {
    assert.equal(parseInstallLink(bad), null, `expected rejection for ${JSON.stringify(bad)}`);
  }
});

test("check-resources treats directories at required file paths as missing", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "frame-res-test-"));
  try {
    const dirAtFile = path.join(root, checkResources.REQUIRED[0]);
    fs.mkdirSync(dirAtFile, { recursive: true });
    assert.ok(checkResources.missing(root).includes(checkResources.REQUIRED[0]));
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});
