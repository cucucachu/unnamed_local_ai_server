// The sandbox document: CSP + config + runtime + app, all inline. The iframe
// `srcdoc` on web, WebView `source={{ html }}` on native. Without the CSP an
// opaque-origin frame still can't read credentials but can send requests.
import type { SandboxConfig } from '../protocol';

export const SANDBOX_CSP = [
  "default-src 'none'",
  "script-src 'unsafe-inline'",
  "style-src 'unsafe-inline'",
  'img-src data: blob:',
  'font-src data:',
  "connect-src 'none'",
  "form-action 'none'",
  "base-uri 'none'",
].join('; ');

/** `<\/` is the same JS in strings, templates and regexps, and can't close the element. */
function inlineScript(js: string) {
  return js.replace(/<\/(script)/gi, '<\\/$1');
}

export function sandboxDocument({ runtime, app, config = {} }: { runtime: string; app: string; config?: SandboxConfig }) {
  const json = JSON.stringify(config).replace(/</g, '\\u003c');
  return `<!doctype html>
<html><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="${SANDBOX_CSP}">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>html,body,#root{height:100%;margin:0}#root{display:flex}</style>
<script>window.__homeai_config=${json};</script>
<script>${inlineScript(runtime)}</script>
</head><body><div id="root"></div>
<script>${inlineScript(app)}</script>
</body></html>`;
}

/** The script a native host passes to WebView.injectJavaScript to deliver one wire message. */
export function injectScriptFor(wire: string) {
  return `window.__homeaiReceive&&window.__homeaiReceive(${JSON.stringify(wire)});true;`;
}
