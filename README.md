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
| `my-registry.example.com` | 컨테이너 레지스트리 |
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
