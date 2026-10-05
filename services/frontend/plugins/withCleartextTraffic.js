// Release builds of the host app talk to the server over plain http
// (`EXPO_PUBLIC_API_HOST`, e.g. http://10.13.13.1 through WireGuard), which
// Android blocks unless the manifest allows cleartext. Debug builds already
// allow it (prebuild's debug manifest), so this only changes release.
const { withAndroidManifest } = require('expo/config-plugins');

module.exports = function withCleartextTraffic(config) {
  return withAndroidManifest(config, (mod) => {
    const app = mod.modResults.manifest.application?.[0];
    if (app) app.$['android:usesCleartextTraffic'] = 'true';
    return mod;
  });
};
