#!/usr/bin/env bash
# Build a debug or release APK of the Home AI host app (Expo prebuild +
# Gradle) without a host Android SDK/JDK and without `eas login`.
#
#   HOMEAI_APK_VARIANT=release EXPO_PUBLIC_API_HOST=http://10.13.13.1 \
#     scripts/build_host_app_android.sh
#
# Maintainer signed/store builds use EAS (`services/frontend/eas.json`
# development profile) and the maintainer's Expo account — this script is
# the local/CI path: throwaway Docker Android image if one can be pulled,
# else a one-command Gradle path when ANDROID_HOME is already set.
#
# Does not commit the APK (*.apk is gitignored). Does not complete
# platform bootstrap. A release build is then copied into every user's
# Personal files as HomeAI.apk (scripts/publish_host_apk.sh, via the live
# platform container); HOMEAI_APK_PUBLISH=0 skips that.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FRONTEND="$ROOT/services/frontend"
OUT_DIR="${HOMEAI_APK_OUT:-$FRONTEND/dist}"
# debug: a dev client that loads JS from Metro (LAN only). release: the JS
# bundle is embedded, signed with the prebuild's debug keystore, so it runs
# with no Metro (e.g. off the LAN over WireGuard). Set EXPO_PUBLIC_API_HOST
# for the server it talks to (it overrides services/frontend/.env).
VARIANT="${HOMEAI_APK_VARIANT:-debug}"
case "$VARIANT" in
  debug) TASK=assembleDebug ;;
  release) TASK=assembleRelease ;;
  *) printf 'error: HOMEAI_APK_VARIANT must be debug or release\n' >&2; exit 1 ;;
esac
# Expo 57's config step needs Node >= 22 (util.parseEnv); v15.0 ships Node 18.
IMAGE="${HOMEAI_ANDROID_IMAGE:-reactnativecommunity/react-native-android:v21.1}"
# Fallback images if the primary cannot be pulled.
FALLBACK_IMAGES=(
  "reactnativecommunity/react-native-android:latest"
  "ghcr.io/cirruslabs/android-sdk:35"
)

log() { printf '%s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

if ! command -v docker >/dev/null 2>&1 && [[ -z "${ANDROID_HOME:-}${ANDROID_SDK_ROOT:-}" ]]; then
  die "need Docker (to pull an Android image) or ANDROID_HOME/ANDROID_SDK_ROOT for a local Gradle build"
fi

if [[ ! -f "$FRONTEND/package.json" ]]; then
  die "frontend not found at $FRONTEND"
fi

cd "$FRONTEND"

if [[ ! -d node_modules ]]; then
  log "npm ci in $FRONTEND"
  npm ci
fi

log "expo prebuild --platform android"
npx expo prebuild --platform android --non-interactive --no-install

if [[ ! -d android ]]; then
  die "expo prebuild did not create services/frontend/android"
fi

assemble() {
  log "gradlew $TASK"
  ./gradlew "$TASK" --no-daemon
}

copy_apk() {
  local apk
  apk="$(find "android/app/build/outputs/apk/$VARIANT" -name '*.apk' | head -n 1 || true)"
  if [[ -z "$apk" ]]; then
    die "gradle finished but no APK was under android/app/build/outputs/apk/$VARIANT"
  fi
  mkdir -p "$OUT_DIR"
  local dest="$OUT_DIR/homeai-host-$VARIANT.apk"
  cp "$apk" "$dest"
  log "APK: $dest"
  log "(gitignored; do not commit)"
  # A debug APK needs Metro on the LAN, so only release builds are shared.
  if [[ "$VARIANT" == release && "${HOMEAI_APK_PUBLISH:-1}" != 0 ]]; then
    "$ROOT/scripts/publish_host_apk.sh" "$dest" \
      || log "warning: could not copy the APK into users' Personal files (is the stack up?); retry with scripts/publish_host_apk.sh"
  fi
}

if [[ -n "${ANDROID_HOME:-${ANDROID_SDK_ROOT:-}}" && -z "${HOMEAI_FORCE_DOCKER:-}" ]]; then
  log "using host Android SDK at ${ANDROID_HOME:-$ANDROID_SDK_ROOT}"
  (cd android && assemble)
  copy_apk
  exit 0
fi

pull_image() {
  local img="$1"
  log "docker pull $img"
  if docker pull "$img"; then
    return 0
  fi
  return 1
}

CHOSEN=""
if pull_image "$IMAGE"; then
  CHOSEN="$IMAGE"
else
  log "primary image $IMAGE could not be pulled"
  for img in "${FALLBACK_IMAGES[@]}"; do
    [[ "$img" == "$IMAGE" ]] && continue
    if pull_image "$img"; then
      CHOSEN="$img"
      break
    fi
  done
fi

if [[ -z "$CHOSEN" ]]; then
  die "could not pull an Android build image (tried $IMAGE ${FALLBACK_IMAGES[*]}). Network/registry blocked. Prebuild is in services/frontend/android (gitignored). With a local SDK: ANDROID_HOME=... $0. Maintainer store builds: eas build --profile development --platform android (eas.json; do not eas login from an agent)."
fi

log "gradle in $CHOSEN"
# Mount the frontend so Gradle sees the prebuild tree. Network is required
# for Maven/Google artifact download inside the image.
ENV_ARGS=()
if [[ -n "${EXPO_PUBLIC_API_HOST:-}" ]]; then
  ENV_ARGS+=(-e "EXPO_PUBLIC_API_HOST=$EXPO_PUBLIC_API_HOST")
fi
# As the invoking user, so the prebuild tree stays deletable; caches live in
# the gitignored android/ dir and persist across builds. The whole repo is
# mounted: Metro resolves @homeai/sdk from packages/homeai-sdk.
mkdir -p android/.home android/.gradle-home
APP_ANDROID=/repo/services/frontend/android
docker run --rm \
  --user "$(id -u):$(id -g)" \
  -e HOME="$APP_ANDROID/.home" \
  -e GRADLE_USER_HOME="$APP_ANDROID/.gradle-home" \
  "${ENV_ARGS[@]}" \
  -v "$ROOT:/repo" \
  -w "$APP_ANDROID" \
  "$CHOSEN" \
  ./gradlew "$TASK" --no-daemon

copy_apk
