#!/bin/sh
# nginx 컨테이너가 시작될 때 실행된다 (/docker-entrypoint.d). 환경변수로 비밀번호(basic auth)를 켠다.
#   FAE_BASIC_AUTH_USER, FAE_BASIC_AUTH_PASSWORD 둘 다 있으면 켜고, 없으면 끈다 (로컬 개발용).
set -e

if [ -n "${FAE_BASIC_AUTH_USER:-}" ] && [ -n "${FAE_BASIC_AUTH_PASSWORD:-}" ]; then
    htpasswd -bcB /etc/nginx/.htpasswd "$FAE_BASIC_AUTH_USER" "$FAE_BASIC_AUTH_PASSWORD" > /dev/null 2>&1
    cat > /etc/nginx/auth.conf <<'EOF'
auth_basic "FAE Agent";
auth_basic_user_file /etc/nginx/.htpasswd;
EOF
    echo "basic auth: 켜짐 (사용자 $FAE_BASIC_AUTH_USER)"
else
    : > /etc/nginx/auth.conf
    echo "basic auth: 꺼짐 (FAE_BASIC_AUTH_USER/PASSWORD가 없음)"
fi
