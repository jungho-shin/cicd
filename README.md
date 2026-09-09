# Bitbucket + Argo CD GitOps CI/CD on Kubernetes

Bitbucket Pipelines 가 **CI**(빌드·테스트·이미지 푸시)를, Argo CD 가 **CD**(클러스터 동기화)를 담당하는
GitOps 구성. 애플리케이션 코드 리포지토리와 매니페스트 리포지토리를 분리한다.

```
[app repo]  bitbucket.org/my-workspace/sample-app
     │  push → Pipelines: build/test → docker push → kustomize edit set image
     ▼
[gitops repo] bitbucket.org/my-workspace/gitops-manifests   ← 이 리포지토리
     │  webhook / polling
     ▼
[Argo CD] argocd 네임스페이스 → 클러스터에 동기화
```

## 디렉터리

```
bootstrap/
  argocd/                     Argo CD 설치 + 설정 (kustomize)
    kustomization.yaml        upstream install.yaml 참조, 버전 고정
    namespace.yaml
    ingress.yaml              UI(HTTP) / CLI(gRPC) 인그레스
    configs/                  ConfigMap/Secret 패치 컴포넌트
      argocd-cm.yaml          url, SSO(dex), kustomize 옵션
      argocd-rbac-cm.yaml     역할/그룹 정책
      argocd-cmd-params-cm.yaml  server.insecure, 튜닝 파라미터
      argocd-secret.yaml      webhook 시크릿
      repo-bitbucket.yaml     Bitbucket 리포지토리 자격증명 (신규 리소스)
      notifications-cm.yaml   배포 결과를 Bitbucket build status 로 회신
      notifications-secret.yaml
  image-updater/              (선택) Argo CD Image Updater
  bitbucket/                  (선택) Bitbucket Data Center 자체 호스팅
    namespace.yaml
    local-pv.yaml             단일 노드용 hostPath PV
    postgres.yaml             PostgreSQL 16 + PVC + Secret
    bitbucket.yaml            Bitbucket StatefulSet + Service + PVC
    ingress.yaml
    nodeport.yaml             DNS 없이 노드 IP 로 접속할 때
  jenkins/                    (선택) Jenkins 컨트롤러 - Pipelines 대체 CI
    namespace.yaml
    local-pv.yaml             단일 노드용 hostPath PV
    rbac.yaml                 에이전트 Pod 생성 권한 (jenkins 네임스페이스 한정)
    casc.yaml                 JCasC 설정 + 플러그인 목록 + Secret
    jenkins.yaml              Jenkins StatefulSet + Service + PVC
    ingress.yaml
    nodeport.yaml
  gitlab/                     (선택) GitLab CE 자체 호스팅 - Bitbucket 대체 (git + CI)
    namespace.yaml
    local-pv.yaml             단일 노드용 hostPath PV (config / data)
    gitlab.yaml               GitLab omnibus StatefulSet + Service + PVC + Secret
    ingress.yaml
    nodeport.yaml
    runner.yaml               GitLab Runner (kubernetes executor) + RBAC
  nexus/                      (선택) Nexus Repository 3 - 컨테이너 레지스트리 + 아티팩트 저장소
    namespace.yaml
    local-pv.yaml             단일 노드용 hostPath PV
    nexus.yaml                Nexus StatefulSet + Service + PVC
    ingress.yaml              UI / docker-hosted / docker-group 인그레스
    nodeport.yaml

apps/
  project.yaml                AppProject: dev / prod
  app-of-apps.yaml            부트스트랩 Application (이것만 apply)
  sample-app.yaml             sample-app dev/prod Application
  applicationset-bitbucket.yaml  (선택) 워크스페이스 리포지토리 자동 등록

manifests/sample-app/
  base/                       Deployment / Service / ServiceAccount
  overlays/dev/               replicas 1, dev 호스트, dev-<sha> 태그
  overlays/prod/              replicas 3, HPA, PDB, 수동 배포

ci/bitbucket-pipelines.yml    앱 리포지토리에 복사해서 사용
```

## 설치 순서

### 0. 사전 준비

- Kubernetes 1.27+ 클러스터, `ingress-nginx`, `cert-manager` (ClusterIssuer `letsencrypt-prod`)
- Bitbucket App Password 2개
  - Argo CD 용: `Repositories: Read`
  - Pipelines 용: `Repositories: Read, Write`
- 컨테이너 레지스트리 자격증명

전체 파일에서 아래 placeholder 를 치환한다.

