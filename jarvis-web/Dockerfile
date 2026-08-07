# jarvis-web: SvelteKit HUD (thin client for HA Assist), adapter-node.
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build

FROM node:22-alpine AS runtime
WORKDIR /app
ENV NODE_ENV=production \
    PORT=8199 \
    JARVIS_PIPELINE=Jarvis \
    JARVIS_TTS_VOICE=en_GB-alan-medium
# HA_URL and HA_TOKEN must be provided at runtime (compose env / secrets).
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund
COPY --from=build /app/build ./build
EXPOSE 8199
# busybox wget ships with alpine
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
  CMD wget -qO- http://localhost:8199/healthz || exit 1
CMD ["node", "build"]
