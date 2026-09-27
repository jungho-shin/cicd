# 5. 컨테이너 이미지와 레지스트리

## 5.1 Dockerfile 멀티 스테이지 빌드: `samples/react-app/Dockerfile`

### 학습 노트: 공장에서 만들고 매장에는 완제품만 (2026-09-27)

- **비유**: `build` 스테이지 = 공장(공구·재료·자투리 잔뜩), 런타임 스테이지 = 매장 진열대.
  공장에서 만든 **완제품(`dist/`)만** 트럭에 실어 매장으로 옮긴다. 공장은 최종 이미지에 남지 않는다.
- **두 개의 `FROM`** 이 곧 두 스테이지다.

| 스테이지 | 베이스 (`Dockerfile` 줄) | 하는 일 | 최종 이미지에 남나 |
|---|---|---|---|
| `build` | `node:22-alpine` (`:6`) | `npm ci` → `npm run build` → `/app/dist` | 아니오 |
| 런타임 | `nginx-unprivileged:1.28-alpine` (`:18`) | `COPY --from=build /app/dist` (`:21`) 후 nginx 로 서빙 | 예 |

- **왜 나누나**: 최종 이미지에 node, `node_modules`, 소스 코드가 없다 → 이미지가 작고, 공격 표면(쓸 수 있는
  도구)이 줄고, 소스가 새지 않는다. 브라우저가 받을 정적 파일만 있으면 되기 때문에 가능하다.
- **줄 순서의 이유 (레이어 캐시)**: `package*.json` 만 먼저 복사하고 `npm ci` (`:8-9`), 그다음 소스 전체
  (`:10`). 소스만 바뀌면 `npm ci` 레이어를 재사용한다. `ARG APP_VERSION` 도 `npm ci` 뒤(`:12`)에 둬서
  태그가 바뀌어도 설치 레이어가 깨지지 않는다.
  - 단, 이 프로젝트의 kaniko 는 `--cache` 를 쓰지 않고(`ci/Jenkinsfile:101-105`) 에이전트 Pod 도 매번 새로 뜨므로
    CI 에서는 매번 처음부터 빌드한다. 캐시 이득은 로컬 `docker build` 에서 본다.
- **`ARG REGISTRY` (`:3`)**: `FROM` 보다 앞에 둔 ARG 는 `FROM` 줄에서만 쓸 수 있다. 베이스 이미지를 Nexus
  docker-group(Docker Hub 프록시)에서 받게 해 요청 한도를 피한다(5.5 에서 자세히).
- **그 밖의 줄**: `USER 10001` (`:23`) 은 5.2, `docker-entrypoint.sh`·`nginx.conf` 는 5.3 에서.
  `ENTRYPOINT ["/bin/sh", ...]` (`:26`) 는 Windows 에서 편집하면 실행 비트가 사라지는 문제를 피하려는 것.

#### 확인 문제 풀이 (2026-09-27) — 2/3

1. 최종 이미지에 든 것 → **nginx + `dist/` 내용 + `nginx.conf` + entrypoint** (정답).
2. `src/App.jsx` 만 고치고 재빌드 → **`npm ci` 까지 캐시, `COPY . .` 부터 재실행** (정답).
3. 런타임 스테이지에 `RUN npm --version` → **빌드 실패** (모름). 스테이지마다 파일시스템이 따로다.
   런타임 스테이지는 `nginx-unprivileged` 이미지에서 새로 시작하므로 node/npm 이 없고(`/bin/sh: npm: not found`),
   build 스테이지에서 가져올 수 있는 건 `COPY --from=build` 로 **명시한 파일뿐**이다. 비유: 매장에서 공장 기계를
   쓸 수 없다 — 트럭에 실어 온 완제품만 있다.

## 5.2 non-root 실행(UID 10001), 읽기 전용 루트 파일시스템

### 학습 노트: 열쇠 없는 직원과 코팅된 매뉴얼 (2026-09-27)

- **비유**: 컨테이너 = 매장 직원. **non-root** = 마스터키가 없는 직원(자기 일만 할 수 있음).
  **읽기 전용 루트 FS** = 매장 매뉴얼을 코팅해 둠(고쳐 쓸 수 없음). 메모가 필요하면 **`/tmp` 메모장(emptyDir)** 에만 쓴다.
  도둑(침입자)이 직원 몸에 들어와도 마스터키도 없고 매뉴얼도 못 고친다.
