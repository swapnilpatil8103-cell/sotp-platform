# Build from apps/web:  docker build -t sotp-web .
# No secret or backend URL is baked into the image: API_BASE_URL (where this server
# reaches the backend) and SESSION_COOKIE_SECURE are read at runtime.
FROM node:20-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:20-slim AS run
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1
COPY package.json package-lock.json ./
RUN npm ci --omit=dev
COPY --chown=node:node --from=build /app/.next ./.next
COPY next.config.js ./
USER node
EXPOSE 3000
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD node -e "fetch('http://localhost:3000/login').then(r => process.exit(r.ok ? 0 : 1)).catch(() => process.exit(1))"
# -H 0.0.0.0: listen on every interface so the published port reaches the server.
CMD ["npm", "start", "--", "-H", "0.0.0.0"]
