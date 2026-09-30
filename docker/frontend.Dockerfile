# 프론트엔드: React를 빌드하고(1단계) nginx로 서빙한다(2단계). 빌드 컨텍스트는 저장소 루트.
FROM node:24-alpine AS build
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM nginx:1.28-alpine
# htpasswd: 시작할 때 환경변수로 비밀번호 파일을 만든다
RUN apk add --no-cache apache2-utils
COPY docker/nginx.conf /etc/nginx/conf.d/default.conf
COPY docker/40-basic-auth.sh /docker-entrypoint.d/40-basic-auth.sh
RUN chmod +x /docker-entrypoint.d/40-basic-auth.sh && touch /etc/nginx/auth.conf
COPY --from=build /web/dist /usr/share/nginx/html
EXPOSE 80
