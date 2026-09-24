# 베이스 이미지는 Nexus docker-group(익명 pull, Docker Hub 프록시)으로 받는다.
# 클러스터 밖에서 빌드할 때는 --build-arg REGISTRY=docker.io 로 바꾼다.
ARG REGISTRY=nexus-docker-group.example.com
FROM ${REGISTRY}/library/python:3.12-slim

# 루트 파일시스템이 읽기 전용이라 .pyc 를 쓰지 않게 한다(써도 실패만 하고 무시되지만 조용히 둔다)
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY app ./app

# CI 가 이미지 태그를 넘긴다(Jenkinsfile 의 --build-arg). GET / 의 version 에 표시된다
ARG APP_VERSION=local
ENV APP_VERSION=${APP_VERSION}

# manifests/python-api/base/deployment.yaml 의 runAsUser 와 맞춘다
USER 10001
EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
