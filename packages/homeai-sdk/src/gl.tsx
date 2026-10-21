// `expo-gl`'s GLView over a browser canvas: the sandbox is always a browser,
// so the context is a real WebGL2 (or WebGL 1) context and `endFrameEXP` has
// nothing to flush. three.js renders into it with
// `new THREE.WebGLRenderer({ canvas: gl.canvas, context: gl })`.
import { useEffect, useRef, useState } from 'react';
import { Text, View, type StyleProp, type ViewProps, type ViewStyle } from 'react-native';
import { useTheme } from './theme';

export type ExpoWebGLRenderingContext = (WebGL2RenderingContext | WebGLRenderingContext) & { endFrameEXP(): void };

export type GLViewProps = ViewProps & {
  /** Called once, after the view is in the page, with its context. */
  onContextCreate: (gl: ExpoWebGLRenderingContext) => void;
  /** The browser dropped the context (memory pressure, a GPU reset); the view shows a message. */
  onContextLost?: () => void;
  style?: StyleProp<ViewStyle>;
};

function sizeCanvas(canvas: HTMLCanvasElement, box: HTMLElement) {
  const dpr = window.devicePixelRatio || 1;
  const w = Math.max(1, Math.round(box.clientWidth * dpr));
  const h = Math.max(1, Math.round(box.clientHeight * dpr));
  if (canvas.width !== w) canvas.width = w;
  if (canvas.height !== h) canvas.height = h;
}

export function GLView({ onContextCreate, onContextLost, style, children, ...rest }: GLViewProps) {
  const t = useTheme();
  const box = useRef<HTMLElement | null>(null);
  const create = useRef(onContextCreate);
  const lost = useRef(onContextLost);
  create.current = onContextCreate;
  lost.current = onContextLost;
  const [problem, setProblem] = useState<string | null>(null);

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    if (typeof WebGLRenderingContext === 'undefined') {
      setProblem("3D graphics aren't available here");
      return;
    }
    const canvas = document.createElement('canvas');
    canvas.style.cssText = 'position:absolute;left:0;top:0;width:100%;height:100%;display:block;touch-action:none';
    el.prepend(canvas);
    sizeCanvas(canvas, el);
    const options = { antialias: true, alpha: true, premultipliedAlpha: true };
    const gl = (canvas.getContext('webgl2', options) ?? canvas.getContext('webgl', options)) as ExpoWebGLRenderingContext | null;
    if (!gl) {
      canvas.remove();
      setProblem("3D graphics aren't available on this device");
      return;
    }
    gl.endFrameEXP = () => {};
    const onLost = (e: Event) => {
      e.preventDefault();
      setProblem('3D graphics stopped (the device reset them); reopen the app');
      lost.current?.();
    };
    canvas.addEventListener('webglcontextlost', onLost);
    const resize = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(() => sizeCanvas(canvas, el));
    resize?.observe(el);
    create.current(gl);
    return () => {
      resize?.disconnect();
      canvas.removeEventListener('webglcontextlost', onLost);
      gl.getExtension('WEBGL_lose_context')?.loseContext();
      canvas.remove();
    };
  }, []);

  return (
    <View ref={box as never} style={[{ position: 'relative', overflow: 'hidden' }, style]} {...rest}>
      {problem ? (
        <View style={{ flex: 1, alignItems: 'center', justifyContent: 'center', padding: t.spacing.lg }}>
          <Text style={{ color: t.colors.textMuted, textAlign: 'center' }}>{problem}</Text>
        </View>
      ) : null}
      {children}
    </View>
  );
}