| placeholder | 의미 |
|---|---|
| `my-workspace` | Bitbucket 워크스페이스 |
| `argocd.example.com` / `grpc.argocd.example.com` | Argo CD 호스트 |
| `my-registry.example.com` | 컨테이너 레지스트리 (Nexus 를 쓰면 `nexus-docker.example.com`) |
| `nexus.example.com` / `nexus-docker.example.com` | Nexus UI / Docker 레지스트리 호스트 |
| `gitlab.example.com` | GitLab 호스트 (Bitbucket 대신 쓸 때) |
| `sample-app.example.com` | 서비스 호스트 |
| `__REPLACE_ME__` | 실제 시크릿 값 (커밋 금지) |

### 1. Argo CD 설치

```bash
# 버전 확인 후 bootstrap/argocd/kustomization.yaml 의 태그 조정
kubectl kustomize --enable-helm bootstrap/argocd | kubectl apply -f -

# 설치 확인
kubectl -n argocd rollout status deploy/argocd-server
kubectl -n argocd get pods
```

### 2. 시크릿 주입

`__REPLACE_ME__` 가 들어간 파일은 **그대로 커밋하지 말 것**. 셋 중 하나를 선택한다.

**A. kubectl 로 직접 생성 (가장 단순)**

```bash
# Bitbucket 리포지토리 자격증명
kubectl -n argocd create secret generic repo-bitbucket-https \
  --from-literal=type=git \
  --from-literal=url=https://bitbucket.org/my-workspace/gitops-manifests.git \
  --from-literal=username='<bitbucket-user>' \
  --from-literal=password='<app-password>'
kubectl -n argocd label secret repo-bitbucket-https argocd.argoproj.io/secret-type=repository

# 레지스트리 pull secret (앱 네임스페이스마다 필요)
kubectl -n sample-app-dev create secret docker-registry regcred \
  --docker-server=my-registry.example.com \
  --docker-username='<user>' --docker-password='<password>'
```

이 경우 `bootstrap/argocd/configs/repo-bitbucket.yaml` 은 `kustomization.yaml` 의 resources 에서 제외한다.

**B. Sealed Secrets** — `kubeseal` 로 암호화한 `SealedSecret` 을 커밋.
**C. External Secrets Operator** — Vault/AWS Secrets Manager 등에서 주입.

### 3. 초기 비밀번호 확인 및 로그인

```bash
kubectl -n argocd get secret argocd-initial-admin-secret \
  -o jsonpath='{.data.password}' | base64 -d; echo

argocd login grpc.argocd.example.com --grpc-web --username admin
argocd account update-password
# 초기 시크릿 삭제
kubectl -n argocd delete secret argocd-initial-admin-secret
```

### 4. CI 전용 계정 토큰 발급

`argocd-cm` 에 `accounts.cicd: apiKey, login` 이 이미 설정돼 있다.

```bash
argocd account generate-token --account cicd --grpc-web
# 출력된 토큰을 Bitbucket Repository variable ARGOCD_AUTH_TOKEN (secured) 에 등록
```

### 5. 애플리케이션 등록

```bash
kubectl apply -f apps/app-of-apps.yaml
# 이후 apps/ 아래 파일을 추가/수정하면 Argo CD 가 자동으로 반영한다.
argocd app list --grpc-web
```

### 6. Bitbucket webhook 연결

Bitbucket 리포지토리 → **Repository settings > Webhooks > Add webhook**

- URL: `https://argocd.example.com/api/webhook`
- Triggers: `Repository push`

Bitbucket Cloud 는 webhook secret 헤더를 지원하지 않으므로 시크릿 검증 없이 동작한다
(엔드포인트는 payload 의 리포지토리 URL 이 등록된 Application 과 일치할 때만 refresh 를 트리거).
Bitbucket Data Center 는 `bootstrap/argocd/configs/argocd-secret.yaml` 의
`webhook.bitbucketserver.secret` 값을 webhook 설정과 동일하게 맞춘다.

webhook 이 없으면 `timeout.reconciliation: 180s` 주기로 폴링된다.

### 7. 앱 리포지토리에 파이프라인 배치

`ci/bitbucket-pipelines.yml` 을 애플리케이션 리포지토리 루트에 `bitbucket-pipelines.yml` 로 복사하고,
Repository variables 와 Deployment environments(`dev`, `production`, 각각 `DEPLOY_ENV`)를 설정한다.

## 이미지 태그 갱신 방식 (택1)