- **설정 위치 — 이미지와 매니페스트 양쪽**

| 설정 | 위치 | 효과 |
|---|---|---|
| `USER 10001` | `samples/react-app/Dockerfile:23` | 이미지 기본 실행 사용자 |
| `runAsNonRoot: true`, `runAsUser: 10001` | `manifests/react-app/base/deployment.yaml:23-24` | kubelet 이 UID 0 이면 기동 거부 / 이미지 USER 를 덮어씀 |
| `seccompProfile: RuntimeDefault` | `deployment.yaml:25-26` | 위험한 시스템 콜 차단 |
| `allowPrivilegeEscalation: false` | `deployment.yaml:57` | setuid 등으로 권한 상승 불가 |
| `readOnlyRootFilesystem: true` | `deployment.yaml:58` | `/` 아래 쓰기 불가 |
| `capabilities: drop: ["ALL"]` | `deployment.yaml:59-60` | root 일부 권한(포트 <1024 바인딩 등)도 전부 제거 |
| `/tmp` ← `emptyDir` | `deployment.yaml:61-66` | 유일한 쓰기 가능 경로 |

- **읽기 전용이라 앱 쪽이 맞춰 준 것**: nginx 의 pid 파일·임시 디렉터리를 `/tmp` 로 (`nginx.conf:7`, `:22-26` —
  기본값 `/var/cache/nginx` 는 쓰기 불가라 기동 실패), 로그는 파일 대신 stdout/stderr (`:8`, `:17`),
  런타임 설정 `config.js` 도 `/tmp` 에 생성 (`docker-entrypoint.sh:6`).
- **포트 8080 인 이유**: 1024 미만 포트는 root 또는 `NET_BIND_SERVICE` capability 가 필요한데 둘 다 없다.
  그래서 `nginx-unprivileged` 이미지(8080 대기)를 쓰고 Service 가 80 → 8080 으로 연결한다.
- 1장 복습 연결: 쓰기 경로를 빠뜨리면 이미지 pull 은 되고 **기동 중 죽는다 → CrashLoopBackOff** (ImagePullBackOff 아님).

#### 확인 문제 풀이 (2026-09-27) — 1/3

1. `/tmp` emptyDir 제거 → 답 **CrashLoopBackOff** (ImagePullBackOff 라고 오답 — 1장에서도 헷갈렸던 지점).
   이미지는 레지스트리에서 **잘 받아진다**. 문제는 그다음: 볼륨이 없으면 `/tmp` 도 읽기 전용 루트 FS 의 일부라
   `docker-entrypoint.sh:6` 의 `cat > /tmp/config.js` 가 `Read-only file system` 으로 실패 → `set -eu` 로 즉시 종료
   → kubelet 이 재시작 반복.
   구분법: **Pull 단계(레지스트리·태그·regcred 문제) = ImagePullBackOff / 실행 단계(설정·권한·파일 문제) = CrashLoopBackOff.**
2. `runAsUser` 만 지우고 `runAsNonRoot: true` 유지 → 답 **이미지의 `USER 10001` 로 정상 실행** (모름).
   `runAsUser` 가 없으면 이미지의 USER 를 쓰고, kubelet 은 그 UID 가 0 이 아닌지만 검사한다. 10001 이라 통과.
   반대로 이미지가 root 였다면 기동 거부(CreateContainerConfigError: `container has runAsNonRoot and image will run as root`).
   이미지 USER 가 숫자가 아닌 이름(`nginx` 등)이어도 kubelet 이 확인할 수 없어 같은 에러가 난다 — Dockerfile 이 숫자 UID 를 쓰는 이유.
   양쪽에 같은 값을 적은 건 이중 안전장치(`Dockerfile:22` 주석).
3. 8080 인 이유 → **1024 미만 포트는 root 또는 `NET_BIND_SERVICE` 필요** (정답).

## 5.3 런타임 설정 주입: `docker-entrypoint.sh`, `nginx.conf`

### 학습 노트: 인쇄된 책과 개점 때 끼우는 쪽지 (2026-09-27)

