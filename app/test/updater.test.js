// Run: node --test app/test/
const test = require("node:test");
const assert = require("node:assert");
const crypto = require("node:crypto");
const { EventEmitter } = require("node:events");
const fs = require("node:fs");
const https = require("node:https");
const os = require("node:os");
const path = require("node:path");
const { PassThrough } = require("node:stream");
const { isNewer, assetName, updateMethod, macBundle, download, prepare } = require("../updater");

test("versions compare numerically, and a release beats its pre-releases", () => {
  assert.ok(isNewer("0.3.10", "0.3.9"));
  assert.ok(isNewer("v1.0.0", "0.9.9"));
  assert.ok(!isNewer("0.3.1", "0.3.1"));
  assert.ok(!isNewer("0.3.0", "0.3.1"));
  assert.ok(isNewer("1.0.0", "1.0.0-beta.1"));
  assert.ok(!isNewer("1.0.0-beta.1", "1.0.0"));
  assert.ok(!isNewer("garbage", "0.1.0"));
});

test("asset names match what electron-builder publishes", () => {
  assert.strictEqual(assetName("darwin", "arm64", "mac-zip"), "Frame-Control-mac-arm64.zip");
  assert.strictEqual(assetName("win32", "x64", "nsis"), "Frame-Control-Setup-x64.exe");
  assert.strictEqual(assetName("linux", "x64", "appimage"), "Frame-Control-linux-x86_64.AppImage");
  assert.strictEqual(assetName("linux", "arm64", "appimage"), "Frame-Control-linux-arm64.AppImage");
});

const base = { isPackaged: true, env: {}, exists: () => false, writable: () => true };

test("macOS updates in place only from a writable, non-translocated location", () => {
  const exe = "/Applications/Frame Control.app/Contents/MacOS/Frame Control";
  assert.strictEqual(macBundle(exe), "/Applications/Frame Control.app");
  assert.deepStrictEqual(updateMethod({ ...base, platform: "darwin", execPath: exe }),
                         { method: "mac-zip", bundle: "/Applications/Frame Control.app" });
  const dmg = "/Volumes/Frame Control 0.3.1/Frame Control.app/Contents/MacOS/Frame Control";
  assert.strictEqual(updateMethod({ ...base, platform: "darwin", execPath: dmg }).method, "manual");
  const trans = "/private/var/folders/x/AppTranslocation/ABC/d/Frame Control.app/Contents/MacOS/Frame Control";
  assert.strictEqual(updateMethod({ ...base, platform: "darwin", execPath: trans }).method, "manual");
  assert.strictEqual(updateMethod({ ...base, platform: "darwin", execPath: exe, writable: () => false }).method, "manual");
});

test("Windows needs the installer's copy; Linux needs an AppImage", () => {
  const exe = "C:\\Users\\a\\AppData\\Local\\Programs\\Frame Control\\Frame Control.exe";
  assert.strictEqual(updateMethod({ ...base, platform: "win32", execPath: exe, exists: () => true }).method, "nsis");
  assert.strictEqual(updateMethod({ ...base, platform: "win32", execPath: exe }).method, "manual");
  assert.strictEqual(updateMethod({ ...base, platform: "linux", execPath: "/opt/x", env: { APPIMAGE: "/home/a/F.AppImage" } }).method,
                     "appimage");
  assert.strictEqual(updateMethod({ ...base, platform: "linux", execPath: "/opt/Frame Control/frame-control" }).method, "manual");
  assert.strictEqual(updateMethod({ ...base, isPackaged: false, platform: "darwin", execPath: "x" }).method, "manual");
});

test("update.json assets always download from this repository's release", () => {
  const r = require("../updater").fromManifest({ version: "0.4.0", notes: "n", assets: [
    { name: "Frame-Control-mac-arm64.zip", url: "https://evil.example/x.zip", digest: "sha256:" + "a".repeat(64) }] });
  assert.strictEqual(r.assets[0].url, "https://github.com/saphid/frame-control/releases/download/v0.4.0/Frame-Control-mac-arm64.zip");
  assert.throws(() => require("../updater").fromManifest({ version: "nope", assets: [] }));
});

