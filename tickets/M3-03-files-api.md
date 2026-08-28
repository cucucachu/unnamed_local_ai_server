# M3-03 — Files REST (list/upload/download/mutations + traversal guard)

**Milestone**: M3 · **Size**: L · **Depends on**: M2-01 · **Blocks**: M3-05, M5-01

## Context

User-facing file management over the same workspace the agent and (later) exec containers see.
Contract fixed in [CONVENTIONS.md §5](./CONVENTIONS.md) "Files"; the traversal guard in §8 is
the security-relevant piece — implement it exactly and share it (media API reuses it in M5-01).

## Spec

1. **`app/core/paths.py`**: `resolve_workspace_path(rel: str) -> Path` per CONVENTIONS §8
   (root from `Settings.workspace_root`, injectable for tests). Reject `rel` containing null
   bytes. Empty string = root.
2. **`app/api/files.py`** implementing exactly the CONVENTIONS §5 Files contract. Notes beyond
   the contract:
   - List: `mime` from `mimetypes.guess_type` (None if unknown); `mtime` UTC ISO-8601; do not
     follow directory symlinks when sizing (use `lstat` for entries; size of a dir = 0).
   - Upload: stream to disk in 1 MiB chunks (`await file.read(1024*1024)` loop) — no full-file
     buffering; target dir must exist (404 otherwise); sanitize each filename with
     `os.path.basename` (no client-controlled paths in the filename part).
   - Download: `FileResponse` with `Content-Disposition: attachment; filename="<name>"`;
     404 if missing or is a dir.
   - Move/copy: `shutil.move` / `shutil.copy2`+`copytree`; parent of `dst` must exist (400);
     `409` if dst exists; moving a dir into itself → 400.
   - Delete: file `unlink`, dir `shutil.rmtree`; deleting the workspace root (`path=""`) → 400.
   - All mutations run in a thread (`anyio.to_thread.run_sync`) — no event-loop blocking on
     big copies.
3. **Tests** (tmp dir as workspace root, httpx ASGI client), ≥ the following:
   - list: empty root; nested listing; sort order (dirs first, case-insensitive).
   - upload single + multiple; overwrite; upload to missing dir → 404; filename with `../` in
     multipart filename lands as basename in the target dir.
   - download file ok; download dir → 404.
   - mkdir nested; move rename semantics; move to existing → 409; copy dir recursive;
     delete file/dir; delete root → 400.
   - **guard suite** per CONVENTIONS §8: `../x`, `/etc/passwd`, `a/../../x`, and a symlink in
     the workspace pointing to `/tmp` (create with `os.symlink`) — all rejected with 400, for
     EVERY endpoint that takes a path (parametrize).

## Out of scope

Media streaming (M5-01); file search; trash/undo; quotas; frontend (M3-05).

## Acceptance criteria (Tier A)

- [ ] Full test suite above green; ruff green.
- [ ] Live smoke through Caddy: upload a file with curl (`-F`), see it via list, `ls` it on the
      host at `/srv/homeai/workspace/`, download it back byte-identical (`cmp`), delete it.
- [ ] Agent-visibility cross-check: with the stack up, drop a file into the host workspace,
      ask the agent over WS to `ls` — the file name appears in a tool result frame (proves the
      "three consumers, one directory" invariant from PLAN.md).

## Tier B

None.