| 방식 | 설명 |
|---|---|
| **Pipelines 커밋** (기본) | CI 가 `kustomize edit set image` 후 gitops 리포지토리에 커밋. 이력이 git 에 남고 롤백이 쉽다. |
| **Argo CD Image Updater** | `bootstrap/image-updater/` 적용. 레지스트리를 폴링해 새 태그를 write-back. CI 가 gitops 권한을 가질 필요가 없다. |

두 방식을 동시에 쓰면 커밋이 충돌하므로 하나만 선택한다.
Image Updater 를 쓸 경우 `apps/sample-app.yaml` 의 `argocd-image-updater.argoproj.io/*` 어노테이션을 유지하고,
그렇지 않으면 제거한다.

## 운영 관련 메모

- **prod 는 수동 동기화**: `apps/sample-app.yaml` 의 `sample-app-prod` 에 `syncPolicy.automated` 가 없다.
  자동화하려면 dev 쪽 블록을 복사하고 `prod` AppProject 의 `syncWindows` 를 확인한다.
- **HA**: 운영 클러스터는 `bootstrap/argocd/kustomization.yaml` 에서 `ha/install.yaml` 로 교체.
- **replicas diff 무시**: HPA 사용 시 `argocd-cm` 의 `resource.customizations.ignoreDifferences.apps_Deployment` 로
  OutOfSync 오탐을 막는다.
- **`[skip ci]`**: gitops 커밋 메시지에 포함해 파이프라인 재귀 실행을 막는다.

## 검증

```bash
kubectl kustomize bootstrap/argocd            > /dev/null
kubectl kustomize manifests/sample-app/overlays/dev
kubectl kustomize manifests/sample-app/overlays/prod
```

## 부록: Bitbucket Data Center 자체 호스팅

Bitbucket Cloud 대신 클러스터 안에 Bitbucket 을 직접 올리는 구성. 폐쇄망이거나
외부에서 클러스터로 webhook 이 들어올 수 없는 환경(NAT 뒤의 로컬 클러스터 등)에서
유용하다. 이 경우 webhook 이 클러스터 내부 통신으로 처리되어 외부 노출이 필요 없다.

### 사전 조건

- **라이선스 필요.** Bitbucket Data Center 는 유료이며, https://my.atlassian.com 에서
  30일 평가 라이선스를 발급받을 수 있다. 라이선스 없이는 설치 마법사를 통과하지 못한다.
- **메모리.** Bitbucket 3Gi + PostgreSQL 1Gi 가 추가로 필요하다. Argo CD 까지 함께
  올린다면 노드 메모리 **16GB 이상**을 권장한다. 8GB 에서는 기동은 되지만 매우 느리다.
- **스토리지.** 동적 프로비저너가 없으면 노드에 디렉터리를 미리 만든다:

```bash
mkdir -p /data/bitbucket /data/postgres
chown -R 2003:2003 /data/bitbucket
chown -R 999:999  /data/postgres
```

### 설치

```bash
# 시크릿 값 채우기 (라이선스, DB 비밀번호, 관리자 비밀번호)
vi bootstrap/bitbucket/postgres.yaml     # POSTGRES_PASSWORD
vi bootstrap/bitbucket/bitbucket.yaml    # SETUP_LICENSE, SETUP_SYSADMIN_PASSWORD

kubectl kustomize bootstrap/bitbucket | kubectl apply -f -

# 최초 기동은 DB 스키마 생성으로 3~5분 걸린다
kubectl -n bitbucket get pods -w
kubectl -n bitbucket logs -f sts/bitbucket
```

접속은 Ingress(`bitbucket.example.com`) 또는 NodePort(`kustomization.yaml` 에서
`nodeport.yaml` 주석 해제 후 `http://<노드IP>:30990`).

### Argo CD 연동 시 달라지는 부분

자체 호스팅이면 리포지토리 URL 이 클러스터 내부 주소가 된다.

```bash
kubectl -n argocd create secret generic repo-bitbucket-https   --from-literal=type=git   --from-literal=url=http://bitbucket.bitbucket.svc.cluster.local:7990/scm/PROJ/gitops-manifests.git   --from-literal=username='<bitbucket-user>'   --from-literal=password='<HTTP access token>'
kubectl -n argocd label secret repo-bitbucket-https argocd.argoproj.io/secret-type=repository
```

- Bitbucket DC 는 App Password 가 아니라 **HTTP access token** 을 쓴다
  (`Profile > Manage account > HTTP access tokens`).