test("the release page is this repository's tag page, never a draft's untagged-... link", () => {
  const { fromManifest, fromApi } = require("../updater");
  const tagPage = "https://github.com/saphid/frame-control/releases/tag/v0.4.0";
  const page = (p) => fromManifest({ version: "0.4.0", assets: [], page: p }).page;
  assert.strictEqual(page(tagPage), tagPage);
  // 0.4.0's real update.json: the draft's address, a 404 once published.
  assert.strictEqual(page("https://github.com/saphid/frame-control/releases/tag/untagged-c6ddfed7f75d67db2e99"), tagPage);
  assert.strictEqual(page(undefined), tagPage);
  assert.strictEqual(page("https://evil.example/releases/tag/v0.4.0"), tagPage);
  assert.strictEqual(page("https://github.com/saphid/frame-control/releases/tag/v0.4.0?x=1"), tagPage);
  // Paths that normalize to another repository.
  assert.strictEqual(page("https://github.com/saphid/frame-control/releases/tag/..\\..\\..\\..\\other-owner\\other-repo"),
                     tagPage);
  assert.strictEqual(page("https://github.com/saphid/frame-control/releases/tag/%2e%2e/%2e%2e/%2e%2e/%2e%2e/other-owner/other-repo"),
                     tagPage);
  assert.strictEqual(page("https://github.com/saphid/frame-control/releases/tag/v0.3.9"), tagPage);  // only its own tag
  assert.strictEqual(fromApi({ tag_name: "../../x", assets: [] }).page, "https://github.com/saphid/frame-control/releases");
  assert.strictEqual(fromApi({ tag_name: "v0.4.0", assets: [],
                               html_url: "https://github.com/saphid/frame-control/releases/tag/untagged-x" }).page, tagPage);
});

