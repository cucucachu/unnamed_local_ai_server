import { Text } from 'react-native';
import { createPortal } from 'react-dom';
import { helper } from '../lib/helper';
import secrets from '../../groceries/schema.sql';

const fs = require('fs');

export default function Index() {
  return <Text>{helper()} {String(createPortal)} {String(fs)} {String(secrets)}</Text>;
}