- `apps/` 의 모든 `repoURL` 도 같은 내부 주소로 바꾼다.
- **Webhook 은 클러스터 내부 주소로 설정한다.** Bitbucket 리포지토리
  `Settings > Webhooks` 에서 URL 을
  `http://argocd-server.argocd.svc.cluster.local/api/webhook` 로 지정하면
  외부 노출 없이 동작한다. Cloud 와 달리 DC 는 webhook secret 을 지원하므로
  `configs/argocd-secret.yaml` 의 `webhook.bitbucketserver.secret` 값과 일치시킨다.

### Pipelines 대체

Bitbucket Data Center 에는 Bitbucket Pipelines 가 없다(Cloud 전용 기능).
`ci/bitbucket-pipelines.yml` 대신 다음 중 하나로 CI 를 구성한다.

| 방식 | 설명 |
|---|---|
| **Argo CD Image Updater** | `bootstrap/image-updater/` 적용. CI 없이 레지스트리 폴링만으로 배포까지 이어진다. 가장 간단하다. |
| **Jenkins / GitLab Runner** | 클러스터에 별도 CI 를 올리고 gitops 리포지토리에 커밋. |
| **Argo Workflows / Tekton** | 쿠버네티스 네이티브 CI. Bitbucket webhook 으로 트리거. |

## 부록: Jenkins 자체 호스팅 (Pipelines 대체)

Bitbucket Data Center 에는 Pipelines 가 없으므로 CI 를 따로 올려야 한다.
`bootstrap/jenkins/` 는 설치 마법사 없이 **JCasC(Configuration as Code)** 로 기동하는
Jenkins 컨트롤러 구성이다. 빌드는 컨트롤러가 아니라 kubernetes 플러그인이 띄우는
에이전트 Pod(`kaniko` + `tools`)에서 실행된다.

### 사전 조건

- 메모리 2Gi + 에이전트 Pod 분량이 추가로 필요하다.
- 동적 프로비저너가 없으면 노드에 디렉터리를 미리 만든다:

```bash
mkdir -p /data/jenkins
chown -R 1000:1000 /data/jenkins
```

### 설치

```bash
# 시크릿 값 채우기 (관리자 비밀번호, gitops 토큰, Argo CD 토큰)
vi bootstrap/jenkins/casc.yaml

# 레지스트리 푸시용 자격증명 (kaniko 가 사용)
kubectl -n jenkins create secret docker-registry regcred   --docker-server=my-registry.example.com   --docker-username='<user>' --docker-password='<password>'
# 생성 후 casc.yaml 의 Pod 템플릿 volumes 주석을 해제한다

kubectl kustomize bootstrap/jenkins | kubectl apply -f -

# 최초 기동은 플러그인 다운로드로 1~3분 걸린다
kubectl -n jenkins logs -f sts/jenkins -c install-plugins
kubectl -n jenkins get pods -w
```

접속은 Ingress(`jenkins.example.com`) 또는 NodePort(`kustomization.yaml` 에서
`nodeport.yaml` 주석 해제 후 `http://<노드IP>:30808`).

### 파이프라인 구성

앱 리포지토리 루트에 `Jenkinsfile` 을 두고 `agent { label 'build' }` 로 에이전트를 지정한다.
흐름은 `ci/bitbucket-pipelines.yml` 과 동일하다.

1. `kaniko` 컨테이너에서 이미지 빌드 & 푸시
2. `tools` 컨테이너에서 gitops 리포지토리를 clone → `kustomize edit set image` → 커밋
   (자격증명 ID: `gitops-repo`)
3. 필요하면 `argocd app sync/wait` (자격증명 ID: `argocd-auth-token`)

잡을 코드로 관리하려면 `casc.yaml` 의 `jobs:` 블록(job-dsl) 주석을 해제한다.
Bitbucket webhook URL 은 `http://jenkins.jenkins.svc.cluster.local:8080/bitbucket-scmsource-hook/notify`
(클러스터 내부)로 지정한다.

> Image Updater 와 Jenkins 커밋을 동시에 쓰면 이미지 태그 커밋이 충돌한다. 하나만 선택한다.

## 부록: GitLab CE 자체 호스팅 (Bitbucket 대체)

Bitbucket 대신 GitLab 을 git 호스트로 쓰는 구성. Bitbucket Data Center 와 달리
**라이선스가 필요 없고**(CE 는 무료), CI 가 내장돼 있어 Jenkins 를 따로 올리지 않아도 된다.

```
[app repo]  gitlab.example.com/my-group/sample-app
     │  push → .gitlab-ci.yml: build/test → kaniko push → kustomize edit set image
     ▼
[gitops repo] gitlab.example.com/my-group/gitops-manifests   ← 이 리포지토리
     │  webhook (클러스터 내부)
     ▼
[Argo CD] argocd 네임스페이스 → 클러스터에 동기화
```

