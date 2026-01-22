FROM node:20-alpine AS ui-build

WORKDIR /app/frontend

COPY frontend/package.json frontend/package-lock.json* ./
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi

COPY frontend/ ./

ARG VITE_API_BASE_URL=/api
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}

RUN npm run build

FROM nginx:1.26-alpine

COPY infra/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=ui-build /app/frontend/dist /app/frontend/dist
COPY backend/static /app/backend/static
