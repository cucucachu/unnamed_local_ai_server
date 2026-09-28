# The app builder image (M12-04, docs/PLATFORM.md §7 "Build and verify").
# Not a compose service: code-exec-manager runs it once per build phase
# (`POST /builds/{id}/{compile|smoke}`), like the exec toolbox image, with no
# network, a read-only root, no capabilities, and only the build's own
# source (read-only) and output dirs mounted. So everything a build needs -
# esbuild, TypeScript, the React / React Native typings, jsdom, the SDK
# (packages/homeai-sdk: typings, allowed modules) and its prebuilt runtime -
# is installed here at image build time; nothing is ever fetched during a
# build. Build context: the repo root (build-builder-image.sh).
FROM node:22-alpine

WORKDIR /builder
ENV NODE_ENV=production \
    NODE_NO_WARNINGS=1

COPY services/app-builder/package.json services/app-builder/package-lock.json ./
# react-native is only here for its typings; --ignore-scripts keeps its (and
# every other package's) install hooks from running.
RUN npm ci --omit=dev --ignore-scripts --no-audit --no-fund && npm cache clean --force

# src/sdk.mjs finds it at ../../packages/homeai-sdk, as in the repo.
COPY packages/homeai-sdk/ /packages/homeai-sdk/
COPY services/app-builder/src/ ./src/
COPY services/app-builder/tests/ ./tests/
RUN node src/build-runtime.mjs

# The uid code-exec-manager runs build containers as; the platform makes each
# build's output dirs writable by it and nothing else.
USER 19999:19999
CMD ["node", "src/cli.mjs"]
