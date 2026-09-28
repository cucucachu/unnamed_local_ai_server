// Web transport: the sandbox as `<iframe sandbox="allow-scripts" srcdoc>`
// (opaque origin: no cookies, storage or parent access; the document's CSP
// blocks requests). The one thing neither can stop is the frame navigating
// itself, which sends one request carrying whatever it put in the URL, so a
// second `load` removes the frame (docs/PLATFORM.md §11).
import { createBridgeHost, type BridgeHost, type BridgeHostOptions } from './bridge-host';

export type SandboxFrame = { frame: HTMLIFrameElement; host: BridgeHost; destroy(): void };

export function mountSandboxFrame(
  container: HTMLElement,
  { html, onKilled, ...opts }: Omit<BridgeHostOptions, 'send'> & { html: string; onKilled?: () => void },
): SandboxFrame {
  const frame = container.ownerDocument.createElement('iframe');
  frame.setAttribute('sandbox', 'allow-scripts');
  frame.setAttribute('referrerpolicy', 'no-referrer');
  frame.title = 'app';
  frame.style.cssText = 'border:0;width:100%;height:100%;flex:1';
  const win = container.ownerDocument.defaultView!;
  const host = createBridgeHost({ ...opts, send: (wire) => frame.contentWindow?.postMessage(wire, '*') });
  const onMessage = (e: MessageEvent) => {
    if (e.source === frame.contentWindow && e.source) host.receive(e.data);
  };
  let loads = 0;
  const destroy = () => {
    host.close();
    win.removeEventListener('message', onMessage);
    frame.remove();
  };
  frame.addEventListener('load', () => {
    if (++loads > 1) {
      destroy();
      onKilled?.();
    }
  });
  win.addEventListener('message', onMessage);
  frame.srcdoc = html;
  container.appendChild(frame);
  return { frame, host, destroy };
}
