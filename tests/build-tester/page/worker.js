// Worker half of the build-tester: reports the identity a worker sees. One
// script for a dedicated, a shared and a service worker, which a renderer
// starts by three different paths.
async function report() {
  const out = {
    userAgent: navigator.userAgent,
    platform: navigator.platform,
    hardwareConcurrency: navigator.hardwareConcurrency,
    deviceMemory: navigator.deviceMemory,
    language: navigator.language,
    languages: Array.from(navigator.languages || []),
    webdriver: navigator.webdriver,
    timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    locale: Intl.DateTimeFormat().resolvedOptions().locale,
    offset: new Date().getTimezoneOffset(),
    uad: null,
    webgl: null,
  };
  try {
    if (navigator.userAgentData) {
      out.uad = {
        platform: navigator.userAgentData.platform,
        mobile: navigator.userAgentData.mobile,
        brands: navigator.userAgentData.brands,
        high: await navigator.userAgentData.getHighEntropyValues(
          ["architecture", "bitness", "model", "platformVersion", "uaFullVersion", "wow64", "fullVersionList"]),
      };
    }
  } catch (error) { out.uadError = String(error); }
  try {
    const gl = new OffscreenCanvas(16, 16).getContext("webgl");
    const ext = gl && gl.getExtension("WEBGL_debug_renderer_info");
    if (ext) {
      out.webgl = {
        vendor: gl.getParameter(ext.UNMASKED_VENDOR_WEBGL),
        renderer: gl.getParameter(ext.UNMASKED_RENDERER_WEBGL),
      };
    }
  } catch (error) { out.webglError = String(error); }
  try {
    const headers = await (await fetch("/headers?from=worker")).json();
    out.fetchHeaders = headers;
  } catch (error) { out.fetchError = String(error); }
  return out;
}

if (typeof SharedWorkerGlobalScope !== "undefined" && self instanceof SharedWorkerGlobalScope) {
  onconnect = (event) => {
    const port = event.ports[0];
    port.onmessage = async () => port.postMessage(await report());
  };
} else if (typeof ServiceWorkerGlobalScope !== "undefined" && self instanceof ServiceWorkerGlobalScope) {
  onmessage = (event) => event.waitUntil(report().then((out) => event.source.postMessage(out)));
} else {
  onmessage = async () => postMessage(await report());
}
