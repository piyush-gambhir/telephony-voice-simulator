FROM node:26.12.1-alpine AS build

RUN apk add --no-cache python3 py3-yaml ffmpeg
RUN npm install --global pnpm@12.15.0

WORKDIR /workspace
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/simulator-backend/package.json apps/simulator-backend/package.json
COPY apps/web-console/package.json apps/web-console/package.json
RUN pnpm install --frozen-lockfile

COPY scripts/ scripts/
COPY LICENSE NOTICE THIRD_PARTY_NOTICES.md ./
COPY apps/simulator-backend/src/ apps/simulator-backend/src/
COPY apps/web-console/ apps/web-console/

ARG NEXT_PUBLIC_SIMULATOR_API_URL=http://127.0.0.1:8978
ENV NEXT_PUBLIC_SIMULATOR_API_URL=$NEXT_PUBLIC_SIMULATOR_API_URL
RUN pnpm --filter web-console build

FROM nginx:1.31-alpine
COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /workspace/apps/web-console/out/ /usr/share/nginx/html/
EXPOSE 8080