- **문제**: React 빌드 결과(`dist/`)는 정적 파일이다. 브라우저에서 돌기 때문에 컨테이너의 환경변수를 읽을 수 없다.
- **비유**: 빌드 결과 = 인쇄된 책(인쇄 후엔 못 고침). 환경별 값 = 매장 개점 때 책 앞에 끼우는 **쪽지**.
  같은 책을 dev 매장·prod 매장에 두고 쪽지만 다르게 끼운다.
- **두 종류의 값**

| 값 | 들어가는 시점 | 경로 | 바꾸려면 |
|---|---|---|---|
| `version` | **빌드 때** (인쇄) | `--build-arg APP_VERSION` → `Dockerfile:12-13` `VITE_APP_VERSION` → JS 에 문자열로 박힘 (`src/main.jsx:12`) | 이미지 재빌드 |
| `env` | **컨테이너 시작 때** (쪽지) | 아래 흐름 | 매니페스트만 수정 |

- **`env` 의 흐름 (5단계)**
  1. overlay 가 JSON patch 로 `APP_ENV` 값을 `dev`/`prod` 로 바꾼다 (`overlays/dev/kustomization.yaml:27-28`).
  2. `docker-entrypoint.sh:6-8` 이 시작할 때 `/tmp/config.js` 에 `window.APP_CONFIG = { env: "dev" }` 를 쓴다
     (루트 FS 가 읽기 전용이라 `/tmp` — 5.2).
  3. `exec nginx` (`:10`) — 셸을 nginx 로 **갈아 끼워** nginx 가 PID 1 이 된다 → 종료 신호(SIGTERM)를 직접 받는다.
  4. `nginx.conf` 의 `location = /config.js { alias /tmp/config.js; }` — 브라우저가 `/config.js` 를 요청하면
     이미지 안 파일 대신 `/tmp` 파일을 준다. `Cache-Control: no-store` 로 브라우저가 옛 값을 캐시하지 않게.
  5. `index.html:12` 이 앱 번들보다 **먼저** `<script src="/config.js">` 를 읽고, `src/main.jsx:5` 가 `window.APP_CONFIG` 를 쓴다.
- **로컬 개발**: `public/config.js` (`env: 'local'`) 가 쓰인다. 이 파일은 `dist/` 에도 복사돼 이미지에 들어 있지만
  nginx 의 `location =` (정확히 일치) 가 우선이라 컨테이너에서는 가려진다.
- 같은 `nginx.conf` 의 다른 캐시 규칙: `/assets/` 는 파일명에 해시가 붙어 1년 캐시, `index.html` 은 `no-cache`,
  모르는 경로는 `try_files $uri /index.html` (SPA 라우팅).

#### 확인 문제 풀이 (2026-09-27) — 2/3

1. prod env 표시를 `production` 으로 → **overlay 값만 수정** (정답). Pod 템플릿이 바뀌므로 롤링 업데이트로 새 Pod 가
   새 쪽지를 쓴다. 이미지 재빌드 불필요.
2. version 을 바꾸려면 → **이미지 재빌드** (정답). 빌드 때 JS 에 박힌 값.
3. `location = /config.js` 블록 삭제 → 답 **`local` 이 보인다** (모름).
   - `/config.js` 요청이 `location /` 로 떨어지고 `try_files $uri` 가 `/usr/share/nginx/html/config.js` 를 찾는다.
   - Vite 는 `public/` 을 `dist/` 로 그대로 복사하므로 이미지 안에 `public/config.js` (`env: 'local'`) 가 **있다** → 200 으로 서빙.
   - entrypoint 는 여전히 `/tmp/config.js` 를 잘 쓰므로 Pod 는 Running·Ready, 404 도 크래시도 없다.
   - **조용한 버그**: 쿠버네티스 입장에선 전부 정상인데 화면만 틀린다. 컨테이너 안 `/tmp/config.js` 와
     브라우저가 받은 `/config.js` 를 비교(`curl http://react-app.dev.example.com/config.js`)해야 찾는다.

## 5.4 kaniko 로 Docker 데몬 없이 이미지 빌드하는 이유

### 학습 노트: 공장 없이 손으로 조립하는 기술자 (2026-09-27)

