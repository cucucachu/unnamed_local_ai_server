import { getApp } from './apps';
import { joinPath, readTextFile } from './files';
import type { Space } from './platform';

/**
 * App context seeded into an "Ask the agent" thread (M13-04): the instance,
 * the space it is installed in, and the source `app.json` / `AGENT.md` /
 * `schema.sql` when the user can read them. Regular chat threads; no extra
 * thread kind.
 */

export const CONTEXT_FILES = ['app.json', 'AGENT.md', 'schema.sql'] as const;
export type ContextFile = (typeof CONTEXT_FILES)[number];

export const APP_CONTEXT_HEADER = 'App context for this thread';

export type AppContext = {
  instanceId: string;
  space: { id: string; slug: string; name: string; role: Space['role'] };
  appId: string;
  appName: string;
  sourcePath: string | null;
  files: Record<ContextFile, string>;
};

export async function loadAppContext(
  instanceId: string,
  space: Space,
  appId: string,
  appName: string,
): Promise<AppContext> {
  let sourcePath: string | null = null;
  try {
    sourcePath = (await getApp(appId)).source_path;
  } catch {
    // Installed from another space, or the app row is gone: still seed ids.
  }
  const files = { 'app.json': '', 'AGENT.md': '', 'schema.sql': '' };
  if (sourcePath) {
    await Promise.all(
      CONTEXT_FILES.map(async (name) => {
        try {
          files[name] = await readTextFile(joinPath(sourcePath, name));
        } catch {
          files[name] = '';
        }
      }),
    );
  }
  return {
    instanceId,
    space: { id: space.id, slug: space.slug, name: space.name, role: space.role },
    appId,
    appName,
    sourcePath,
    files,
  };
}

export function formatAppContext(ctx: AppContext): string {
  const lines = [
    APP_CONTEXT_HEADER,
    `Instance: ${ctx.instanceId}`,
    `Space: ${ctx.space.slug} (${ctx.space.name}, role ${ctx.space.role ?? 'none'})`,
    `App: ${ctx.appName} (${ctx.appId})`,
  ];
  if (ctx.sourcePath) lines.push(`Source: ${ctx.sourcePath}`);
  for (const name of CONTEXT_FILES) {
    lines.push(`--- ${name} ---`, ctx.files[name] || '(empty or unavailable)');
  }
  return lines.join('\n');
}

/** First user_message on a new app-panel thread: context, then the prompt. */
export function seedUserMessage(ctx: AppContext, prompt: string): string {
  return `${formatAppContext(ctx)}\n---\n${prompt}`;
}

/** Strip the seed prefix so the bubble shows only what the user typed. */
export function displayUserPrompt(text: string): string {
  if (!text.startsWith(APP_CONTEXT_HEADER)) return text;
  const idx = text.lastIndexOf('\n---\n');
  return idx === -1 ? text : text.slice(idx + '\n---\n'.length);
}

export function messageHasAppContext(text: string): boolean {
  return text.startsWith(APP_CONTEXT_HEADER);
}
