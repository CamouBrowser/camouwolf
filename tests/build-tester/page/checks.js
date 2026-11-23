// Page side of the build-tester. Plain script, no bundler: `selfChecks()` are
// the checks a page can decide alone (automation tells, tampering, engine
// signals), `collect()` is what the page reports for the driver to compare with
// what the launcher claimed, and `probe()` is the part of that which needs the
// claimed values to know what to ask for.
const buildTester = (() => {
  const ok = (passed, detail) => ({ passed: Boolean(passed), detail: detail === undefined ? "" : String(detail) });
  const attempt = (fn) => { try { return fn(); } catch (error) { return ok(false, `threw ${error}`); } };

  // ------------------------------------------------------------------ helpers
  function nativeSource(fn) {
    return Function.prototype.toString.call(fn);
  }
  function isNative(fn, name) {
    const source = nativeSource(fn).replace(/\s+/g, " ");
    return source === `function ${name}() { [native code] }`;
  }
  function getter(proto, prop) {
    const descriptor = Object.getOwnPropertyDescriptor(proto, prop);
    return descriptor && descriptor.get;
  }
  function webglInfo(type, canvas) {
    const gl = (canvas || document.createElement("canvas")).getContext(type);
    if (!gl) return null;
    const ext = gl.getExtension("WEBGL_debug_renderer_info");
    return {
      vendor: ext ? gl.getParameter(ext.UNMASKED_VENDOR_WEBGL) : null,
      renderer: ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : null,
      plainVendor: gl.getParameter(gl.VENDOR),
      plainRenderer: gl.getParameter(gl.RENDERER),
      version: gl.getParameter(gl.VERSION),
    };
  }
  function fromWorker(timeout = 8000) {
    return new Promise((resolve) => {
      try {
        const worker = new Worker("/worker.js");
        const timer = setTimeout(() => { worker.terminate(); resolve({ error: "worker timeout" }); }, timeout);
        worker.onmessage = (event) => { clearTimeout(timer); worker.terminate(); resolve(event.data); };
        worker.onerror = (event) => { clearTimeout(timer); resolve({ error: `worker error: ${event.message}` }); };
        worker.postMessage("go");
      } catch (error) { resolve({ error: String(error) }); }
    });
  }
  function fromIframe() {
    return new Promise((resolve) => {
      const frame = document.createElement("iframe");
      frame.style.display = "none";
      frame.srcdoc = "<!doctype html><title>f</title>";
      frame.onload = () => {
        try {
          const w = frame.contentWindow;
          const canvas = w.document.createElement("canvas");
          const out = {
            userAgent: w.navigator.userAgent,
            platform: w.navigator.platform,
            hardwareConcurrency: w.navigator.hardwareConcurrency,
            deviceMemory: w.navigator.deviceMemory,
            languages: Array.from(w.navigator.languages),
            webdriver: w.navigator.webdriver,
            screenWidth: w.screen.width,
            screenHeight: w.screen.height,
            timeZone: new w.Intl.DateTimeFormat().resolvedOptions().timeZone,
            webgl: webglInfo("webgl", canvas),
          };
          frame.remove();
          resolve(out);
        } catch (error) { frame.remove(); resolve({ error: String(error) }); }
      };
      document.body.appendChild(frame);
    });
  }
  function canvasHash() {
    const canvas = document.createElement("canvas");
    canvas.width = 240; canvas.height = 60;
    const context = canvas.getContext("2d");
    context.textBaseline = "top";
    context.font = "16px Arial";
    context.fillStyle = "#f60";
    context.fillRect(100, 1, 62, 20);
    context.fillStyle = "#069";
    context.fillText("Camouwolf <canvas> 1.0 \u{1F60A}", 2, 15);
    context.fillStyle = "rgba(102, 204, 0, 0.7)";
    context.fillText("Camouwolf <canvas> 1.0", 4, 17);
    context.globalCompositeOperation = "multiply";
    context.beginPath(); context.arc(50, 30, 25, 0, Math.PI * 2); context.fillStyle = "#f0f"; context.fill();
    const data = canvas.toDataURL();
    let hash = 0;
    for (let i = 0; i < data.length; i++) hash = (Math.imul(hash, 31) + data.charCodeAt(i)) | 0;
    return `${data.length}:${hash >>> 0}`;
  }
  function voices(timeout = 2500) {
    return new Promise((resolve) => {
      if (!window.speechSynthesis) return resolve(null);
      const read = () => speechSynthesis.getVoices().map(
        (v) => ({ name: v.name, lang: v.lang, voiceURI: v.voiceURI, localService: v.localService, default: v.default }));
      const now = read();
      if (now.length) return resolve(now);
      const timer = setTimeout(() => resolve(read()), timeout);
      speechSynthesis.addEventListener("voiceschanged", () => { clearTimeout(timer); resolve(read()); }, { once: true });
    });
  }

  // ------------------------------------------------------------- self checks
  async function selfChecks() {
    const checks = {};

    // ---- Automation Detection: what a driver leaves on the page -----------
    const automation = checks["Automation Detection"] = {};
    automation.webdriverFalse = attempt(() => ok(navigator.webdriver === false, `navigator.webdriver = ${navigator.webdriver}`));
    automation.webdriverOnPrototype = attempt(() => ok(
      "webdriver" in navigator && !Object.prototype.hasOwnProperty.call(navigator, "webdriver")
      && Boolean(getter(Navigator.prototype, "webdriver")),
      "stock Chrome defines webdriver as a getter on Navigator.prototype"));
    automation.noCdcProperties = attempt(() => {
      const hits = [];
      for (const target of [window, document]) {
        for (const name of Object.getOwnPropertyNames(target)) {
          if (/^\$?cdc_|^\$chrome_asyncScriptInfo|^__webdriver|^__driver|^__selenium|^__fxdriver|^_Selenium|^callSelenium|^_phantom|^callPhantom|^domAutomation/.test(name)) hits.push(name);
        }
      }
      return ok(hits.length === 0, hits.join(", "));
    });
    automation.noDriverGlobals = attempt(() => {
      const hits = Object.getOwnPropertyNames(window).filter(
        (name) => /^__playwright|^__pw|^__puppeteer|^puppeteer_|^__nightmare|^__lastWatirAlert/.test(name));
      return ok(hits.length === 0, hits.join(", "));
    });
    automation.noDocumentDriverAttributes = attempt(() => {
      const hits = ["webdriver", "selenium", "driver"].filter((name) => document.documentElement.getAttribute(name) !== null);
      return ok(hits.length === 0, hits.join(", "));
    });
    // With CDP's Runtime domain enabled, a console call used to serialize its
    // arguments for the client, which read an Error's `stack`. Stock Chromium
    // 151 no longer does under Playwright; this stays as the regression check
    // for a driver or build that brings the read back.
    automation.runtimeEnableNotObservable = attempt(() => {
      let read = 0;
      const error = new Error("probe");
      Object.defineProperty(error, "stack", { configurable: true, get() { read += 1; return ""; } });
      console.debug(error);
      return ok(read === 0, `console.debug read Error.stack ${read} time(s)`);
    });
    automation.noHeadlessInUserAgent = attempt(() => ok(!/headless/i.test(navigator.userAgent + navigator.appVersion), navigator.userAgent));
    automation.noHeadlessBrand = attempt(() => {
      const brands = navigator.userAgentData ? navigator.userAgentData.brands.map((b) => b.brand) : [];
      return ok(!brands.some((b) => /headless/i.test(b)), brands.join(", "));
    });
    automation.pluginsPresent = attempt(() => ok(navigator.plugins.length === 5 && navigator.mimeTypes.length === 2 && navigator.pdfViewerEnabled === true,
      `plugins ${navigator.plugins.length}, mimeTypes ${navigator.mimeTypes.length}, pdfViewerEnabled ${navigator.pdfViewerEnabled}`));
    automation.chromeObject = attempt(() => ok(
      typeof window.chrome === "object" && window.chrome !== null
      && typeof window.chrome.loadTimes === "function" && typeof window.chrome.csi === "function",
      `window.chrome is ${typeof window.chrome}`));
    try {
      const status = await navigator.permissions.query({ name: "notifications" });
      // Old headless answered "denied" to Notification.permission and "prompt"
      // to the Permissions API; a real profile answers the two alike.
      const expected = { default: "prompt", granted: "granted", denied: "denied" }[Notification.permission];
      automation.notificationPermissionCoherent = ok(status.state === expected, `Notification.permission ${Notification.permission}, permissions.query ${status.state}`);
    } catch (error) { automation.notificationPermissionCoherent = ok(false, `threw ${error}`); }
    automation.languagesPresent = attempt(() => ok(navigator.languages.length > 0 && navigator.language === navigator.languages[0], JSON.stringify(navigator.languages)));

    // ---- Lie Detection: spoofed values must come from native code ---------
    const lies = checks["Lie Detection"] = {};
    const getters = [
      [Navigator.prototype, "userAgent"], [Navigator.prototype, "appVersion"], [Navigator.prototype, "platform"],
      [Navigator.prototype, "vendor"], [Navigator.prototype, "hardwareConcurrency"], [Navigator.prototype, "deviceMemory"],
      [Navigator.prototype, "languages"], [Navigator.prototype, "language"], [Navigator.prototype, "webdriver"],
      [Navigator.prototype, "maxTouchPoints"], [Navigator.prototype, "userAgentData"], [Navigator.prototype, "plugins"],
      [Screen.prototype, "width"], [Screen.prototype, "height"], [Screen.prototype, "availWidth"],
      [Screen.prototype, "availHeight"], [Screen.prototype, "availLeft"], [Screen.prototype, "availTop"],
      [Screen.prototype, "colorDepth"], [Screen.prototype, "pixelDepth"],
    ];
    lies.gettersAreNative = attempt(() => {
      const bad = [];
      for (const [proto, prop] of getters) {
        const get = getter(proto, prop);
        if (!get) { bad.push(`${prop}: no getter on the prototype`); continue; }
        if (!isNative(get, `get ${prop}`)) bad.push(`${prop}: ${nativeSource(get).slice(0, 60)}`);
        if (get.name !== `get ${prop}`) bad.push(`${prop}: name ${get.name}`);
        if ("prototype" in get) bad.push(`${prop}: getter has a prototype`);
      }
      return ok(bad.length === 0, bad.join("; "));
    });
    lies.gettersRejectForeignReceiver = attempt(() => {
      const bad = [];
      for (const [proto, prop] of getters) {
        const get = getter(proto, prop);
        if (!get) continue;
        try { get.call({}); bad.push(`${prop}: returned for a plain object`); }
        catch (error) { if (!(error instanceof TypeError) || !/Illegal invocation/.test(error.message)) bad.push(`${prop}: ${error}`); }
      }
      return ok(bad.length === 0, bad.join("; "));
    });
    lies.noOwnPropertiesOnInstances = attempt(() => {
      const own = [...Object.getOwnPropertyNames(navigator).map((n) => `navigator.${n}`),
                   ...Object.getOwnPropertyNames(screen).map((n) => `screen.${n}`)];
      return ok(own.length === 0, own.join(", "));
    });
    const methods = [
      [WebGLRenderingContext.prototype, "getParameter"], [WebGLRenderingContext.prototype, "getExtension"],
      [WebGLRenderingContext.prototype, "getSupportedExtensions"], [WebGLRenderingContext.prototype, "getShaderPrecisionFormat"],
      [WebGL2RenderingContext.prototype, "getParameter"],
      [HTMLCanvasElement.prototype, "toDataURL"], [HTMLCanvasElement.prototype, "toBlob"], [HTMLCanvasElement.prototype, "getContext"],
      [CanvasRenderingContext2D.prototype, "getImageData"], [CanvasRenderingContext2D.prototype, "measureText"],
      [Intl.DateTimeFormat.prototype, "resolvedOptions"], [Date.prototype, "getTimezoneOffset"], [Date.prototype, "toString"],
      [Function.prototype, "toString"], [Element.prototype, "getBoundingClientRect"],
      [AudioBuffer.prototype, "getChannelData"], [Permissions.prototype, "query"],
    ];
    lies.methodsAreNative = attempt(() => {
      const bad = [];
      for (const [proto, name] of methods) {
        const fn = proto[name];
        if (typeof fn !== "function") { bad.push(`${name}: missing`); continue; }
        if (!isNative(fn, name)) bad.push(`${name}: ${nativeSource(fn).slice(0, 60)}`);
        if (fn.name !== name) bad.push(`${name}: name ${fn.name}`);
        if (Object.prototype.hasOwnProperty.call(fn, "prototype")) bad.push(`${name}: has a prototype`);
      }
      return ok(bad.length === 0, bad.join("; "));
    });
    lies.toStringNotProxied = attempt(() => {
      // A Proxy around a native function still prints as native, but throws a
      // different error than the function it wraps when misused.
      const bad = [];
      for (const fn of [Function.prototype.toString, getter(Navigator.prototype, "userAgent"), WebGLRenderingContext.prototype.getParameter]) {
        try { Object.create(fn).toString(); bad.push(`${fn.name}: no throw`); }
        catch (error) {
          if (!(error instanceof TypeError)) bad.push(`${fn.name}: ${error}`);
          else if (/Proxy|at Object\.apply|at Reflect/.test(error.stack)) bad.push(`${fn.name}: proxy frames in the stack`);
        }
      }
      return ok(bad.length === 0, bad.join("; "));
    });
    const frame = await fromIframe();
    lies.iframeAgrees = attempt(() => {
      if (frame.error) return ok(false, frame.error);
      const top = {
        userAgent: navigator.userAgent, platform: navigator.platform, hardwareConcurrency: navigator.hardwareConcurrency,
        deviceMemory: navigator.deviceMemory, languages: Array.from(navigator.languages), webdriver: navigator.webdriver,
        screenWidth: screen.width, screenHeight: screen.height,
        timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone, webgl: webglInfo("webgl"),
      };
      const bad = Object.keys(top).filter((key) => JSON.stringify(top[key]) !== JSON.stringify(frame[key]))
        .map((key) => `${key}: top ${JSON.stringify(top[key])}, iframe ${JSON.stringify(frame[key])}`);
      return ok(bad.length === 0, bad.join("; "));
    });

    // ---- JS Engine: a Chrome identity on a Blink/V8 engine ----------------
    const engine = checks["JS Engine"] = {};
    engine.v8ErrorStack = attempt(() => ok(/^Error: x\n\s+at /.test(new Error("x").stack), new Error("x").stack.split("\n").slice(0, 2).join(" | ")));
    engine.evalSourceLength = attempt(() => ok(eval.toString().length === 33, `eval.toString().length = ${eval.toString().length}`));
    engine.vendorConstants = attempt(() => ok(navigator.productSub === "20030107" && navigator.product === "Gecko" && navigator.appName === "Netscape",
      `productSub ${navigator.productSub}, product ${navigator.product}, appName ${navigator.appName}`));
    engine.vendorIsGoogle = attempt(() => ok(navigator.vendor === "Google Inc.", `navigator.vendor = ${JSON.stringify(navigator.vendor)}`));
    engine.appVersionFollowsUserAgent = attempt(() => ok(navigator.userAgent === `Mozilla/${navigator.appVersion}`, navigator.appVersion));
    engine.noForeignEngineGlobals = attempt(() => {
      const hits = ["InstallTrigger", "safari", "netscape", "mozInnerScreenX", "opr", "opera"].filter((name) => name in window);
      return ok(hits.length === 0, hits.join(", "));
    });
    engine.userAgentShape = attempt(() => ok(
      /^Mozilla\/5\.0 \([^)]+\) AppleWebKit\/537\.36 \(KHTML, like Gecko\) Chrome\/\d+\.0\.0\.0 Safari\/537\.36$/.test(navigator.userAgent),
      navigator.userAgent));
    engine.userAgentDataPresent = attempt(() => ok(Boolean(navigator.userAgentData), "navigator.userAgentData exists in a secure context"));

    // ---- Headless Detection: reported, not required ----------------------
    const headless = checks["Headless Detection"] = {};
    headless.windowHasChrome = attempt(() => ok(outerHeight > innerHeight && outerWidth >= innerWidth, `outer ${outerWidth}x${outerHeight}, inner ${innerWidth}x${innerHeight}`));
    headless.outerSizeNonZero = attempt(() => ok(outerWidth > 0 && outerHeight > 0, `outer ${outerWidth}x${outerHeight}`));
    headless.noSoftwareRenderer = attempt(() => {
      const info = webglInfo("webgl");
      return ok(Boolean(info) && !/swiftshader|llvmpipe|software/i.test(`${info.renderer}`), info ? info.renderer : "no WebGL");
    });
    headless.finePointer = attempt(() => ok(matchMedia("(any-pointer: fine)").matches && matchMedia("(any-hover: hover)").matches, "a desktop has a mouse"));
    headless.hasVoices = ok(((await voices()) || []).length > 0, "speechSynthesis.getVoices() is empty in a browser with no speech backend");
    headless.connectionRtt = attempt(() => ok(!navigator.connection || navigator.connection.rtt > 0, `rtt ${navigator.connection && navigator.connection.rtt}`));

    return checks;
  }

  // ------------------------------------------------------------- collection
  async function collect() {
    const out = {};
    out.navigator = {
      userAgent: navigator.userAgent, appVersion: navigator.appVersion, platform: navigator.platform,
      vendor: navigator.vendor, webdriver: navigator.webdriver, hardwareConcurrency: navigator.hardwareConcurrency,
      deviceMemory: navigator.deviceMemory, maxTouchPoints: navigator.maxTouchPoints, language: navigator.language,
      languages: Array.from(navigator.languages),
    };
    out.uad = null;
    if (navigator.userAgentData) {
      out.uad = {
        platform: navigator.userAgentData.platform, mobile: navigator.userAgentData.mobile,
        brands: navigator.userAgentData.brands,
        high: await navigator.userAgentData.getHighEntropyValues(
          ["architecture", "bitness", "model", "platformVersion", "uaFullVersion", "wow64", "fullVersionList"]),
      };
    }
    out.screen = {
      width: screen.width, height: screen.height, availWidth: screen.availWidth, availHeight: screen.availHeight,
      availLeft: screen.availLeft, availTop: screen.availTop, colorDepth: screen.colorDepth, pixelDepth: screen.pixelDepth,
      isExtended: screen.isExtended,
    };
    out.window = { outerWidth, outerHeight, innerWidth, innerHeight, screenX, screenY, devicePixelRatio };
    out.media = {
      deviceWidth: matchMedia(`(device-width: ${screen.width}px)`).matches,
      deviceHeight: matchMedia(`(device-height: ${screen.height}px)`).matches,
      coarsePointer: matchMedia("(any-pointer: coarse)").matches,
      touchEvents: "ontouchstart" in window,
      colorDepth: matchMedia(`(color: ${screen.colorDepth / 3})`).matches,
    };
    const options = Intl.DateTimeFormat().resolvedOptions();
    out.intl = {
      locale: options.locale, timeZone: options.timeZone, numberLocale: Intl.NumberFormat().resolvedOptions().locale,
      collatorLocale: Intl.Collator().resolvedOptions().locale,
      offsetNow: new Date().getTimezoneOffset(), now: Date.now(),
      offsetJanuary: new Date(2026, 0, 15, 12).getTimezoneOffset(), offsetJuly: new Date(2026, 6, 15, 12).getTimezoneOffset(),
      dateString: new Date(Date.UTC(2026, 0, 15, 12)).toString(),
      localeDate: new Date(Date.UTC(2026, 0, 15, 12)).toLocaleDateString(),
    };
    out.webgl = { webgl: webglInfo("webgl"), webgl2: webglInfo("webgl2") };
    out.worker = await fromWorker();
    out.canvas = [canvasHash(), canvasHash()];
    out.voices = await voices();
    try {
      const { quota, usage } = await navigator.storage.estimate();
      out.storage = { quota, usage };
    } catch (error) { out.storage = { error: String(error) }; }
    try {
      const devices = await navigator.mediaDevices.enumerateDevices();
      out.mediaDevices = devices.map((d) => d.kind);
    } catch (error) { out.mediaDevices = { error: String(error) }; }
    try {
      out.keyboard = navigator.keyboard ? Array.from((await navigator.keyboard.getLayoutMap()).entries()).map(([k, v]) => `${k}=${v}`) : null;
    } catch (error) { out.keyboard = { error: String(error) }; }
    try {
      out.fetchHeaders = await (await fetch("/headers?from=page")).json();
    } catch (error) { out.fetchHeaders = { error: String(error) }; }
    try {
      const adapter = navigator.gpu ? await navigator.gpu.requestAdapter() : null;
      out.webgpu = adapter ? { vendor: adapter.info.vendor, architecture: adapter.info.architecture, device: adapter.info.device } : null;
    } catch (error) { out.webgpu = { error: String(error) }; }
    out.codecs = await codecs();
    try {
      const battery = await navigator.getBattery();
      // JSON has no Infinity; the times are compared as strings.
      out.battery = { charging: battery.charging, level: battery.level,
                      chargingTime: String(battery.chargingTime), dischargingTime: String(battery.dischargingTime) };
    } catch (error) { out.battery = { error: String(error) }; }
    return out;
  }

  // Every surface that names a platform codec, by the names the launcher
  // claims them under (media::GetCodecName).
  async function codecs() {
    const VIDEO = { h264: "avc1.42E01E", vp8: "vp8", vp9: "vp09.00.10.08", av1: "av01.0.04M.08", hevc: "hvc1.1.6.L93.B0" };
    const attempt = async (fn) => { try { return await fn(); } catch (error) { return `threw ${error}`; } };
    const out = { hardwareDecode: {}, powerEfficient: {}, hardwareEncode: {} };
    for (const [name, codec] of Object.entries(VIDEO)) {
      out.hardwareDecode[name] = await attempt(async () => (await VideoDecoder.isConfigSupported(
        { codec, codedWidth: 1920, codedHeight: 1080, hardwareAcceleration: "prefer-hardware" })).supported);
      out.powerEfficient[name] = await attempt(async () => (await navigator.mediaCapabilities.decodingInfo({
        type: "file", video: { contentType: `video/mp4; codecs="${codec}"`, width: 1920, height: 1080, bitrate: 2e6, framerate: 30 } })).powerEfficient);
    }
    for (const [name, codec] of [["h264", "avc1.640028"], ["hevc", "hvc1.1.6.L93.B0"]]) {
      out.hardwareEncode[name] = await attempt(async () => (await VideoEncoder.isConfigSupported(
        { codec, width: 1280, height: 720, bitrate: 2e6, hardwareAcceleration: "prefer-hardware" })).supported);
    }
    const hevc = `video/mp4; codecs="${VIDEO.hevc}"`;
    out.hevcPlayback = { canPlayType: document.createElement("video").canPlayType(hevc), mse: MediaSource.isTypeSupported(hevc) };
    out.aacEncode = {
      audioEncoder: await attempt(async () => (await AudioEncoder.isConfigSupported(
        { codec: "mp4a.40.2", sampleRate: 48000, numberOfChannels: 2, bitrate: 128000 })).supported),
      mediaRecorder: MediaRecorder.isTypeSupported('audio/mp4; codecs="mp4a.40.2"'),
    };
    out.hevcRecord = MediaRecorder.isTypeSupported(`video/mp4; codecs="${VIDEO.hevc}"`);
    const formats = (cls) => cls.getCapabilities("video").codecs.map((c) => `${c.mimeType.replace(/^video\//, "")}|${c.sdpFmtpLine || ""}`);
    out.rtc = { send: formats(RTCRtpSender), receive: formats(RTCRtpReceiver) };
    return out;
  }

  // What needs the claimed values to know what to ask: WebGL parameters by
  // pname, font families by name, system colours by keyword.
  function probe(request) {
    const out = { webglParameters: {}, webgl2Parameters: {}, fonts: {}, systemColors: {} };
    for (const [type, key] of [["webgl", "webglParameters"], ["webgl2", "webgl2Parameters"]]) {
      const gl = document.createElement("canvas").getContext(type);
      for (const pname of request[key] || []) {
        try {
          const value = gl ? gl.getParameter(Number(pname)) : null;
          out[key][pname] = value && typeof value === "object" && "length" in value ? Array.from(value) : value;
        } catch (error) { out[key][pname] = `threw ${error}`; }
      }
    }
    // A family is installed when text set in it measures differently from the
    // generic it would otherwise fall back to, for at least one generic and
    // one text. The second text holds every printable ASCII character: a
    // symbol font such as MS Outlook has none of the first text's characters
    // and a shorter line than the fallback, so only its own glyphs show it.
    const span = document.createElement("span");
    span.style.cssText = "position:absolute;left:-9999px;top:0;font-size:72px;white-space:nowrap;font-variant:normal;font-style:normal;font-weight:400";
    document.body.appendChild(span);
    const ascii = String.fromCharCode(...Array.from({ length: 0x7f - 0x20 }, (_, i) => 0x20 + i));
    const texts = ["mmmmmmmmmmlli1WQ@#0O", ascii];
    const size = (family, text) => {
      span.textContent = text;
      span.style.fontFamily = family;
      return `${span.offsetWidth}x${span.offsetHeight}`;
    };
    const generics = ["monospace", "serif", "sans-serif"];
    const base = Object.fromEntries(generics.map((g) => [g, texts.map((t) => size(g, t))]));
    for (const family of request.fonts || []) {
      const quoted = `"${family.replace(/"/g, "")}"`;
      out.fonts[family] = generics.some((g) => texts.some((t, i) => size(`${quoted}, ${g}`, t) !== base[g][i]));
    }
    span.remove();
    const swatch = document.createElement("div");
    document.body.appendChild(swatch);
    for (const keyword of request.systemColors || []) {
      swatch.style.color = "";
      swatch.style.color = keyword;
      const match = /rgba?\((\d+), (\d+), (\d+)/.exec(getComputedStyle(swatch).color);
      out.systemColors[keyword] = match ? (Number(match[1]) << 16) | (Number(match[2]) << 8) | Number(match[3]) : null;
    }
    swatch.remove();
    return out;
  }

  return { selfChecks, collect, probe };
})();
