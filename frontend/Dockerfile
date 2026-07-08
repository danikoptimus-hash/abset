# syntax=docker/dockerfile:1
# Мультистейдж (FRONTEND.md §6): стадия сборки (node) -> статика в
# nginx-образе. Собранный dist/ ничего не знает про backend origin —
# src/api/client.ts обращается по относительным путям ("/api/v1/..."), один
# origin через внешний nginx (docker/nginx.conf.template) — VITE_API_BASE не
# нужен в проде (см. .env.example).

FROM node:22-alpine AS build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
