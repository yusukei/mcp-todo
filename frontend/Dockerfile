FROM node:20-alpine AS builder
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci
COPY . .
# Phase 1.5: opt-in design-system preview (`/dev/preview`). Set to "1"
# during the build to bundle the dev preview into a non-production
# image. Defaults to empty so true-production builds tree-shake it.
ARG VITE_DEV_PREVIEW=""
ENV VITE_DEV_PREVIEW=$VITE_DEV_PREVIEW
RUN npm run build

FROM nginx:alpine
RUN addgroup -S app && adduser -S app -G app
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 3000
