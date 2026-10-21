# GL check

Fixture app for the GLView / three tests: a red cube spinning on a black background. After each frame it reads the centre pixel back with `gl.readPixels` and shows it, with the frame count, in a `pixel` line.
