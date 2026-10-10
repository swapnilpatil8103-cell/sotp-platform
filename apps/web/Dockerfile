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
COPY --from=build /app/.next ./.next
COPY next.config.js ./
USER node
EXPOSE 3000
CMD ["npm", "start"]
