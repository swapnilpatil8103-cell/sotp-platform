# Build from apps/web:  docker build -t sotp-web --build-arg NEXT_PUBLIC_API_BASE_URL=http://localhost:8000 .
# NEXT_PUBLIC_* values are inlined into the browser bundle at build time, so they are build args.
FROM node:20-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
ARG NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
ARG NEXT_PUBLIC_API_TOKEN=
ENV NEXT_PUBLIC_API_BASE_URL=$NEXT_PUBLIC_API_BASE_URL \
    NEXT_PUBLIC_API_TOKEN=$NEXT_PUBLIC_API_TOKEN \
    NEXT_TELEMETRY_DISABLED=1
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
