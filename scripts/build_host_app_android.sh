#!/usr/bin/env bash
# Build a debug APK of the Home AI host app (Expo prebuild + Gradle) without
# a host Android SDK/JDK and without `eas login`.
#
# Maintainer signed/store builds use EAS (`services/frontend/eas.json`
# development profile) and the maintainer's Expo account — this script is
# the local/CI path: throwaway Docker Android image if one can be pulled,
# else a one-command Gradle path when ANDROID_HOME is already set.
#
# Does not commit the APK (*.apk is gitignored). Does not complete
# platform bootstrap. Does not touch the live compose stack.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FRONTEND="$ROOT/services/frontend"
OUT_DIR="${HOMEAI_APK_OUT:-$FRONTEND/dist}"
IMAGE="${HOMEAI_ANDROID_IMAGE:-reactnativecommunity/react-native-android:v15.0}"
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
  log "gradlew assembleDebug"
  ./gradlew assembleDebug --no-daemon
}

copy_apk() {
  local apk
  apk="$(find android/app/build/outputs/apk -name '*.apk' | head -n 1 || true)"
  if [[ -z "$apk" ]]; then
    die "gradle finished but no APK was under android/app/build/outputs/apk"
  fi
  mkdir -p "$OUT_DIR"
  local dest="$OUT_DIR/homeai-host-debug.apk"
  cp "$apk" "$dest"
  log "APK: $dest"
  log "(gitignored; do not commit)"
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
docker run --rm \
  -v "$FRONTEND:/src" \
  -w /src/android \
  "$CHOSEN" \
  ./gradlew assembleDebug --no-daemon

copy_apk
