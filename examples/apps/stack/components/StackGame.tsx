import { useEffect, useRef, useState } from 'react';
import { Pressable, Text, View } from 'react-native';
import { GLView, type ExpoWebGLRenderingContext } from 'expo-gl';
import { useTheme } from '@homeai/ui';
import * as THREE from 'three';
import { BASE, HEIGHT, SIZE, axisFor, drop, hueAt, slide, type Block } from '../lib/stack';

type Phase = 'ready' | 'playing' | 'over';
type Events = {
  onStart: () => void;
  onPlace: (score: number, perfect: boolean) => void;
  onOver: (score: number, perfects: number) => void;
};
type Engine = { tap: () => void; setBackground: (color: string) => void; dispose: () => void };

/** The scene and its frame loop. Everything moving is a function of Date.now(), so a drop lands
 * where the block was at the moment of the tap, not at the last frame. */
function createEngine(gl: ExpoWebGLRenderingContext, background: string, events: Events): Engine {
  const renderer = new THREE.WebGLRenderer({ canvas: gl.canvas, context: gl, antialias: true });
  renderer.setClearColor(background, 1);
  const scene = new THREE.Scene();
  scene.add(new THREE.AmbientLight(0xffffff, 1.4));
  const sun = new THREE.DirectionalLight(0xffffff, 2.2);
  sun.position.set(4, 10, 7);
  scene.add(sun);
  const camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.1, 200);
  const box = new THREE.BoxGeometry(1, 1, 1);
  const plane = new THREE.PlaneGeometry(1, 1);
  plane.rotateX(-Math.PI / 2);

  const material = (level: number) => new THREE.MeshLambertMaterial({ color: new THREE.Color().setHSL(hueAt(level), 0.55, 0.55) });
  function blockMesh(b: Block, level: number) {
    const mesh = new THREE.Mesh(box, material(level));
    mesh.scale.set(b.w, HEIGHT, b.d);
    mesh.position.set(b.x, level * HEIGHT, b.z);
    scene.add(mesh);
    return mesh;
  }
  function remove(mesh: THREE.Mesh) {
    scene.remove(mesh);
    (mesh.material as THREE.Material).dispose();
  }

  const pedestal = new THREE.Mesh(box, material(0));
  pedestal.scale.set(SIZE, 20, SIZE);
  pedestal.position.set(0, -10 - HEIGHT / 2, 0);
  scene.add(pedestal);
  let tower: { block: Block; mesh: THREE.Mesh }[] = [{ block: BASE, mesh: blockMesh(BASE, 0) }];
  let moving: THREE.Mesh | null = null;
  let spawn = 0;
  let phase: Phase = 'ready';
  let score = 0;
  let perfects = 0;
  let falling: { mesh: THREE.Mesh; vy: number; spin: THREE.Vector3 }[] = [];
  let flashes: { mesh: THREE.Mesh; age: number; w: number; d: number }[] = [];
  let camY = 0;

  const top = () => tower[tower.length - 1].block;
  const offsetAt = (now: number) => slide(score, (now - spawn) / 1000);

  function spawnNext(now: number) {
    moving = blockMesh(top(), score + 1);
    spawn = now;
    moving.position[axisFor(score)] = top()[axisFor(score)] + offsetAt(now);
  }
  function fall(b: Block, level: number) {
    const mesh = blockMesh(b, level);
    const axis = axisFor(score);
    const away = Math.sign(b[axis] - top()[axis]) || 1;
    falling.push({ mesh, vy: 0, spin: new THREE.Vector3(axis === 'z' ? away * 2 : 0, 0, axis === 'x' ? -away * 2 : 0) });
  }
  function flash(b: Block, level: number) {
    const mesh = new THREE.Mesh(plane, new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.8, depthWrite: false }));
    mesh.scale.set(b.w, 1, b.d);
    mesh.position.set(b.x, level * HEIGHT + HEIGHT / 2 + 0.01, b.z);
    scene.add(mesh);
    flashes.push({ mesh, age: 0, w: b.w, d: b.d });
  }
  function reset() {
    tower.slice(1).forEach((t) => remove(t.mesh));
    tower = tower.slice(0, 1);
    if (moving) remove(moving);
    moving = null;
    score = 0;
    perfects = 0;
  }

  function tap() {
    const now = Date.now();
    if (phase !== 'playing') {
      reset();
      phase = 'playing';
      events.onStart();
      spawnNext(now);
      return;
    }
    if (!moving) return;
    const level = score + 1;
    const result = drop(top(), axisFor(score), offsetAt(now));
    remove(moving);
    moving = null;
    if (result.kind === 'miss') {
      fall(result.falling, level);
      phase = 'over';
      events.onOver(score, perfects);
      return;
    }
    if (result.kind === 'cut') fall(result.falling, level);
    tower.push({ block: result.placed, mesh: blockMesh(result.placed, level) });
    if (result.kind === 'perfect') {
      perfects++;
      flash(result.placed, level);
    }
    score++;
    events.onPlace(score, result.kind === 'perfect');
    spawnNext(now);
  }

  let w = 0;
  let h = 0;
  let last = Date.now();
  let raf = 0;
  function frame() {
    const now = Date.now();
    const dt = Math.min(0.05, (now - last) / 1000);
    last = now;
    if (w !== gl.drawingBufferWidth || h !== gl.drawingBufferHeight) {
      w = gl.drawingBufferWidth;
      h = gl.drawingBufferHeight;
      renderer.setSize(w, h, false);
      // Tall enough for the tower and wide enough for the block's swing, whatever the shape.
      const viewH = Math.max(12, 13 / (w / h));
      const viewW = viewH * (w / h);
      Object.assign(camera, { left: -viewW / 2, right: viewW / 2, top: viewH / 2, bottom: -viewH / 2 });
      camera.updateProjectionMatrix();
    }
    if (moving) moving.position[axisFor(score)] = top()[axisFor(score)] + offsetAt(now);
    falling = falling.filter((f) => {
      f.vy -= 25 * dt;
      f.mesh.position.y += f.vy * dt;
      f.mesh.rotation.x += f.spin.x * dt;
      f.mesh.rotation.z += f.spin.z * dt;
      if (f.mesh.position.y > camY - 30) return true;
      remove(f.mesh);
      return false;
    });
    flashes = flashes.filter((f) => {
      f.age += dt;
      const grow = 1 + f.age * 0.8;
      f.mesh.scale.set(f.w * grow, 1, f.d * grow);
      (f.mesh.material as THREE.MeshBasicMaterial).opacity = Math.max(0, 0.8 - f.age * 1.6);
      if (f.age < 0.5) return true;
      remove(f.mesh);
      return false;
    });
    camY += (score * HEIGHT - camY) * Math.min(1, dt * 4);
    camera.position.set(10, camY + 10, 10);
    camera.lookAt(0, camY - 1, 0);
    renderer.render(scene, camera);
    gl.endFrameEXP();
    raf = requestAnimationFrame(frame);
  }
  frame();

  return {
    tap,
    setBackground: (color) => renderer.setClearColor(color, 1),
    dispose: () => {
      cancelAnimationFrame(raf);
      [...tower.map((t) => t.mesh), ...falling.map((f) => f.mesh), ...flashes.map((f) => f.mesh), ...(moving ? [moving] : []), pedestal].forEach(remove);
      box.dispose();
      plane.dispose();
      renderer.dispose();
    },
  };
}

