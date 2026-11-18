/**
 * ONE canonical fingerprint dump, evaluated identically in every browser under
 * comparison.
 *
 * The whole point of this file is that it is a single function with no imports:
 * the same source is serialised into real Chrome on Windows and into Camouwolf on
 * Linux, so a difference in the output is a difference in the BROWSER and never
 * a difference in how the two were measured. Anything that reads differently on
 * the two runs is, by construction, a divergence a detector can also see.
 *
 * Every read is individually guarded. A surface that throws records the throw
 * rather than aborting the dump — a missing API is itself a signal worth
 * diffing, and one bad read must not cost the other two hundred.
 */

/** Fonts probed for presence. Windows defaults, Linux defaults, macOS defaults. */
export const PROBE_FONTS = [
	// Windows 10/11 defaults and Office-installed families
	"Arial", "Arial Black", "Arial Narrow", "Bahnschrift", "Calibri", "Cambria",
	"Cambria Math", "Candara", "Comic Sans MS", "Consolas", "Constantia",
	"Corbel", "Courier New", "Ebrima", "Franklin Gothic Medium", "Gabriola",
	"Gadugi", "Georgia", "HoloLens MDL2 Assets", "Impact", "Ink Free",
	"Javanese Text", "Leelawadee UI", "Lucida Console", "Lucida Sans Unicode",
	"Malgun Gothic", "Marlett", "Microsoft Himalaya", "Microsoft JhengHei",
	"Microsoft New Tai Lue", "Microsoft PhagsPa", "Microsoft Sans Serif",
	"Microsoft Tai Le", "Microsoft YaHei", "Microsoft Yi Baiti",
	"MingLiU-ExtB", "Mongolian Baiti", "MS Gothic", "MV Boli", "Myanmar Text",
	"Nirmala UI", "Palatino Linotype", "Segoe Fluent Icons", "Segoe MDL2 Assets",
	"Segoe Print", "Segoe Script", "Segoe UI", "Segoe UI Emoji",
	"Segoe UI Historic", "Segoe UI Symbol", "Segoe UI Variable",
	"SimSun", "Sitka", "Sylfaen", "Symbol", "Tahoma", "Times New Roman",
	"Trebuchet MS", "Verdana", "Webdings", "Wingdings", "Yu Gothic",
	// Office / Adobe additions that commonly ride along
	"Agency FB", "Bookman Old Style", "Bradley Hand ITC", "Century Gothic",
	"Garamond", "Lucida Handwriting", "Monotype Corsiva", "Papyrus",
	"Rockwell", "Script MT Bold", "Tw Cen MT",
	// Linux defaults — presence of ANY of these on a "Windows" box is a leak
	"DejaVu Sans", "DejaVu Serif", "DejaVu Sans Mono", "Liberation Sans",
	"Liberation Serif", "Liberation Mono", "Nimbus Roman", "Nimbus Sans",
	"Noto Sans", "Noto Serif", "Noto Color Emoji", "Noto Sans CJK JP",
	"Ubuntu", "Ubuntu Mono", "Cantarell", "FreeSans", "FreeSerif",
	"Bitstream Vera Sans", "URW Bookman", "C059", "P052", "Z003",
	// macOS defaults — presence on either box would be a fabrication
	"Helvetica", "Helvetica Neue", "Lucida Grande", "Menlo", "Monaco",
	"SF Pro Text", "Geneva", "Optima", "Chalkboard", "American Typewriter",
];

/** CSS system colors. Resolved through the native ColorProvider — OS-shaped. */
export const SYSTEM_COLORS = [
	"AccentColor", "AccentColorText", "ActiveText", "ButtonBorder",
	"ButtonFace", "ButtonText", "Canvas", "CanvasText", "Field", "FieldText",
	"GrayText", "Highlight", "HighlightText", "LinkText", "Mark", "MarkText",
	"SelectedItem", "SelectedItemText", "VisitedText",
	// Deprecated-but-still-resolved CSS2 system colors
	"ActiveBorder", "ActiveCaption", "AppWorkspace", "Background",
	"ButtonHighlight", "ButtonShadow", "CaptionText", "InactiveBorder",
	"InactiveCaption", "InactiveCaptionText", "InfoBackground", "InfoText",
	"Menu", "MenuText", "Scrollbar", "ThreeDDarkShadow", "ThreeDFace",
	"ThreeDHighlight", "ThreeDLightShadow", "ThreeDShadow", "Window",
	"WindowFrame", "WindowText",
];

/** CSS system font shorthands. Each resolves through LayoutThemeFontProvider. */
export const SYSTEM_FONTS = [
	"caption", "icon", "menu", "message-box", "small-caption", "status-bar",
];