`bootstrap/gitlab/` 은 omnibus 이미지(`gitlab/gitlab-ce`) 하나로 PostgreSQL·Redis·
Gitaly·nginx 를 모두 띄우는 단일 Pod 구성이다. 공식 Helm 차트(`gitlab/gitlab`)는
컴포넌트를 20개 넘게 쪼개므로 단일 노드 실습 환경에는 omnibus 가 훨씬 가볍다.

### 사전 조건

- **메모리.** 이 리포지토리에서 가장 무거운 구성요소다. 컨테이너 요청 4Gi / 제한 6Gi 를
  잡았고 이는 `puma['worker_processes'] = 2`, `sidekiq['max_concurrency'] = 9`,
  `prometheus_monitoring` 비활성화를 전제로 한 값이다. Argo CD 와 함께 올린다면
  노드 메모리 **16GB 이상**을 권장한다. 러너 잡 Pod 는 그 위에 추가로 뜬다.
- **스토리지.** `/var/opt/gitlab` 에 git 리포지토리 + DB + 아티팩트가 모두 들어간다.
  동적 프로비저너가 없으면 노드에 디렉터리를 미리 만든다:

```bash
mkdir -p /data/gitlab/config /data/gitlab/data
```

  omnibus 컨테이너는 root 로 동작하며 내부 프로세스 UID 를 스스로 맞추므로
  bitbucket/jenkins/nexus 와 달리 `chown` 이 필요 없다.

### 설치

```bash
# root 초기 비밀번호 채우기
vi bootstrap/gitlab/gitlab.yaml     # GITLAB_ROOT_PASSWORD

kubectl kustomize bootstrap/gitlab | kubectl apply -f -

# 최초 기동은 reconfigure + DB 마이그레이션으로 8~15분 걸린다.
# 그동안 startupProbe 가 실패해도 정상이다(20분까지 기다린다).
kubectl -n gitlab get pods -w
kubectl -n gitlab logs -f sts/gitlab
```

접속은 Ingress(`gitlab.example.com`) 또는 NodePort(`kustomization.yaml` 에서
`nodeport.yaml` 주석 해제 후 `http://<노드IP>:30080`).

> NodePort 로 접속한다면 `gitlab.yaml` 의 `external_url` 도
> `'http://<노드IP>:30080'` 으로 바꾼다. GitLab 은 이 값으로 clone 주소와
> 리디렉션을 만들기 때문에, 실제 접속 주소와 다르면 로그인 후 튕긴다.

초기 계정은 `root` / `GITLAB_ROOT_PASSWORD` 값. 이 값은 **최초 기동(DB 시딩) 때만**
반영되고 이후에는 무시된다. 나중에 바꾸려면:

```bash
kubectl -n gitlab exec -it sts/gitlab -- gitlab-rake "gitlab:password:reset[root]"
```

`GITLAB_ROOT_PASSWORD` 를 지우고 임의 생성 비밀번호를 쓰려면 최초 기동 후 24시간 안에:

```bash
kubectl -n gitlab exec sts/gitlab -- cat /etc/gitlab/initial_root_password
```

### git+ssh

NodePort 30022 로 노출되고, `gitlab_shell_ssh_port = 30022` 가 clone 주소에 반영된다.

```bash
# Profile > SSH Keys 에 공개키 등록 후
git clone ssh://git@<노드IP>:30022/my-group/gitops-manifests.git
```

HTTPS(평문 HTTP) clone 만 쓴다면 `nodeport.yaml` 의 ssh 포트와
`gitlab_shell_ssh_port` 설정은 지워도 된다.

### GitLab Runner 등록

GitLab 은 설치만으로 CI 가 돌지 않는다. 잡을 실행할 러너가 따로 필요하다.
러너는 토큰이 있어야 기동되므로 **GitLab 이 뜬 뒤에** 적용한다.

1. **Admin Area > CI/CD > Runners > New instance runner**
   - Tags: `build`
   - *Run untagged jobs* 체크 (태그 없는 잡도 받게)
   - 발급된 authentication token(`glrt-...`)을 복사
2. 토큰을 채우고 러너를 켠다

```bash
vi bootstrap/gitlab/runner.yaml          # RUNNER_TOKEN
vi bootstrap/gitlab/kustomization.yaml   # - runner.yaml 주석 해제

kubectl kustomize bootstrap/gitlab | kubectl apply -f -
kubectl -n gitlab logs -f deploy/gitlab-runner
```