- **문제**: `docker build` 는 **Docker 데몬**(root 로 도는 상주 프로세스)에게 일을 시키는 명령이다.
  그런데 빌드는 에이전트 **Pod 안**에서 돈다(4.2). 그리고 kind 노드는 Docker 가 아니라 **containerd** 로 컨테이너를 돌린다
  → 노드에 Docker 데몬이 없다.
- **흔한 우회책과 문제점**

| 방법 | 문제 |
|---|---|
| 노드의 `docker.sock` 마운트 | 소켓을 쥔 Pod = 노드 root 권한. 게다가 kind 노드엔 Docker 소켓 자체가 없다 |
| DinD (Pod 안에 Docker 데몬) | `privileged: true` 필요 → 컨테이너 격리를 사실상 포기 |
| **kaniko** | 데몬도, privileged 도 필요 없음 |

- **비유**: docker build = 공장(데몬)에 주문서를 넣는 것. kaniko = 공구 가방 든 기술자가 **자기 작업대(자기 컨테이너 파일시스템)
  위에서** 직접 조립하고 완제품을 **곧바로 창고(Nexus)로 배송**.
- **kaniko 동작**: 베이스 이미지 레이어를 자기 컨테이너 FS 에 풀기 → Dockerfile 의 `RUN` 을 그 자리에서 실행 →
  바뀐 파일을 스냅숏해 레이어로 → 레지스트리에 **직접 push** (`ci/Jenkinsfile:101-105`).
  로컬 이미지 저장소가 없으므로 결과물은 **Nexus 에만** 있다. 노드는 Pod 를 띄울 때 Nexus 에서 pull 한다.
- **이 프로젝트에서 본 흔적**
  - `casc.yaml:89` 이미지 `executor:v1.23.2-debug` — `-debug` 에만 busybox 셸이 있다. 셸이 있어야
    `command: /busybox/cat` (`:90`) 으로 대기하고 Jenkins `sh` 단계를 실행할 수 있다.
  - 인증 파일을 Jenkinsfile 이 직접 만든다 (`ci/Jenkinsfile:93-95`, `/kaniko/.docker/config.json`) —
    Secret 볼륨으로 붙이면 파일명이 `.dockerconfigjson` 이라 kaniko 가 못 찾는다(`casc.yaml:119-122`).
  - `--insecure --skip-tls-verify --insecure-pull` — Nexus 가 평문 HTTP 라서(6장 README 3.4).
- **한계(솔직히)**: kaniko 도 자기 컨테이너 **안에서는 root** 로 돈다(파일시스템을 통째로 풀어야 하므로). 노드 root 를 넘기지
  않는다는 점이 차이. 또 원 프로젝트(GoogleContainerTools/kaniko)는 2025년에 보관(archived) 처리되어, 새로 만든다면
  포크나 BuildKit rootless·buildah 같은 대안도 검토 대상이다.

#### 확인 문제 풀이 (2026-09-27) — 3/3

1. `-debug` 태그 → **busybox 셸이 있어야 `/busybox/cat` 대기 + `sh` 단계 실행 가능** (정답).
2. `docker.sock` 마운트 → **kind 노드는 containerd 라 소켓이 없고, 있어도 노드 root 를 넘기는 셈** (정답).
3. 빌드 직후 노드 `crictl images` → **안 보인다. Nexus 로 바로 push, 노드는 앱 Pod 기동 때 pull** (정답).
   덧: 그래서 다음 항목의 Nexus 구성과 노드 pull 인증(`regcred`)이 필요해진다.

## 5.5 Nexus: docker-hosted / docker-proxy / docker-group (README 3.3)

### 학습 노트: 자체 창고, 수입 대행, 통합 매장 (2026-09-27)

- **비유**
  - **hosted** = 우리 회사 **자체 창고**. 우리가 만든 물건(앱 이미지)을 넣고 꺼낸다. push 가능.
  - **proxy** = **수입 대행**. 외국 공급처(Docker Hub, gcr.io, quay.io)에서 처음 한 번 사 와서 쌓아 두고,
    다음부터는 쌓아 둔 것을 준다(캐시). push 불가.
  - **group** = **통합 매장 창구**. 여러 창고를 하나의 입구로 묶어, 정해진 순서대로 뒤져서 먼저 찾은 것을 준다. pull 전용.
