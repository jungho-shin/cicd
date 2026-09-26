# GitLab + Jenkins + Argo CD GitOps CI/CD on Kubernetes

GitLab 이 **git 호스트**를, Jenkins 가 **CI**(빌드·테스트·이미지 푸시)를, Argo CD 가
**CD**(클러스터 동기화)를 담당하는 GitOps 구성. 애플리케이션 코드 리포지토리와
매니페스트 리포지토리를 분리한다.

```
[app repo]  gitlab.example.com/my-group/react-app   (루트에 Jenkinsfile)
     │  Jenkins Multibranch: npm test/build → kaniko push(Nexus) → kustomize edit set image
     ▼
[gitops repo] gitlab.example.com/my-group/gitops-manifests   ← 이 리포지토리의 apps/, manifests/
     │  webhook / polling
     ▼
[Argo CD] argocd 네임스페이스 → 클러스터에 동기화
```

클러스터(kind) → GitLab → Nexus → Argo CD → Jenkins → 파이프라인 순으로 올린다. 아래
`설치 순서` 표가 전체 흐름이고, 각 장이 그 순서대로 이어진다. Jenkins 대신 GitLab 내장
CI(러너)로 돌리는 구성은 `부록: GitLab CI 로 돌리기` 로 뺐다.

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
      argocd-secret.yaml      webhook 시크릿 (예시, 적용 안 함 — README 6)
      repo-gitlab.yaml        GitLab 리포지토리 자격증명 (예시, 적용 안 함 — README 4.2)
      notifications-cm.yaml   배포 결과를 GitLab commit status 로 회신
      notifications-secret.yaml  (예시, 적용 안 함 — README 4.2)
  coredns/                    *.example.com 을 ingress-nginx 로 보내는 Corefile (1.6)
  metrics-server/             upstream components.yaml + --kubelet-insecure-tls 패치 (1.8)
  image-updater/              (선택) Argo CD Image Updater
  jenkins/                    Jenkins 컨트롤러 (CI) — 5장
    namespace.yaml
    local-pv.yaml             단일 노드용 hostPath PV
    rbac.yaml                 에이전트 Pod 생성 권한 (jenkins 네임스페이스 한정)
    casc.yaml                 JCasC 설정 + 플러그인 목록 (Secret 은 kubectl 로 — 5.2)
    jenkins.yaml              Jenkins StatefulSet + Service + PVC
    ingress.yaml
    nodeport.yaml
  gitlab/                     GitLab CE 자체 호스팅 (git 호스트)
    namespace.yaml
    local-pv.yaml             단일 노드용 hostPath PV (config / data)
    gitlab.yaml               GitLab omnibus StatefulSet + Service + PVC (root 비밀번호 Secret 은 kubectl 로 — 2.2)
    ingress.yaml
    nodeport.yaml
    runner.yaml               (부록) GitLab Runner — 기본 kustomization 에서 제외
  postgres/                   PostgreSQL dev/prod 용 hostPath PV (9장, 나머지는 manifests/postgres)
    local-pv.yaml
  nexus/                      (선택) Nexus Repository 3 - 컨테이너 레지스트리 + 아티팩트 저장소
    namespace.yaml
    local-pv.yaml             단일 노드용 hostPath PV
    nexus.yaml                Nexus StatefulSet + Service + PVC
    ingress.yaml              UI / docker-hosted / docker-group 인그레스
    nodeport.yaml

apps/
  project.yaml                AppProject: dev / prod
  app-of-apps.yaml            부트스트랩 Application (이것만 apply)
  react-app.yaml              react-app dev/prod Application
  python-api.yaml             python-api dev/prod Application (8장)
  postgres.yaml               PostgreSQL dev/prod Application (9장)
  applicationset-gitlab.yaml  (선택) 그룹 리포지토리 자동 등록

manifests/react-app/
  base/                       Deployment / Service / ServiceAccount
  overlays/dev/               replicas 1, dev 호스트, develop-<sha> 태그
  overlays/prod/              replicas 3, HPA, PDB, 수동 배포
manifests/python-api/         두 번째 앱 (8장). replicas 1, HPA·PDB 없음
manifests/postgres/           PostgreSQL (9장). StatefulSet 1개 + headless Service + PVC

pipelines/postgres/           PostgreSQL 버전 변경 잡 (9.5). gitops 리포지토리에 복사해 Jenkins Pipeline 잡이 읽는다

ci/Jenkinsfile                앱 리포지토리 루트에 복사해서 사용 (7장)
ci/.gitlab-ci.yml             (부록) GitLab CI 로 돌릴 때 대신 사용 — 둘 중 하나만

samples/                      배포 테스트용 샘플 앱 (각각 별도 앱 리포지토리로 복사해서 사용)
  react-app/                  앱 리포지토리 템플릿 (React + Vite, nginx 로 서빙) — 7장
    src/                      App.jsx, main.jsx, App.test.jsx
    public/config.js          로컬 개발용 런타임 설정 (컨테이너에서는 /tmp/config.js 로 대체)
    Dockerfile                node 빌드 → nginx-unprivileged, UID 10001
    nginx.conf                8080, /healthz, 읽기 전용 루트 대응(쓰기 경로는 /tmp)
    docker-entrypoint.sh      APP_ENV 로 /tmp/config.js 생성 후 nginx 기동

  python-api/                 두 번째 앱 템플릿 (FastAPI, 메모리 CRUD) — 8장
    app/main.py               /, /healthz, /items CRUD
    tests/test_main.py        pytest
    Dockerfile                python:3.12-slim + uvicorn, UID 10001
    Jenkinsfile               ci/Jenkinsfile 과 같은 흐름, APP_NAME 으로 앱 이름 지정, pytest
```

## 설치 순서

아래 순서대로 진행한다. 각 단계는 앞 단계가 끝나 있어야 한다.

| 단계 | 내용 | 비고 |
|---|---|---|
| 0. 사전 준비 | placeholder 치환, 토큰 종류 확인 | |
| 1. 클러스터 준비 | kind 클러스터 + ingress-nginx + hosts + CoreDNS + metrics-server | 이미 쓰는 클러스터가 있으면 건너뛴다 |
| 2. GitLab CE | git 호스트 + 토큰 발급 | 최초 기동 8~15분. 가장 무겁다 |
| 3. Nexus | 컨테이너 레지스트리 + 이미지 프록시 | 외부 레지스트리를 쓰면 생략 |
| 4. Argo CD | CD + 시크릿 + Application 등록 + CI 용 토큰 | |
| 5. Jenkins | CI 컨트롤러 (JCasC) | 4.4 의 Argo CD 토큰이 필요하다 |
| 6. webhook | GitLab → Argo CD 푸시 알림 | 없으면 180초 폴링 |
| 7. 파이프라인 | 앱 리포지토리에 `Jenkinsfile` 배치 + Jenkins 잡 등록 | 여기까지 하면 push → 배포가 이어진다 |
| 8. 두 번째 앱 | python-api (FastAPI) | 앱을 추가할 때 앱마다 필요한 것만 |
| 9. PostgreSQL | dev/prod DB 를 GitOps 로 배포 + 버전 변경 잡 | Jenkins 에서 드롭다운으로 버전 선택(메이저 포함) |

GitLab CI(러너)로 돌리려면 5 를 건너뛰고 7 대신 `부록: GitLab CI 로 돌리기` 로 간다.

## 0. 사전 준비

- Kubernetes 1.27+ 클러스터와 `ingress-nginx` — 로컬이면 `1. 클러스터 준비` 에서 만든다
- TLS 를 붙인다면 `cert-manager` (ClusterIssuer `letsencrypt-prod`). 로컬 평문 HTTP 라면 필요 없다
- git 호스트(GitLab) — `2. GitLab CE 설치`
- 컨테이너 레지스트리 — `3. Nexus Repository 3 설치` 또는 외부 레지스트리
- GitLab 토큰 2개 (발급은 `2.4 토큰 발급`)
  - Argo CD 용: gitops 프로젝트의 **Deploy token** (`read_repository`)
  - Jenkins 용: 그룹의 **Group access token** (`read_repository` + `write_repository`) —
    앱 리포지토리 체크아웃과 gitops 리포지토리 커밋을 하나로 한다

전체 파일에서 아래 placeholder 를 치환한다.

| placeholder | 의미 |
|---|---|
| `my-group` | GitLab 그룹 |
| `__REPLACE_ME__` | 실제 시크릿 값 (커밋 금지) |

아래 `*.example.com` 호스트는 **치환 대상이 아니라 그대로 쓰는 이름**이다. 클러스터 밖에서는
Windows hosts 파일(1.5), 클러스터 안에서는 CoreDNS rewrite(1.6)로 같은 이름이 풀린다.
실제 도메인으로 옮길 때만 바꾼다.

| 호스트 | 용도 |
|---|---|
| `gitlab.example.com` | GitLab (브라우저, git clone, Argo CD `repoURL`, API) |
| `argocd.example.com` | Argo CD UI·CLI (`:80`, 4.3). `grpc.argocd.example.com` 은 TLS 구성용 |
| `jenkins.example.com` | Jenkins UI (5장) |
| `nexus.example.com` / `nexus-docker.example.com` | Nexus UI / Docker 레지스트리(이미지 주소) |
| `react-app.example.com` / `react-app.dev.example.com` | 서비스 호스트 (7장) |
| `python-api.example.com` / `python-api.dev.example.com` | 서비스 호스트 (8장) |

## 1. 클러스터 준비 (kind on WSL2)

Windows 의 WSL2(Ubuntu) 안에서 네이티브 docker 로 kind 클러스터를 만드는 경우를
기준으로 한다. **NodePort 는 그대로 쓸 수 없다.** kind 노드는 WSL 안의 Docker
컨테이너이고, `extraPortMappings` 로 명시한 포트만 WSL 의 네트워크로 올라오기
때문이다. 각 장의 `nodeport.yaml` 안내는 kind 가 아닌 일반 클러스터
(호스트 자체가 노드인 경우) 기준이다.

kind 에서는 **80/443 만 매핑하고 나머지는 Ingress + hosts 파일로 접속한다.**

WSL2 는 localhost 포워딩이 기본이라, WSL 안에서 publish 한 포트는 Windows 의
`127.0.0.1` 에서 그대로 보인다. 그래서 Windows hosts 파일은 별도 IP 가 아니라
`127.0.0.1` 을 가리키면 된다.

### 1.1 WSL 쪽 사전 준비

```bash
# hostPath PV 가 들어갈 디렉터리. 반드시 WSL 의 ext4(/) 아래여야 한다.
# /mnt/c 는 DrvFs 라 chown/파일 모드가 먹지 않아 Nexus(UID 200) 가 뜨지 못한다.
sudo mkdir -p /data && sudo chown "$USER" /data

# docker 가 WSL 부팅 때 함께 뜨는지 확인 (systemd=true 필요)
systemctl is-enabled docker      # enabled 여야 한다