로그에 `Registering runner... succeeded` 또는 `Starting multi-runner` 가 뜨고
Admin Area 의 러너 목록이 초록색이 되면 성공이다. 잡 Pod 는 `gitlab` 네임스페이스에
`gitlab-runner-job` 서비스 어카운트로 뜬다(권한 없음).

### 앱 리포지토리 `.gitlab-ci.yml`

`ci/bitbucket-pipelines.yml` 과 같은 흐름을 GitLab CI 로 옮긴 예시다.
**Settings > CI/CD > Variables** 에 `DOCKER_USER`, `DOCKER_PASSWORD`,
`GITOPS_TOKEN`, `ARGOCD_AUTH_TOKEN` 을 masked 로 등록한다.

```yaml
stages: [test, build, deploy]

variables:
  DOCKER_REGISTRY: nexus-docker.example.com
  IMAGE: nexus-docker.example.com/my-group/sample-app
  GITOPS_REPO: gitlab.gitlab.svc.cluster.local/my-group/gitops-manifests.git
  GITOPS_USER: gitops-ci

build-test:
  stage: test
  image: gradle:8-jdk21
  script:
    - ./gradlew clean build

docker-build-push:
  stage: build
  image:
    name: gcr.io/kaniko-project/executor:v1.23.2-debug
    entrypoint: [""]
  script:
    - mkdir -p /kaniko/.docker
    - AUTH=$(printf "%s:%s" "$DOCKER_USER" "$DOCKER_PASSWORD" | base64 | tr -d '\n')
    - printf '{"auths":{"%s":{"auth":"%s"}}}' "$DOCKER_REGISTRY" "$AUTH" > /kaniko/.docker/config.json
    - /kaniko/executor --context "$CI_PROJECT_DIR" --dockerfile Dockerfile
        --destination "$IMAGE:$CI_COMMIT_REF_SLUG-$CI_COMMIT_SHORT_SHA"
        --insecure --skip-tls-verify
  rules:
    - if: $CI_COMMIT_BRANCH == "develop" || $CI_COMMIT_BRANCH == "main"

update-gitops:
  stage: deploy
  image: line/kubectl-kustomize:latest
  variables:
    DEPLOY_ENV: dev
  script:
    - git config --global user.email "ci@example.com"
    - git config --global user.name "gitlab-ci"
    - git clone --depth 1 "http://${GITOPS_USER}:${GITOPS_TOKEN}@${GITOPS_REPO}" gitops
    - cd gitops/manifests/sample-app/overlays/${DEPLOY_ENV}
    - kustomize edit set image "my-registry.example.com/my-workspace/sample-app=${IMAGE}:${CI_COMMIT_REF_SLUG}-${CI_COMMIT_SHORT_SHA}"
    - cd $CI_PROJECT_DIR/gitops
    - git add -A
    - git diff --cached --quiet && exit 0
    - git commit -m "chore(${DEPLOY_ENV}): sample-app -> ${CI_COMMIT_SHORT_SHA} [skip ci]"
    - git push origin HEAD:main
  rules:
    - if: $CI_COMMIT_BRANCH == "develop"
```

`main` 브랜치의 prod 배포는 위 잡을 복사해 `DEPLOY_ENV: prod`,
`when: manual` 을 주면 `ci/bitbucket-pipelines.yml` 의 수동 승인과 같아진다.

- `--insecure --skip-tls-verify` 는 Nexus 를 평문 HTTP 로 쓸 때만 필요하다.
- `[skip ci]` 는 gitops 리포지토리에서 파이프라인이 재귀 실행되는 것을 막는다.

### Argo CD 연동

**1. 리포지토리 자격증명** — GitLab 은 App Password 대신 **Deploy token**(읽기 전용,
Argo CD 용)과 **Project/Group access token**(쓰기, CI 용)을 쓴다.

Argo CD 용: 프로젝트 **Settings > Repository > Deploy tokens** 에서
`read_repository` 스코프로 발급.

```bash
kubectl -n argocd create secret generic repo-gitlab-https \
  --from-literal=type=git \
  --from-literal=url=http://gitlab.gitlab.svc.cluster.local/my-group/gitops-manifests.git \
  --from-literal=username='<deploy-token-username>' \
  --from-literal=password='<deploy-token>'
kubectl -n argocd label secret repo-gitlab-https argocd.argoproj.io/secret-type=repository
```

`bootstrap/argocd/configs/repo-bitbucket.yaml` 은 `configs/kustomization.yaml` 의
resources 에서 제외하고, `apps/` 의 모든 `repoURL` 을 위 주소로 바꾼다.

