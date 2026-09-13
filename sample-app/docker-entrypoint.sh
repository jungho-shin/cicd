#!/bin/sh
# 런타임 설정을 만든 뒤 nginx 를 PID 1 로 띄운다.
# APP_ENV 는 Deployment 의 env(overlay 가 dev/prod 로 바꾼다). 루트 파일시스템이 읽기 전용이라 /tmp 에 쓴다.
set -eu

cat > /tmp/config.js <<EOF
window.APP_CONFIG = { env: "${APP_ENV:-unknown}" }
EOF

exec nginx -g 'daemon off;'