test("prepare refuses a release that isn't newer (no downgrades)", async () => {
  const release = { version: "0.3.1", assets: [] };
  await assert.rejects(prepare(release, { method: "appimage", appImage: "/nonexistent/x" }, null, "0.4.0"), /isn't newer/);
  await assert.rejects(prepare(release, { method: "appimage", appImage: "/nonexistent/x" }, null, "0.3.1"), /isn't newer/);
});

function stubHttpsGet(handler) {
  const original = https.get;
  https.get = handler;
  return () => { https.get = original; };
}

function sha256Digest(buf) {
  return "sha256:" + crypto.createHash("sha256").update(buf).digest("hex");
}

test("download removes partial file and closes output stream when response aborts mid-stream", async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "frame-updater-test-"));
  const dest = path.join(dir, "update.bin");
  const origCreateWriteStream = fs.createWriteStream;
  let capturedOut = null;
  fs.createWriteStream = (...args) => {
    capturedOut = origCreateWriteStream.apply(fs, args);
    return capturedOut;
  };
  const restoreHttps = stubHttpsGet((_url, _opts, cb) => {
    const req = new EventEmitter();
    req.destroy = () => {};
    queueMicrotask(() => {
      const res = new PassThrough();
      res.statusCode = 200;
      res.headers = { "content-length": "100" };
      cb(res);
      res.write(Buffer.from("partial-payload"));
      setImmediate(() => res.destroy(new Error("aborted")));
    });
    return req;
  });
  try {
    const asset = { name: "update.bin", url: "https://example.invalid/update.bin", size: 100, digest: sha256Digest("expected") };
    await assert.rejects(download(asset, dest), /aborted/);
    assert.ok(capturedOut, "expected output write stream to be created");
    assert.strictEqual(capturedOut.closed, true, "output stream should be closed before download rejects");
    assert.strictEqual(fs.existsSync(dest), false, "partial file should be removed after mid-stream failure");
  } finally {
    fs.createWriteStream = origCreateWriteStream;
    restoreHttps();
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("download preserves pre-existing destination file on EEXIST and destroys response stream", async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "frame-updater-test-"));
  const dest = path.join(dir, "existing.bin");
  fs.writeFileSync(dest, "keep-me");
  let capturedRes = null;
  const restoreHttps = stubHttpsGet((_url, _opts, cb) => {
    const req = new EventEmitter();
    req.destroy = () => {};
    queueMicrotask(() => {
      const res = new PassThrough();
      res.statusCode = 200;
      res.headers = { "content-length": "10" };
      capturedRes = res;
      cb(res);
    });
    return req;
  });
  try {
    const asset = { name: "existing.bin", url: "https://example.invalid/existing.bin", size: 10, digest: sha256Digest("0123456789") };
    await assert.rejects(download(asset, dest), (err) => err && err.code === "EEXIST");
    assert.strictEqual(fs.readFileSync(dest, "utf8"), "keep-me", "pre-existing file must not be deleted on EEXIST");
    assert.ok(capturedRes && capturedRes.destroyed, "response stream should be destroyed when write open fails");
  } finally {
    restoreHttps();
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("download writes verified bytes and deletes file on SHA-256 mismatch", async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "frame-updater-test-"));
  const okDest = path.join(dir, "ok.bin");
  const badDest = path.join(dir, "bad.bin");
  const payload = Buffer.from("verified installer bytes");
  const restoreHttps = stubHttpsGet((_url, _opts, cb) => {
    const req = new EventEmitter();
    req.destroy = () => {};
    queueMicrotask(() => {
      const res = new PassThrough();
      res.statusCode = 200;
      res.headers = { "content-length": String(payload.length) };
      cb(res);
      res.end(payload);
    });
    return req;
  });
  try {
    const seen = [];
    await download(
      { name: "ok.bin", url: "https://example.invalid/ok.bin", size: payload.length, digest: sha256Digest(payload) },
      okDest,
      (done, total) => seen.push([done, total]),
    );
    assert.deepStrictEqual(fs.readFileSync(okDest), payload);
    assert.deepStrictEqual(seen, [[payload.length, payload.length]]);

    await assert.rejects(
      download({ name: "bad.bin", url: "https://example.invalid/bad.bin", size: payload.length, digest: sha256Digest("other") }, badDest),
      /didn't match its SHA-256/,
    );
    assert.strictEqual(fs.existsSync(badDest), false, "mismatched download must be deleted");
  } finally {
    restoreHttps();
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("prepare cleans up Windows NSIS staging directory when download fails", async () => {
  const sandboxTmp = fs.mkdtempSync(path.join(os.tmpdir(), "frame-updater-nsis-"));
  const origTmpdir = os.tmpdir;
  os.tmpdir = () => sandboxTmp;
  const restoreHttps = stubHttpsGet((_url, _opts, cb) => {
    const req = new EventEmitter();
    req.destroy = () => {};
    queueMicrotask(() => {
      const res = new PassThrough();
      res.statusCode = 200;
      res.headers = { "content-length": "50" };
      cb(res);
      res.write(Buffer.from("partial-exe"));
      setImmediate(() => res.destroy(new Error("connection reset")));
    });
    return req;
  });
  try {
    const name = assetName("win32", process.arch, "nsis");
    const release = {
      version: "0.9.0",
      assets: [{ name, url: `https://example.invalid/${name}`, size: 50, digest: sha256Digest("full-exe") }],
    };
    await assert.rejects(prepare(release, { method: "nsis" }, null, "0.4.0"), /connection reset/);
    assert.deepStrictEqual(fs.readdirSync(sandboxTmp), [], "failed NSIS download must remove its staging directory");
  } finally {
    os.tmpdir = origTmpdir;
    restoreHttps();
    fs.rmSync(sandboxTmp, { recursive: true, force: true });
  }
});

test("download rejects malformed redirect Location headers cleanly without creating dest", async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "frame-updater-test-"));
  const dest = path.join(dir, "redirect.bin");
  const restoreHttps = stubHttpsGet((_url, _opts, cb) => {
    const req = new EventEmitter();
    req.destroy = () => {};
    queueMicrotask(() => {
      const res = new PassThrough();
      res.statusCode = 302;
      res.headers = { location: "http://[::1" };
      cb(res);
    });
    return req;
  });
  try {
    const asset = { name: "redirect.bin", url: "https://example.invalid/redirect.bin", size: 10, digest: sha256Digest("x") };
    await assert.rejects(download(asset, dest), TypeError);
    assert.strictEqual(fs.existsSync(dest), false);
  } finally {
    restoreHttps();
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("download rejects and removes partial file when response ends with complete=false", async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "frame-updater-test-"));
  const dest = path.join(dir, "truncated.bin");
  const payload = Buffer.from("short");
  const restoreHttps = stubHttpsGet((_url, _opts, cb) => {
    const req = new EventEmitter();
    req.destroy = () => {};
    queueMicrotask(() => {
      const res = new PassThrough();
      res.statusCode = 200;
      res.headers = { "content-length": String(payload.length) };
      res.complete = false;
      cb(res);
      res.end(payload);
    });
    return req;
  });
  try {
    const asset = { name: "truncated.bin", url: "https://example.invalid/truncated.bin", size: payload.length, digest: sha256Digest(payload) };
    await assert.rejects(download(asset, dest), /cut short/);
    assert.strictEqual(fs.existsSync(dest), false, "truncated download must be removed");
  } finally {
    restoreHttps();
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("updater hardening: malformed manifests/API, UTF-8 chunk boundaries, size cap, uppercase SHA-256, and cleanup errors", async () => {
  const { fromManifest, fromApi, latestRelease } = require("../updater");

  // 1. macBundle guards non-string execPath
  assert.strictEqual(macBundle(null), null);
  assert.strictEqual(macBundle(123), null);

  // 2. fromManifest trims padded version and rejects non-object manifest or invalid asset entries
  const padded = fromManifest({
    version: " v0.4.65 ",
    assets: [{ name: "Frame-Control-mac-arm64.zip", size: 10, digest: "sha256:" + "a".repeat(64) }],
  });
  assert.strictEqual(padded.version, "0.4.65");
  assert.strictEqual(
    padded.assets[0].url,
    "https://github.com/saphid/frame-control/releases/download/v0.4.65/Frame-Control-mac-arm64.zip",
  );
  assert.throws(() => fromManifest(null), /malformed/);
  assert.throws(() => fromManifest({ version: "0.4.65", assets: [null] }), /malformed/);
  assert.throws(() => fromManifest({ version: "0.4.65", assets: [{ name: "" }] }), /malformed/);

  // 3. fromApi rejects non-object payload and tolerates non-array / null assets
  assert.throws(() => fromApi(null), /malformed/);
  const apiRes = fromApi({ tag_name: " v0.4.65 ", assets: [null, { name: "ok.zip", browser_download_url: "https://example.invalid/ok.zip" }] });
  assert.strictEqual(apiRes.version, "0.4.65");
  assert.strictEqual(apiRes.assets.length, 1);

  // 4. latestRelease decodes multi-byte UTF-8 split across chunks without corruption
  const notes = "Release notes: 🚀 café 日本語";
  const jsonBuf = Buffer.from(JSON.stringify({ version: "0.4.65", notes, assets: [] }), "utf8");
  const emojiIdx = jsonBuf.indexOf(Buffer.from("🚀", "utf8"));
  const restoreUtf8 = stubHttpsGet((_url, _opts, cb) => {
    const req = new EventEmitter();
    req.destroy = () => {};
    queueMicrotask(() => {
      const res = new PassThrough();
      res.statusCode = 200;
      res.headers = {};
      cb(res);
      res.write(jsonBuf.subarray(0, emojiIdx + 2));
      res.end(jsonBuf.subarray(emojiIdx + 2));
    });
    return req;
  });
  try {
    const rel = await latestRelease();
    assert.strictEqual(rel.notes, notes);
  } finally {
    restoreUtf8();
  }

  // 5. latestRelease rejects oversized JSON responses
  const restoreBig = stubHttpsGet((_url, _opts, cb) => {
    const req = new EventEmitter();
    req.destroy = () => {};
    queueMicrotask(() => {
      const res = new PassThrough();
      res.statusCode = 200;
      res.headers = {};
      cb(res);
      res.write(Buffer.alloc(3 * 1024 * 1024, 0x20));
      res.end();
    });
    return req;
  });
  try {
    await assert.rejects(latestRelease(), /too large/i);
  } finally {
    restoreBig();
  }

  // 6. download accepts uppercase SHA-256 hex and does not mask primary error if cleanup rmSync throws
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "frame-updater-hardening-"));
  const okDest = path.join(dir, "upper.bin");
  const failDest = path.join(dir, "fail.bin");
  const payload = Buffer.from("hello-updater");
  const upperDigest = sha256Digest(payload).replace(/^sha256:/, (p) => p)
    .replace(/([0-9a-f]{64})$/, (h) => h.toUpperCase());
  const restoreDl = stubHttpsGet((_url, _opts, cb) => {
    const req = new EventEmitter();
    req.destroy = () => {};
    queueMicrotask(() => {
      const res = new PassThrough();
      res.statusCode = 200;
      res.headers = { "content-length": "-5" };
      cb(res);
      res.end(payload);
    });
    return req;
  });
  try {
    const progress = [];
    await download(
      { name: "upper.bin", url: "https://example.invalid/upper.bin", size: payload.length, digest: upperDigest },
      okDest,
      (done, total) => progress.push([done, total]),
    );
    assert.deepStrictEqual(fs.readFileSync(okDest), payload);
    assert.deepStrictEqual(progress, [[payload.length, payload.length]]);

    const origRmSync = fs.rmSync;
    fs.rmSync = () => { throw new Error("EBUSY cleanup failure"); };
    try {
      await assert.rejects(
        download({ name: "fail.bin", url: "https://example.invalid/fail.bin", size: payload.length, digest: sha256Digest("mismatch") }, failDest),
        /didn't match its SHA-256/,
      );
    } finally {
      fs.rmSync = origRmSync;
    }
  } finally {
    restoreDl();
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
