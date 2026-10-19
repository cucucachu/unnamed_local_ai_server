import { useState } from 'react';
import { Text, View } from 'react-native';
import { runExportAction, useQuery, useSpace } from '@homeai/sdk';
import { Button, Select, useTheme } from '@homeai/ui';

type GroceryList = { _instance: string; _space: string };

/**
 * "Add ingredients to the grocery list": the Grocery list's `items` export
 * (homeai.reads in app.json). homeai_exports lists every grocery list this
 * app can see, even an empty one; without any, the view doesn't exist and
 * the query fails, which means "no grocery list".
 */
export function GroceryButton({ lines }: { lines: string[] }) {
  const t = useTheme();
  const space = useSpace();
  const { data } = useQuery<GroceryList>(
    "SELECT _instance, _space FROM homeai_exports WHERE app = 'grocery-list' AND export = 'items' ORDER BY _space",
  );
  const lists = data ?? [];
  const [picked, setPicked] = useState<string | null>(null);
  // _space is a path: /spaces/<slug> for a shared space, /personal for the reader's own.
  const target =
    lists.find((l) => l._instance === picked) ??
    lists.find((l) => space !== null && l._space === `/spaces/${space.slug}`) ??
    lists.find((l) => l._space === '/personal') ??
    lists[0];
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);

  if (!lists.length) {
    return (
      <Text style={{ color: t.colors.textMuted }} testID="grocery-none">
        Add the Grocery list app to send ingredients to it.
      </Text>
    );
  }

  async function send() {
    if (!target) return;
    setBusy(true);
    try {
      for (const name of lines) await runExportAction(target._instance, 'items', 'addItem', { name });
      setMessage(`Added ${lines.length} item${lines.length === 1 ? '' : 's'} to the grocery list.`);
    } catch (e) {
      const code = (e as { code?: string }).code;
      setMessage(code === 'insufficient_role' ? "You can't change that grocery list." : (e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <View style={{ gap: t.spacing.sm }}>
      {lists.length > 1 ? (
        <Select
          label="Grocery list"
          value={target?._instance ?? null}
          options={lists.map((l) => ({ label: l._space === '/personal' ? 'Personal' : l._space.replace('/spaces/', ''), value: l._instance }))}
          onChange={setPicked}
          testID="grocery-pick"
        />
      ) : null}
      <Button
        title="Add ingredients to the grocery list"
        icon="cart-outline"
        variant="secondary"
        onPress={send}
        loading={busy}
        disabled={!lines.length}
        testID="grocery-add"
      />
      {message ? (
        <Text style={{ color: t.colors.textMuted }} testID="grocery-message">
          {message}
        </Text>
      ) : null}
    </View>
  );
}
