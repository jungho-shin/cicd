# 베이스 이미지는 Nexus docker-group(익명 pull, Docker Hub 프록시)으로 받는다.
# Docker Hub 요청 한도를 피하고, 클러스터 밖에서 빌드할 때는 --build-arg REGISTRY=docker.io 로 바꾼다.
ARG REGISTRY=nexus-docker-group.example.com

# ---- build ----
FROM ${REGISTRY}/library/node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
# CI 가 이미지 태그를 넘긴다(.gitlab-ci.yml 의 --build-arg). 화면의 version 에 표시된다
ARG APP_VERSION=local
ENV VITE_APP_VERSION=${APP_VERSION}
RUN npm run build

# ---- runtime ----
# nginx-unprivileged: root 없이 8080 에서 동작하는 공식 nginx 이미지
FROM ${REGISTRY}/nginxinc/nginx-unprivileged:1.28-alpine
COPY nginx.conf /etc/nginx/nginx.conf
COPY docker-entrypoint.sh /app-entrypoint.sh
COPY --from=build /app/dist /usr/share/nginx/html
# manifests/react-app/base/deployment.yaml 의 runAsUser 와 맞춘다
USER 10001
EXPOSE 8080
# sh 로 실행해 파일 실행 권한(Windows 에서 편집하면 사라진다)에 기대지 않는다
ENTRYPOINT ["/bin/sh", "/app-entrypoint.sh"]