/** Media features whose value is decided by the platform or the display. */
export const MEDIA_QUERIES = [
	"(pointer: fine)", "(pointer: coarse)", "(pointer: none)",
	"(any-pointer: fine)", "(any-pointer: coarse)",
	"(hover: hover)", "(hover: none)", "(any-hover: hover)",
	"(prefers-color-scheme: dark)", "(prefers-color-scheme: light)",
	"(prefers-reduced-motion: reduce)", "(prefers-reduced-transparency: reduce)",
	"(prefers-contrast: more)", "(prefers-contrast: no-preference)",
	"(forced-colors: active)", "(inverted-colors: inverted)",
	"(color-gamut: srgb)", "(color-gamut: p3)", "(color-gamut: rec2020)",
	"(dynamic-range: high)", "(video-dynamic-range: high)",
	"(display-mode: browser)", "(scripting: enabled)",
	"(update: fast)", "(update: slow)",
	"(monochrome: 0)", "(orientation: landscape)",
	"(-webkit-device-pixel-ratio: 1)", "(-webkit-device-pixel-ratio: 2)",
];

/** APIs whose mere PRESENCE is decided by platform or build flags. */
export const FEATURE_PROBES = [
	"window.showOpenFilePicker", "window.showSaveFilePicker",
	"window.showDirectoryPicker", "window.launchQueue",
	"window.DocumentPictureInPicture", "window.documentPictureInPicture",
	"window.EyeDropper", "window.Ink", "navigator.ink",
	"navigator.virtualKeyboard", "navigator.windowControlsOverlay",
	"navigator.keyboard", "navigator.usb", "navigator.serial", "navigator.hid",
	"navigator.bluetooth", "navigator.xr", "navigator.gpu",
	"navigator.getBattery", "navigator.connection", "navigator.setAppBadge",
	"navigator.managed", "navigator.deviceMemory", "navigator.scheduling",
	"navigator.userActivation", "navigator.wakeLock", "navigator.presentation",
	"navigator.mediaSession", "navigator.credentials", "navigator.storageBuckets",
	"window.chrome", "window.chrome.runtime", "window.chrome.loadTimes",
	"window.chrome.csi", "window.chrome.app", "window.external",
	"window.PressureObserver", "window.NavigationActivation",
	"window.CSSStyleSheet", "window.SharedWorker", "window.speechSynthesis",
	"window.Notification", "window.PaymentRequest", "window.Serial",
	"window.RTCRtpScriptTransform", "window.HTMLVideoElement.prototype.requestVideoFrameCallback",
	"window.OffscreenCanvas", "window.queryLocalFonts", "window.FileSystemHandle",
	"window.CaptureController", "window.Sanitizer", "window.AudioData",
	"window.VideoEncoder", "window.ImageDecoder", "window.WebTransport",
	"window.CookieStore", "window.IdleDetector", "window.ContactsManager",
	"window.BarcodeDetector", "window.FaceDetector",
];

/** Codec strings. Support is decided by the platform's media stack. */
export const CODECS = [
	'video/mp4; codecs="avc1.42E01E"',
	'video/mp4; codecs="avc1.640028"',
	'video/mp4; codecs="hev1.1.6.L93.B0"',
	'video/mp4; codecs="hvc1.1.6.L93.B0"',
	'video/mp4; codecs="av01.0.08M.08"',
	'video/mp4; codecs="vp09.00.10.08"',
	'video/mp4; codecs="dvh1.05.07"',
	'video/webm; codecs="vp8"',
	'video/webm; codecs="vp9"',
	'video/webm; codecs="av01.0.08M.08"',
	'video/ogg; codecs="theora"',
	'audio/mp4; codecs="mp4a.40.2"',
	'audio/mp4; codecs="ac-3"',
	'audio/mp4; codecs="ec-3"',
	'audio/mp4; codecs="mha1.2.01"',
	'audio/mpeg',
	'audio/webm; codecs="opus"',
	'audio/webm; codecs="vorbis"',
	'audio/ogg; codecs="flac"',
	'audio/wav; codecs="1"',
];

/** Math inputs. Chromium delegates these to the platform libm (UCRT on Windows). */
export const MATH_INPUTS = [
	["sin", 1e300], ["sin", 0.5], ["cos", 1e300], ["cos", 0.5],
	["tan", -1e308], ["tan", 0.5], ["asin", 0.5], ["acos", 0.5],
	["atan", 0.5], ["asinh", 1e308], ["acosh", 1e308], ["atanh", 0.5],
	["sinh", 1], ["cosh", 1], ["tanh", 1], ["exp", 1], ["expm1", 1],
	["log", 1e308], ["log1p", 10], ["log2", 10], ["log10", 10],
	["cbrt", 100], ["pow", 39], ["hypot", 1],
];

/**
 * The dump. Runs inside the page; `opts` carries the probe lists so the exact
 * same inputs are used on both sides even if this file is edited between runs.
 */
