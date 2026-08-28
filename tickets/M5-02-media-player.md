# M5-02 — MediaPlayer component + files-screen hookup

**Milestone**: M5 · **Size**: M · **Depends on**: M5-01, M3-05 · **Blocks**: M6-03

## Context

Tap a video/audio file in the files screen → it plays, with seek, on web and native — one
wrapper component hiding the platform split (PLAN.md P4-5, `MediaPlayer.tsx`).

## Spec

1. **Media detection** (`lib/media.ts`): `mediaKind(name: string): "video"|"audio"|null` by
   extension — video: `mp4 mov m4v webm mkv`; audio: `mp3 m4a aac wav ogg flac`. Export
   `streamUrl(path)` → `apiBase() + "/api/media/stream?path=" + encodeURIComponent(path)`.
2. **`components/MediaPlayer.tsx`** (native) — `expo-video`'s `VideoView` + `useVideoPlayer`
   for video; `expo-audio` for audio with a minimal transport (play/pause, slider seek bar,
   position/duration text). Add both deps.
   **`components/MediaPlayer.web.tsx`** (web, platform-file resolution) — plain
   `<video controls>` / `<audio controls>` DOM elements (native browser controls give
   seek/scrub for free). Shared props: `{ path: string, kind: "video"|"audio" }`.
3. **Player screen**: modal route `app/media.tsx` (Expo Router modal presentation), receives
   `path` + `kind` via params, dark background, close button, filename title. Video sized to
   fit width, letterboxed.
4. **Files screen hookup** (M3-05's action sheet): if `mediaKind(entry.name)` is non-null, tap
   opens the media modal directly; "Play" also appears in the action sheet (long-press still
   offers Download/Rename/etc.).
5. **Tests** (jest): `mediaKind` table; `streamUrl` encoding (space, unicode, `#`, `?` in
   names); files-screen tap routing logic (mocked router — media file → media route, other
   file → action sheet).

## Out of scope

Transcoding/unsupported-codec messaging beyond the player's native error state; subtitles;
playlists; background audio; casting.

## Acceptance criteria (Tier A)

- [ ] `npm test` + `npx tsc --noEmit` green; web export builds; caddy image rebuilt.
- [ ] Playwright `scripts/e2e/media_browser_smoke.sh`: seed `test-video.mp4` (M5-01's ffmpeg
      one-liner) into the workspace; via UI navigate Files → tap it → assert a `<video>`
      element appears, `currentTime` advances after play, and setting `currentTime = 5`
      (script-driven seek) results in playback from ~5 s (readyState recovers, no error event).
      Exits 0 on host.

## Tier B (append to docs/HOST-CHECKS.md under M5 — GATE G5)

- [ ] Phone browser: play the video, scrub the timeline, audio file plays too.
- [ ] Expo Go: same file plays with expo-video controls; seek works.
- [ ] PM sign-off line: `G5 passed <date>`.
