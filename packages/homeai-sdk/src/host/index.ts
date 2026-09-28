// `@homeai/sdk/host`: what the app host (services/frontend, web and native)
// uses to run an app instance. DOM-free; the iframe transport is in
// `@homeai/sdk/host/web`, and native passes `injectScriptFor(wire)` to
// WebView.injectJavaScript as `send`.
export * from '../protocol';
export { MAX_IN_FLIGHT, checkParams, createBridgeHost, type BridgeHost, type BridgeHostOptions, type Forward } from './bridge-host';
export { SANDBOX_CSP, injectScriptFor, sandboxDocument } from './document';
export { fetchBundle, fetchRuntime, platformEventRelay, platformForward, runtimeUrl, type Bundle, type FetchLike, type PlatformOptions } from './platform';