export const DUMP = async (opts) => {
	const out = {};
	const guard = async (key, fn) => {
		try {
			out[key] = await fn();
		} catch (e) {
			out[key] = { __error: String(e && e.message ? e.message : e) };
		}
	};
	const sha = async (buf) => {
		const d = await crypto.subtle.digest("SHA-256", buf);
		return [...new Uint8Array(d)].map((b) => b.toString(16).padStart(2, "0")).join("");
	};
	const withTimeout = (p, ms, fallback) =>
		Promise.race([p, new Promise((r) => setTimeout(() => r(fallback), ms))]);

	// ---- navigator -------------------------------------------------------
	await guard("navigator", async () => {
		const n = navigator;
		return {
			userAgent: n.userAgent, appVersion: n.appVersion, platform: n.platform,
			vendor: n.vendor, vendorSub: n.vendorSub, product: n.product,
			productSub: n.productSub, appName: n.appName, appCodeName: n.appCodeName,
			language: n.language, languages: [...n.languages],
			hardwareConcurrency: n.hardwareConcurrency, deviceMemory: n.deviceMemory,
			maxTouchPoints: n.maxTouchPoints, webdriver: n.webdriver,
			pdfViewerEnabled: n.pdfViewerEnabled, cookieEnabled: n.cookieEnabled,
			onLine: n.onLine, doNotTrack: n.doNotTrack,
			globalPrivacyControl: n.globalPrivacyControl,
			oscpu: n.oscpu, buildID: n.buildID,
		};
	});

	await guard("userAgentData", async () => {
		const uad = navigator.userAgentData;
		if (!uad) return null;
		const hi = await uad.getHighEntropyValues([
			"architecture", "bitness", "model", "platformVersion",
			"uaFullVersion", "fullVersionList", "wow64", "formFactors",
		]);
		return { brands: uad.brands, mobile: uad.mobile, platform: uad.platform, ...hi };
	});

	// ---- screen / window -------------------------------------------------
	await guard("screen", async () => ({
		width: screen.width, height: screen.height,
		availWidth: screen.availWidth, availHeight: screen.availHeight,
		availLeft: screen.availLeft, availTop: screen.availTop,
		colorDepth: screen.colorDepth, pixelDepth: screen.pixelDepth,
		isExtended: screen.isExtended,
		orientationType: screen.orientation && screen.orientation.type,
		orientationAngle: screen.orientation && screen.orientation.angle,
		devicePixelRatio,
		// The chrome height is (outer - inner): a real WM produces a small set of
		// values, and a spoofed screen with an unspoofed window produces one no
		// window manager ever makes.
		chromeHeight: outerHeight - innerHeight,
		chromeWidth: outerWidth - innerWidth,
		screenAvailInset: [
			screen.width - screen.availWidth,
			screen.height - screen.availHeight,
		],
	}));

	// ---- CSS system colors ----------------------------------------------
	await guard("systemColors", async () => {
		const el = document.createElement("div");
		document.documentElement.appendChild(el);
		const got = {};
		for (const name of opts.systemColors) {
			el.style.color = "";
			el.style.color = name;
			got[name] = getComputedStyle(el).color;
		}
		el.remove();
		return got;
	});

	// ---- CSS system fonts ------------------------------------------------
	await guard("systemFonts", async () => {
		const el = document.createElement("div");
		document.documentElement.appendChild(el);
		const got = {};
		for (const name of opts.systemFonts) {
			el.style.font = "";
			el.style.font = name;
			const cs = getComputedStyle(el);
			got[name] = {
				family: cs.fontFamily, size: cs.fontSize, weight: cs.fontWeight,
				style: cs.fontStyle,
			};
		}
		el.remove();
		return got;
	});

	// ---- installed fonts -------------------------------------------------
	await guard("fonts", async () => {
		const canvas = document.createElement("canvas");
		const ctx = canvas.getContext("2d");
		const text = "mmmmmmmmmmlli WQ@ 0123";
		const generics = ["monospace", "serif", "sans-serif"];
		const base = {};
		for (const g of generics) {
			ctx.font = `72px ${g}`;
			base[g] = ctx.measureText(text).width;
		}
		const present = [];
		const widths = {};
		for (const f of opts.fonts) {
			let differs = 0;
			const per = [];
			for (const g of generics) {
				ctx.font = `72px "${f}", ${g}`;
				const w = ctx.measureText(text).width;
				per.push(Math.round(w * 100) / 100);
				if (w !== base[g]) differs++;
			}
			widths[f] = per;
			// Present iff it displaced ANY fallback. Requiring ALL three is a
			// false negative for whichever family the platform maps a generic
			// to — on Windows `sans-serif` IS Arial, so Arial measures equal to
			// its own baseline and an all-three rule reports Arial absent.
			if (differs > 0) present.push(f);
		}
		return {
			present, count: present.length,
			baseline: base,
			widths,
			// document.fonts.check is a second, independent oracle. Chromium answers
			// it from the same font selector, so a mismatch between the two means
			// only one of the two paths got gated.
			checkSaysPresent: opts.fonts.filter((f) => document.fonts.check(`72px "${f}"`)),
		};
	});

	// ---- text metrics ----------------------------------------------------
	await guard("textMetrics", async () => {
		const ctx = document.createElement("canvas").getContext("2d");
		const out2 = {};
		for (const font of ['16px "Arial"', '16px "Segoe UI"', "16px monospace", '48px "Times New Roman"']) {
			ctx.font = font;
			const m = ctx.measureText("The quick brown fox jumps over 12345 \u{1F600}");
			out2[font] = {
				width: m.width,
				actualBoundingBoxLeft: m.actualBoundingBoxLeft,
				actualBoundingBoxRight: m.actualBoundingBoxRight,
				actualBoundingBoxAscent: m.actualBoundingBoxAscent,
				actualBoundingBoxDescent: m.actualBoundingBoxDescent,
				fontBoundingBoxAscent: m.fontBoundingBoxAscent,
				fontBoundingBoxDescent: m.fontBoundingBoxDescent,
				emHeightAscent: m.emHeightAscent, emHeightDescent: m.emHeightDescent,
				alphabeticBaseline: m.alphabeticBaseline,
				hangingBaseline: m.hangingBaseline, ideographicBaseline: m.ideographicBaseline,
			};
		}
		// The DOM's own measurement, which does not go through TextMetrics at all.
		const span = document.createElement("span");
		span.style.cssText = "position:absolute;white-space:nowrap;font:16px Arial";
		span.textContent = "The quick brown fox jumps over 12345";
		document.body.appendChild(span);
		out2.domClientWidth = span.getBoundingClientRect().width;
		span.remove();
		return out2;
	});

	// ---- canvas 2D -------------------------------------------------------
	await guard("canvas2d", async () => {
		const c = document.createElement("canvas");
		c.width = 300; c.height = 80;
		const ctx = c.getContext("2d");
		ctx.textBaseline = "top";
		ctx.font = '14px "Arial"';
		ctx.fillStyle = "#f60";
		ctx.fillRect(0, 0, 200, 40);
		ctx.fillStyle = "#069";
		ctx.fillText("Cwm fjordbank glyphs vext quiz \u{1F600}", 2, 2);
		ctx.strokeStyle = "rgba(0,255,0,0.5)";
		ctx.beginPath();
		ctx.arc(150, 40, 30, 0, Math.PI * 2, true);
		ctx.stroke();
		const a = await sha(ctx.getImageData(0, 0, 300, 80).data.buffer);
		const b = await sha(ctx.getImageData(0, 0, 300, 80).data.buffer);
		return {
			imageDataHash: a,
			stable: a === b,
			dataUrlHash: await sha(new TextEncoder().encode(c.toDataURL())),
			// A geometry-only surface: no text, so it isolates rasterisation from
			// font rendering.
			geometryHash: await (async () => {
				const g = document.createElement("canvas");
				g.width = 120; g.height = 60;
				const gx = g.getContext("2d");
				gx.fillStyle = "rgb(255,0,255)";
				gx.beginPath();
				gx.ellipse(60, 30, 50, 25, Math.PI / 5, 0, Math.PI * 2);
				gx.fill();
				gx.globalCompositeOperation = "multiply";
				gx.fillStyle = "rgb(0,255,255)";
				gx.fillRect(10, 10, 60, 40);
				return sha(gx.getImageData(0, 0, 120, 60).data.buffer);
			})(),
		};
	});

	// ---- WebGL -----------------------------------------------------------
	const glDump = (kind) => {
		const c = document.createElement("canvas");
		c.width = 64; c.height = 64;
		const gl = c.getContext(kind);
		if (!gl) return null;
		const dbg = gl.getExtension("WEBGL_debug_renderer_info");
		const params = {};
		// Every numeric/string GL constant the context exposes, by name.
		for (const key of Object.keys(Object.getPrototypeOf(gl))) {
			if (!/^[A-Z0-9_]+$/.test(key)) continue;
			const id = gl[key];
			if (typeof id !== "number") continue;
			let v;
			try { v = gl.getParameter(id); } catch { continue; }
			if (v === null || v === undefined) continue;
			if (typeof v === "object") {
				if (ArrayBuffer.isView(v)) v = Array.from(v);
				else continue;
			}
			if (typeof v === "boolean" || typeof v === "number" || typeof v === "string" || Array.isArray(v)) {
				params[key] = v;
			}
		}
		const precisions = {};
		for (const st of ["VERTEX_SHADER", "FRAGMENT_SHADER"]) {
			for (const pt of ["LOW_FLOAT", "MEDIUM_FLOAT", "HIGH_FLOAT", "LOW_INT", "MEDIUM_INT", "HIGH_INT"]) {
				const p = gl.getShaderPrecisionFormat(gl[st], gl[pt]);
				precisions[`${st}.${pt}`] = p ? [p.rangeMin, p.rangeMax, p.precision] : null;
			}
		}
		return {
			vendor: gl.getParameter(gl.VENDOR),
			renderer: gl.getParameter(gl.RENDERER),
			version: gl.getParameter(gl.VERSION),
			shadingLanguageVersion: gl.getParameter(gl.SHADING_LANGUAGE_VERSION),
			unmaskedVendor: dbg ? gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL) : null,
			unmaskedRenderer: dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : null,
			extensions: (gl.getSupportedExtensions() || []).slice().sort(),
			contextAttributes: gl.getContextAttributes(),
			parameters: params,
			shaderPrecisionFormats: precisions,
		};
	};
	await guard("webgl1", async () => glDump("webgl"));
	await guard("webgl2", async () => glDump("webgl2"));

	// A rendered-pixel readback: the surface canvas farbling perturbs, and the
	// one that actually differs between GPU stacks.
	await guard("webglRender", async () => {
		const c = document.createElement("canvas");
		c.width = 64; c.height = 64;
		const gl = c.getContext("webgl");
		if (!gl) return null;
		const vs = gl.createShader(gl.VERTEX_SHADER);
		gl.shaderSource(vs, "attribute vec2 p;varying vec2 v;void main(){v=p;gl_Position=vec4(p,0.0,1.0);}");
		gl.compileShader(vs);
		const fs = gl.createShader(gl.FRAGMENT_SHADER);
		gl.shaderSource(fs, "precision highp float;varying vec2 v;void main(){gl_FragColor=vec4(abs(sin(v.x*12.0)),abs(cos(v.y*9.0)),v.x*v.y+0.5,1.0);}");
		gl.compileShader(fs);
		const prog = gl.createProgram();
		gl.attachShader(prog, vs); gl.attachShader(prog, fs);
		gl.linkProgram(prog); gl.useProgram(prog);
		const buf = gl.createBuffer();
		gl.bindBuffer(gl.ARRAY_BUFFER, buf);
		gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
		const loc = gl.getAttribLocation(prog, "p");
		gl.enableVertexAttribArray(loc);
		gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
		gl.drawArrays(gl.TRIANGLES, 0, 3);
		const px = new Uint8Array(64 * 64 * 4);
		gl.readPixels(0, 0, 64, 64, gl.RGBA, gl.UNSIGNED_BYTE, px);
		const h1 = await sha(px.buffer);
		gl.readPixels(0, 0, 64, 64, gl.RGBA, gl.UNSIGNED_BYTE, px);
		const h2 = await sha(px.buffer);
		return { readPixelsHash: h1, stable: h1 === h2, toDataURLHash: await sha(new TextEncoder().encode(c.toDataURL())) };
	});

	// ---- WebGPU ----------------------------------------------------------
	await guard("webgpu", async () => {
		if (!navigator.gpu) return null;
		const adapter = await withTimeout(navigator.gpu.requestAdapter(), 8000, null);
		if (!adapter) return { adapter: null };
		const info = adapter.info || (adapter.requestAdapterInfo ? await adapter.requestAdapterInfo() : null);
		const limits = {};
		if (adapter.limits) for (const k in adapter.limits) limits[k] = adapter.limits[k];
		return {
			info: info ? {
				vendor: info.vendor, architecture: info.architecture,
				device: info.device, description: info.description,
				subgroupMinSize: info.subgroupMinSize, subgroupMaxSize: info.subgroupMaxSize,
			} : null,
			features: [...(adapter.features || [])].sort(),
			limits,
			preferredCanvasFormat: navigator.gpu.getPreferredCanvasFormat(),
		};
	});

	// ---- audio -----------------------------------------------------------
	await guard("audio", async () => {
		const res = {};
		try {
			const ac = new (window.AudioContext || window.webkitAudioContext)();
			res.live = {
				sampleRate: ac.sampleRate, baseLatency: ac.baseLatency,
				outputLatency: ac.outputLatency, state: ac.state,
				maxChannelCount: ac.destination.maxChannelCount,
				channelCount: ac.destination.channelCount,
				numberOfInputs: ac.destination.numberOfInputs,
			};
			ac.close();
		} catch (e) { res.live = { __error: String(e) }; }
		// The CLASS signal: pure CPU math, no hardware. Two renders must agree.
		const render = async () => {
			const oc = new OfflineAudioContext(1, 44100, 44100);
			const osc = oc.createOscillator();
			osc.type = "triangle";
			osc.frequency.value = 10000;
			const comp = oc.createDynamicsCompressor();
			comp.threshold.value = -50; comp.knee.value = 40; comp.ratio.value = 12;
			comp.attack.value = 0; comp.release.value = 0.25;
			osc.connect(comp); comp.connect(oc.destination);
			osc.start(0);
			const buf = await oc.startRendering();
			const data = buf.getChannelData(0);
			let sum = 0;
			for (let i = 4500; i < 5000; i++) sum += Math.abs(data[i]);
			return { sum, hash: await sha(data.buffer), sample: data[4500] };
		};
		const a = await withTimeout(render(), 15000, null);
		const b = await withTimeout(render(), 15000, null);
		res.offline = a;
		res.offlineStable = a && b ? a.hash === b.hash : null;
		return res;
	});

	// ---- Intl / time -----------------------------------------------------
	await guard("intl", async () => {
		const d = new Date(Date.UTC(2026, 0, 15, 12, 0, 0));
		const s = new Date(Date.UTC(2026, 6, 15, 12, 0, 0));
		return {
			resolvedTimeZone: Intl.DateTimeFormat().resolvedOptions().timeZone,
			resolvedLocale: Intl.DateTimeFormat().resolvedOptions().locale,
			calendar: Intl.DateTimeFormat().resolvedOptions().calendar,
			numberingSystem: Intl.DateTimeFormat().resolvedOptions().numberingSystem,
			offsetJan: d.getTimezoneOffset(), offsetJul: s.getTimezoneOffset(),
			dateStringJan: d.toString(), dateStringJul: s.toString(),
			toLocaleString: d.toLocaleString(),
			toLocaleDateString: d.toLocaleDateString(),
			toLocaleTimeString: d.toLocaleTimeString(),
			numberFormat: (1234567.891).toLocaleString(),
			currency: (1234.5).toLocaleString(undefined, { style: "currency", currency: "USD" }),
			relative: new Intl.RelativeTimeFormat().format(-1, "day"),
			collatorLocale: new Intl.Collator().resolvedOptions().locale,
			timeZoneCount: Intl.supportedValuesOf ? Intl.supportedValuesOf("timeZone").length : null,
			currencyCount: Intl.supportedValuesOf ? Intl.supportedValuesOf("currency").length : null,
			longTZ: new Intl.DateTimeFormat("en-US", { timeZoneName: "long" }).format(d),
			hourCycle: Intl.DateTimeFormat().resolvedOptions().hourCycle,
			firstDayOfWeek: new Intl.Locale(navigator.language).getWeekInfo
				? new Intl.Locale(navigator.language).getWeekInfo().firstDay : null,
		};
	});

	// ---- media -----------------------------------------------------------
	await guard("media", async () => {
		const video = document.createElement("video");
		const audio = document.createElement("audio");
		const canPlay = {}, mse = {}, recorder = {};
		for (const c of opts.codecs) {
			const el = c.startsWith("audio") ? audio : video;
			canPlay[c] = el.canPlayType(c);
			mse[c] = window.MediaSource ? MediaSource.isTypeSupported(c) : null;
			recorder[c] = window.MediaRecorder ? MediaRecorder.isTypeSupported(c) : null;
		}
		let devices = null;
		try {
			const list = await withTimeout(navigator.mediaDevices.enumerateDevices(), 5000, null);
			devices = list ? list.map((d) => ({ kind: d.kind, hasLabel: !!d.label, idLen: d.deviceId.length, groupLen: d.groupId.length })) : null;
		} catch (e) { devices = { __error: String(e) }; }
		let decoding = null;
		try {
			decoding = await withTimeout(navigator.mediaCapabilities.decodingInfo({
				type: "file",
				video: { contentType: 'video/mp4; codecs="avc1.640028"', width: 1920, height: 1080, bitrate: 4000000, framerate: 30 },
			}), 5000, null);
		} catch (e) { decoding = { __error: String(e) }; }
		return { canPlayType: canPlay, mediaSource: mse, mediaRecorder: recorder, devices, decodingInfo: decoding };
	});

	// ---- speech voices ---------------------------------------------------
	await guard("voices", async () => {
		const get = () => speechSynthesis.getVoices().map((v) => ({
			name: v.name, lang: v.lang, uri: v.voiceURI, local: v.localService, def: v.default,
		}));
		// Poll until the list stops growing. A single voiceschanged wait is NOT
		// enough on Windows: Chrome publishes its ~19 remote Google voices first
		// and the local SAPI voices arrive about a second later, so a one-shot
		// wait silently captures a partial list and under-reports the surface.
		let v = get();
		let stable = 0;
		for (let i = 0; i < 20 && stable < 3; i++) {
			await new Promise((r) => setTimeout(r, 500));
			const next = get();
			stable = next.length === v.length && next.length > 0 ? stable + 1 : 0;
			v = next;
		}
		return { count: v.length, voices: v };
	});

	// ---- plugins ---------------------------------------------------------
	await guard("plugins", async () => ({
		plugins: [...navigator.plugins].map((p) => ({
			name: p.name, filename: p.filename, description: p.description,
			mimes: [...p].map((m) => m.type),
		})),
		mimeTypes: [...navigator.mimeTypes].map((m) => m.type),
	}));

	// ---- permissions -----------------------------------------------------
	await guard("permissions", async () => {
		const names = ["geolocation", "notifications", "camera", "microphone", "midi",
			"background-sync", "persistent-storage", "clipboard-read", "clipboard-write",
			"payment-handler", "screen-wake-lock", "accelerometer", "gyroscope",
			"magnetometer", "ambient-light-sensor", "storage-access", "local-fonts",
			"window-management", "top-level-storage-access", "captured-surface-control"];
		const got = {};
		for (const name of names) {
			try {
				const st = await withTimeout(navigator.permissions.query({ name }), 3000, null);
				got[name] = st ? st.state : "timeout";
			} catch (e) { got[name] = `throw:${String(e.name || e)}`; }
		}
		got.__notificationPermission = window.Notification ? Notification.permission : null;
		return got;
	});

	// ---- storage ---------------------------------------------------------
	await guard("storage", async () => {
		const est = await withTimeout(navigator.storage.estimate(), 5000, null);
		return est ? { quota: est.quota, usage: est.usage, quotaGiB: est.quota / 1024 / 1024 / 1024 } : null;
	});

	// ---- network information ---------------------------------------------
	await guard("connection", async () => {
		const c = navigator.connection;
		if (!c) return null;
		return { effectiveType: c.effectiveType, rtt: c.rtt, downlink: c.downlink, saveData: c.saveData, type: c.type };
	});

	// ---- keyboard layout --------------------------------------------------
	await guard("keyboard", async () => {
		if (!navigator.keyboard || !navigator.keyboard.getLayoutMap) return null;
		const map = await withTimeout(navigator.keyboard.getLayoutMap(), 5000, null);
		if (!map) return "timeout";
		const got = {};
		for (const k of ["KeyA", "KeyQ", "KeyW", "KeyY", "KeyZ", "Semicolon", "Quote",
			"Backquote", "Backslash", "BracketLeft", "Comma", "Period", "Slash",
			"Minus", "Equal", "IntlBackslash", "IntlRo", "IntlYen", "Digit1", "Digit2"]) {
			got[k] = map.get(k) ?? null;
		}
		got.__size = map.size;
		return got;
	});

	// ---- media queries ---------------------------------------------------
	await guard("mediaQueries", async () => {
		const got = {};
		for (const q of opts.mediaQueries) got[q] = matchMedia(q).matches;
		return got;
	});

	// ---- feature presence -------------------------------------------------
	await guard("features", async () => {
		const got = {};
		for (const path of opts.features) {
			try {
				let cur = window;
				for (const part of path.split(".").slice(1)) {
					if (cur == null) break;
					cur = cur[part];
				}
				got[path] = cur === undefined ? false : typeof cur === "function" ? "function" : cur === null ? "null" : typeof cur;
			} catch (e) { got[path] = `throw:${String(e.name || e)}`; }
		}
		return got;
	});

	// ---- platform libm ----------------------------------------------------
	await guard("math", async () => {
		const got = {};
		for (const [fn, x] of opts.mathInputs) {
			try {
				const v = fn === "pow" ? Math.pow(Math.PI, -100) : fn === "hypot" ? Math.hypot(1, 2, 3) : Math[fn](x);
				got[`${fn}(${x})`] = v.toString();
			} catch (e) { got[`${fn}(${x})`] = `throw`; }
		}
		// The exact bit patterns, which is what a detector actually compares.
		const f = new Float64Array([Math.sin(1e300), Math.tan(-1e308), Math.exp(1), Math.atan(2), Math.acosh(1e308)]);
		got.__bits = [...new Uint8Array(f.buffer)].map((b) => b.toString(16).padStart(2, "0")).join("");
		return got;
	});

	// ---- error / native shape ---------------------------------------------
	await guard("shape", async () => {
		let stack = null;
		try { null.f(); } catch (e) { stack = { name: e.name, message: e.message, lines: e.stack.split("\n").length }; }
		const desc = (obj, key) => {
			const d = Object.getOwnPropertyDescriptor(obj, key);
			if (!d) return null;
			return { get: d.get ? d.get.toString() : null, value: typeof d.value, enumerable: d.enumerable, configurable: d.configurable };
		};
		return {
			typeError: stack,
			navPlatform: desc(Navigator.prototype, "platform"),
			navWebdriver: desc(Navigator.prototype, "webdriver"),
			navHwc: desc(Navigator.prototype, "hardwareConcurrency"),
			screenWidth: desc(Screen.prototype, "width"),
			toStringTag: Object.prototype.toString.call(navigator),
			automationResidue: Object.keys(window).filter((k) =>
				/^(cdc_|\$cdc|__webdriver|__selenium|__nightmare|_phantom|callPhantom|domAutomation|__playwright|__pw)/.test(k)),
			functionToString: Function.prototype.toString.call(Function.prototype.toString),
			errorStackTraceLimit: Error.stackTraceLimit,
			v8Version: (() => { try { return new Error().stack.includes("at ") ? "v8" : "other"; } catch { return null; } })(),
		};
	});

	// ---- performance -------------------------------------------------------
	await guard("performance", async () => {
		const deltas = [];
		let last = performance.now();
		for (let i = 0; i < 5000; i++) {
			const n = performance.now();
			if (n !== last) { deltas.push(n - last); last = n; }
			if (deltas.length >= 50) break;
		}
		deltas.sort((a, b) => a - b);
		return {
			resolutionMedian: deltas[Math.floor(deltas.length / 2)] ?? null,
			resolutionMin: deltas[0] ?? null,
			memory: performance.memory ? {
				jsHeapSizeLimit: performance.memory.jsHeapSizeLimit,
				totalJSHeapSize: undefined, usedJSHeapSize: undefined,
			} : null,
			timeOriginIsFinite: Number.isFinite(performance.timeOrigin),
		};
	});

	// ---- battery -----------------------------------------------------------
	await guard("battery", async () => {
		if (!navigator.getBattery) return null;
		const b = await withTimeout(navigator.getBattery(), 5000, null);
		if (!b) return "timeout";
		return { charging: b.charging, level: b.level, chargingTimeFinite: Number.isFinite(b.chargingTime), dischargingTimeFinite: Number.isFinite(b.dischargingTime) };
	});

	// ---- WebRTC ------------------------------------------------------------
	await guard("webrtc", async () => {
		if (!window.RTCPeerConnection) return null;
		const pc = new RTCPeerConnection({ iceServers: [] });
		pc.createDataChannel("probe");
		const offer = await pc.createOffer();
		const lines = offer.sdp.split("\r\n");
		const res = {
			// The codec/extension list in an SDP offer is a large, stable
			// fingerprint that varies with the platform media stack.
			sdpFingerprintLines: lines.filter((l) => /^a=(fingerprint|setup|extmap|rtpmap|fmtp)/.test(l)).length,
			extmaps: lines.filter((l) => l.startsWith("a=extmap")).map((l) => l.replace(/^a=extmap:\d+ /, "")).sort(),
			hashAlgo: (lines.find((l) => l.startsWith("a=fingerprint")) || "").split(" ")[0],
			msidSemantic: lines.find((l) => l.startsWith("a=msid-semantic")) || null,
			candidates: [],
		};
		await new Promise((resolve) => {
			const t = setTimeout(resolve, 4000);
			pc.onicecandidate = (e) => {
				if (!e.candidate) { clearTimeout(t); resolve(); return; }
				const c = e.candidate.candidate;
				res.candidates.push({
					typ: (c.match(/ typ (\w+)/) || [])[1] || null,
					proto: (c.match(/ (udp|tcp) /i) || [])[1] || null,
					// The literal address matters: a real host candidate leaks the LAN
					// IP, and an mDNS one is an .local UUID.
					addressKind: /\.local/.test(c) ? "mdns" : /(\d{1,3}\.){3}\d{1,3}/.test(c) ? "ipv4" : "other",
					address: c.replace(/candidate:\S+ /, "").split(" ").slice(2, 4).join(":"),
				});
			};
			pc.setLocalDescription(offer);
		});
		pc.close();
		return res;
	});

	// ---- worker + iframe twins ---------------------------------------------
	await guard("worker", async () => {
		const src = `self.onmessage=async()=>{
			const gl=(()=>{try{const c=new OffscreenCanvas(32,32);const g=c.getContext('webgl');
				if(!g)return null;const d=g.getExtension('WEBGL_debug_renderer_info');
				return {vendor:g.getParameter(g.VENDOR),renderer:d?g.getParameter(d.UNMASKED_RENDERER_WEBGL):null,
				exts:(g.getSupportedExtensions()||[]).length};}catch(e){return {err:String(e)}}})();
			postMessage({platform:navigator.platform,userAgent:navigator.userAgent,
				hardwareConcurrency:navigator.hardwareConcurrency,deviceMemory:navigator.deviceMemory,
				languages:navigator.languages.join(','),tz:Intl.DateTimeFormat().resolvedOptions().timeZone,
				uaPlatform:navigator.userAgentData?navigator.userAgentData.platform:null,
				uaBrands:navigator.userAgentData?JSON.stringify(navigator.userAgentData.brands):null,
				gl});};`;
		const w = new Worker(URL.createObjectURL(new Blob([src], { type: "text/javascript" })));
		const r = await withTimeout(new Promise((res) => { w.onmessage = (e) => res(e.data); w.postMessage(1); }), 8000, { __error: "timeout" });
		w.terminate();
		return r;
	});

	await guard("iframe", async () => {
		const f = document.createElement("iframe");
		f.src = "about:blank";
		document.body.appendChild(f);
		const cw = f.contentWindow;
		const r = {
			platform: cw.navigator.platform, userAgent: cw.navigator.userAgent,
			hardwareConcurrency: cw.navigator.hardwareConcurrency,
			webdriver: cw.navigator.webdriver,
			tz: cw.Intl.DateTimeFormat().resolvedOptions().timeZone,
			screenWidth: cw.screen.width, screenHeight: cw.screen.height,
			uaPlatform: cw.navigator.userAgentData ? cw.navigator.userAgentData.platform : null,
		};
		f.remove();
		return r;
	});

	return out;
};
