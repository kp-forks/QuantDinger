# QuantDinger Frontend — multi-stage build
# Build context: repository root (see docker-compose.yml)
#
# Stage 1: compile Vue app from QuantDinger-Vue-src
# Stage 2: nginx static + API reverse proxy (nginx.conf)

FROM node:18-alpine AS builder

WORKDIR /app

# Layer cache: deps only
COPY QuantDinger-Vue-src/package.json QuantDinger-Vue-src/package-lock.json ./
RUN npm ci

COPY QuantDinger-Vue-src/ ./

# Match local production build (`npm run build` in QuantDinger-Vue-src)
ENV NODE_OPTIONS=--max_old_space_size=4096
RUN npm run build

# --- runtime ---

FROM nginx:1.25-alpine

# curl: docker-compose healthcheck
RUN apk add --no-cache curl

COPY --from=builder /app/dist /usr/share/nginx/html/
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
