import { Text } from 'react-native';
import { useQuery } from '@homeai/sdk';

export default function Index() {
  const { data } = useQuery('SELECT id, text FROM greetings ORDER BY id', []);
  return <Text>{data?.[0]?.text ?? 'Hello'}</Text>;
}
