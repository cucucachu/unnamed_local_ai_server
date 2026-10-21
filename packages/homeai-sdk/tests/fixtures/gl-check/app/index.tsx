import { useEffect, useRef, useState } from 'react';
import { Pressable, Text, View } from 'react-native';
import { GLView, type ExpoWebGLRenderingContext } from 'expo-gl';
import * as THREE from 'three';

export default function Cube() {
  const [pixel, setPixel] = useState('none');
  const [taps, setTaps] = useState(0);
  const frame = useRef(0);
  const stop = useRef<number | null>(null);
  useEffect(() => () => {
    if (stop.current != null) cancelAnimationFrame(stop.current);
  }, []);

  function onContextCreate(gl: ExpoWebGLRenderingContext) {
    const renderer = new THREE.WebGLRenderer({ canvas: gl.canvas, context: gl });
    renderer.setClearColor(0x000000, 1);
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(50, 1, 0.1, 100);
    camera.position.z = 3;
    const cube = new THREE.Mesh(new THREE.BoxGeometry(1.4, 1.4, 1.4), new THREE.MeshBasicMaterial({ color: 0xff0000 }));
    scene.add(cube);
    let w = 0;
    let h = 0;
    const out = new Uint8Array(4);
    const tick = () => {
      if (w !== gl.drawingBufferWidth || h !== gl.drawingBufferHeight) {
        w = gl.drawingBufferWidth;
        h = gl.drawingBufferHeight;
        renderer.setSize(w, h, false);
        camera.aspect = w / h;
        camera.updateProjectionMatrix();
      }
      cube.rotation.x += 0.02;
      cube.rotation.y += 0.03;
      renderer.render(scene, camera);
      gl.readPixels(Math.floor(w / 2), Math.floor(h / 2), 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, out);
      gl.endFrameEXP();
      frame.current++;
      if (frame.current % 10 === 1) setPixel(`${out.join(',')} frame ${frame.current} size ${w}x${h}`);
      stop.current = requestAnimationFrame(tick);
    };
    tick();
  }

  return (
    <View style={{ flex: 1 }}>
      <Pressable testID="stage" onPressIn={() => setTaps((n) => n + 1)} style={{ flex: 1 }}>
        <GLView testID="gl" style={{ flex: 1 }} onContextCreate={onContextCreate} />
      </Pressable>
      <Text testID="pixel">{pixel}</Text>
      <Text testID="taps">{taps}</Text>
    </View>
  );
}
