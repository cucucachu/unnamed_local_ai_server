import { Text } from 'react-native';
import { rpc } from '../lib/linked';
export default function Index() { return <Text>{String(rpc)}</Text>; }
