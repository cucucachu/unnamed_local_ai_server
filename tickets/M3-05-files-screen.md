# M3-05 — Files screen (browse + all mutations)

**Milestone**: M3 · **Size**: L · **Depends on**: M3-03, M2-06 · **Blocks**: M3-06, M5-02

## Context

The file-manager UI over the Files REST API: browse, upload, download, rename, move, copy,
delete, new folder. Phone-first layout. Media playback hooks in at M5-02 — leave the tap
behavior extensible.

## Spec

1. **`lib/files.ts`**: typed client for CONVENTIONS §5 Files endpoints. Upload uses `FormData`
   (works on web and native via `fetch`); native file picking via `expo-document-picker`
   (add dependency), web via `<input type="file">` bridged with a `Platform.OS === "web"`
   branch inside one `pickAndUpload(targetDir)` helper in this module.
2. **`app/(tabs)/files.tsx`** — single screen with internal path state (no nested routes;
   breadcrumb-driven):
   - Header: breadcrumb (tappable segments, horizontal scroll), refresh button.
   - Entry list: icon by type (folder / file-by-mime: image, video, audio, text, archive,
     other), name, size (human units) + mtime for files. Tap dir → descend. Tap file → action
     sheet (see 3). Long-press (native) / right-click (web) → same action sheet.
   - Action bar (bottom): "Upload here", "New folder" (name prompt dialog).
   - Empty-dir state and error state (banner + retry).
   - Sort: dirs first then name (server already guarantees; don't re-sort client-side).
3. **File action sheet** (use `@expo/react-native-action-sheet` or a simple custom modal —
   custom modal preferred, zero new deps): Download, Rename, Move, Copy, Delete (confirm).
   - Download: web → `window.open('/api/files/download?path=...')`; native →
     `expo-file-system` `downloadAsync` to cache dir then `expo-sharing` share sheet (add both
     deps). One `downloadFile(path)` helper in `lib/files.ts` hides the branch.
   - Rename: prompt dialog, calls move with same parent.
   - Move/Copy: destination picker = a minimal folder-browser modal (reuses the list component
     with dirs only + "Select this folder" button).
   - All mutations optimistic-refresh: re-fetch the current dir after success; toast on error
     (`ToastAndroid`/web `alert`-free custom toast component `components/Toast.tsx` shared with
     M3-04 — extract it if M3-04 built one).
4. Directory-item component split out as `components/FileList.tsx` (reused by the
   destination picker) — keep props minimal: `entries`, `onPressEntry`, `dirsOnly?`.
5. **Tests** (jest): size-formatting helper; icon-by-mime mapping; `lib/files.ts` calls hit the
   right URLs (mock fetch, table-driven incl. URL-encoding of paths with spaces/unicode);
   FileList renders dirs-first list.

## Out of scope

Media playback (M5-02 — tapping a video shows the normal action sheet for now); multi-select;
drag-drop; previews/thumbnails.

## Acceptance criteria (Tier A)

- [ ] `npm test` + `npx tsc --noEmit` green; web export builds.
- [ ] Playwright script `scripts/e2e/files_browser_smoke.sh`: via UI — create folder `e2e-dir`,
      upload a small file into it, rename it, verify the rename via `GET /api/files` (curl),
      delete folder, verify gone. Exits 0 on host.
- [ ] Paths with spaces and non-ASCII names round-trip through every UI action in the
      playwright script (include one `тест файл.txt`).

## Tier B (append to docs/HOST-CHECKS.md under M3)

- [ ] Phone browser + Expo Go: browse, upload a photo from the phone, download it back, delete.