- **이 프로젝트의 구성 (README 3.3 표)**

| 리포지토리 | 타입 | 입구 (포트 / 호스트) | 누가 쓰나 |
|---|---|---|---|
| `docker-hosted` | hosted | 8082 / `nexus-docker.example.com` | kaniko push, 노드가 앱 이미지 pull (`regcred`) |
| `docker-hub`, `gcr-proxy`, `quay-proxy` | proxy | (직접 안 씀) | group 을 통해서만 |
| `docker-group` | group | 8083 / `nexus-docker-group.example.com` | 베이스 이미지(`Dockerfile:3`), 에이전트 이미지(`casc.yaml:89,94,99`) 익명 pull |

  포트는 Nexus 컨테이너(`bootstrap/nexus/nexus.yaml:34,37`), 호스트명은 Ingress(`bootstrap/nexus/ingress.yaml:56,66`).
- **왜 쓰나**: Docker Hub 요청 한도 회피, 외부 장애·느린 회선에도 캐시로 빌드 지속, 사내 앱 이미지 보관을 한곳에서.
- **멤버 순서가 중요**: `docker-hosted → docker-hub → gcr-proxy → quay-proxy`. 같은 이름이 여러 곳에 있으면 **앞 멤버가 이긴다.**
  예: `argoproj/argocd` 는 Docker Hub 에도 있지만 `v3.5.2` 태그가 없다 → 순서가 바뀌면 엉뚱한 곳을 먼저 뒤진다(README 3.3).
- **익명 pull 은 group 에만**, hosted 는 인증(`regcred`, 5.6). 단 **빈틈**: group 멤버에 `docker-hosted` 가 들어 있으므로
  앱 이미지도 `nexus-docker-group.example.com/my-group/react-app:...` 으로 **익명 pull 된다**(README 3.3 인용 블록이 스스로 경고).
  막으려면 group 멤버에서 `docker-hosted` 를 뺀다. 로컬 학습용이라 편의를 택한 것.

#### 확인 문제 풀이 (2026-09-27) — 3/3

1. kaniko push 목적지 → **`docker-hosted` (8082)** (정답). group 은 pull 전용.
2. 인터넷 끊김 + 어제 받은 `node:22-alpine` → **proxy 캐시로 성공** (정답). 단 처음 받는 이미지·태그는 실패하고,
   `latest` 처럼 움직이는 태그는 캐시 만료 후 원격 확인을 시도할 수 있다 — 고정 태그를 쓰는 이유 하나.
3. 같은 이름·태그가 hosted 와 docker-hub 에 → **멤버 순서상 앞 (`docker-hosted`)** (정답).
   덧: 이 순서는 **보안 장치**이기도 하다. proxy 가 hosted 보다 앞이면 누군가 Docker Hub 에 같은 이름을 올려
   사내 이미지를 가로챌 수 있다(dependency confusion 과 같은 원리).

## 5.6 이미지 pull 인증: `regcred` Secret (README 3.5)

### 학습 노트: 창고 출입증은 트럭 기사가 쓴다 (2026-09-27)

- **비유**: 자체 창고(`docker-hosted`)는 출입증이 있어야 물건을 내준다. 물건을 가지러 가는 사람은 매장 직원(앱 컨테이너)이
  아니라 **트럭 기사 = 노드의 kubelet/containerd** 다. `regcred` 는 기사에게 쥐여 주는 출입증이다.
  → 컨테이너 안에 마운트되지 않고, 앱은 이 값을 볼 수 없다.
- **연결 구조**

| 조각 | 위치 |
|---|---|
| 참조 (이름만) | `manifests/react-app/base/deployment.yaml:67-68` `imagePullSecrets: [{name: regcred}]` |
| 실제 Secret | **Git 에 없음.** 네임스페이스마다 손으로 `kubectl create secret docker-registry` (README 3.5) |
| 타입·키 | `kubernetes.io/dockerconfigjson`, 키 `.dockerconfigjson` (`auths.<서버>.auth` = `user:pass` 의 base64) |