# 파드가 늘어나면 inotify instance 기본값(128)이 모자랄 수 있다
echo 'fs.inotify.max_user_instances=1024' | sudo tee /etc/sysctl.d/99-kind.conf
sudo sysctl --system
```

메모리는 WSL2 기본값(Windows RAM 의 절반)이면 대개 충분하다. GitLab omnibus 하나가
2~4Gi 를 쓰므로 8Gi 미만이면 `%USERPROFILE%\.wslconfig` 에 다음을 넣고
`wsl --shutdown` 으로 재기동한다.

```ini
[wsl2]
memory=16GB
```

### 1.2 클러스터 생성

클러스터 설정은 리포지토리 루트의 [`kind-config.yaml`](kind-config.yaml) 을 쓴다.
GitLab/Jenkins/Nexus/Argo CD 기준으로 다음이 들어 있다.

- control-plane 에 `ingress-ready=true` 라벨과 80/443 `extraPortMappings`
- 노드 3대 (control-plane 1 + worker 2). worker 한 대에만 `storage-node=true`
  라벨과 WSL 의 `/data` `extraMounts` 를 준다 — 이 노드가 스토리지 노드다
- `bootstrap/*/local-pv.yaml` 의 모든 hostPath PV 가 `nodeAffinity` 로 그 노드에
  고정된다. 세 노드 모두에 `/data` 를 마운트하면 같은 WSL 디렉터리를 공유하게 되어,
  StatefulSet 롤아웃 중 구/신 파드가 서로 다른 워커에서 같은 디렉터리를 동시에
  쓴다. GitLab(PostgreSQL+Gitaly)과 Nexus(내장 DB)는 이를 견디지 못하고,
  hostPath 에서는 `ReadWriteOnce` 도 강제되지 않는다
- `extraMounts` 자체가 없으면 hostPath PV 가 노드 컨테이너 안에만 생겨
  `kind delete cluster` 와 함께 사라진다
- git+ssh(30022)와 Nexus Docker 레지스트리(30082/30083) 포트 매핑
- API 서버를 `127.0.0.1:6443` 으로 고정 — 재생성해도 kubeconfig 주소가 그대로고,
  Windows 쪽 kubectl 에서도 같은 주소로 붙는다
- 노드 이미지를 `kindest/node:v1.34.0@sha256:...` 로 고정 — 생략하면 kind 버전에
  딸린 기본값이 쓰여, kind 를 업그레이드하면 클러스터 Kubernetes 버전이 조용히
  바뀐다. 다른 버전으로 올리려면 이 값을 바꾼다
- Nexus Docker 레지스트리를 containerd 미러로 등록(`containerdConfigPatches`).
  `docker exec` 로 넣은 설정과 달리 클러스터를 다시 만들어도 유지된다

```bash
kind create cluster --name devops --config kind-config.yaml
kubectl get nodes -o wide
```

> 라벨은 `kubeadmConfigPatches` 의 `kubeletExtraArgs` 대신 노드의 `labels`
> 필드로 붙인다. Kubernetes 1.31+ 는 kubeadm 설정이 v1beta4 로 바뀌면서
> `kubeletExtraArgs` 가 맵에서 리스트(`- name: / value:`) 형식이 됐고, 옛 맵
> 형식은 조용히 무시될 수 있다. 생성 후 한 번 확인한다:
> `kubectl get node devops-control-plane -o jsonpath='{.metadata.labels.ingress-ready}'`

### 1.3 ingress-nginx 설치

**kind 전용 매니페스트를 써야 한다.** cloud/baremetal 판은 `LoadBalancer` 서비스만
만들고 hostPort 를 쓰지 않아, kind 에서는 `EXTERNAL-IP <pending>` 인 채로 80 포트가
열리지 않는다.

```bash
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v1.15.1/deploy/static/provider/kind/deploy.yaml

# kind 판에도 nodeSelector 는 없다. control-plane 에만 80/443 매핑이 있으므로
# 컨트롤러를 거기에 고정하지 않으면 worker 에 떠서 접속이 안 된다.
kubectl -n ingress-nginx patch deploy ingress-nginx-controller --type=merge -p '{"spec":{"template":{"spec":{"nodeSelector":{"kubernetes.io/os":"linux","ingress-ready":"true"}}}}}'

# reuseport 끄기 + 워커 수 고정 (아래 설명). 설정 리로드 후 요청 일부가 영영 응답 없이 멈추는 것을 막는다
kubectl -n ingress-nginx patch configmap ingress-nginx-controller --type=merge -p '{"data":{"reuse-port":"false","worker-processes":"4"}}'
kubectl -n ingress-nginx rollout restart deploy/ingress-nginx-controller

kubectl -n ingress-nginx rollout status deploy/ingress-nginx-controller --timeout=180s
kubectl -n ingress-nginx get pods -o wide     # NODE 가 control-plane 이어야 한다
```

- 리포지토리 org 는 `kubernetes/ingress-nginx` 다 (`kubernetes-sigs` 아님).
  `main` 브랜치에는 static 매니페스트가 없으므로 릴리스 태그를 지정한다.
- `ingress-nginx-controller` 서비스가 `LoadBalancer` / `EXTERNAL-IP <pending>` 으로
  남는 것은 정상이다. kind 에 LB 프로바이더가 없을 뿐, 실제 통로는
  hostPort 80/443 → docker 포트 매핑이다.
- **`reuse-port: "false"` / `worker-processes` 고정을 빼면** 설정 리로드(Ingress 를 추가·수정할
  때마다 일어난다) 뒤 요청의 일부가 연결만 되고 응답 없이 멈출 수 있다. 기본값
  `listen 80 reuseport` 는 워커마다 LISTEN 소켓을 따로 만드는데, 리로드로 워커 수가
  줄면(`worker-processes: auto`) 사라진 워커 몫의 소켓이 master 에만 남아 아무도
  `accept` 하지 않는다. 커널은 새 연결을 소켓들에 해시로 나누므로 **같은 요청이 되다
  안 되다 한다**. nginx error 로그도 남지 않고 파드는 계속 Ready 다.
  - 증상: `curl http://argocd.example.com/healthz` 가 가끔 `000`(타임아웃),
    `argocd login` 이 비밀번호 입력 후 멈춤, 브라우저에서 GitLab/Nexus 가 간헐적으로 무응답.
  - 확인: 주인 없는 LISTEN 소켓(워커 PID 없이 master 만 있고 Recv-Q 가 쌓인 줄)이 있으면 이 문제다.
    ```bash
    MASTER=$(pgrep -f 'nginx: master process /usr/bin/nginx')
    sudo nsenter -t "$MASTER" -n ss -ltnp 'sport = :80'
    ```
  - 복구: 위 `patch configmap` + `rollout restart` 를 실행한다. 재시작만 해도 당장은 풀리지만
    다음 리로드에서 재발할 수 있다.

### 1.4 접속 확인

```bash
# WSL 안에서
curl -sS -o /dev/null -w '%{http_code}\n' -H 'Host: nexus.example.com' http://localhost/
```

```powershell
# Windows(PowerShell)에서 — localhost 포워딩이 도는지까지 확인된다
curl.exe -sS -o NUL -w "%{http_code}\n" -H "Host: nexus.example.com" http://127.0.0.1/
```

`200` 또는 `302` 면 클러스터 쪽은 끝이다. `Connection reset` 이면 컨트롤러가
control-plane 이 아닌 노드에 떠 있는 것이고, `404` 면 Host 헤더가 Ingress 규칙과
맞지 않는 것이다. WSL 안에서는 되는데 Windows 에서만 안 되면 localhost 포워딩
문제이므로 `wsl --shutdown` 후 재기동하거나 Windows 쪽에서 80 포트를 이미 쓰는
프로세스가 없는지 본다(`Get-NetTCPConnection -State Listen -LocalPort 80`).

> `docker exec <노드> ss -lntp | grep ':80 '` 이 비어 있어도 정상이다.
> hostPort 는 리스닝 소켓이 아니라 CNI portmap 의 iptables DNAT 으로 동작한다.
> 매핑 확인은 `docker ps --format 'table {{.Names}}\t{{.Ports}}'` 로 한다.

### 1.5 Windows 의 hosts 파일

모든 Ingress 호스트를 `127.0.0.1` 로 보낸다. 관리자 권한으로
`C:\Windows\System32\drivers\etc\hosts` 를 편집한다.

```
127.0.0.1  nexus.example.com
127.0.0.1  nexus-docker.example.com
127.0.0.1  nexus-docker-group.example.com
127.0.0.1  jenkins.example.com
127.0.0.1  gitlab.example.com
127.0.0.1  argocd.example.com
127.0.0.1  grpc.argocd.example.com
127.0.0.1  react-app.dev.example.com
127.0.0.1  react-app.example.com
127.0.0.1  python-api.dev.example.com
127.0.0.1  python-api.example.com
```

WSL 안의 `/etc/hosts` 는 기본적으로 Windows hosts 파일에서 자동 생성되므로 따로
손대지 않아도 된다. 단 **WSL 이 시작될 때 한 번만 생성된다.** 이미 떠 있는 WSL 에는
나중에 추가한 줄이 반영되지 않으므로, kind 를 멈추지 않으려면 `wsl --shutdown` 대신
WSL 의 `/etc/hosts` 에도 같은 줄을 직접 넣는다(다음 WSL 시작 때 Windows 쪽 내용으로
다시 덮어쓰이므로 Windows hosts 에도 반드시 있어야 한다).
`/etc/wsl.conf` 에서 `generateHosts=false` 로 꺼 뒀다면 같은 내용을 직접 넣는다. Windows 에서는 등록 후 `ipconfig /flushdns` 를 실행하고,
크롬은 자체 DNS 캐시가 있어 재시작이 필요할 수 있다.

### 1.6 CoreDNS 에 로컬 호스트명 등록

**클러스터 밖과 안이 이름을 푸는 방식이 다르다.** 이걸 처리하지 않으면 클러스터
안에서 도는 것들이 전부 깨진다 — CI 의 kaniko push, Argo CD 의 `repoURL`,
CI 잡의 `argocd` CLI, Image Updater.

먼저 문제를 확인한다.

```bash
kubectl run dnstest --rm -it --image=busybox:1.36 --restart=Never \
  -- nslookup nexus-docker.example.com
```

`Address: 127.0.0.1` 이 나온다. **이름이 안 풀리는 게 아니라 엉뚱하게 풀리는 것이
문제다.** CoreDNS 는 `example.com` 을 모르니 `forward . /etc/resolv.conf` 로 넘기는데,
이 사슬이 Windows hosts 파일까지 닿는다.

```
파드 → CoreDNS → 노드의 resolv.conf → Docker 내장 DNS(127.0.0.11)
     → WSL 리졸버 → Windows DNS 프록시 → Windows hosts 파일 → 127.0.0.1
```

파드 안에서 `127.0.0.1` 은 그 파드 자신이다. kaniko 가 push 하면 자기 자신에게
붙으려다 `connection refused` 로 죽는다. NXDOMAIN 이면 차라리 원인이 분명한데,
이렇게 잘못된 주소가 돌아오면 진단이 훨씬 어렵다.

`bootstrap/coredns/coredns-cm.yaml` 이 `*.example.com` 을 ingress-nginx 컨트롤러로
보내는 `rewrite` 를 넣은 Corefile 이다. nexus Service 로 직접 보내지 않는 이유는
포트다. 이미지 참조에 포트가 없어 80 으로 붙는데 `nexus` Service 는 8081/8082/8083 만
연다. 컨트롤러는 80 을 열고, `rewrite` 는 DNS 질의만 바꾸고 HTTP Host 헤더는
건드리지 않으므로 기존 Ingress 의 host 라우팅이 그대로 살아난다.

```bash
kubectl apply -f bootstrap/coredns/coredns-cm.yaml
kubectl -n kube-system rollout restart deploy/coredns
kubectl -n kube-system rollout status  deploy/coredns
```

같은 명령으로 확인하면 이번엔 컨트롤러 Service 의 ClusterIP 가 나온다.

```bash
kubectl run dnstest --rm -it --image=busybox:1.36 --restart=Never \
  -- nslookup nexus-docker.example.com
kubectl -n ingress-nginx get svc ingress-nginx-controller   # 위 IP 와 같아야 한다
```

- **ingress-nginx 설치(1.3) 후에 적용한다.** 컨트롤러 Service 가 없으면 rewrite
  대상이 해석되지 않는다.
- Corefile 은 ConfigMap 의 문자열 값이라 부분 병합이 안 되고 통째로 교체된다.
  커밋된 파일은 kind v0.30.0 / k8s v1.34.0 의 원본을 기준으로 한다. 다른 버전으로
  클러스터를 만들었다면 `kubectl -n kube-system get cm coredns -o jsonpath='{.data.Corefile}'`
  로 원본을 확인하고 rewrite 블록만 옮겨 붙인다.
- **이 패치는 `kind delete cluster` 와 함께 사라진다.** 클러스터를 다시 만들면
  1.3 다음에 다시 적용한다.
- 호스트를 추가하려면 그 이름의 Ingress 를 만들고 이 파일에 `rewrite` 한 줄을 더한다.

### 1.7 kind 사용 시 주의

- **`nodeport.yaml` 은 원칙적으로 쓰지 않는다.** kind 노드는 컨테이너라
  `kind-config.yaml` 에 매핑한 포트만 WSL/Windows 로 올라온다. 웹 UI 는 모두
  Ingress 로 접속하고, `jenkins` 의 `nodeport.yaml` 은 주석 처리된 상태가 기본값이다.
  예외는 HTTP 로 뚫을 수 없는 둘이다.
  - `nexus` — **기본 활성.** Docker 레지스트리(30082/30083)가 아래 containerd
    미러의 엔드포인트라 꺼 두면 클러스터 안 이미지 pull 이 실패한다.
  - `gitlab` — git+ssh(30022)를 쓸 때만 주석을 푼다.
- **Docker 레지스트리는 두 가지 경로가 있다.** 사람이 쓰는 웹 UI 와 달리
  레지스트리는 평문 HTTP 라 노드 쪽 설정이 필요하다.
  - 클러스터 안(kubelet 의 이미지 pull): `kind-config.yaml` 의
    `containerdConfigPatches` 가 `nexus-docker.example.com` 을
    노드 로컬 NodePort(30082/30083)로 보낸다. `bootstrap/nexus/kustomization.yaml`
    의 `nodeport.yaml` 이 그래서 기본 활성이다. `docker exec` 로 넣던 설정과 달리
    클러스터를 다시 만들어도 유지된다.
  - 클러스터 밖(WSL/Windows 의 `docker login/push`): `localhost:30082` 를 쓴다.
    Docker 는 `localhost` 를 기본으로 insecure 취급하므로 데몬 설정이 필요 없다.
    `nexus-docker.example.com`(Ingress)으로 쓰고 싶으면 데몬의
    `insecure-registries` 에 등록해야 한다(`3.4 TLS 없이 쓸 때` 참고).
- **hostPath PV 는 스토리지 노드에만 뜬다.** `storage-node=true` 라벨이 붙은
  worker 한 대에만 WSL 의 `/data` 가 마운트돼 있고, `bootstrap/*/local-pv.yaml`
  의 `nodeAffinity` 가 GitLab/Nexus/Jenkins 를 그 노드로 고정한다. 라벨이 붙은
  노드가 없거나 둘 이상이면 파드가 Pending 으로 멈추거나 고정이 무의미해지므로,
  `kind-config.yaml` 을 고칠 때 라벨이 정확히 한 대에만 있는지 확인한다.

  ```bash
  kubectl get nodes -l storage-node=true        # 정확히 1개여야 한다
  kubectl get pv -o custom-columns=NAME:.metadata.name,NODE:.spec.nodeAffinity.required.nodeSelectorTerms[0].matchExpressions[0].values
  ```

  마운트된 `/data` 는 WSL 의 실제 디렉터리이므로 백업은 WSL 에서 직접 받는다
  (`tar czf backup.tar.gz -C / data`). `extraMounts` 가 없는 노드에서는 `/data`
  가 노드 컨테이너 안의 경로일 뿐이고 `kind delete cluster` 와 함께 사라진다.
- **WSL 을 종료하면 노드 컨테이너도 멈춘다.** kind 노드의 재시작 정책은
  `on-failure:1`(실패 시 1회 재시도)이다. `wsl --shutdown` 처럼 데몬째 죽으면 컨테이너도
  비정상 종료로 끝나므로 docker 가 다시 뜰 때 한 번은 같이 올라오지만, 재시도 1회를 이미
  쓴 컨테이너는 그대로 `Exited` 로 남는다. **올라온다는 보장이 없으니** docker 기동 후
  상태를 보고 필요하면 직접 시작해 준다.

  ```bash
  # 먼저 상태 확인 — 일부만 Exited 인 경우가 흔하다
  docker ps -a --filter label=io.x-k8s.kind.cluster=devops --format 'table {{.Names}}	{{.Status}}'

  docker start $(docker ps -aq --filter label=io.x-k8s.kind.cluster=devops)
  kubectl get nodes            # 전부 Ready 가 될 때까지 1~2분
  ```

  세 노드가 한꺼번에 죽는다는 보장이 없다. control-plane 만 `Exited (128)` 이고
  워커는 살아 있는 경우가 있는데, 이때 `kubectl` 은 API 서버에 못 붙고
  Ingress 는 연결 자체가 끊긴다(`curl` 이 404 가 아니라 `Failed to connect`).
  노드가 전부 Ready 가 된 뒤에도 ingress-nginx 컨트롤러가 잠시 `0/1 Running` 인데,
  readiness 프로브를 통과할 때까지 기다린다.

  ```bash
  kubectl -n ingress-nginx rollout status deploy/ingress-nginx-controller --timeout=120s
  curl -sS -o /dev/null -w '%{http_code}
' http://localhost/   # 404 면 복구 완료
  ```

  매번 켜기가 번거로우면 재시작 정책을 바꿔둘 수 있다. 대신 클러스터를 멈춰두려면
  `docker stop` 을 명시적으로 해야 한다.

  ```bash
  docker update --restart=unless-stopped $(docker ps -aq --filter label=io.x-k8s.kind.cluster=devops)
  ```

### 1.8 metrics-server 설치

prod overlay 의 HPA 는 CPU 사용률을 `metrics.k8s.io` API 에서 읽는다. 이 API 는
metrics-server 가 제공하는데 kind 에는 기본으로 들어 있지 않다. 없으면 HPA 가
`FailedGetResourceMetric ... (get pods.metrics.k8s.io)` 경고를 내며 `<unknown>` 으로 남는다.
**kubectl 로 보면 파드는 minReplicas 로 떠 있어 문제없어 보이지만, Argo CD 는 이 HPA 를
Degraded 로 판정한다.** 그래서 앱 Health 가 Degraded 가 되고 CI 의
`argocd app wait --health` 가 실패한다(7.5).

```bash
kubectl apply -k bootstrap/metrics-server
kubectl -n kube-system rollout status deploy/metrics-server

