// `@homeai/sdk` is `file:../../packages/homeai-sdk` (a symlink in
// node_modules), and Metro only sees files under its watch folders. The
// frontend imports only the package's own sources (`@homeai/sdk/host`,
// dependency-free), so the package's node_modules, if a developer installed
// them for its tests, stay out of the graph, and anything its transpiled
// sources import (Babel helpers) resolves from this app's node_modules.
const path = require('path');
const { getDefaultConfig } = require('expo/metro-config');

const sdk = path.resolve(__dirname, '../../packages/homeai-sdk');
const escape = (s) => s.replace(/[/\\^$.*+?()[\]{}|-]/g, '\\$&');

const config = getDefaultConfig(__dirname);
config.watchFolders = [...(config.watchFolders ?? []), sdk];
config.resolver.nodeModulesPaths = [...(config.resolver.nodeModulesPaths ?? []), path.resolve(__dirname, 'node_modules')];
const blockList = config.resolver.blockList;
config.resolver.blockList = [
  ...(Array.isArray(blockList) ? blockList : blockList ? [blockList] : []),
  new RegExp(`^${escape(path.join(sdk, 'node_modules'))}(/.*)?$`),
];

module.exports = config;