/** The game: tap to start, tap to drop. `onOver` gets each finished game's score. */
export function StackGame({ canPlay, best, onOver }: { canPlay: boolean; best: number; onOver: (score: number, perfects: number) => void }) {
  const t = useTheme();
  const engine = useRef<Engine | null>(null);
  const [phase, setPhase] = useState<Phase>('ready');
  const [score, setScore] = useState(0);
  const [perfect, setPerfect] = useState(false);
  const [record, setRecord] = useState(false);
  const [oldBest, setOldBest] = useState(0);
  const live = useRef({ onOver, best });
  live.current = { onOver, best };

  useEffect(() => engine.current?.setBackground(t.colors.background), [t.colors.background]);
  useEffect(() => () => engine.current?.dispose(), []);

  function onContextCreate(gl: ExpoWebGLRenderingContext) {
    engine.current = createEngine(gl, t.colors.background, {
      onStart: () => {
        setPhase('playing');
        setScore(0);
        setPerfect(false);
      },
      onPlace: (s, p) => {
        setScore(s);
        setPerfect(p);
      },
      onOver: (s, p) => {
        setPhase('over');
        setRecord(s > 0 && s > live.current.best);
        setOldBest(live.current.best);
        live.current.onOver(s, p);
      },
    });
  }

  const card = (lines: { text: string; big?: boolean; testID?: string }[], testID: string) => (
    <View pointerEvents="none" style={{ position: 'absolute', left: 0, right: 0, bottom: '18%', alignItems: 'center' }}>
      <View testID={testID} style={{ backgroundColor: t.colors.surface, borderRadius: t.radius.lg, paddingVertical: t.spacing.md, paddingHorizontal: t.spacing.xl, alignItems: 'center', gap: 4 }}>
        {lines.map((l) => (
          <Text key={l.text} testID={l.testID} style={{ color: l.big ? t.colors.text : t.colors.textMuted, fontSize: l.big ? t.font.large : t.font.body, fontWeight: l.big ? '700' : '400' }}>
            {l.text}
          </Text>
        ))}
      </View>
    </View>
  );

  return (
    <Pressable testID="stage" accessibilityLabel="Drop the block" onPressIn={() => canPlay && engine.current?.tap()} style={{ flex: 1 }}>
      <GLView style={{ flex: 1 }} onContextCreate={onContextCreate} />
      <View pointerEvents="none" style={{ position: 'absolute', top: t.spacing.xl, left: 0, right: 0, alignItems: 'center' }}>
        <Text testID="score" style={{ color: t.colors.text, fontSize: 56, fontWeight: '800' }}>
          {phase === 'ready' ? '' : score}
        </Text>
        {phase === 'playing' && perfect ? (
          <Text testID="perfect" style={{ color: t.colors.textMuted, fontWeight: '700' }}>
            Perfect!
          </Text>
        ) : null}
      </View>
      {!canPlay
        ? card([{ text: 'Viewers can see the scores but not play' }], 'viewer-note')
        : phase === 'ready'
          ? card([{ text: 'Tap to start', big: true }, { text: best ? `Your best: ${best}` : 'Stack the blocks as high as you can' }], 'start')
          : phase === 'over'
            ? card(
                [
                  { text: record ? 'New best!' : 'Game over', big: true, testID: 'over-title' },
                  ...(record && !oldBest ? [] : [{ text: record ? `Your old best was ${oldBest}` : `Your best: ${Math.max(oldBest, score)}` }]),
                  { text: 'Tap to play again' },
                ],
                'game-over',
              )
            : null}
    </Pressable>
  );
}