**2. webhook** — GitLab 은 webhook secret 을 지원하므로 Bitbucket Cloud 와 달리
검증을 걸 수 있다. `bootstrap/argocd/configs/argocd-secret.yaml` 에 항목을 추가한다.

```yaml
stringData:
  webhook.gitlab.secret: __REPLACE_ME__
```

리포지토리 **Settings > Webhooks** 에서:

- URL: `http://argocd-server.argocd.svc.cluster.local/api/webhook`
- Secret token: 위와 같은 값
- Trigger: `Push events`

클러스터 내부 주소이므로 외부 노출이 필요 없다. 단, GitLab 의
**Admin Area > Settings > Network > Outbound requests** 에서
*Allow requests to the local network from webhooks* 를 켜야 한다.
(기본값은 차단이라 webhook 이 조용히 실패한다.)

### Bitbucket / Jenkins 와의 관계

셋을 다 올릴 필요는 없다. 조합을 하나 고른다.

| 구성 | git 호스트 | CI | 비고 |
|---|---|---|---|
| **GitLab 단독** | GitLab CE | GitLab CI + Runner | 라이선스 불필요, 컴포넌트 최소. 이 부록의 기본. |
| Bitbucket + Jenkins | Bitbucket DC | Jenkins | Bitbucket DC 라이선스 필요. |
| Bitbucket Cloud | Bitbucket Cloud | Pipelines | `ci/bitbucket-pipelines.yml`. 외부 SaaS. |

GitLab 을 쓴다면 `bootstrap/bitbucket/` 과 `bootstrap/jenkins/` 는 적용하지 않는다.
레지스트리는 GitLab 내장 registry 를 끄고(`registry['enable'] = false`)
`bootstrap/nexus/` 를 쓰도록 해 뒀다. GitLab 내장 registry 를 쓰려면
`external_url` 과 별도의 registry 호스트/인그레스를 추가로 잡아야 한다.

## 부록: Nexus Repository 3 자체 호스팅

컨테이너 레지스트리와 빌드 아티팩트 저장소를 클러스터 안에 두는 구성.
`my-registry.example.com` 같은 외부 레지스트리 대신 쓰거나, 폐쇄망에서
Docker Hub / Maven Central / npmjs 프록시로 쓴다.

```
[Jenkins 에이전트] kaniko  ──push──▶ [Nexus docker-hosted :8082]
                                          │
[쿠버네티스 노드]  image pull ◀───────────┘  (regcred)
[빌드 컨테이너]   maven/npm ──▶ [Nexus maven-group / npm-group :8081]
```

### 사전 조건

- **메모리.** Nexus 힙 1.2Gi + 다이렉트 메모리로 컨테이너 요청 2Gi, 제한 3Gi 를 잡았다.
  Argo CD + Bitbucket + Jenkins 까지 같이 올린다면 노드 메모리 **16GB 이상**을 권장한다.
  더 줄이려면 `bootstrap/nexus/nexus.yaml` 의 `INSTALL4J_ADD_VM_PARAMS` 를 조정한다.
- **스토리지.** 프록시 캐시가 쌓이므로 넉넉히 잡는다(기본 50Gi).
  동적 프로비저너가 없으면 노드에 디렉터리를 미리 만든다:

```bash
mkdir -p /data/nexus
chown -R 200:200 /data/nexus     # nexus 컨테이너 UID
```

### 설치

```bash
kubectl kustomize bootstrap/nexus | kubectl apply -f -

# 최초 기동은 DB 초기화로 2~4분 걸린다
kubectl -n nexus get pods -w
kubectl -n nexus logs -f sts/nexus
```

접속은 Ingress(`nexus.example.com`) 또는 NodePort(`kustomization.yaml` 에서
`nodeport.yaml` 주석 해제 후 `http://<노드IP>:30081`).

초기 계정은 `admin` / `admin123` (`NEXUS_SECURITY_RANDOMPASSWORD: "false"` 로 고정).
로그인 후 **즉시 비밀번호를 바꾼다.** 임의 비밀번호(기본 동작)를 쓰려면 해당 env 를
`"true"` 로 바꾸고 아래로 확인한다.

```bash
kubectl -n nexus exec sts/nexus -- cat /nexus-data/admin.password; echo
```

### 레지스트리 구성 (UI 에서 1회 설정)

**Settings > Repository > Repositories > Create repository**

