import { createElement, forwardRef, useEffect, useImperativeHandle, useMemo, useRef } from 'react';
import { makeHost, type Envelope, type RpcHandler } from './bridgeHost';

export type SandboxHandle = { event(event: string, data?: any): void; inject(js: string): void };
type Props = { html: string; rpc: RpcHandler; onEvent: (event: string, data: any) => void; onBlockedNavigation: (url: string) => void };

export const Sandbox = forwardRef<SandboxHandle, Props>(function Sandbox({ html, rpc, onEvent, onBlockedNavigation }, ref) {
  const frame = useRef<HTMLIFrameElement>(null);
  const loads = useRef(0);
  const host = useMemo(() => makeHost((env: Envelope) => frame.current?.contentWindow?.postMessage(JSON.stringify(env), '*'), rpc, onEvent), [rpc, onEvent]);
  useImperativeHandle(ref, () => ({ event: host.event, inject: () => {} }), [host]);
  useEffect(() => {
    const onMessage = (e: MessageEvent) => {
      if (frame.current && e.source === frame.current.contentWindow) host.receive(e.data);
    };
    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, [host]);
  return createElement('iframe', {
    ref: frame,
    title: 'app',
    sandbox: 'allow-scripts',
    srcDoc: html,
    onLoad: () => {
      // srcdoc loads once; a second load means the frame navigated itself.
      if (++loads.current > 1) {
        onBlockedNavigation('(iframe self-navigation)');
        frame.current?.remove();
      }
    },
    style: { flex: 1, border: 0, width: '100%', height: '100%' },
  });
});