- **왜 Git 에 없나**: 비밀번호가 평문(base64 는 암호화가 아니다)으로 리포에 남으면 안 되니까. 대가: 클러스터를 다시 만들거나
  네임스페이스를 지우면 **다시 만들어야** 한다. (SealedSecrets·External Secrets 같은 도구가 이 빈틈을 메운다.)
- **네임스페이스 범위**: Pod 는 **자기 네임스페이스의 Secret 만** 참조할 수 있다 → `react-app-dev`, `react-app-prod`,
  `python-api-dev`, `python-api-prod` 네 곳에 각각 만든다.
- **2장 복습 연결**: `regcred` 는 kustomization 의 resources 가 아니므로 `namePrefix` 가 붙지 않고, 참조도 `regcred` 그대로 남는다
  (외부 리소스 참조). Argo CD 가 관리하지 않으므로 prune 대상도 아니다.
- **push 자격증명과 별개**: Jenkins 의 `nexus-registry` (push, kaniko `config.json`, 4.3·5.4) ↔ `regcred` (pull, 노드).
  원칙은 pull 전용 계정을 따로 두는 것(최소 권한). `jenkins` 네임스페이스에는 `regcred` 를 만들지 않는다.
- **없거나 틀리면**: `ImagePullBackOff` — 이벤트에 `401 Unauthorized` / `no basic auth credentials`.
  **Pull 단계 실패**이므로 CrashLoopBackOff 가 아니다(5.2 표). 확인은 README 3.5 의 `curl .../v2/` → 200/401.
- **발견한 빈틈 (8장에서)**: Image Updater 설정이 `pullsecret:argocd/regcred` 를 참조하지만(`bootstrap/image-updater/config.yaml:22`)
  README 3.5 의 루프는 `argocd` 네임스페이스에 `regcred` 를 만들지 않는다.

#### 확인 문제 풀이 (2026-09-27) — 3/3

1. prod 에 `regcred` 누락 → **ImagePullBackOff** (정답 — 1·5.2 에서 헷갈리던 pull/run 구분을 이번엔 맞힘).
   덧: `imagePullPolicy: IfNotPresent` (`deployment.yaml:30`) 라 **노드에 같은 이미지가 이미 있으면** pull 을 안 해서
   `regcred` 없이도 뜰 수 있다. dev(`develop-*`)와 prod(`main-*`)는 태그가 달라 여기선 해당 없지만,
   "어떤 노드에선 되고 어떤 노드에선 안 되는" 증상의 흔한 원인이다.
2. dev 의 `regcred` 를 prod 가 빌려 쓰기 → **불가, 같은 네임스페이스만** (정답 — 1장의 Namespace 약점 해소).
3. `regcred` 사용 주체 → **노드의 kubelet/containerd** (정답).

## 5.7 이미지 태그 규칙: `<브랜치 slug>-<SHA 8자리>`

### 학습 노트: 택배 송장 번호 (2026-09-27)

- **비유**: 이미지 태그 = 택배 **송장 번호**. `latest` 는 "최근 택배" 라는 쪽지 — 누가 언제 붙였는지, 어제 것과 같은지 알 수 없다.
  송장 번호는 **한 번 붙으면 그 상자만 가리킨다** → 추적·반품(롤백)이 된다.
- **만드는 곳**: `ci/Jenkinsfile:47-55`

| 단계 | 예 (`feature/Add_Button`, 커밋 `3f9a1c7e5b...`) |
|---|---|
| `BRANCH_NAME` (앞의 `origin/` 제거) | `feature/Add_Button` |
| 소문자 + 영숫자 외 → `-` (`:52`) | `feature-add-button` |
| 앞뒤 `-` 제거 (`:53`) | `feature-add-button` |
| SHA 앞 8자리 (`:54`) | `3f9a1c7e` |
| 결과 `IMAGE_TAG` (`:55`) | `feature-add-button-3f9a1c7e` |

  slug 가 필요한 이유: Docker 태그에는 `/` 를 쓸 수 없다(허용: 영문·숫자·`_` `.` `-`, 최대 128자).
- **실제 태그 예**: `develop-3f9a1c7e` → dev, `main-3f9a1c7e` → prod. **환경 이름(dev/prod)이 아니라 브랜치 이름**이 들어간다
  (2장에서 "환경 이름 + 5자리" 로 잘못 기억했던 부분).
