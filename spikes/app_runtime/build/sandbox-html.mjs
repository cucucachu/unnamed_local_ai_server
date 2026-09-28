// The single HTML document the sandbox loads: used as iframe `srcdoc` on web
// and as WebView `source={{ html }}` on native.

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

const inline = (js) => js.replace(/<\/(script)/gi, '<\\/$1');

export function sandboxHtml({ runtime, app, config = {}, csp = SANDBOX_CSP }) {
  return `<!doctype html>
<html><head><meta charset="utf-8">
${csp ? `<meta http-equiv="Content-Security-Policy" content="${csp}">` : ''}
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>html,body,#root{height:100%;margin:0}#root{display:flex}</style>
<script>window.__homeai_config=${inline(JSON.stringify(config))};</script>
<script>${inline(runtime)}</script>
</head><body><div id="root"></div>
<script>${inline(app)}</script>
</body></html>`;
}
