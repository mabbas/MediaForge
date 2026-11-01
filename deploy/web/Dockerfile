# ==============================================================================
# MediaForge / GrabItDown — Next.js Web Administration Portal Dockerfile
# Production multi-stage container running Next.js 15.5.28 on Node.js 24 LTS
# Non-root user, reproducible npm ci, minimal runtime footprint.
# ==============================================================================

# Stage 1: Dependency installation
FROM node:24-alpine AS deps
WORKDIR /app
RUN apk add --no-cache libc6-compat
COPY web/package.json web/package-lock.json ./
RUN npm ci

# Stage 2: Application build
FROM node:24-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY web/ ./
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1
RUN npm run build

# Stage 3: Minimal production runtime
FROM node:24-alpine AS runner
WORKDIR /app

ENV NODE_ENV=production \
    PORT=3000 \
    HOSTNAME="0.0.0.0" \
    NEXT_TELEMETRY_DISABLED=1

RUN apk add --no-cache curl && \
    mkdir -p /app/public && \
    chown -R node:node /app/public

# Use non-root node user provided by the base image (UID 1000)
USER node

# Copy built application artifacts with non-root ownership
COPY --from=deps --chown=node:node /app/node_modules ./node_modules
COPY --from=builder --chown=node:node /app/.next ./.next
COPY --chown=node:node web/package.json ./package.json

EXPOSE 3000

HEALTHCHECK --interval=15s --timeout=5s --start-period=15s --retries=3 \
  CMD curl -f http://127.0.0.1:3000/admin/login || exit 1

CMD ["node", "node_modules/next/dist/bin/next", "start", "-p", "3000"]