- **push 되는 태그는 2개** (`:103-104`): `IMAGE_TAG` 와 **전체 40자 SHA**. gitops 에 적히는 건 `IMAGE_TAG` (`:138`).
  전체 SHA 태그는 "이 커밋의 이미지가 있나?" 를 브랜치 무관하게 찾을 때 쓴다.
- **같은 태그가 쓰이는 곳**: 이미지 태그 · 화면 version (`--build-arg APP_VERSION`, `:102`) · prod 승인 메시지 (`:117`) ·
  gitops 커밋 메시지 (`:147`). 화면에 보이는 version 으로 **어느 커밋인지 바로 역추적**할 수 있다.
- **왜 `latest` 를 안 쓰나** (1장 복습: "latest = 자동으로 최신" 이 아니다)
  1. Git 의 kustomization 이 안 바뀌면 Argo CD 는 바뀐 걸 모른다 → 배포가 안 일어난다.
  2. `IfNotPresent` 면 노드가 옛 `latest` 를 그대로 쓴다 → 노드마다 다른 버전.
  3. 롤백 = gitops 커밋 revert 인데, 태그가 `latest` 뿐이면 되돌릴 대상이 없다.
- **빈틈 (정리)**
  - Nexus `docker-hosted` 가 `Allow redeploy` (README 3.3) → 같은 태그를 **덮어쓸 수 있다.** 같은 커밋을 다시 빌드하면
    같은 태그로 다른 이미지가 올라간다. 엄격히 하려면 `Disable redeploy` (불변 태그).
  - overlay 의 초기값 `dev-0000000`·`v0.1.0` (`overlays/*/kustomization.yaml:19`) 은 규칙과 다른 자리표시자 — 첫 CI 가 덮어쓴다.
  - Image Updater 예시 정규식 `^dev-[0-9a-f]{7}$` (README 2225행) 은 `develop-<8자리>` 와 절대 맞지 않는다 (8장에서).

#### 확인 문제 풀이 (2026-09-27) — 1/3

1. `release/1.2` + `a1b2c3d4e5f6...` → **`release-1-2-a1b2c3d4`** (정답). `/` 뿐 아니라 `.` 도 `-` 가 된다(`[^a-z0-9]+`).
2. `main` 커밋의 prod 태그 → 답 **`main-9e8d7c6b`** (`prod-...` 라고 오답 — 2장과 **같은 오해가 반복**).
   태그 재료는 `env.BRANCH` 뿐이고(`Jenkinsfile:52-55`), `DEPLOY_ENV` 는 그 **뒤**(`:58-64`)에 따로 정해진다.
   태그 = "어디서 왔나"(출신 브랜치), 환경 = "어디로 가나"(목적지). 송장 번호에는 보낸 곳이 찍히지, 받는 매장 이름이 찍히지 않는다.
   prod 로 가는 이미지가 `main-` 으로 시작하는 건 main 만 prod 로 보내는 규칙(4.6) 때문일 뿐이다.
3. `latest` 고정 → 답 **Git 변화 없음 → Argo CD 배포 안 함, 노드도 옛 이미지** (자동 배포된다고 오답 — 1장 오해 반복).
   Argo CD 는 **Git 만 본다**(3.1). kustomization 의 `newTag: latest` 가 그대로면 렌더링 결과도 그대로 → Synced, 할 일 없음.
   Pod 를 손으로 재시작해도 `IfNotPresent` 면 노드 캐시의 옛 `latest` 를 쓴다. "새 이미지를 올렸다" 와 "배포됐다" 는 별개의 사건이고,
   둘을 잇는 게 **gitops 태그 커밋(⑤단계)** 이다 — 태그가 바뀌어야 Git 이 바뀌고, Git 이 바뀌어야 Argo CD 가 움직인다.

#### 재확인 (2026-09-27, 6.1 뒤) — 2/2

- `develop` 커밋 `1234abcd...` 의 화면 version → **`develop-1234abcd`** (정답, 보기 순서 섞음). 태그 = 출신 브랜치.
- image push 성공 + gitops 커밋 실패 → **dev 는 아무 변화 없음** (정답). Argo CD 는 Git 만 본다.
  → 5.7 에서 반복됐던 두 오해가 해소됨.