# Ready 후 1분쯤 지나야 값이 나온다
kubectl top nodes
```

- `bootstrap/metrics-server/kustomization.yaml` 은 upstream `components.yaml` 에
  `--kubelet-insecure-tls` 인자 하나만 더한다. kind 노드의 kubelet 인증서는 자체
  서명이고 노드 IP 가 SAN 에 없어서, 이 인자가 없으면 스크랩이 x509 오류로 실패해
  파드가 `0/1 Running` 에서 멈춘다(`kubectl -n kube-system logs deploy/metrics-server`).
  **kind 같은 로컬 클러스터 전용 설정이다.** 운영 클러스터에서는 kubelet 서빙 인증서를
  정식으로 발급(`serverTLSBootstrap`)하고 이 패치를 뺀다.
- 이미지는 `registry.k8s.io` 에서 받는다. ingress-nginx(1.3)와 같은 경로라 그쪽이
  떴다면 따로 할 일은 없다.
- **이 설치는 `kind delete cluster` 와 함께 사라진다.** 클러스터를 다시 만들면 다시 적용한다.

## 2. GitLab CE 설치

이 구성의 git 호스트를 클러스터 안에 올린다. 앱 리포지토리와 gitops 리포지토리가 모두
여기에 있다. CE 에는 CI 도 내장돼 있지만 이 구성의 CI 는 Jenkins(5장)가 맡는다
(GitLab CI 로 돌리려면 `부록: GitLab CI 로 돌리기`).

`bootstrap/gitlab/` 은 omnibus 이미지(`gitlab/gitlab-ce`) 하나로 PostgreSQL·Redis·
Gitaly·nginx 를 모두 띄우는 단일 Pod 구성이다. 공식 Helm 차트(`gitlab/gitlab`)는
컴포넌트를 20개 넘게 쪼개므로 단일 노드 실습 환경에는 omnibus 가 훨씬 가볍다.

레지스트리는 GitLab 내장 registry 를 끄고(`registry['enable'] = false`) `3. Nexus` 를
쓰도록 해 뒀다. 내장 registry 를 쓰려면 `external_url` 과 별도의 registry
호스트/인그레스를 추가로 잡아야 한다.

### 2.1 사전 조건

- **메모리.** 이 리포지토리에서 가장 무거운 구성요소다. 컨테이너 요청 4Gi / 제한 6Gi 를
  잡았고 이는 `puma['worker_processes'] = 2`, `sidekiq['max_concurrency'] = 9`,
  `prometheus_monitoring` 비활성화를 전제로 한 값이다. Argo CD 와 함께 올린다면
  노드 메모리 **16GB 이상**을 권장한다. Jenkins 에이전트 Pod 는 그 위에 추가로 뜬다.
- **스토리지.** `/var/opt/gitlab` 에 git 리포지토리 + DB + 아티팩트가 모두 들어간다.
  동적 프로비저너가 없으면 디렉터리를 미리 만든다. kind 에서는 노드 컨테이너가
  아니라 **WSL 에서** 만든다(`/data` 가 스토리지 노드로 마운트돼 있다):

```bash
mkdir -p /data/gitlab/config /data/gitlab/data
```

  omnibus 컨테이너는 root 로 동작하며 내부 프로세스 UID 를 스스로 맞추므로
  jenkins/nexus 와 달리 `chown` 이 필요 없다.

### 2.2 설치

```bash
# root 초기 비밀번호 Secret. 파일(gitlab.yaml)에는 적지 않는다 — 공개 리포지토리에 커밋된다.
# 8자 이상이고 사전에 없는 조합이어야 한다(아래 경고 참고). 셸 기록에 남지 않게 read 로 받는다
kubectl create namespace gitlab --dry-run=client -o yaml | kubectl apply -f -
read -rsp 'gitlab root password: ' GL_ROOT; echo
[ ${#GL_ROOT} -ge 8 ] && kubectl -n gitlab create secret generic gitlab-secrets \
  --from-literal=GITLAB_ROOT_PASSWORD="$GL_ROOT" --dry-run=client -o yaml | kubectl apply -f -
unset GL_ROOT
kubectl -n gitlab get secret gitlab-secrets

kubectl kustomize bootstrap/gitlab | kubectl apply -f -

# 최초 기동은 reconfigure + DB 마이그레이션으로 8~15분 걸린다.
# 그동안 startupProbe 가 실패해도 정상이다(20분까지 기다린다).
kubectl -n gitlab get pods -w
kubectl -n gitlab logs -f sts/gitlab
```

접속은 Ingress(`http://gitlab.example.com`)로 한다. Windows hosts 파일에
`127.0.0.1 gitlab.example.com` 이 있어야 브라우저에서 열린다(1.7 참고).

```bash
# Ingress 까지 붙었는지 확인. 302 가 정상이다 (미로그인 → /users/sign_in)
curl -sS -o /dev/null -w '%{http_code}\n' -H 'Host: gitlab.example.com' http://localhost/
```

> **kind 에서는 NodePort(`http://<노드IP>:30080`)로 접속할 수 없다.** 노드가
> 컨테이너라 `kind-config.yaml` 에 매핑한 포트(80/443/30022/30082/30083)만
> WSL/Windows 로 올라온다. `nodeport.yaml` 을 주석 해제해도 30080 은 노드
> 컨테이너 안에서만 열린다. GitLab UI 는 Ingress 전용이고, `nodeport.yaml` 은
> git+ssh(30022)가 필요할 때만 쓴다(2.3).
>
> 따라서 `gitlab.yaml` 의 `external_url` 은 `'http://gitlab.example.com'` 을
> **그대로 유지한다.** GitLab 은 이 값으로 clone 주소와 리디렉션을 만들기 때문에
> 실제 접속 주소와 다르면 로그인 후 튕긴다. 이 이름은 클러스터 안에서도 CoreDNS
> rewrite(1.6)로 같은 Ingress 컨트롤러를 가리키므로, webhook payload 의 리포지토리
> URL 과 Argo CD Application 의 `repoURL` 도 자연히 일치한다.
>
> 일반 클러스터(호스트가 곧 노드)라면 NodePort 접속이 가능하고, 그때는
> `external_url` 도 `'http://<노드IP>:30080'` 으로 바꿔야 한다.

초기 계정은 `root` / `GITLAB_ROOT_PASSWORD` 값. 이 값은 **최초 기동(DB 시딩) 때만**
반영되고 이후에는 무시된다.

> **비밀번호가 약하면 컨테이너가 exit 1 로 죽는다.** GitLab 은 취약 비밀번호 사전
> 검사를 하는데, 거부되면 시드(`003_admin.rb`)가 실패하고 `gitlab-ctl reconfigure`
> 전체가 `Infra Phase failed` 로 끝난다. 파드는 `Error` → 재시작을 반복하고, 증상만
> 보면 메모리나 스토리지 문제처럼 보인다. 직전 컨테이너 로그 끝을 확인한다.
>
> ```bash
> kubectl -n gitlab describe pod gitlab-0 | grep -A6 "Last State"   # Exit Code: 1
> kubectl -n gitlab logs gitlab-0 --previous --tail=60
> ```
>
> `--> Password must not contain commonly used combinations of words and letters`
> 가 보이면 이 경우다. `11111111`, `admin123` 은 실제로 거부됐다. 위의 Secret 생성 명령을
> 새 비밀번호로 다시 실행한 뒤 **파드를 직접 지워야** 반영된다 — `envFrom` 으로 읽는 Secret 은
> 값이 바뀌어도 파드를 자동 재시작시키지 않고, Ready 가 아닌 파드는 StatefulSet 롤아웃으로도
> 교체되지 않는다.
>
> ```bash
> # (위 read → create secret ... | kubectl apply 를 다시 실행한 뒤)
> kubectl -n gitlab delete pod gitlab-0
> ```
>
> **주의: 비밀번호를 고쳐 파드가 살아나도 root 계정은 생기지 않는다.** 첫 기동에서
> 시드가 실패하는 동안 DB 스키마 마이그레이션은 이미 끝나 있다. 그래서 새 비밀번호로
> 재기동하면 `reconfigure` 는 성공하고 파드는 Ready 가 되며 Ingress 도 302 를
> 돌려주는데, 시드는 "초기 설치"가 아니므로 다시 돌지 않는다 — root 계정이 없는
> 상태 그대로다. 로그인하면 `Invalid login or password` 만 나오고, 비밀번호를 또
> 바꿔 봐도 `initial_root_password` 는 계정 생성 시점에만 쓰이므로 달라지지 않는다.
>
> 먼저 계정 유무를 확인한다.
>
> ```bash
> kubectl -n gitlab exec sts/gitlab -- gitlab-rails runner 'u = User.find_by(username: "root"); puts u ? "root EXISTS id=#{u.id} state=#{u.state}" : "root MISSING"'
> ```
>
> `root MISSING` 이면 실패했던 시드를 다시 돌린다. 비밀번호는 컨테이너의
> `GITLAB_ROOT_PASSWORD` 를 그대로 읽으므로 따로 넘기지 않는다. 파드가 새 값을
> 들고 있는지만 먼저 확인한다.
>
> ```bash
> kubectl -n gitlab exec sts/gitlab -- printenv GITLAB_ROOT_PASSWORD   # 새 값이어야 한다
> kubectl -n gitlab exec sts/gitlab -- gitlab-rake db:seed_fu
> ```
>
> `root EXISTS` 인데 로그인만 안 되는 경우라면 비밀번호만 재설정하면 된다
> (아래 「나중에 바꾸려면」과 같은 명령이다).
>
> `User.new` + `save!` 로 직접 만들려 하면 GitLab 16 이후 개인 namespace 가
> 필수라 `Namespace can't be blank` 로 실패한다. 시드를 쓰는 이유다.
>
> 시드로도 안 되면 DB 가 어중간하게 시딩된 것이다. 아래로 초기화한다(재기동에
> 다시 8~15분 걸린다).
>
> ```bash
> kubectl -n gitlab delete sts gitlab
> sudo rm -rf /data/gitlab/config/* /data/gitlab/data/*
> kubectl kustomize bootstrap/gitlab | kubectl apply -f -
> ```
>
> 로그인 ID 는 `root` 다(이메일이 아니다). 실패가 10회 누적되면 계정이 10분간
> 잠기는데 이때도 메시지가 같으므로, 비밀번호를 고친 직후에 안 되면 10분 뒤 다시 시도한다.

나중에 바꾸려면:

```bash
kubectl -n gitlab exec -it sts/gitlab -- gitlab-rake "gitlab:password:reset[root]"
```

`GITLAB_ROOT_PASSWORD` 를 지우고 임의 생성 비밀번호를 쓰려면 최초 기동 후 24시간 안에:

```bash
kubectl -n gitlab exec sts/gitlab -- cat /etc/gitlab/initial_root_password
```

> **예전 방식(`gitlab.yaml` 에 비밀번호를 적어 `apply`)으로 설치했다면** 파일에서 Secret 을 뺀 뒤에도
> 클러스터의 `gitlab-secrets` 에 옛 값이 남는다. `apply` 는 매니페스트에서 사라진 리소스를 지우지 않고,
> 옛 값은 `kubectl.kubernetes.io/last-applied-configuration` 어노테이션에도 한 번 더 들어 있다.
> root 비밀번호를 UI 에서 바꿨다면 이 값은 더 이상 쓰이지 않으므로(시딩 때만 사용) 임의 값으로 덮는다.
> `replace` 는 객체 전체를 교체하므로 어노테이션까지 사라진다.
>
> ```bash
> kubectl -n gitlab create secret generic gitlab-secrets \
>   --from-literal=GITLAB_ROOT_PASSWORD="$(openssl rand -base64 24)" --dry-run=client -o yaml \
>   | kubectl replace -f -
> kubectl -n gitlab get secret gitlab-secrets -o jsonpath='{.metadata.annotations}'; echo   # 비어 있어야 한다
> ```
>
> 실행 중인 `gitlab-0` 의 환경변수에는 다음 재시작까지 옛 값이 남는다. 로그인에는 영향이 없으므로
> 급하지 않다면 다음에 파드가 재시작될 때 자연히 사라지게 둔다(재시작에 5~10분 걸린다).

### 2.3 git+ssh

git+ssh 는 HTTP 로 뚫을 수 없어 NodePort 를 쓴다. `bootstrap/gitlab/kustomization.yaml`
의 `nodeport.yaml` 주석을 해제해야 30022 가 열리고, `gitlab_shell_ssh_port = 30022`
가 clone 주소에 반영된다. kind 에서는 `kind-config.yaml` 이 30022 를 매핑해 두었으므로
WSL/Windows 에서 `localhost` 로 붙는다.

```bash
kubectl kustomize bootstrap/gitlab | kubectl apply -f -   # nodeport.yaml 주석 해제 후

# Profile > SSH Keys 에 공개키 등록 후
git clone ssh://git@localhost:30022/my-group/gitops-manifests.git
```

> 일반 클러스터라면 `localhost` 대신 `<노드IP>` 를 쓴다.

HTTPS(평문 HTTP) clone 만 쓴다면 `nodeport.yaml` 의 ssh 포트와
`gitlab_shell_ssh_port` 설정은 지워도 된다.

### 2.4 토큰 발급

Argo CD 와 Jenkins 가 쓸 토큰을 발급해 둔다. GitLab 은 용도별로 토큰이 나뉜다.
먼저 UI 에서 그룹 `my-group` 을 만든다. Group access token 은 그룹만 있으면 발급되고,
Deploy token 은 프로젝트 단위라 4.5-1 에서 gitops 프로젝트를 만든 직후 발급한다.

| 용도 | 종류 | 발급 위치 | 스코프 |
|---|---|---|---|
| Argo CD 가 gitops 리포지토리를 읽기 | Deploy token | gitops 프로젝트 > Settings > Repository > Deploy tokens | `read_repository` |
| Jenkins 가 앱 리포지토리를 체크아웃하고 gitops 리포지토리에 커밋 | Group access token (이름 `ci-bot`, **role: Maintainer**) | 그룹 > Settings > Access tokens | `read_repository`, `write_repository` |
| (선택) ApplicationSet 이 그룹을 스캔 | Group access token | 그룹 > Settings > Access tokens | `read_api`, `read_repository` |

- **Jenkins 토큰을 그룹 단위로 발급하는 이유.** 앱 리포지토리는 Private 이라 익명 체크아웃이
  안 되고(`info/refs` → 401), gitops 프로젝트 전용 토큰으로는 앱 리포지토리를 읽을 수 없다.
  그룹 토큰 하나면 두 리포지토리를 모두 다루고, 앱 리포지토리가 늘어도 그대로 쓴다. 대가는
  권한 범위가 그룹 전체로 넓어지는 것이다.
- **role 은 Maintainer.** gitops 리포지토리의 `main` 이 보호 브랜치라 Developer 로는 push 가
  `pre-receive hook declined` 로 거부된다.
- git 인증에 쓰는 **사용자명은 토큰 이름(`ci-bot`)** 이다. 값은 발급 화면을 벗어나면 다시 볼 수
  없으므로 화면에 보이지 않게 받아 권한 600 파일로 저장한다. `cat > 파일` 로 붙여 넣으면 값이
  터미널에 그대로 찍힌다.

```bash
read -rsp 'ci-bot token: ' T; echo
[[ $T == glpat-* ]] && ( umask 077; printf '%s' "$T" > ~/gitlab-group-token.txt )
unset T
ls -l ~/gitlab-group-token.txt      # 크기만 확인. 내용은 출력하지 않는다
```

값이 터미널·채팅·스크린샷에 한 번이라도 노출됐다면 **Revoke 후 재발급**한다.
발급한 값은 `4.2 시크릿 주입` 에서 Argo CD 에, `5.2 시크릿` 에서 Jenkins 에 들어간다.
토큰이 두 리포지토리에 실제로 통하는지는 두 프로젝트를 모두 만든 뒤 7.2 에서 확인한다.

## 3. Nexus Repository 3 설치

컨테이너 레지스트리와 빌드 아티팩트 저장소를 클러스터 안에 두는 구성.
`my-registry.example.com` 같은 외부 레지스트리 대신 쓰거나, 폐쇄망에서
Docker Hub / Maven Central / npmjs 프록시로 쓴다.

```
[Jenkins 에이전트] kaniko  ──push──▶ [Nexus docker-hosted :8082]
                                          │
[쿠버네티스 노드]  image pull ◀───────────┘  (regcred)
[빌드 컨테이너]   maven/npm ──▶ [Nexus maven-group / npm-group :8081]
```

### 3.1 사전 조건

- **메모리.** Nexus 힙 1.2Gi + 다이렉트 메모리로 컨테이너 요청 2Gi, 제한 3Gi 를 잡았다.
  Argo CD + GitLab + Jenkins 까지 같이 올린다면 노드 메모리 **16GB 이상**을 권장한다.
  더 줄이려면 `bootstrap/nexus/nexus.yaml` 의 `INSTALL4J_ADD_VM_PARAMS` 를 조정한다.
- **스토리지.** 프록시 캐시가 쌓이므로 넉넉히 잡는다(기본 50Gi).
  동적 프로비저너가 없으면 디렉터리를 미리 만든다. kind 에서는 노드 컨테이너가
  아니라 **WSL 에서** 만든다(`/data` 가 스토리지 노드로 마운트돼 있다):

```bash
mkdir -p /data/nexus
sudo chown -R 200:200 /data/nexus     # nexus 컨테이너 UID
```

### 3.2 설치

```bash
kubectl kustomize bootstrap/nexus | kubectl apply -f -

# 최초 기동은 DB 초기화로 2~4분 걸린다
kubectl -n nexus get pods -w
kubectl -n nexus logs -f sts/nexus
```

접속은 Ingress(`nexus.example.com`). kind 에서는 UI 의 NodePort(30081)에 포트
매핑이 없어 클러스터 밖에서 열리지 않는다. `nodeport.yaml` 이 기본 활성인 것은
UI 때문이 아니라 Docker 레지스트리(30082/30083) 때문이다(README 1.7 참고).
일반 클러스터라면 `http://<노드IP>:30081` 로 UI 에 바로 붙는다.

초기 계정은 `admin` / `admin123` (`NEXUS_SECURITY_RANDOMPASSWORD: "false"` 로 고정).
로그인 후 **즉시 비밀번호를 바꾼다.** 임의 비밀번호(기본 동작)를 쓰려면 해당 env 를
`"true"` 로 바꾸고 아래로 확인한다.

```bash
kubectl -n nexus exec sts/nexus -- cat /nexus-data/admin.password; echo
```

### 3.3 레지스트리 구성 (UI 에서 1회 설정)

**Settings > Repository > Repositories > Create repository**

| 리포지토리 | 타입 | 설정 |
|---|---|---|
| `docker-hosted` | docker (hosted) | HTTP 커넥터 **8082**, Deployment policy `Allow redeploy` |
| `docker-hub` | docker (proxy) | Remote storage `https://registry-1.docker.io`, Docker Index `Use Docker Hub` |
| `gcr-proxy` | docker (proxy) | Remote storage `https://gcr.io`, Docker Index `Use proxy registry` |
| `quay-proxy` | docker (proxy) | Remote storage `https://quay.io`, Docker Index `Use proxy registry` |
| `docker-group` | docker (group) | HTTP 커넥터 **8083**, 멤버 `docker-hosted` → `docker-hub` → `gcr-proxy` → `quay-proxy` (이 순서) |
| `maven-central` | maven2 (proxy) | Remote storage `https://repo1.maven.org/maven2/` |
| `maven-releases` / `maven-snapshots` | maven2 (hosted) | 기본 생성돼 있음 |
| `maven-group` | maven2 (group) | 위 셋을 멤버로 |
| `npm-proxy` | npm (proxy) | Remote storage `https://registry.npmjs.org` |

포트 8082/8083 은 **커넥터를 만들어야 열린다.** Service/NodePort 에는 이미
포트가 뚫려 있으므로 UI 설정만 하면 된다.

`gcr-proxy`·`quay-proxy` 는 Jenkins 에이전트 이미지(5.4) 때문에 필요하다. kaniko 는 `gcr.io`,
argocd CLI 는 `quay.io` 에 있다. `docker-group` 의 **멤버 순서를 표대로 둔다.** Docker Hub 에도
`argoproj/argocd` 리포지토리가 있어서(단 `v3.5.2` 태그는 없다) 순서를 바꾸면 엉뚱한 이미지를
받을 수 있다. 프록시를 만들지 않을 거면 `bootstrap/jenkins/casc.yaml` 의 두 이미지를
`gcr.io/...`, `quay.io/...` 직행으로 되돌린다(외부 인터넷 필요).

> UI 대신 REST API 로도 만들 수 있다. 프록시는
> `POST /service/rest/v1/repositories/docker/proxy` (`dockerProxy.indexType=REGISTRY` 가
> *Use proxy registry*), 그룹 멤버 변경은 `GET` 으로 받은 설정의 `group.memberNames` 를 고쳐
> 전체를 `PUT` 한다. 재현성은 API 쪽이 낫다.

`docker login` 을 쓰려면 **Settings > Security > Realms** 에서
`Docker Bearer Token Realm` 을 Active 로 옮긴다.

**익명 pull 은 `docker-group` 에만 허용한다.** Docker Hub 공개 이미지 캐시는
자격증명 없이 받고, `docker-hosted` 에 올린 앱 이미지는 `regcred`(3.5)로 받는다.

1. **Security > Realms** — `Docker Bearer Token Realm` 이 Active 인지 확인
2. **Security > Anonymous Access** — *Allow anonymous users to access the server* 체크
3. **Repositories > docker-group** — *Allow anonymous docker pull* 체크
4. **Repositories > docker-hosted** — *Allow anonymous docker pull* 은 체크하지 않는다

> 그룹은 멤버의 내용을 합쳐 보여주므로, `docker-hosted` 가 멤버로 들어 있으면 앱
> 이미지도 `nexus-docker-group.example.com/...` 으로 익명 pull 된다. 막으려면
> `docker-group` 의 멤버를 `docker-hub` 하나로 줄인다.

확인 — 자격증명 없이 성공해야 한다.

```bash
docker exec devops-worker crictl pull nexus-docker-group.example.com/library/alpine:3.20
# 프록시 경로 — Jenkins 에이전트가 쓰는 이미지
docker exec devops-worker crictl pull nexus-docker-group.example.com/kaniko-project/executor:v1.23.2-debug
docker exec devops-worker crictl pull nexus-docker-group.example.com/argoproj/argocd:v3.5.2
```

설정 전에는 `no basic auth credentials` 로 실패한다. 경로는 정상이고 인증에서 막힌 것이다.

### 3.4 TLS 없이 쓸 때 (로컬 클러스터)

Docker/containerd 는 레지스트리에 HTTPS 로 접속하므로, 평문 레지스트리는 접속하는
주체마다 예외 등록이 필요하다. **주체가 셋인데 방식이 각각 다르다.**

| 주체 | 무엇을 하나 | 주소 | 예외 등록 방법 |
|---|---|---|---|
| 노드의 containerd | 이미지 pull | `nexus-docker.example.com` | `kind-config.yaml` 의 `containerdConfigPatches` |
| kaniko (Jenkins 에이전트 Pod) | 이미지 push | `nexus-docker.example.com` | CoreDNS rewrite(1.6) + kaniko `--insecure` |
| WSL/Windows 의 docker | 수동 login/push | `localhost:30082` | 없음 (Docker 가 localhost 를 기본 insecure 취급) |

> **kind 에서는 아래 `daemon.json` · `certs.d` 절차를 따라 하지 않는다.** 노드가
> 컨테이너라 적용할 대상이 없고, 컨테이너 안에서 고쳐도 `kind delete cluster` 와
> 함께 사라진다. 위 표의 세 경로로 이미 전부 해결돼 있다.

세 경로를 풀어 쓰면 이렇다.

- **노드 pull.** `kind-config.yaml` 의 `containerdConfigPatches` 가
  `nexus-docker.example.com` 을 노드 로컬 NodePort(`http://localhost:30082`)로
  보낸다. DNS 를 타지 않으므로 CoreDNS 와 무관하고, 클러스터를 다시 만들어도
  `kind-config.yaml` 에 들어 있으니 유지된다. `bootstrap/nexus/kustomization.yaml`
  의 `nodeport.yaml` 이 기본 활성인 이유가 이것이다(1.7).
- **kaniko push.** kaniko 는 containerd 를 거치지 않고 직접 레지스트리에 붙는다.
  이름은 CoreDNS rewrite(1.6)가 ingress-nginx 컨트롤러로 보내고, 평문이라
  `--insecure`(또는 `--insecure-registry`)가 필요하다. `ci/Jenkinsfile` 에
  이미 들어 있다.
- **클러스터 밖 수동 push.** `localhost:30082` 를 쓴다. Docker 는 `localhost` 를
  기본으로 insecure 취급하므로 데몬 설정이 필요 없다.

```bash
docker login localhost:30082 -u admin
docker tag myapp:1.0 localhost:30082/myapp:1.0
docker push localhost:30082/myapp:1.0
```

> 태그의 호스트 부분이 레지스트리 주소가 되므로, 같은 이미지를 클러스터에서 pull 할
> 때 쓰는 `nexus-docker.example.com/myapp:1.0` 과 태그가 다르다. 매니페스트에는
> `nexus-docker.example.com/...` 을 쓰고, 수동 push 한 이미지는
> `docker tag localhost:30082/myapp:1.0 nexus-docker.example.com/myapp:1.0` 로
> 다시 태그해 push 한다(같은 Nexus 이므로 레이어는 재전송되지 않는다).

도달 확인:

```bash
# 클러스터 안에서 (CoreDNS + Ingress 경로)
kubectl run curltest --rm -it --image=curlimages/curl --restart=Never -- curl -sS -o /dev/null -w '%{http_code}' http://nexus-docker.example.com/v2/

# 노드의 containerd 경로 (미러 설정)
docker exec devops-worker crictl pull nexus-docker.example.com/<repo>:<tag>
```

`/v2/` 는 인증이 걸려 있으면 `401` 을 돌려준다. `401` 이면 도달은 된 것이다.
`000` 이나 connection refused 면 이름 해석 또는 Ingress 문제다.

#### 일반 클러스터(호스트가 곧 노드)에서는

노드가 실제 호스트이므로 런타임 설정을 직접 넣는다. kind 에는 해당하지 않는다.

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

클러스터 내부 전용 주소(`nexus.nexus.svc.cluster.local:8082`)를 쓸 수도 있지만,
이 구성에서는 쓰지 않는다. 호스트명을 `*.example.com` 하나로 통일해 두면 나중에
실제 레지스트리로 옮길 때 CoreDNS 항목만 빼면 되고, 클러스터 전용 주소를
매니페스트에 박지 않아도 된다.

### 3.5 레지스트리 연동 (CI / 앱 네임스페이스)

**1. CI 의 push 자격증명** — Jenkins 의 `jenkins-secrets` 에 `NEXUS_USER` / `NEXUS_PASSWORD`
로 넣는다(5.2). Jenkinsfile 이 이 값(자격증명 `nexus-registry`)으로 kaniko 의
`/kaniko/.docker/config.json` 을 직접 만든다. `jenkins` 네임스페이스에 `regcred` 는 만들지 않는다(5.2).

**2. 앱 네임스페이스 pull secret** — 이미지를 내려받을 네임스페이스마다 필요하다.

```bash
# 비밀번호가 셸 기록에 남지 않게 read 로 받는다. create ... | apply 형태라 다시 실행해도 된다
read -rp 'nexus user: ' NX_USER; read -rsp 'nexus password: ' NX_PASS; echo
for ns in react-app-dev react-app-prod; do
  kubectl create namespace "$ns" --dry-run=client -o yaml | kubectl apply -f -
  kubectl -n "$ns" create secret docker-registry regcred \
    --docker-server=nexus-docker.example.com \
    --docker-username="$NX_USER" --docker-password="$NX_PASS" \
    --dry-run=client -o yaml | kubectl apply -f -
done
unset NX_USER NX_PASS
kubectl get secret regcred -n react-app-dev; kubectl get secret regcred -n react-app-prod
```

`manifests/react-app/base/deployment.yaml` 에는 `imagePullSecrets: [{name: regcred}]` 가
이미 들어 있다. 시크릿 이름만 `regcred` 로 맞추면 된다. docker-group 만 익명 pull 을 허용하므로(3.3)
docker-hosted 의 앱 이미지는 이 시크릿이 없으면 `ImagePullBackOff` 가 난다.

**3. 이미지 주소** — 매니페스트·CI·Image Updater 는 이미 `nexus-docker.example.com` 으로
맞춰져 있다. 다른 레지스트리로 옮길 때 바꿀 곳은 아래로 찾는다.

```bash
grep -rl 'nexus-docker.example.com' apps/ manifests/ ci/ bootstrap/
```

**4. Maven/npm 캐시** — 빌드 잡의 컨테이너에서 Nexus 를 미러로 지정한다.

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

### 3.6 백업

`/nexus-data` 전체가 상태다. blob store 와 내장 DB 가 함께 들어 있으므로
Pod 를 멈춘 뒤 디렉터리를 통째로 복사하는 것이 가장 확실하다.

```bash
kubectl -n nexus scale sts/nexus --replicas=0
tar czf nexus-$(date +%F).tar.gz -C /data nexus
kubectl -n nexus scale sts/nexus --replicas=1
```

## 4. Argo CD 설치

### 4.1 설치

```bash
# 버전 확인 후 bootstrap/argocd/kustomization.yaml 의 태그 조정
# --server-side 필수 (아래 설명)
kubectl kustomize --enable-helm bootstrap/argocd | kubectl apply --server-side --force-conflicts -f -

# 설치 확인
kubectl -n argocd rollout status deploy/argocd-server
kubectl -n argocd get pods
kubectl get crd applicationsets.argoproj.io
```

> **`--server-side` 를 빼면** `The CustomResourceDefinition "applicationsets.argoproj.io"
> is invalid: metadata.annotations: Too long` 로 ApplicationSet CRD 만 생성되지 않는다.
> 클라이언트 측 apply 는 전체 매니페스트를 `last-applied-configuration` annotation 에
> 저장하는데, 이 CRD 가 annotation 한도(262144 bytes)를 넘기 때문이다. 나머지 리소스는
> 만들어지므로 `argocd-applicationset-controller` 만 CRD 를 못 찾아 재시작을 반복한다.
> 이미 그렇게 설치했다면 위 명령을 다시 실행하면 된다. `--force-conflicts` 는 클라이언트
> 측 apply 로 만든 리소스의 필드 소유권을 넘겨받기 위해 필요하다.
> `Warning: unrecognized format "int64"` 는 무해하다.

**argocd CLI** — 서버와 같은 버전을 WSL 에 설치한다.

```bash
VERSION=v3.5.2
# -f: 404 면 실패로 끝낸다(없으면 "Not Found" 본문이 파일로 저장된다)
curl -fsSL -o /tmp/argocd https://github.com/argoproj/argo-cd/releases/download/${VERSION:?VERSION 을 먼저 설정}/argocd-linux-amd64 \
  && sudo install -m 555 /tmp/argocd /usr/local/bin/argocd && rm /tmp/argocd
argocd version --client
```

> `VERSION=` 줄을 빼먹으면 URL 이 `.../download//argocd-linux-amd64` 가 되어 404 가 난다.
> `-f` 없이 받으면 `Not Found` 라는 텍스트가 설치되어 `argocd: line 1: Not: command not found` 가 뜬다.

WSL 에서 `argocd.example.com` 은 Windows hosts 파일(1.5)로 `127.0.0.1` 로 풀린다.

### 4.2 시크릿 주입

`__REPLACE_ME__` 가 들어간 파일은 **그대로 커밋하지 말 것**. 셋 중 하나를 선택한다.

**A. kubectl 로 직접 생성 (가장 단순)**

```bash
# GitLab 리포지토리 자격증명 (Deploy token, read_repository)
kubectl -n argocd create secret generic repo-gitlab-https \
  --from-literal=type=git \
  --from-literal=url=http://gitlab.example.com/my-group/gitops-manifests.git \
  --from-literal=username='<deploy-token-username>' \
  --from-literal=password='<deploy-token>'
kubectl -n argocd label secret repo-gitlab-https argocd.argoproj.io/secret-type=repository

# 레지스트리 pull secret (앱 네임스페이스마다 필요)
kubectl -n react-app-dev create secret docker-registry regcred \
  --docker-server=nexus-docker.example.com \
  --docker-username='<user>' --docker-password='<password>'
```

`bootstrap/argocd/configs/repo-gitlab.yaml` 은 `configs/kustomization.yaml` 의 resources 에서
**기본 제외**돼 있다. placeholder 인 채로 적용하면 같은 이름의 시크릿이 먼저 생겨 위
`create` 가 `AlreadyExists` 로 실패하고, 이후 `apply` 할 때마다 실제 값을 덮어쓴다.
토큰이 아직 없어도(2.4) 4.1 설치는 먼저 진행해도 된다.

같은 이유로 `configs/argocd-secret.yaml`(webhook 시크릿)과 `configs/notifications-secret.yaml`
(알림용 GitLab 토큰)도 patches 에서 **기본 제외**돼 있다. 이 둘은 install.yaml 에 이미 있는
Secret 이라 `create` 대신 `patch` 로 키만 넣는다. webhook 시크릿은 6장에서 넣는다.

```bash
# (선택) 알림용 GitLab 토큰 — commit status 를 기록할 프로젝트의 access token (api 스코프)
read -rsp 'gitlab api token: ' GL_TOKEN; echo
kubectl -n argocd patch secret argocd-notifications-secret --type merge \
  -p "{\"stringData\":{\"gitlab-token\":\"$GL_TOKEN\"}}"
unset GL_TOKEN
```

> 4.1 을 이 변경 전에 적용했다면 두 Secret 에 `__REPLACE_ME__` 가 이미 들어가 있다.
> 위 `patch` 로 덮어쓰면 된다. patch 로 바꾼 키는 소유자가 `kubectl-patch` 로 넘어가므로,
> 이후 `bootstrap/argocd` 를 `--server-side` 로 다시 적용해도 지워지거나 되돌아가지 않는다.

**B. Sealed Secrets** — `kubeseal` 로 암호화한 `SealedSecret` 을 커밋.
**C. External Secrets Operator** — Vault/AWS Secrets Manager 등에서 주입.

### 4.3 초기 비밀번호 확인 및 로그인

```bash
kubectl -n argocd get secret argocd-initial-admin-secret \
  -o jsonpath='{.data.password}' | base64 -d; echo

# 평문(HTTP) Ingress 로 로그인한다. 네 옵션과 :80 이 모두 필요하다(아래 설명)
argocd login argocd.example.com:80 --grpc-web --plaintext --skip-test-tls --username admin
argocd account update-password
# 초기 시크릿 삭제
kubectl -n argocd delete secret argocd-initial-admin-secret
```

> 옵션을 하나라도 빼면 비밀번호를 묻기도 전에
> `gRPC connection not ready: context deadline exceeded` 로 멈춘다.
>
> - `--skip-test-tls` — `login` 은 로그인 전에 TLS 확인을 하는데, 이 단계는 `--grpc-web` 을
>   따르지 않고 native gRPC(HTTP/2)로 붙는다. 평문 80 에서는 nginx 가 HTTP/2 를 받지 않아
>   여기서 시간 초과가 난다(argo-cd issue #27210, #12359). `login` 에만 있는 옵션이다.
> - `:80` — 포트를 생략하면 CLI 가 443 을 기본으로 붙이는 경우가 있다.
> - `--grpc-web` / `--plaintext` — HTTP/1.1 로, TLS 없이 붙는다. 로그인 후 컨텍스트에
>   저장되므로 4.4·4.5 의 명령에는 `--grpc-web` 만 붙여도 된다.
>
> `grpc.argocd.example.com`(backend-protocol GRPC)은 평문 구성에서는 쓰지 않는다.
> 클라이언트→nginx 구간이 HTTP/2 여야 의미가 있는데, 그러려면 TLS 가 필요하다.

### 4.4 CI 전용 계정 토큰 발급

`argocd-cm` 에 `accounts.cicd: apiKey, login` 이 이미 설정돼 있고, `argocd-rbac-cm` 이 이 계정에
`role:ci`(앱 조회·sync)를 준다. Jenkins 가 `argocd app sync` / `wait` 에 쓴다.

```bash
( umask 077; argocd account generate-token --account cicd --grpc-web > ~/cicd-argocd-token.txt )
ls -l ~/cicd-argocd-token.txt      # 5.2 에서 jenkins-secrets 의 ARGOCD_AUTH_TOKEN 으로 들어간다
```

> 이 토큰은 admin 의 CLI 로그인 세션(4.3)과 별개다. admin 세션은 24시간이면 만료돼
> `token is expired` 가 나고 `argocd login` 을 다시 하면 되지만, `cicd` 토큰은 만료 없이 발급된다.

### 4.5 애플리케이션 등록

Argo CD 는 이 리포지토리가 아니라 **GitLab 의 `my-group/gitops-manifests`** 를 읽는다.
그래서 아래 선행 조건이 먼저 필요하다.

**1. gitops 리포지토리 만들기** — GitLab UI 에서 그룹 `my-group` 과 프로젝트
`gitops-manifests` 를 만든다. *Initialize repository with a README* 는 **끈다**(켜면 첫 push 가 거부된다).
이 리포지토리의 `apps/` 와 `manifests/` 만 복사해 **루트에** 올린다. 이후 CI 가 이미지 태그를
이 리포지토리에 직접 커밋하므로, cicd 리포지토리와는 별개 이력으로 관리한다.
프로젝트를 만들었으면 2.4 의 **Deploy token** 을 이 프로젝트에서 발급한다.

```bash
# WSL 에서 처음 커밋한다면 작성자부터 설정한다(없으면 "Author identity unknown" 으로 실패)
git config --global user.name >/dev/null || git config --global user.name '<name>'
git config --global user.email >/dev/null || git config --global user.email '<email>'

mkdir -p ~/workspace/gitops-manifests && cd ~/workspace/gitops-manifests
git init -b main
cp -r ~/workspace/cicd/apps ~/workspace/cicd/manifests .
git add . && git commit -m "initial: apps, manifests"
git remote add origin http://gitlab.example.com/my-group/gitops-manifests.git
git push -u origin main      # GitLab 사용자명 + 비밀번호(또는 write_repository 토큰)
```

**2. Argo CD 에 리포지토리 자격증명 등록** — 2.4 의 Deploy token(`read_repository`)으로 4.2-A 를 실행한다.
토큰이 셸 기록에 남지 않게 `read` 로 받는다. Deploy token 의 username 은 발급 화면에 나오는
`gitlab+deploy-token-<N>` 형태다(GitLab 로그인 계정이 아니다). 발급 화면을 닫으면 토큰은 다시 볼 수 없다.

```bash
read -rp 'deploy token username: ' DT_USER; read -rsp 'deploy token: ' DT_PASS; echo
kubectl -n argocd create secret generic repo-gitlab-https \
  --from-literal=type=git \
  --from-literal=url=http://gitlab.example.com/my-group/gitops-manifests.git \
  --from-literal=username="$DT_USER" --from-literal=password="$DT_PASS" \
  --dry-run=client -o yaml \
  | kubectl label --local -f - argocd.argoproj.io/secret-type=repository -o yaml \
  | kubectl apply -f -
unset DT_USER DT_PASS

argocd repo list --grpc-web     # STATUS 가 Successful 이어야 한다
```

**3. 앱 네임스페이스 pull secret** — 3.5-2 를 실행한다(dev, prod 두 네임스페이스).

**4. 등록**

```bash
cd ~/workspace/cicd
kubectl apply -f apps/app-of-apps.yaml
# 이후 gitops-manifests 의 apps/ 아래 파일을 추가/수정하면 Argo CD 가 자동으로 반영한다.
argocd app list --grpc-web
```

기대 상태:

| Application | SYNC | HEALTH | 이유 |
|---|---|---|---|
| `bootstrap` | Synced | Healthy | |
| `react-app-dev` | Synced | Degraded / Progressing | 이미지 `dev-0000000` 이 아직 없다. 7장에서 Jenkins 가 첫 태그를 커밋하면 풀린다 |
| `react-app-prod` | OutOfSync | Missing | 수동 동기화 대상(`automated` 없음) |

- `apps/applicationset-gitlab.yaml` 은 `app-of-apps.yaml` 의 `exclude` 로 **기본 제외**된다.
  같은 파일의 placeholder Secret 이 selfHeal 로 실제 토큰을 덮어쓰기 때문이다.
- `argocd repo list` 가 실패하면 Application 이 `ComparisonError` / `repository not found` 가 된다.
  Deploy token 의 스코프(`read_repository`)와 URL 끝의 `.git` 을 확인한다.

## 5. Jenkins 설치

`bootstrap/jenkins/` 는 설치 마법사 없이 **JCasC(Configuration as Code)** 로 기동하는
Jenkins 컨트롤러 구성이다. 관리자 계정·자격증명·kubernetes 클라우드·에이전트 Pod 템플릿이
모두 `casc.yaml` 에 들어 있다. 컨트롤러는 빌드를 돌리지 않고(`numExecutors: 0`), 빌드마다
kubernetes 플러그인이 `jenkins` 네임스페이스에 에이전트 Pod 를 띄운다(5.4).

Jenkins 는 4장까지 끝난 뒤에 올린다. 시크릿에 Argo CD `cicd` 토큰(4.4)과 GitLab 그룹 토큰(2.4)이
들어가고, 에이전트 이미지는 Nexus 프록시(3.3)로 받는다.

### 5.1 사전 조건

1. **메모리** — 컨트롤러가 요청 1Gi / 제한 2Gi, 여기에 빌드 중에만 에이전트 Pod 분량이 더 붙는다.
   GitLab + Nexus + Argo CD 와 같이 돌리므로 노드 메모리 16GB 이상을 권장한다.

2. **스토리지 디렉터리** — 동적 프로비저너가 없으면 미리 만든다. kind 에서는 노드
   컨테이너가 아니라 **WSL 에서** 만든다(`/data` 가 스토리지 노드로 마운트돼 있다):

   ```bash
   mkdir -p /data/jenkins
   sudo chown -R 1000:1000 /data/jenkins     # jenkins 컨테이너 UID
   ```

3. **hosts 에 `jenkins.example.com`** — Windows 와 WSL 양쪽(1.5). CoreDNS rewrite 는
   이미 들어 있다(`bootstrap/coredns/coredns-cm.yaml`).

4. **Nexus 프록시** — 3.3 의 `gcr-proxy` / `quay-proxy` 가 `docker-group` 에 들어가 있어야
   에이전트 Pod 가 뜬다. 컨트롤러 기동에는 필요 없으므로 **첫 빌드(7장) 전까지만** 하면 된다.

### 5.2 시크릿

값은 매니페스트에 적지 않는다(2.2·4.2 와 같은 이유 — 이 리포지토리는 공개다). 컨트롤러가
`envFrom` 으로 읽고, JCasC 가 `${...}` 자리에 넣어 자격증명 3개를 만든다.

| 키 | 값 | JCasC 자격증명 |
|---|---|---|
| `JENKINS_ADMIN_ID` / `JENKINS_ADMIN_PASSWORD` | Jenkins 관리자 계정 | (로그인 계정) |
| `GITOPS_USER` / `GITOPS_TOKEN` | `ci-bot` / 2.4 의 Group access token | `gitops-repo` — 앱 체크아웃 + gitops push |
| `ARGOCD_AUTH_TOKEN` | 4.4 의 `cicd` 토큰 | `argocd-auth-token` |
| `NEXUS_USER` / `NEXUS_PASSWORD` | Nexus docker-hosted push 계정 | `nexus-registry` — kaniko |

```bash
kubectl create namespace jenkins --dry-run=client -o yaml | kubectl apply -f -

read -rsp 'jenkins admin password: ' JP; echo
read -rp  'nexus user: ' NU; read -rsp 'nexus password: ' NP; echo
# 파일로 보관한 토큰은 끝의 줄바꿈을 떼고 넣는다(섞이면 인증이 401 로 실패한다)
kubectl -n jenkins create secret generic jenkins-secrets \
  --from-literal=JENKINS_ADMIN_ID='admin' \
  --from-literal=JENKINS_ADMIN_PASSWORD="$JP" \
  --from-literal=GITOPS_USER='ci-bot' \
  --from-literal=GITOPS_TOKEN="$(tr -d '\n' < ~/gitlab-group-token.txt)" \
  --from-literal=ARGOCD_AUTH_TOKEN="$(tr -d '\n' < ~/cicd-argocd-token.txt)" \
  --from-literal=NEXUS_USER="$NU" \
  --from-literal=NEXUS_PASSWORD="$NP" \
  --dry-run=client -o yaml | kubectl apply -f -
unset JP NU NP
kubectl -n jenkins get secret jenkins-secrets      # DATA 7
```

- `GITOPS_USER` 는 비밀이 아니다. 그룹 토큰의 **이름**이고, git 인증 사용자명으로 쓰인다(2.4).
- **`regcred` 는 만들지 않는다.** kaniko 의 `/kaniko/.docker/config.json` 은 Jenkinsfile 이
  `nexus-registry` 자격증명으로 직접 만든다. `docker-registry` 타입 Secret 을 Pod 템플릿의
  `secretVolume` 으로 붙이면 파일 이름이 `.dockerconfigjson` 이 되어 kaniko 가 찾지 못한다.
  에이전트 이미지는 `docker-group` 익명 pull 로 받으므로 `imagePullSecrets` 도 필요 없다.
- **값을 나중에 바꾸면 컨트롤러를 재시작한다.** `envFrom` 은 파드가 뜰 때만 읽힌다.

  ```bash
  kubectl -n jenkins rollout restart sts/jenkins
  kubectl -n jenkins exec jenkins-0 -- printenv GITOPS_USER     # 새 값인지 확인
  ```

### 5.3 설치

```bash
kubectl kustomize bootstrap/jenkins | kubectl apply -f -

# 최초 기동은 플러그인 다운로드로 1~3분 걸린다
kubectl -n jenkins logs -f sts/jenkins -c install-plugins
kubectl -n jenkins get pods -w                     # jenkins-0 1/1

curl -sS -o /dev/null -w '%{http_code}\n' http://jenkins.example.com/login   # 200
```

브라우저에서 `http://jenkins.example.com` 에 5.2 의 관리자 계정으로 로그인한다.
**Manage Jenkins > Credentials** 에 `gitops-repo` / `argocd-auth-token` / `nexus-registry` 3개가,
**Manage Jenkins > Clouds > kubernetes** 에 Pod 템플릿 `build` 가 보이면 된다.

> **플러그인은 외부(updates.jenkins.io)에서 받는다.** 이미지는 Nexus 를 거치지만
> `install-plugins` 초기화 컨테이너는 그렇지 않아, 파드가 재생성될 때마다 인터넷이
> 필요하다. `Temporary failure in name resolution` 으로 CrashLoopBackOff 면 WSL DNS 문제다.
> `plugins.txt` 에 버전을 고정하지 않고 `--latest true` 를 쓰므로 받는 시점에 따라
> 플러그인 버전이 달라질 수 있다.

> **kind 에서는 NodePort(`http://<노드IP>:30808`)로 접속할 수 없다.**
> `kind-config.yaml` 에 30808 매핑이 없어 노드 컨테이너 안에서만 열린다.
> `nodeport.yaml` 은 주석 처리된 상태가 기본값이고, 그대로 둔다.

**`casc.yaml` 을 고친 뒤에는** 적용하고 컨트롤러에 다시 읽힌다. 재시작하지 않아도 된다.
ConfigMap 이 파드 안 파일에 반영되기까지 1분 정도 걸리므로 먼저 확인한다.

```bash
kubectl kustomize bootstrap/jenkins | kubectl apply -f -
kubectl -n jenkins exec jenkins-0 -- cat /var/jenkins_home/casc.d/jenkins.yaml | grep <바꾼 줄>
# 반영됐으면 Manage Jenkins > Configuration as Code > Reload existing configuration
```

`plugins.txt` 를 바꿨다면 재시작해야 한다(`rollout restart sts/jenkins`).

### 5.4 에이전트 Pod 템플릿

Jenkinsfile 의 `agent { label 'build' }` 가 `casc.yaml` 의 `build` 템플릿을 쓴다.
컨테이너 5개(+ Jenkins 가 붙이는 `jnlp`)가 한 Pod 에 뜨고 워크스페이스를 공유한다.

| 컨테이너 | 이미지 (`nexus-docker-group.example.com/...`) | 단계 |
|---|---|---|
| `node` | `library/node:22-alpine` | `npm ci` / `npm test` / `npm run build` |
| `python` | `library/python:3.12-slim` | python-api 의 `pytest` (8장) |
| `kaniko` | `kaniko-project/executor:v1.23.2-debug` (gcr-proxy) | 이미지 빌드·Nexus push |
| `tools` | `alpine/k8s:1.30.2` | `kustomize edit set image` → gitops 커밋 |
| `argocd` | `argoproj/argocd:v3.5.2` (quay-proxy) | `argocd app sync` / `wait` |

- `argocd` 태그는 서버(`bootstrap/argocd/kustomization.yaml`)와 맞춘다.
- **`argocd` 컨테이너만 `runAsUser: "0"` 이 붙어 있다.** 이 이미지는 기본이 uid 999 인데,
  워크스페이스는 `jnlp`(uid 1000)가 만들기 때문에 999 가 `@tmp/durable-*` 에 결과 파일을 쓰지
  못한다. 그러면 sh 스텝이 `process apparently never started` 로 **5분 뒤** 실패한다. 이미지
  push 와 gitops 커밋은 앞 단계에서 이미 끝났으므로 dev 는 배포되는데 빌드만 실패로 남는다.
  나머지 세 이미지는 원래 root 로 돈다.

## 6. GitLab webhook 연결 (gitops → Argo CD)

**1. webhook 시크릿 만들기** — 임의 값을 만들어 `argocd-secret` 에 넣는다. 파일(`configs/argocd-secret.yaml`)에는
적지 않는다(4.2). GitLab 에 붙여 넣을 수 있게 권한 600 파일로 보관한다.

```bash
kubectl -n argocd get secret argocd-secret -o jsonpath='{.data.webhook\.gitlab\.secret}' | base64 -d; echo
# __REPLACE_ME__ 또는 빈 줄이면 아직 넣지 않은 상태

( umask 077; openssl rand -hex 20 > ~/argocd-webhook-secret.txt )
# test -s: 파일이 없거나 비면 patch 하지 않는다. 빈 값이 들어가면 Argo CD 는 서명 검증 없이 모든 요청을 받는다
test -s ~/argocd-webhook-secret.txt && kubectl -n argocd patch secret argocd-secret --type merge \
  -p "{\"stringData\":{\"webhook.gitlab.secret\":\"$(cat ~/argocd-webhook-secret.txt)\"}}"
kubectl -n argocd rollout restart deploy/argocd-server     # 확실히 새 값을 읽게 한다
kubectl -n argocd rollout status deploy/argocd-server

# 값을 출력하지 않고 파일과 같은지만 확인
[ "$(kubectl -n argocd get secret argocd-secret -o jsonpath='{.data.webhook\.gitlab\.secret}' | base64 -d)" \
  = "$(cat ~/argocd-webhook-secret.txt)" ] && echo OK
```

**2. GitLab 에 등록**

먼저 **Admin Area > Settings > Network > Outbound requests** 에서
*Allow requests to the local network from webhooks and integrations* 를 켜고 저장한다.
기본값은 차단이라, 켜지 않으면 클러스터 내부 URL(`*.svc.cluster.local`)을 넣은 webhook 저장이
`Url is blocked: Requests to the local network are not allowed` 로 거부된다.

그다음 gitops 리포지토리 → **Settings > Webhooks > Add new webhook**

- URL: `http://argocd-server.argocd.svc.cluster.local/api/webhook` (자체 호스팅이면 클러스터 내부 주소로 충분하다)
- Secret token: `cat ~/argocd-webhook-secret.txt` 의 값
- Trigger: `Push events`
- SSL verification: 평문 URL 이라 무관하다

**3. 확인** — 등록한 webhook 의 **Test > Push events** → 상단에 `Hook executed successfully: HTTP 200`.
실패하면 같은 화면의 **Edit > Recent events** 에서 응답 본문을 본다.

```bash
kubectl -n argocd logs deploy/argocd-server --since=2m | grep -i webhook
```

webhook 은 payload 의 리포지토리 URL 이 등록된 Application 의 `repoURL` 과 일치할 때만
refresh 를 트리거한다. payload 의 URL 은 GitLab `external_url`(`http://gitlab.example.com`)을
따르고, `apps/` 의 `repoURL` 도 같은 주소로 맞춰 두었으므로 추가 설정은 없다.

webhook 이 없으면 `timeout.reconciliation: 180s` 주기로 폴링된다.

## 7. 앱 리포지토리에 파이프라인 배치

`samples/react-app/`(React 템플릿)과 `ci/Jenkinsfile` 을 GitLab 의 `my-group/react-app` 리포지토리
루트에 올리고, Jenkins 에 Multibranch Pipeline 으로 등록한다. `develop` 은 dev 로 자동 배포,
`main` 은 Jenkins 에서 승인한 뒤 prod 로 배포된다.

```
준비 → 테스트·빌드(node) → 이미지 빌드·푸시(kaniko → Nexus)
  → [main 만] prod 승인(input) → gitops 태그 갱신(tools) → 배포 대기(argocd: Synced + Healthy)
```

| 브랜치 | 이미지 태그 | 배포 | Argo CD |
|---|---|---|---|
| `develop` | `develop-<sha 8자리>` | dev (`overlays/dev`) | 자동 동기화 — Jenkins 는 기다리기만 한다 |
| `main` | `main-<sha 8자리>` | prod (`overlays/prod`), **승인 후** | 수동 동기화 — Jenkins 가 `argocd app sync` 를 건다 |
| 그 외 | — | 테스트·빌드까지만 | — |

앱은 정적 파일을 nginx 가 서빙한다. 매니페스트(`manifests/react-app/base/deployment.yaml`)의 조건 —
포트 8080, `/healthz`, UID 10001, **읽기 전용 루트 파일시스템** — 에 맞춰 `nginx.conf` 가 쓰기 경로를 전부
`/tmp`(emptyDir)로 옮긴다. 화면에는 `environment`(Deployment 의 `APP_ENV`, 시작 시 `/tmp/config.js` 로 주입)와
`version`(빌드 때 넣은 이미지 태그)이 보여서, 같은 이미지가 dev/prod 로 흘러가는 것을 눈으로 확인할 수 있다.

### 7.1 로컬에서 먼저 확인 (WSL)

`npm ci` 에 필요한 `package-lock.json` 을 만들고, 이미지가 **읽기 전용 + UID 10001** 로 뜨는지 CI 전에 본다.
WSL 에 node 가 없어도 되도록 컨테이너로 돌린다.

```bash
mkdir -p ~/workspace/react-app && cd ~/workspace/react-app
git init -b main
cp -r ~/workspace/cicd/samples/react-app/. .
cp ~/workspace/cicd/ci/Jenkinsfile .

# package-lock.json 생성 + 테스트
docker run --rm -u "$(id -u):$(id -g)" -e npm_config_cache=/tmp/.npm -v "$PWD":/app -w /app \
  node:22-alpine sh -c 'npm install --no-audit --no-fund && npm test'
ls package-lock.json

# 매니페스트와 같은 조건으로 실행해 본다(클러스터 밖이라 베이스 이미지는 Docker Hub 에서)
docker build --build-arg REGISTRY=docker.io --build-arg APP_VERSION=local-test -t react-app:local .
docker run -d --name react-app-test --read-only --tmpfs /tmp -u 10001 -e APP_ENV=local -p 8088:8080 react-app:local
curl -s localhost:8088/healthz            # ok
curl -s localhost:8088/config.js          # window.APP_CONFIG = { env: "local" }
docker logs react-app-test | tail -5     # Read-only file system 오류가 없어야 한다
docker rm -f react-app-test
```

### 7.2 GitLab 프로젝트 만들고 push

`my-group` 에 `react-app` 프로젝트를 만든다(Private, *Initialize repository with a README* 끔).
두 프로젝트가 모두 생겼으니 2.4 의 그룹 토큰이 둘 다에 통하는지 확인한다.

```bash
# 읽기(앱 리포지토리)·쓰기(gitops) 둘 다 200 이어야 한다
curl -s -o /dev/null -w 'read  %{http_code}\n' -u "ci-bot:$(tr -d '\n' < ~/gitlab-group-token.txt)" \
  'http://gitlab.example.com/my-group/react-app.git/info/refs?service=git-upload-pack'
curl -s -o /dev/null -w 'write %{http_code}\n' -u "ci-bot:$(tr -d '\n' < ~/gitlab-group-token.txt)" \
  'http://gitlab.example.com/my-group/gitops-manifests.git/info/refs?service=git-receive-pack'
```

```bash
cd ~/workspace/react-app
git add . && git commit -m "initial: react-app"
git remote add origin http://gitlab.example.com/my-group/react-app.git
git push -u origin main
git push origin main:develop
```

아직 Jenkins 잡이 없으므로 push 만으로는 아무것도 돌지 않는다.

### 7.3 Jenkins 잡 등록

Jenkins UI > **새로운 Item** > 이름 `react-app` > **Multibranch Pipeline**.

- **Branch Sources > Add source > Git**
  - Project Repository: `http://gitlab.gitlab.svc.cluster.local/my-group/react-app.git`
  - Credentials: `ci-bot/****** (gitops 매니페스트 리포지토리 push 용)` — 이것이 `gitops-repo` 다.
    드롭다운에는 ID 가 아니라 `사용자명/****** (설명)` 으로 보인다. `argocd-auth-token` 은
    Secret text 라 이 칸에 나오지 않는 게 정상이다.
- **Build Configuration** — 기본값(`by Jenkinsfile`) 그대로.

Save 하면 바로 브랜치 스캔이 돌고, `Jenkinsfile` 이 있는 `main` 과 `develop` 에 빌드가 하나씩 걸린다.
스캔 로그는 사이드바 **Scan Multibranch Pipeline Log** 에서 본다.

- **`main` 빌드는 "prod 승인" 단계에서 멈춰 선다 — 정상이다.** 7.4 에서 dev 를 먼저 확인한 뒤
  7.5 에서 승인한다(승인 대기는 60분이 지나면 빌드가 중단된다. 그때는 `main` 을 다시 빌드한다).
- **자동 트리거가 없다.** 이후 push 하면 잡 화면의 **Scan Multibranch Pipeline Now** 를 눌러야
  빌드가 걸린다. 손으로 누르기 번거로우면 잡 설정의 *Scan Multibranch Pipeline Triggers >
  Periodically if not otherwise run* 을 켜서 주기적으로 스캔하게 한다.
- 잡을 UI 대신 코드로 관리하려면 `casc.yaml` 의 `jobs:` 블록(job-dsl) 주석을 해제한다.

### 7.4 dev 배포 확인

Jenkins 의 `react-app » develop` 빌드에서 모든 단계가 초록이면 된다. 첫 실행은 에이전트 이미지
pull 과 `npm ci` 로 수 분 걸린다. 콘솔 로그 마지막 줄에
`배포 완료: dev → http://react-app.dev.example.com (version: develop-xxxxxxxx)` 가 찍힌다.

```bash
argocd app get react-app-dev --grpc-web | grep -E 'Sync Status|Health Status'   # Synced / Healthy
kubectl -n react-app-dev get pods                     # Deployment 이름은 dev-react-app (namePrefix)
curl -s http://react-app.dev.example.com/healthz      # ok
```

브라우저에서 `http://react-app.dev.example.com` → `environment: dev`, `version: develop-<sha>`.
`version` 은 빌드 때 JS 번들에 들어가므로 `printenv` 나 `curl` 로는 보이지 않고 **브라우저에서만** 보인다.
gitops-manifests 에는 `chore(dev): react-app -> develop-<sha>` 커밋이 생긴다.

### 7.5 prod 배포

사전 조건 두 가지:

- **metrics-server(1.8)** — prod 에는 HPA 가 있다. 없으면 HPA 가 `<unknown>` 으로 남고 Argo CD 가
  이를 Degraded 로 판정해, 배포 자체는 됐는데도 "배포 대기" 단계가 health 에서 실패한다.
- **hosts 에 `127.0.0.1 react-app.example.com`** — Windows 와 WSL 양쪽(1.5).

`react-app » main` 빌드의 "prod 승인" 단계에서 **배포** 를 누른다(Stage View 의 단계 위, 또는
콘솔 로그의 `Input requested` 링크). 이후 gitops 태그 커밋 → `argocd app sync` → Synced + Healthy
대기까지 이어진다.

배포 확인은 경로를 따라 **같은 태그(`main-xxxxxxxx`)가 보이는지** 본다. 태그는 콘솔 로그 마지막 줄
`배포 완료: prod → ... (version: main-xxxxxxxx)` 에 있다.

```bash
# 1. gitops 리포지토리 — chore(prod): react-app -> main-xxxxxxxx 커밋 (GitLab UI 로 봐도 된다)
# 2. Argo CD — Synced / Healthy / 1 의 커밋 SHA
kubectl -n argocd get application react-app-prod \
  -o jsonpath='{.status.sync.status} {.status.health.status} {.status.sync.revision}{"\n"}'
# 3. 클러스터
kubectl -n react-app-prod get deploy prod-react-app \
  -o jsonpath='{.spec.template.spec.containers[0].image}{"\n"}'      # ...:main-xxxxxxxx
kubectl -n react-app-prod get pods,hpa,pdb       # 파드 3개, cpu: x%/70%, ALLOWED DISRUPTIONS 1
# 4. Ingress
curl -s http://react-app.example.com/healthz     # ok
curl -s http://react-app.example.com/config.js   # env: "prod"
```

마지막으로 브라우저에서 `http://react-app.example.com` → `environment: prod`, `version: main-<sha>`.
태그가 어느 단계에서 달라지면 그 앞 단계에서 멈춘 것이다.

- **같은 커밋을 다시 빌드하면 파드는 교체되지 않는다.** 태그가 같아 "gitops 태그 갱신" 이
  `변경 없음 - 커밋 생략` 으로 끝나고, Argo CD 가 바꿀 것이 없다. `rollout history` 의 리비전이
  늘지 않는 게 정상이다.
- **prod 를 처음 배포하면 "배포 대기" 에 `app wait 실패 - 일시적인 Degraded 인지 2분 더 지켜본다` 가
  찍힐 수 있다 — 정상이다.** 새로 만든 HPA 가 첫 지표를 받기 전(`FailedGetResourceMetric`)에 Argo CD 가
  잠깐 Degraded 로 판정하고, `argocd app wait` 는 그 순간 실패한다. Jenkinsfile 이 10초 간격으로 다시 보고
  `Synced Healthy` 가 되면 성공으로 끝난다.
- curl 이 빈 응답이면 `-s` 가 에러를 숨긴 것이다. `curl -sS -H 'Host: react-app.example.com'
  http://localhost/healthz` 가 `ok` 면 Ingress 는 정상이고 이름 해석(hosts)이 문제다.

### 7.6 자주 막히는 곳

| 증상 | 원인 |
|---|---|
| push 했는데 빌드가 안 걸린다 | 자동 트리거 없음 — **Scan Multibranch Pipeline Now** (7.3) |
| 스캔·체크아웃이 `401` / `Authentication failed` | `gitops-repo`(그룹 토큰, 2.4). 토큰 파일 끝 줄바꿈이 섞였거나 시크릿을 바꾼 뒤 재시작 안 함(5.2) |
| 에이전트 Pod `ImagePullBackOff` (kaniko / argocd) | Nexus `gcr-proxy`·`quay-proxy` 가 없거나 `docker-group` 멤버에 없음 (3.3) |
| 에이전트 Pod 가 `Pending` | 노드 메모리 부족. `kubectl -n jenkins describe pod <에이전트>` |
| "배포 대기" 가 5분 뒤 `process apparently never started` | `argocd` 컨테이너 uid (5.4) |
| kaniko `UNAUTHORIZED` | `NEXUS_USER`/`NEXUS_PASSWORD` (5.2), push 권한 |
| kaniko `http: server gave HTTP response to HTTPS client` | `--insecure` / `--skip-tls-verify` / `--insecure-pull` 누락 |
| sh 스텝에서 줄 연결이 깨진다 | Groovy `'''` 문자열은 줄 끝 `\` 를 먹는다 — Jenkinsfile 에서는 `\\` 로 적는다 |
| "gitops 태그 갱신" 이 `pre-receive hook declined` | 그룹 토큰 role 이 Maintainer 가 아님 (2.4) |
| "배포 대기" 가 `permission denied` | `ARGOCD_AUTH_TOKEN` (cicd 계정, `argocd-rbac-cm` 의 `role:ci`) |
| "배포 대기" 가 health 에서 실패, 앱 Degraded, HPA `<unknown>` | metrics-server 없음 (1.8) |
| "배포 대기" 가 `transitioned from Progressing to Degraded` 뒤 2분 재확인에도 실패 | 일시적 Degraded 가 아니다 — `argocd app get` 으로 Degraded 리소스 확인 (HPA 면 1.8) |
| 파드 `CrashLoopBackOff`, 로그에 `Read-only file system` | `nginx.conf` 의 `/tmp` 경로 — 7.1 의 `--read-only` 실행으로 재현 |
| 파드 `ImagePullBackOff` + `401` | `regcred` (3.5-2) |
| `jenkins-0` 의 `install-plugins` 가 CrashLoopBackOff | 외부 DNS (5.3) |

## 8. 두 번째 앱: python-api (FastAPI)

같은 파이프라인에 앱을 하나 더 올린다. 7장까지 만든 것(Jenkins 자격증명, 그룹 토큰, Argo CD
`cicd` 계정, Nexus)은 그대로 쓰고, **앱마다 새로 필요한 것만** 이 장에서 만든다.

| 경로 | 설명 |
|---|---|
| `GET /` | `service` / `environment` / `version` / `hostname`(응답한 파드) |
| `GET /healthz` | `ok` (프로브) |
| `GET /items`, `POST /items` | 목록 / 생성(201) |
| `GET·PUT·DELETE /items/{id}` | 조회 / 수정 / 삭제(204). 없으면 404, 입력이 틀리면 422 |
| `GET /docs` | Swagger UI (FastAPI 가 자동 생성) |

**데이터는 파드 메모리에만 있다.** 재배포·재시작하면 비워지고, 파드가 둘 이상이면 요청마다 다른
파드가 받아 목록이 달라진다. 그래서 dev·prod 모두 `replicas: 1` 이고 HPA·PDB 를 두지 않았다.
늘리려면 저장소를 DB 로 빼야 한다.

| 파일 | 역할 |
|---|---|
| `samples/python-api/` | 앱 리포지토리 템플릿 — `app/main.py`, `tests/`, `Dockerfile`, `Jenkinsfile` |
| `manifests/python-api/` | base + overlays(dev/prod). react-app 과 같은 구조, 네임스페이스 `python-api-dev/prod` |
| `apps/python-api.yaml` | Argo CD Application 두 개 (dev 자동 / prod 수동 동기화) |
| `apps/project.yaml` | AppProject `dev`/`prod` 의 허용 네임스페이스에 `python-api-*` 추가 |
| `bootstrap/jenkins/casc.yaml` | 에이전트 Pod 템플릿에 `python` 컨테이너(`python:3.12-slim`) 추가 |

`samples/python-api/Jenkinsfile` 은 `ci/Jenkinsfile` 과 흐름이 같다. 앱 이름을 `APP_NAME` 한 곳에서
정하고(이미지 이름·overlay 경로·Argo CD 앱 이름이 여기서 나온다), 테스트 단계가 `python`
컨테이너에서 `pytest` 를 도는 것만 다르다.

### 8.1 로컬에서 먼저 확인 (WSL)

```bash
mkdir -p ~/workspace/python-api && cd ~/workspace/python-api
git init -b main
cp -r ~/workspace/cicd/samples/python-api/. .

# 테스트 — 바인드 마운트에 root 소유 캐시가 남지 않게 .pyc·pytest 캐시를 끈다
docker run --rm -e PYTHONDONTWRITEBYTECODE=1 -v "$PWD":/app -w /app python:3.12-slim \
  sh -c 'pip install -q --root-user-action=ignore -r requirements-dev.txt && pytest -q -p no:cacheprovider'

# 매니페스트와 같은 조건(읽기 전용 + UID 10001)으로 실행
docker build --build-arg REGISTRY=docker.io --build-arg APP_VERSION=local-test -t python-api:local .
docker run -d --name python-api-test --read-only --tmpfs /tmp -u 10001 -e APP_ENV=local -p 8089:8080 python-api:local
curl -s localhost:8089/                   # {"service":"python-api","environment":"local","version":"local-test",...}
curl -s -X POST localhost:8089/items -H 'Content-Type: application/json' -d '{"name":"pen","price":1.5}'
curl -s localhost:8089/items
docker logs python-api-test | tail -5     # Read-only file system 오류가 없어야 한다
docker rm -f python-api-test
```

### 8.2 클러스터 쪽 준비

**1. Jenkins 에이전트에 python 컨테이너** — `casc.yaml` 만 바뀌었으므로 적용 후 JCasC 를 다시 읽힌다(5.3).

```bash
cd ~/workspace/cicd && git pull
kubectl kustomize bootstrap/jenkins | kubectl apply -f -
kubectl -n jenkins exec jenkins-0 -- grep -A1 'name: "python"' /var/jenkins_home/casc.d/jenkins.yaml
# 보이면 Manage Jenkins > Configuration as Code > Reload existing configuration
docker exec devops-worker crictl pull nexus-docker-group.example.com/library/python:3.12-slim   # docker-hub 프록시 경유
```

**2. 앱 네임스페이스 pull secret** — 3.5-2 와 같다. 네임스페이스 이름만 바뀐다.

```bash
read -rp 'nexus user: ' NX_USER; read -rsp 'nexus password: ' NX_PASS; echo
for ns in python-api-dev python-api-prod; do
  kubectl create namespace "$ns" --dry-run=client -o yaml | kubectl apply -f -
  kubectl -n "$ns" create secret docker-registry regcred \
    --docker-server=nexus-docker.example.com \
    --docker-username="$NX_USER" --docker-password="$NX_PASS" \
    --dry-run=client -o yaml | kubectl apply -f -
done
unset NX_USER NX_PASS
```

**3. gitops-manifests 에 올리기** — **새 파일만 골라서** 복사한다. `manifests/` 를 통째로 복사하면
Jenkins 가 커밋해 둔 react-app 의 이미지 태그가 이 리포지토리의 옛 값(`dev-0000000` 등)으로 되돌아간다.

```bash
cd ~/workspace/gitops-manifests && git pull
cp ~/workspace/cicd/apps/python-api.yaml apps/
cp ~/workspace/cicd/apps/project.yaml apps/
cp -r ~/workspace/cicd/manifests/python-api manifests/
git status --short            # apps/python-api.yaml, apps/project.yaml, manifests/python-api/ 만 보여야 한다
git diff apps/project.yaml    # python-api-dev / python-api-prod 두 줄씩만 늘어야 한다
git add -A && git commit -m "add python-api" && git push
```

`bootstrap` Application 이 `apps/` 를 보고 있으므로 새 Application 이 자동으로 생긴다
(webhook 이 있으면 바로, 없으면 3분 안).

```bash
argocd app list --grpc-web | grep python-api
```

| Application | SYNC | HEALTH | 이유 |
|---|---|---|---|
| `python-api-dev` | Synced | Degraded / Progressing | 이미지 `dev-0000000` 이 아직 없다. 첫 빌드가 태그를 커밋하면 풀린다 |
| `python-api-prod` | OutOfSync | Missing | 수동 동기화 대상 |

`application destination ... is not permitted in project` 가 보이면 `apps/project.yaml` 이 아직
반영되지 않은 것이다.

**4. hosts** — Windows 와 WSL 양쪽에 `127.0.0.1 python-api.dev.example.com`,
`127.0.0.1 python-api.example.com` (1.5).

### 8.3 GitLab 프로젝트와 Jenkins 잡

`my-group` 에 `python-api` 프로젝트를 만든다(Private, README 초기화 끔). 그룹 토큰(2.4)은 그룹의 새
프로젝트에도 그대로 통한다 — 앱이 늘어도 토큰을 다시 발급하지 않는 것이 그룹 토큰을 쓴 이유다.

```bash
curl -s -o /dev/null -w 'read  %{http_code}\n' -u "ci-bot:$(tr -d '\n' < ~/gitlab-group-token.txt)" \
  'http://gitlab.example.com/my-group/python-api.git/info/refs?service=git-upload-pack'     # 200

cd ~/workspace/python-api
git add . && git commit -m "initial: python-api"
git remote add origin http://gitlab.example.com/my-group/python-api.git
git push -u origin main
git push origin main:develop
```

Jenkins 에 7.3 과 같은 방법으로 Multibranch Pipeline `python-api` 를 만든다. 다른 것은 URL 뿐이다.

- Project Repository: `http://gitlab.gitlab.svc.cluster.local/my-group/python-api.git`
- Credentials: `ci-bot/****** (gitops 매니페스트 리포지토리 push 용)`

저장하면 스캔 후 `develop`·`main` 빌드가 걸리고, `main` 은 "prod 승인" 에서 멈춘다(7.3 과 같다).

### 8.4 dev 확인

`python-api » develop` 이 초록이면 된다. 콘솔 마지막 줄에
`배포 완료: dev → http://python-api.dev.example.com (version: develop-xxxxxxxx)`.

```bash
H=http://python-api.dev.example.com
curl -s $H/                                   # environment: dev, version: develop-xxxxxxxx
curl -s -X POST $H/items -H 'Content-Type: application/json' -d '{"name":"pen","price":1.5}'
curl -s $H/items                              # [{"id":1,"name":"pen",...}]
curl -s -X PUT $H/items/1 -H 'Content-Type: application/json' -d '{"name":"pencil","price":2}'
curl -s -o /dev/null -w '%{http_code}\n' -X DELETE $H/items/1     # 204
curl -s -o /dev/null -w '%{http_code}\n' $H/items/1               # 404
```

브라우저에서 `http://python-api.dev.example.com/docs` 를 열면 Swagger UI 에서 같은 요청을 보낼 수 있다.
react-app 과 달리 `version` 이 API 응답에 있으므로 브라우저 없이 `curl` 로 배포 버전을 확인할 수 있다.

### 8.5 prod 배포

`python-api » main` 의 "prod 승인" 에서 **배포** 를 누른다. 확인 방법은 7.5 와 같다(앱 이름과 호스트만 바꾼다).

```bash
kubectl -n argocd get application python-api-prod \
  -o jsonpath='{.status.sync.status} {.status.health.status}{"\n"}'     # Synced Healthy
kubectl -n python-api-prod get deploy prod-python-api \
  -o jsonpath='{.spec.template.spec.containers[0].image}{"\n"}'         # ...:main-xxxxxxxx
curl -s http://python-api.example.com/                                  # environment: prod
```

HPA 가 없으므로 metrics-server 가 없어도 prod health 는 통과한다(react-app 과 다른 점).

| 증상 | 원인 |
|---|---|
| 에이전트 Pod 에 `python` 컨테이너가 없음 (`container python not found`) | 8.2-1 적용·Reload 안 함 |
| "테스트" 단계 `pip install` 이 이름 해석 실패 | 에이전트가 pypi.org 에 직접 붙는다. 외부 DNS (5.3 과 같은 원인) |
| "gitops 태그 갱신" 이 `cd: .../manifests/python-api/...: No such file` | 8.2-3 을 안 함 — gitops 리포지토리에 overlay 가 없다 |
| "배포 대기" 가 app not found / `permission denied` | Application 이 아직 없다(8.2-3), 또는 AppProject 허용 네임스페이스 |
| 파드 `ImagePullBackOff` + `401` | `python-api-*` 네임스페이스에 `regcred` 없음 (8.2-2) |
| 만든 item 이 사라짐 | 정상 — 재배포·재시작하면 메모리가 비워진다 |

## 9. PostgreSQL (dev / prod)

앱이 쓸 DB 를 같은 GitOps 흐름으로 올린다. 공식 `postgres:17` 이미지를 Nexus 의 Docker Hub 프록시로 받아
StatefulSet 하나로 띄우고, 데이터는 스토리지 노드의 `/data/postgres/<env>` 에 둔다.
이미지를 직접 빌드하지 않는다. 버전 변경은 Jenkins 잡 `postgres-deploy` 에서 **드롭다운으로 버전을 골라** 한다(9.5).
메이저 버전도 바꿀 수 있다 — 데이터 디렉터리를 메이저별로 두고, 잡이 백업 → 새 메이저 initdb → 복원을 한다.

| 파일 | 역할 |
|---|---|
| `bootstrap/postgres/local-pv.yaml` | hostPath PV 2개(`postgres-dev-pv`, `postgres-prod-pv`). **kubectl 로 적용** |
| `manifests/postgres/base/` | StatefulSet(`replicas: 1`) + headless Service + PVC + ServiceAccount + Jenkins 에이전트용 exec 권한(`rbac.yaml`) |
| `manifests/postgres/overlays/{dev,prod}` | 네임스페이스 `postgres-dev/prod`, 이름 앞에 `dev-`/`prod-`, PVC 가 붙을 PV 이름 |
| `apps/postgres.yaml` | Argo CD Application 두 개 (dev 자동 / prod 수동 동기화) |
| `apps/project.yaml` | AppProject `dev`/`prod` 의 허용 네임스페이스에 `postgres-*` 추가 |
| `pipelines/postgres/Jenkinsfile`, `list-versions.py` | 버전 변경 잡. gitops 리포지토리에 두고 Jenkins Pipeline 잡이 읽는다(9.5) |

설계에서 정한 것:

- **PV 는 Argo CD 밖에 둔다.** PV 는 클러스터 범위 리소스인데 AppProject 는 Namespace 만 허용한다(`clusterResourceWhitelist`).
  허용을 넓히는 대신 GitLab·Jenkins·Nexus 와 같이 kubectl 로 적용한다. PV 의 `claimRef` 로 어느 PVC 에 붙을지 미리 정한다.
- **PVC 는 Argo CD 가 만들되 지우지 않는다.** `argocd.argoproj.io/sync-options: Delete=false`. PV 는 `Retain` 이다.
- **비밀번호는 git 에 없다.** 네임스페이스마다 `postgres-auth` Secret 을 kubectl 로 만든다(9.2-3).
  DB 사용자·DB 이름은 `app`/`app` 으로 매니페스트에 적었다.
- **읽기 전용 루트 + UID 999.** 앱들과 같은 보안 설정을 쓴다. 쓰기 경로는 데이터·소켓(`/var/run/postgresql`)·`/tmp` 뿐이다.
- **데이터 디렉터리는 메이저별.** PV 를 `/var/lib/postgresql` 에 붙이고 PGDATA 는 `/var/lib/postgresql/<메이저>/data` (9.5).
- **외부 노출 없음.** Ingress·NodePort 가 없다. 클러스터 안에서 `<env>-postgres.postgres-<env>.svc.cluster.local:5432`
  로 붙고, 밖(WSL)에서 볼 때는 `kubectl port-forward` 를 쓴다(9.4).

### 9.1 배포 전 확인 (WSL)

```bash
cd ~/workspace/cicd && git pull
kubectl kustomize manifests/postgres/overlays/dev  | grep -E '^kind:|^  name:|namespace:|volumeName|claimName|serviceName|image:'
kubectl kustomize manifests/postgres/overlays/dev  | grep -A3 '^subjects:'      # RoleBinding 대상 — namespace: jenkins 여야 한다
kubectl kustomize manifests/postgres/overlays/prod | grep -E 'namespace:|volumeName|claimName'
```

dev 는 `dev-postgres`(StatefulSet·Service, 이미지 `...postgres:17.6`), `dev-postgres-data`(PVC, `volumeName: postgres-dev-pv`),
`claimName: dev-postgres-data`, `serviceName: dev-postgres` 이고 모두 `namespace: postgres-dev` 여야 한다.
prod 는 같은 자리가 `prod-` / `postgres-prod` / `postgres-prod-pv` 다.

이미지를 스토리지 노드에 미리 받아 둔다(docker-group 프록시 경유 — 첫 기동이 빨라지고 pull 경로 문제를 먼저 잡는다).

```bash
docker exec devops-worker crictl pull nexus-docker-group.example.com/library/postgres:17.6
```

### 9.2 클러스터 쪽 준비

**1. 데이터 디렉터리** — WSL 의 `/data` 가 스토리지 노드(`devops-worker`)의 `/data` 로 마운트돼 있다(1.2).

```bash
mkdir -p /data/postgres/dev /data/postgres/prod
sudo chown -R 999:999 /data/postgres     # postgres 컨테이너 UID
ls -ln /data/postgres                    # dev, prod 모두 999 999
```

**2. PV**

```bash
kubectl apply -k bootstrap/postgres
kubectl get pv postgres-dev-pv postgres-prod-pv    # STATUS Available, CLAIM postgres-dev/dev-postgres-data ...
```

**3. 네임스페이스와 비밀번호 Secret** — 비밀번호가 셸 기록에 남지 않게 `read` 로 받는다.
dev 와 prod 는 **다른 비밀번호**를 쓰고 비밀번호 관리 도구에 보관한다.

```bash
for env in dev prod; do
  read -rsp "postgres ${env} password: " PG_PASS; echo
  kubectl create namespace "postgres-${env}" --dry-run=client -o yaml | kubectl apply -f -
  kubectl -n "postgres-${env}" create secret generic postgres-auth \
    --from-literal=password="$PG_PASS" \
    --dry-run=client -o yaml | kubectl apply -f -
done
unset PG_PASS
kubectl -n postgres-dev get secret postgres-auth; kubectl -n postgres-prod get secret postgres-auth
```

`POSTGRES_PASSWORD` 는 **빈 데이터 디렉터리에서 처음 뜰 때만** 쓰인다. 초기화 뒤에 Secret 을 바꿔도 DB 의 비밀번호는
그대로다 — 바꾸려면 `ALTER USER app PASSWORD '...'` 와 Secret 을 함께 고친다.

**4. gitops-manifests 에 올리기** — 8.2-3 과 같이 **새 파일만 골라서** 복사한다.

```bash
cd ~/workspace/gitops-manifests && git pull
cp ~/workspace/cicd/apps/postgres.yaml apps/
cp ~/workspace/cicd/apps/project.yaml apps/
cp -r ~/workspace/cicd/manifests/postgres manifests/
mkdir -p pipelines && cp -r ~/workspace/cicd/pipelines/postgres pipelines/     # 9.5 의 버전 변경 잡
git status --short            # apps/postgres.yaml, apps/project.yaml, manifests/postgres/, pipelines/ 만 보여야 한다
git diff apps/project.yaml    # postgres-dev / postgres-prod 두 줄씩만 늘어야 한다
git add -A && git commit -m "add postgres" && git push
```

### 9.3 dev 확인 · prod 동기화

`bootstrap` Application 이 새 Application 을 만든다(webhook 이 있으면 바로, 없으면 3분 안).
dev 는 자동 동기화된다. 첫 기동은 initdb 때문에 30초~1분 걸린다.

```bash
argocd app list --grpc-web | grep postgres
kubectl -n postgres-dev get pvc,pod
kubectl -n postgres-dev logs statefulset/dev-postgres | tail -5   # database system is ready to accept connections
```

| Application | SYNC | HEALTH | 이유 |
|---|---|---|---|
| `postgres-dev` | Synced | Healthy | PVC `Bound`, 파드 `1/1` |
| `postgres-prod` | OutOfSync | Missing | 수동 동기화 대상 |

prod 는 Jenkins 승인 단계가 없으므로 직접 동기화한다.

```bash
argocd app sync postgres-prod --grpc-web
argocd app wait postgres-prod --sync --health --timeout 300 --grpc-web
kubectl -n postgres-prod get pvc,pod
```

### 9.4 접속 확인

클러스터 안에서(앱이 붙는 경로와 같다):

```bash
# 파드 안 유닉스 소켓(local trust) — 비밀번호 없이 붙는다
kubectl -n postgres-dev exec dev-postgres-0 -- psql -U app -d app -c 'select version();'

# 서비스 이름으로 TCP + 비밀번호 인증. Secret 값을 화면에 꺼내지 않고 파드 안의 환경변수를 쓴다
kubectl -n postgres-dev exec dev-postgres-0 -- bash -c 'PGPASSWORD="$POSTGRES_PASSWORD" psql -h dev-postgres.postgres-dev.svc.cluster.local -U app -d app -c "select current_user, inet_server_addr();"'

# (선택) 다른 네임스페이스의 파드에서, 기억하는 비밀번호로 — 앱이 붙는 경로와 같다.
# psql 의 Password: 프롬프트가 kubectl 이 붙기 전에 찍혀 보이지 않는다.
# "If you don't see a command prompt" 가 나오면 그대로 비밀번호를 치고 Enter (그냥 Enter 면 no password supplied)
kubectl run pgtest --rm -it --restart=Never --image=nexus-docker-group.example.com/library/postgres:17.6 -- \
  psql -h dev-postgres.postgres-dev.svc.cluster.local -U app -d app -c 'select current_user, inet_server_addr();'
```

데이터가 PV 에 남는지 — 파드를 지워도 테이블이 그대로여야 한다.

```bash
kubectl -n postgres-dev exec dev-postgres-0 -- psql -U app -d app -c 'create table if not exists t(id int); insert into t values (1);'
kubectl -n postgres-dev delete pod dev-postgres-0
kubectl -n postgres-dev wait --for=condition=Ready pod/dev-postgres-0 --timeout=120s
kubectl -n postgres-dev exec dev-postgres-0 -- psql -U app -d app -c 'select count(*) from t;'   # 1 이상
sudo ls -ln /data/postgres/dev/17/data | head -3                                                  # WSL 에 실제 파일 (메이저별 디렉터리)
```

WSL·Windows 의 GUI 도구(DBeaver 등)로 볼 때는 port-forward 를 연다. 켜 둔 동안만 `localhost:15432` 로 붙는다.

```bash
kubectl -n postgres-dev port-forward svc/dev-postgres 15432:5432
```

| 증상 | 원인 |
|---|---|
| PVC `Pending`, 이벤트에 `volume ... already bound to a different claim` | PV 의 `claimRef` 와 PVC 이름·네임스페이스가 다르다(9.1 확인) |
| PVC `Pending`, PV 가 없다 | 9.2-2 안 함 |
| 파드 `CreateContainerConfigError` + `secret "postgres-auth" not found` | 9.2-3 안 함 |
| 파드 `ContainerCreating` + `hostPath type check failed` | `/data/postgres/<env>` 가 없다(9.2-1) |
| 로그 `initdb: could not change permissions` / `Permission denied` | `/data/postgres` 를 `chown 999:999` 안 함 |
| `application destination ... is not permitted in project` | `apps/project.yaml` 이 gitops 리포지토리에 반영되지 않았다 |
| 비밀번호를 바꿨는데 예전 비밀번호로만 붙는다 | 정상 — 초기화 때만 쓰인다(9.2-3) |
| pgtest 가 `password authentication failed`, 위 PGPASSWORD 방식은 성공 | 입력한 값이 Secret 과 다르다. 9.2-3 의 `read -rsp` 는 확인 입력이 없어 오타가 그대로 들어간다 |

되돌리기: Application 을 지워도 PVC 와 데이터는 남는다(`Delete=false`, `Retain`). 완전히 지우려면
`kubectl -n postgres-<env> delete pvc <env>-postgres-data` → `kubectl delete pv postgres-<env>-pv` →
`sudo rm -rf /data/postgres/<env>` 순서로 직접 지운다.

### 9.5 버전 변경 (Jenkins 잡 `postgres-deploy`)

버전은 overlay 의 `images[].newTag`(처음 `17.6`)로 정해진다. 이 값을 바꿔 gitops 리포지토리에 커밋하면
Argo CD 가 StatefulSet 을 새 이미지로 바꾼다(파드가 한 번 재시작된다 — 그동안 접속이 끊긴다).
Jenkins 잡을 쓰면 **버전 목록에서 골라** 배포하고, 메이저가 바뀔 때 필요한 백업·복원까지 잡이 한다.

흐름: **Build with Parameters**(환경 dev/prod) → 현재 태그 읽기 → Docker Hub 에서 버전 목록 조회
→ **버전 선택 화면**(드롭다운) → (메이저 변경이면) 확인 화면 → `pg_dumpall` 백업
→ gitops 태그 커밋 → Argo CD 배포 완료까지 대기 → (메이저 변경이면) 새 메이저에 복원

**데이터 디렉터리는 메이저별이다.** 컨테이너가 PGDATA 를 `/var/lib/postgresql/<PG_MAJOR>/data` 로 잡는다
(`PG_MAJOR` 는 공식 이미지의 환경변수). 18 이미지의 기본값 `<메이저>/docker` 는 쓰지 않는다 — 18 의 엔트리포인트는
PGDATA 가 그 기본값이고 비어 있으면 옆의 `*/docker` 에서 다른 메이저의 데이터를 찾아 initdb 를 거부한다
(`Error: in 18+ ... there appears to be PostgreSQL data in: /var/lib/postgresql/17/docker`).
pg_upgrade 를 쓰라는 안전장치인데, 이 구성은 덤프/복원으로 옮기므로 이름을 달리해 비켜 간다. PV 안은 이렇게 된다:

```
/data/postgres/dev/            (파드 안 /var/lib/postgresql)
  17/data/                   17.x 가 쓰는 데이터
  18/data/                   18 로 바꾼 뒤의 데이터 (빈 디렉터리에서 initdb → 복원)
  backup/pg17-to-pg18-<시각>.sql   메이저 변경 직전의 pg_dumpall (시각은 에이전트 기준 UTC)
  18.old-<시각>/               새 메이저의 디렉터리가 이미 있었으면 잡이 지우지 않고 옮겨 둔 것
```

| 변경 | 잡이 하는 일 | 되돌리기 |
|---|---|---|
| 마이너 (17.6 → 17.x) | 태그만 바꾼다. 같은 디렉터리를 그대로 쓴다 | 같은 잡에서 이전 마이너를 고른다 |
| 메이저 올리기 (17 → 18) | 17 에서 덤프 → 태그 변경 → 18 이 `18/data` 에 initdb → 덤프 복원 | 같은 잡에서 17 을 고른다(아래) |
| 메이저 내리기 (18 → 17) | 올리기와 같다. 이미 있는 `17/` 은 `17.old-<시각>` 으로 치우고 18 의 덤프를 복원 | 새 문법을 쓴 객체가 있으면 복원이 실패할 수 있다 |

- **메이저 변경 중에는 DB 가 멈추고, 백업 뒤에 들어온 쓰기는 옮겨지지 않는다.** 앱의 쓰기를 먼저 멈춘다.
- 이전 메이저의 디렉터리와 덤프 파일은 **지우지 않는다.** 디스크를 비우려면 확인 후 WSL 에서 직접 지운다.
- **마이너 버전**은 데이터 형식이 같아 올리고 내릴 수 있다. 다만 릴리스 노트에 "업데이트 후 REINDEX 필요" 같은
  추가 작업이 적힌 버전이 있으니 prod 에 올리기 전에 확인한다.
- **prod 는 버전 선택 화면이 곧 승인이다.** 앱 파이프라인의 "prod 승인" 과 같은 역할.
- 드롭다운에는 Jenkinsfile 의 `MAJORS`(`15 16 17 18 19`)에서 실제로 나와 있는 메이저만 보인다.
  현재 메이저는 최근 10개, 다른 메이저는 최근 3개 마이너.
- 목록은 에이전트가 `hub.docker.com` 에 직접 물어 만든다(외부 DNS 필요 — 8.3 과 같은 조건).
  이미지 자체는 노드가 Nexus docker-group 프록시로 받는다.
- 백업·복원은 잡이 `kubectl exec` 로 파드 안에서 한다. 그 권한은 `manifests/postgres/base/rbac.yaml` 이
  에이전트 계정(`jenkins/jenkins-agent`)에 **`postgres-*` 네임스페이스에서만** 준다.

**1. 잡 만들기** — 9.2-4 에서 `pipelines/postgres/` 를 gitops 리포지토리에 올렸어야 한다.
Jenkins → **New Item** → 이름 `postgres-deploy`, **Pipeline** 선택 → OK.

- Pipeline → Definition: **Pipeline script from SCM**
- SCM: Git, Repository URL: `http://gitlab.gitlab.svc.cluster.local/my-group/gitops-manifests.git`
- Credentials: `ci-bot/****** (gitops 매니페스트 리포지토리 push 용)` (7.3 과 같은 것)
- Branch Specifier: `*/main`, Script Path: `pipelines/postgres/Jenkinsfile`
- Build Triggers 는 아무것도 켜지 않는다 — 사람이 돌릴 때만 실행된다. 이 잡 자신이 gitops 리포지토리에 커밋하므로
  SCM 폴링을 켜면 자기 커밋으로 다시 실행된다.

**2. 첫 실행** — 새 잡은 Jenkinsfile 을 한 번 읽기 전에는 파라미터를 모른다. 처음에는 버튼이 **Build Now** 이고
기본값(dev)으로 돈다. 버전 선택 화면에서 멈추므로 거기서 현재 버전 그대로 **배포**(변경 없음)하거나 **Abort** 한다.
그 뒤부터 버튼이 **Build with Parameters** 로 바뀐다.

**3. 마이너 변경** — `postgres-deploy` → **Build with Parameters** → `TARGET_ENV` = `dev` → Build.
"버전 목록" 단계가 끝나면 빌드가 멈춘다. 빌드 화면 왼쪽의 **Input requested**(또는 단계 뷰의 일시정지 칸)를 누르면
드롭다운이 뜬다. `17.x` 를 골라 **배포**. 콘솔 마지막 줄 `배포 완료: dev → postgres 17.x`,
빌드 목록에는 설명으로 `dev: 17.6 → 17.x` 가 남는다.

```bash
kubectl -n postgres-dev get pod dev-postgres-0 -o jsonpath='{.spec.containers[0].image}{"\n"}'   # ...postgres:17.x
kubectl -n postgres-dev exec dev-postgres-0 -- psql -U app -d app -tAc 'show server_version;'     # 17.x
kubectl -n postgres-dev exec dev-postgres-0 -- psql -U app -d app -c 'select count(*) from t;'    # 9.4 에서 넣은 행이 그대로
```

**4. 메이저 변경** — 같은 방법으로 `18.x` 를 고르면 확인 화면이 한 번 더 뜬다(**진행**).
"백업 (메이저 변경)" → "gitops 태그 갱신" → "배포 대기" → "복원 (메이저 변경)" 순으로 돈다.
"복원" 단계 콘솔에 `새 서버 버전: 18.x`, `복원 오류 N건 (예상된 already exists 를 뺀 나머지 0건)`, 그리고 테이블 목록이 나오면 된다.
`already exists` 는 initdb 가 이미 만든 `app` 사용자·`app` DB 를 덤프가 다시 만들려다 난 것으로 정상이다.
복원 로그는 빌드의 아티팩트 `restore.log` 로 남는다.

```bash
kubectl -n postgres-dev exec dev-postgres-0 -- psql -U app -d app -tAc 'show server_version;'     # 18.x
kubectl -n postgres-dev exec dev-postgres-0 -- psql -U app -d app -c 'select count(*) from t;'    # 17 때와 같은 값
sudo ls -l /data/postgres/dev /data/postgres/dev/backup                                         # 17/ 18/ backup/
```

prod 도 같다(`TARGET_ENV` = `prod`). gitops 리포지토리에 `chore(<env>): postgres 17.6 -> 18.x` 형태의 커밋이 쌓인다.

**복원이 실패했을 때** — 잡은 "복원" 단계에서 멈추고 예상 밖의 오류를 보여 준다. 이때 파드는 이미 새 메이저로 떠 있고
데이터는 일부만 들어가 있다. 원래 데이터는 이전 메이저 디렉터리(`17/data`)에 그대로 있다.
이때는 잡으로 되돌리지 **않는다** — 잡은 메이저 변경으로 보고 불완전한 18 의 데이터를 덤프해 17 에 복원하며, 멀쩡한 `17/` 을 치워 버린다.
대신 태그만 손으로 돌려 이전 디렉터리로 다시 띄운다:

```bash
cd ~/workspace/gitops-manifests && git pull
cd manifests/postgres/overlays/dev && kustomize edit set image nexus-docker-group.example.com/library/postgres=nexus-docker-group.example.com/library/postgres:17.6
cd ~/workspace/gitops-manifests && git commit -am "revert(dev): postgres -> 17.6" && git push
# 17.6 은 그대로 남은 17/data 로 뜬다 — 메이저 변경 직전 상태. prod 면 argocd app sync postgres-prod --grpc-web
```

실패 원인(`restore.log`)을 고친 뒤 다시 잡으로 올린다. 그때 남아 있던 `18/` 은 잡이 `18.old-<시각>` 으로 치운다.

| 증상 | 원인 |
|---|---|
| "버전 목록" 이 `Name or service not known` / timeout | 에이전트의 외부 DNS·인터넷 (TODOLIST 0) 점검의 파드 `nslookup`) |
| "버전 목록" 이 `버전 태그를 하나도 찾지 못했다` | Docker Hub API 응답 형식이 바뀌었거나 요청 제한. 콘솔의 URL 을 브라우저로 열어 본다 |
| 버전 선택 화면 없이 끝남 / `Scripts not permitted` | Manage Jenkins > In-process Script Approval 에서 승인 |
| "백업" 이 `pods "dev-postgres-0" is forbidden` | `rbac.yaml` 이 반영되지 않았다 — `kubectl -n postgres-dev get rolebinding` (9.1 의 subjects 확인) |
| "배포 대기" 가 600초 뒤 실패, 파드 `ImagePullBackOff` | Nexus docker-group 이 그 태그를 받지 못했다 — `docker exec devops-worker crictl pull ...postgres:<버전>` |
| "복원" 이 `새 메이저(..)로 바뀌지 않았다` | 파드가 아직 옛 이미지. `kubectl -n postgres-<env> get pod -o wide`, Argo CD 앱 상태 확인 후 다시 실행 |
| "복원" 이 예상 밖 오류로 실패 | 위 "복원이 실패했을 때". 덤프는 PV 의 `backup/` 에 있다 |
| "gitops 태그 갱신" 이 `변경 없음 - 커밋 생략` | 현재와 같은 버전을 골랐다. 정상 |
| 18+ 파드 `CrashLoopBackOff` + `Error: in 18+ ... there appears to be PostgreSQL data in: .../17/docker` | PGDATA 가 18 기본값(`<메이저>/docker`)이다. statefulset 의 command 가 `<메이저>/data` 인지 확인 |

## 이미지 태그 갱신 방식 (택1)

| 방식 | 설명 |
|---|---|
| **CI 커밋** (기본) | Jenkins 가 `kustomize edit set image` 후 gitops 리포지토리에 커밋. 이력이 git 에 남고 롤백이 쉽다. |
| **Argo CD Image Updater** | `bootstrap/image-updater/` 적용. 레지스트리를 폴링해 새 태그를 write-back. CI 가 gitops 쓰기 권한을 가질 필요가 없다. |

두 방식을 동시에 쓰면 커밋이 충돌하므로 하나만 선택한다. **이 리포지토리는 CI 커밋 방식**이므로
`apps/react-app.yaml` 에 image-updater 어노테이션이 없다. Image Updater 로 바꾸려면
`bootstrap/image-updater/` 를 적용하고 `react-app-dev` 의 `annotations` 에 아래를 되돌린다.

```yaml
    argocd-image-updater.argoproj.io/image-list: app=nexus-docker.example.com/my-group/react-app
    argocd-image-updater.argoproj.io/app.update-strategy: newest-build
    argocd-image-updater.argoproj.io/app.allow-tags: regexp:^dev-[0-9a-f]{7}$
    argocd-image-updater.argoproj.io/write-back-method: git
    argocd-image-updater.argoproj.io/git-branch: main
```

이때 Jenkinsfile 의 "gitops 태그 갱신" 단계는 필요 없어진다. 그룹 토큰은 앱 리포지토리
체크아웃에 계속 쓰이므로, 스코프를 `read_repository` 로 줄여 다시 발급하면 된다.

## 운영 관련 메모

- **prod 는 수동 동기화**: `apps/react-app.yaml` 의 `react-app-prod` 에 `syncPolicy.automated` 가 없다.
  Jenkins 가 승인 후 `argocd app sync` 를 건다. 자동화하려면 dev 쪽 블록을 복사하고 `prod`
  AppProject 의 `syncWindows` 를 확인한다.
- **HA**: 운영 클러스터는 `bootstrap/argocd/kustomization.yaml` 에서 `ha/install.yaml` 로 교체.
- **replicas diff 무시**: HPA 사용 시 `argocd-cm` 의 `resource.customizations.ignoreDifferences.apps_Deployment` 로
  OutOfSync 오탐을 막는다.
- **gitops 커밋이 CI 를 다시 부르지 않는다**: Jenkins 잡은 앱 리포지토리만 보므로 gitops 리포지토리
  커밋으로 빌드가 재귀 실행되지 않는다. GitLab CI(부록)로 돌릴 때는 커밋 메시지의 `[skip ci]` 가
  그 역할을 한다.
- **동시 빌드 없음**: Jenkinsfile 의 `disableConcurrentBuilds()` 로 같은 브랜치 빌드가 겹치지 않는다.
  두 빌드가 동시에 gitops 에 push 하다 한쪽이 거부되는 것을 막는다.

## 검증

```bash
kubectl kustomize bootstrap/argocd            > /dev/null
kubectl kustomize bootstrap/jenkins           > /dev/null
kubectl kustomize manifests/react-app/overlays/dev
kubectl kustomize manifests/react-app/overlays/prod
kubectl kustomize manifests/python-api/overlays/dev
kubectl kustomize manifests/python-api/overlays/prod
```

## 부록: GitLab CI 로 돌리기 (Jenkins 대체)

Jenkins(5장) 대신 GitLab 내장 CI 와 러너로 같은 흐름을 돌리는 구성. 컴포넌트가 하나 줄고
`ci/.gitlab-ci.yml` 하나면 된다. CI 는 하나만 고른다.

| 구성 | git 호스트 | CI | 파이프라인 파일 |
|---|---|---|---|
| **GitLab + Jenkins** (기본, 5·7장) | GitLab CE | Jenkins | `ci/Jenkinsfile` |
| GitLab 단독 (이 부록) | GitLab CE | GitLab CI + Runner | `ci/.gitlab-ci.yml` |

> ⚠️ **앱 리포지토리에는 둘 중 하나만 둔다.** `Jenkinsfile` 과 `.gitlab-ci.yml` 이 같이 있으면
> push 한 번에 두 CI 가 모두 돌아 같은 overlay 에 이미지 태그를 커밋하고 서로 밀어낸다.
> Jenkins 에서 옮겨 온다면 Jenkins 의 `react-app` 잡도 비활성화한다.

순서는 본문 1~4 → A.1 → 6 → A.2~A.4 다(5·7장 대신).

### A.1 GitLab Runner 등록

GitLab 은 설치만으로 CI 가 돌지 않는다. 잡을 실행할 러너가 따로 필요하다.
러너는 토큰이 있어야 기동되므로 **GitLab 이 뜬 뒤에** 적용한다.

1. **토큰 발급** — **Admin Area > CI/CD > Runners > New instance runner**
   - Tags: `build`
   - *Run untagged jobs* 체크 (태그 없는 잡도 받게)
   - **Create runner** → 화면의 authentication token(`glrt-...`)을 복사한다.
     이 화면을 벗어나면 토큰을 다시 볼 수 없다(다시 만들어야 한다).
     그 아래 `gitlab-runner register` 안내는 따르지 않는다 — 토큰을 config.toml 에 바로 넣는 방식이라 필요 없다.

2. **토큰 Secret 생성** — `runner.yaml` 에는 토큰을 적지 않는다(2.2·4.2 와 같은 이유).

```bash
read -rsp 'runner token (glrt-...): ' RT; echo
# glrt- 로 시작하지 않으면(빈 값·잘못 붙여 넣기) 만들지 않는다
[[ $RT == glrt-* ]] && kubectl -n gitlab create secret generic gitlab-runner-secrets \
  --from-literal=RUNNER_TOKEN="$RT" --dry-run=client -o yaml | kubectl apply -f -
unset RT
kubectl -n gitlab get secret gitlab-runner-secrets
```

3. **러너 적용** — `bootstrap/gitlab/kustomization.yaml` 에서 `runner.yaml` 줄의 주석을 해제한다(기본은 제외).
   GitLab 본체도 같은 kustomization 이므로, 러너 외에 바뀌는 게 없는지 먼저 diff 로 본다.

```bash
kubectl kustomize bootstrap/gitlab | kubectl diff -f - | grep -E '^(\+\+\+|---) '
# gitlab-runner 관련 리소스만 나오면 적용한다. gitlab StatefulSet 등이 보이면 멈추고 내용을 확인한다
kubectl kustomize bootstrap/gitlab | kubectl apply -f -
kubectl -n gitlab rollout status deploy/gitlab-runner
kubectl -n gitlab logs deploy/gitlab-runner --tail=20
```

로그에 `Configuration loaded` 와 `Starting multi-runner` 가 뜨고, Admin Area > CI/CD > Runners 목록에서
러너가 **Online**(초록 점)이면 성공이다. glrt 토큰은 등록 절차가 없으므로 `Registering runner` 줄은 나오지 않는다.
잡 Pod 는 `gitlab` 네임스페이스에 `gitlab-runner-job` 서비스 어카운트로 뜬다(권한 없음).

> - 토큰이 틀리면 로그에 `403 Forbidden` 이 반복된다. 2 를 다시 실행하고
>   `kubectl -n gitlab rollout restart deploy/gitlab-runner` — 환경변수는 Pod 시작 때만 읽힌다.
> - Pod 가 `CreateContainerConfigError` 면 2 의 Secret 이 없는 것이다.
> - **잡 파드의 이미지는 Nexus(3장)를 거친다.** `config.toml` 의 `image` 와 `helper_image` 가
>   `nexus-docker-group.example.com/...` 을 가리킨다. 러너 자체는 Nexus 없이도 Online 이 되지만,
>   **잡은 3장을 끝낸 뒤에야 돈다.** 여기서 먼저 잡을 돌려 보고 싶으면 두 줄을 각각
>   `alpine:3.22`, `registry.gitlab.com/gitlab-org/gitlab-runner/gitlab-runner-helper:x86_64-v17.11.0`
>   으로 되돌리면 된다(외부 인터넷 필요).
> - **러너 버전을 올릴 때는 `helper_image` 태그도 같이 올린다.** 본체(`gitlab/gitlab-runner:v17.11.0`)와
>   helper 의 버전이 어긋나면 잡이 실패한다. helper 는 `.gitlab-ci.yml` 에 안 보이는 숨은 컨테이너라
>   `git clone` 단계에서 엉뚱하게 터진다.

### A.2 CI/CD 변수 등록

`my-group/react-app` 을 7.2 처럼 만든 뒤 Settings > CI/CD > **Variables** > Add variable.
**모든 변수에서 *Protect variable* 체크를 끈다.** 켜 두면 보호 브랜치(`main`)에서만 값이 들어가
`develop` 파이프라인이 빈 값으로 실패한다.

| Key | Value | Visibility |
|---|---|---|
| `DOCKER_REGISTRY` | `nexus-docker.example.com` | Visible |
| `DOCKER_USER` | Nexus 사용자 (push 권한) | Visible |
| `DOCKER_PASSWORD` | Nexus 비밀번호 | Masked |
| `GITOPS_REPO` | `gitlab.example.com/my-group/gitops-manifests.git` | Visible |
| `GITOPS_USER` | `ci-bot` | Visible |
| `GITOPS_TOKEN` | `~/gitlab-group-token.txt` (2.4) | Masked |
| `ARGOCD_SERVER` | `argocd.example.com:80` | Visible |
| `ARGOCD_AUTH_TOKEN` | `~/cicd-argocd-token.txt` (4.4) | Masked |

Masked 는 값이 8자 이상이고 공백이 없어야 저장된다. 앱 리포지토리 체크아웃은 GitLab CI 가
잡 토큰으로 하므로, 그룹 토큰은 gitops push 에만 쓰인다(gitops 프로젝트 전용 Project access
token 을 Maintainer / `write_repository` 로 발급해 대신 써도 된다).

### A.3 파이프라인 배치와 dev 배포

7.1 에서 `Jenkinsfile` 대신 `.gitlab-ci.yml` 을 복사한다.

```bash
cd ~/workspace/react-app
rm -f Jenkinsfile
cp ~/workspace/cicd/ci/.gitlab-ci.yml .
git add -A && git commit -m "ci: GitLab CI"
git push -u origin main -o ci.skip       # main 은 prod 용이라 이 push 는 파이프라인을 건너뛴다
git push origin main:develop             # develop → dev 파이프라인 시작
```

`react-app` > Build > **Pipelines** 에서 `build-test → docker-build-push → deploy-dev → wait-dev` 가
모두 초록이면 된다. 확인 방법은 7.4 와 같다. gitops-manifests 에는
`chore(dev): react-app -> develop-<sha> [skip ci]` 커밋이 생긴다.

### A.4 prod 배포와 자주 막히는 곳

```bash
git push origin main       # main 파이프라인: build-test → docker-build-push → deploy-prod(수동)
```

Pipelines 에서 `deploy-prod` 의 ▶ 를 누르면 태그 커밋 후 `wait-prod` 가 sync 까지 건다.
사전 조건과 확인 방법은 7.5 와 같다.

GitLab CI 에만 있는 증상(나머지는 7.6 과 같다):

| 증상 | 원인 |
|---|---|
| `Unable to create pipeline` + 잡 0개 | `.gitlab-ci.yml` 문법. script 한 줄에 따옴표 없이 `: `(콜론+공백)가 들어가면 YAML 이 문자열이 아닌 맵으로 읽어 `script config should be a string ...` 가 난다 → 줄 전체를 `'...'` 로 감싼다. Build > Pipeline editor > Validate 로 미리 확인 |
| 잡이 `pending` | 러너 Offline (A.1) 또는 태그 불일치 |
| `build-test` 가 이미지 pull 실패 | Nexus docker-group 익명 pull (3.3) |
| kaniko `UNAUTHORIZED` | `DOCKER_USER`/`DOCKER_PASSWORD`, 또는 변수가 Protected |
| `git clone` 단계에서 엉뚱하게 실패 | 러너 본체와 `helper_image` 버전 불일치 (A.1) |