| 리포지토리 | 타입 | 설정 |
|---|---|---|
| `docker-hosted` | docker (hosted) | HTTP 커넥터 **8082**, Deployment policy `Allow redeploy` |
| `docker-hub` | docker (proxy) | Remote storage `https://registry-1.docker.io`, Docker Index `Use Docker Hub` |
| `docker-group` | docker (group) | HTTP 커넥터 **8083**, 멤버 `docker-hosted`, `docker-hub` |
| `maven-central` | maven2 (proxy) | Remote storage `https://repo1.maven.org/maven2/` |
| `maven-releases` / `maven-snapshots` | maven2 (hosted) | 기본 생성돼 있음 |
| `maven-group` | maven2 (group) | 위 셋을 멤버로 |
| `npm-proxy` | npm (proxy) | Remote storage `https://registry.npmjs.org` |

포트 8082/8083 은 **커넥터를 만들어야 열린다.** Service/NodePort 에는 이미
포트가 뚫려 있으므로 UI 설정만 하면 된다.

`docker login` 을 쓰려면 **Settings > Security > Realms** 에서
`Docker Bearer Token Realm` 을 Active 로 옮긴다. 익명 pull 을 막으려면
**Security > Anonymous Access** 를 끈다.

### TLS 없이 쓸 때 (로컬 클러스터)

Docker/containerd 는 레지스트리에 HTTPS 로 접속한다. NodePort 나 평문 Ingress 를
쓴다면 각 노드에 예외를 등록해야 이미지 pull/push 가 된다.

```bash
# docker 런타임
cat >/etc/docker/daemon.json <<'JSON'
{ "insecure-registries": ["<노드IP>:30082", "<노드IP>:30083"] }
JSON
systemctl restart docker

# containerd (k8s 1.27+ 기본)
mkdir -p /etc/containerd/certs.d/<노드IP>:30082
cat >/etc/containerd/certs.d/<노드IP>:30082/hosts.toml <<'TOML'
server = "http://<노드IP>:30082"
[host."http://<노드IP>:30082"]
  capabilities = ["pull", "resolve", "push"]
  skip_verify = true
TOML
systemctl restart containerd
```

클러스터 내부에서만 쓴다면 `nexus.nexus.svc.cluster.local:8082` 를 그대로 쓸 수 있지만,
이 주소도 평문이므로 위와 같은 예외 등록이 필요하다.

### Jenkins / Argo CD 연동

**1. kaniko push 자격증명** — `bootstrap/jenkins/` 의 `regcred` 를 Nexus 주소로 만든다.

```bash
kubectl -n jenkins create secret docker-registry regcred \
  --docker-server=nexus-docker.example.com \
  --docker-username='<nexus-user>' --docker-password='<password>'
```

`bootstrap/jenkins/casc.yaml` 의 Pod 템플릿 `volumes:` 주석을 해제하면
kaniko 가 `/kaniko/.docker` 로 이 시크릿을 읽는다.

**2. 앱 네임스페이스 pull secret** — 이미지를 내려받을 네임스페이스마다 필요하다.

```bash
kubectl -n sample-app-dev create secret docker-registry regcred \
  --docker-server=nexus-docker.example.com \
  --docker-username='<nexus-user>' --docker-password='<password>'
```

`manifests/sample-app/base/deployment.yaml` 에 `imagePullSecrets: [{name: regcred}]` 를 추가한다.

**3. 이미지 주소 교체** — `my-registry.example.com` 을 쓰는 곳을 모두 바꾼다.

```bash
grep -rl 'my-registry.example.com' apps/ manifests/ ci/ bootstrap/
```

**4. Maven/npm 캐시** — Jenkinsfile 의 빌드 컨테이너에서 Nexus 를 미러로 지정한다.

```xml
<!-- ~/.m2/settings.xml -->
<mirror>
  <id>nexus</id>
  <mirrorOf>*</mirrorOf>
  <url>http://nexus.nexus.svc.cluster.local:8081/repository/maven-group/</url>
</mirror>
```

```bash
npm config set registry http://nexus.nexus.svc.cluster.local:8081/repository/npm-proxy/
```

**5. Image Updater** — `bootstrap/image-updater/` 를 쓴다면 Nexus 자격증명을 등록한다.
Nexus 는 표준 Docker Registry v2 API 를 제공하므로 별도 설정 없이 동작한다.

### 백업

`/nexus-data` 전체가 상태다. blob store 와 내장 DB 가 함께 들어 있으므로
Pod 를 멈춘 뒤 디렉터리를 통째로 복사하는 것이 가장 확실하다.

```bash
kubectl -n nexus scale sts/nexus --replicas=0
tar czf nexus-$(date +%F).tar.gz -C /data nexus
kubectl -n nexus scale sts/nexus --replicas=1
```
