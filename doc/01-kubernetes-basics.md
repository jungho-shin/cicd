# 1. Kubernetes 기초 리소스

## 이 장의 목표

react-app 이 클러스터에 뜨려면 어떤 리소스가 필요한지 안다.
Deployment → Pod → Service → Ingress 로 이어지는 요청 경로를 이 프로젝트 파일로 따라간다.
뒤 장(Kustomize, Argo CD)은 모두 이 장의 YAML 을 "어떻게 만들고 어떻게 적용하는가" 의 이야기다.

먼저 용어 두 개.

- **리소스(resource)**: 쿠버네티스에 "이런 것이 있어야 한다" 고 선언하는 YAML 한 덩어리. `kind:` 가 종류다.
- **컨트롤러(controller)**: 선언한 상태와 실제 상태를 계속 비교해 맞추는 프로그램. 사람이 "파드 3개 띄워" 라고
  명령하는 것이 아니라 "파드는 3개여야 한다" 고 적어 두면 컨트롤러가 알아서 맞춘다. 이 "선언 → 자동으로 맞춤" 이
  GitOps 의 기반이다.

## 1.1 Pod, Deployment, ReplicaSet 의 관계

| 리소스 | 한 줄 설명 |
|---|---|
| **Pod** | 컨테이너 1개 이상을 묶은 실행 단위. IP 를 하나 받는다. 죽으면 되살아나지 않고 새 Pod 로 대체된다 |
| **ReplicaSet** | "이 모양의 Pod 를 N 개 유지" 하는 컨트롤러 |
| **Deployment** | ReplicaSet 을 관리한다. 이미지가 바뀌면 새 ReplicaSet 을 만들어 **롤링 업데이트** 한다 |

사람은 Deployment 만 작성한다. ReplicaSet 과 Pod 는 Deployment 가 만든다.

```
Deployment react-app
  └─ ReplicaSet react-app-7d9f...   (이미지 태그가 바뀔 때마다 새로 생긴다)
       ├─ Pod react-app-7d9f...-abcde
       └─ Pod react-app-7d9f...-fghij
```

`manifests/react-app/base/deployment.yaml:1-21`

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: react-app
spec:
  replicas: 2
  revisionHistoryLimit: 5
  selector:
    matchLabels:
      app: react-app
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxSurge: 1
      maxUnavailable: 0
  template:
    metadata:
      labels:
        app: react-app
    spec:
      serviceAccountName: react-app
```

- `replicas: 2` — Pod 개수. 실제 값은 overlay 가 dev 1, prod 3 으로 덮어쓴다(2장).
- `selector.matchLabels` 와 `template.metadata.labels` — **라벨(label)** 은 리소스에 붙이는 이름표다.
  Deployment 는 `app: react-app` 이름표가 붙은 Pod 를 "내 것" 으로 센다. 두 값이 같아야 한다.
- `template` — 만들 Pod 의 설계도. 이 아래가 바뀌면(예: 이미지 태그) 새 ReplicaSet 이 생기고 롤링 업데이트가 시작된다.
- `revisionHistoryLimit: 5` — 옛 ReplicaSet 을 5개까지 남긴다. `kubectl rollout undo` 로 되돌릴 때 쓰인다.
  (GitOps 에서는 보통 git revert 로 되돌린다 — 3장)
- `maxSurge: 1`, `maxUnavailable: 0` — 새 Pod 를 1개 먼저 더 띄우고, 준비되면 옛 Pod 를 1개 내린다.
  업데이트 중에도 준비된 Pod 수가 줄지 않는다(무중단 배포).

### 컨테이너 설정

`manifests/react-app/base/deployment.yaml:27-55`

```yaml
      containers:
        - name: app
          image: nexus-docker.example.com/my-group/react-app:latest
          imagePullPolicy: IfNotPresent
          ports:
            - name: http
              containerPort: 8080
          env:
            - name: APP_ENV
              value: base
          resources:
            requests:
              cpu: 100m
              memory: 128Mi
            limits:
              cpu: 500m
              memory: 512Mi
          readinessProbe:
            httpGet:
              path: /healthz
              port: http
            ...
          livenessProbe:
            httpGet:
              path: /healthz
              port: http
            ...
```

- `image` — Nexus 레지스트리의 이미지. 태그 `latest` 는 자리표시자이고, 실제 태그는 Kustomize 의 `images` 가 바꾼다(2장).
- `ports.name: http` — 포트에 이름을 붙였다. Service 와 probe 가 숫자(8080) 대신 이름으로 가리킨다.
- `env.APP_ENV` — 환경 이름. overlay 가 `dev` / `prod` 로 바꾼다. 컨테이너의 `docker-entrypoint.sh` 가 이 값을 읽는다(5장).
- `resources` — `requests` 는 스케줄링할 때 "최소 이만큼 필요" 하다고 예약하는 양, `limits` 는 넘으면 제한되는 상한.
  `100m` 은 0.1 코어다. HPA(1.7)는 requests 대비 사용률로 계산하므로 requests 가 꼭 있어야 한다.
- **readinessProbe** — 통과해야 Service 가 트래픽을 보낸다. 롤링 업데이트는 새 Pod 가 ready 가 되어야 다음으로 넘어간다.
- **livenessProbe** — 실패가 이어지면 컨테이너를 재시작한다.

### 보안 설정

`manifests/react-app/base/deployment.yaml:22-26`, `56-66`

```yaml
      securityContext:
        runAsNonRoot: true
        runAsUser: 10001
        ...
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities:
              drop: ["ALL"]
          volumeMounts:
            - name: tmp
              mountPath: /tmp
      volumes:
        - name: tmp
          emptyDir: {}
```

root 가 아닌 UID 10001 로 돌고, 루트 파일시스템을 읽기 전용으로 둔다. 쓰기가 필요한 곳은 `/tmp` 하나뿐이고
여기에 `emptyDir`(Pod 가 살아 있는 동안만 있는 빈 디렉터리)을 붙였다. nginx 설정이 쓰기 경로를 `/tmp` 로
맞춘 이유가 이것이다(5장).

### 학습 노트: 왜 세 겹인가 (2026-09-27)

```
Deployment  ─ 만든다 →  ReplicaSet  ─ 만든다 →  Pod
(버전 교체 담당)        (개수 유지 담당)        (실제로 도는 앱)
```

아래에서부터 쌓아 보면 이유가 보인다.

1. **Pod 만 있다면**: 한 번 죽으면 끝이다. 스스로 되살아나지 않는다.
2. **ReplicaSet 이 개수를 책임진다**: "이 설계도의 Pod 를 N 개" 를 계속 세어 모자라면 만들고 남으면 지운다.
   drain 으로 내보낸 Pod 를 다시 채우는 것도 ReplicaSet 이다.
3. **Deployment 가 버전 교체를 책임진다**: ReplicaSet 은 설계도 하나만 든다. 버전이 바뀌면 Deployment 가 새 설계도로
   ReplicaSet 을 하나 더 만들고, 옛 것은 줄이고 새 것은 늘린다 = **롤링 업데이트**.

비유(프랜차이즈): Pod = 직원, ReplicaSet = "파란 유니폼 직원 3명 유지" 담당 매니저,
Deployment = "이제 초록 유니폼" 을 정하고 초록 담당 매니저를 새로 두어 한 명씩 교체시키는 본사.

**CI/CD 의 마지막 장면이 여기다.** Jenkins 가 gitops 에 태그 변경 커밋 → Argo CD 가 Deployment 의 image 변경
→ Deployment 가 새 ReplicaSet 생성 → 롤링 업데이트.

**prod(3개) 롤링 업데이트** — `maxSurge: 1`, `maxUnavailable: 0` (`manifests/react-app/base/deployment.yaml:12-15`)

| 단계 | 옛 RS (v1) | 새 RS (v2) | 합계 | 설명 |
|---|---|---|---|---|
| 시작 | 3 | 0 | 3 | |
| ① | 3 | 1 | 4 | 새 Pod 1개를 먼저 더 띄운다 (maxSurge 1) |
| ② | 2 | 1 | 3 | 새 Pod 가 Ready(readinessProbe 통과)면 옛 Pod 1개 종료 |
| ③ | 2 | 2 | 4 | 반복 |
| ④ | 1 | 2 | 3 | |
| ⑤ | 1 | 3 | 4 | |
| 끝 | 0 | 3 | 3 | 옛 RS 는 지우지 않고 0개로 남긴다 (롤백용, `revisionHistoryLimit: 5`) |

- Ready Pod 가 3개 밑으로 떨어지지 않는다 = 무중단 배포.
- 새 버전이 `/healthz` 에 실패하면 ① 에서 멈추고 옛 Pod 3개가 계속 서비스한다.

**새 ReplicaSet 이 생기는 경우**

| 변경 | 새 RS? | 이유 |
|---|---|---|
| 이미지 태그 | 생긴다 | `template` 변경 |
| 환경변수·probe·resources | 생긴다 | `template` 안의 내용 |
| `replicas` 변경 (HPA 포함) | 안 생긴다 | 개수만 바뀜 → 기존 RS 의 개수만 조정 |

HPA 가 Pod 를 늘리는 것은 배포가 아니다. 같은 버전을 더 만들 뿐이다.

**이름에 관계가 드러난다**

```
Deployment   prod-react-app
ReplicaSet   prod-react-app-7d9f8c6b5d          ← 설계도(template)의 해시
Pod          prod-react-app-7d9f8c6b5d-x2k4p    ← RS 이름 + 랜덤 5글자
```

**`revisionHistoryLimit` 이 두 군데 있다**

| 위치 | 값 | 무엇의 이력 |
|---|---|---|
| `manifests/react-app/base/deployment.yaml:7` | 5 | Kubernetes 가 남기는 옛 ReplicaSet 수 |
| `apps/react-app.yaml:32`, `:56` | 10 / 20 | Argo CD 가 남기는 sync 이력 수 (dev / prod) |

GitOps 에서 롤백은 gitops 리포지토리의 git revert 로 한다. `kubectl rollout undo` 는 Git 과 달라지므로
Argo CD 가 되돌려 버린다(dev 는 selfHeal).

**Q. prod 에서 `kubectl delete pod prod-react-app-...` 로 Pod 하나를 지우면?**
잠시 뒤 다시 3개. ReplicaSet 이 모자란 1개를 새로 만든다(이름 끝 5글자가 새로 붙음).

- 용어 구분: **ReplicaSet** 은 리소스, **`replicas`** 는 Deployment 에 적힌 개수 값이다.
- Pod 삭제는 사실상 "재시작" 이다. 정말 줄이려면 Deployment 의 `replicas` 를, GitOps 에서는 Git 의 값을 바꿔야 한다.

**Q. 새 버전의 `/healthz` 가 계속 실패하면 롤링 업데이트는?**
① 단계(옛 3 + 새 1)에서 멈추고, 사용자는 v1 을 본다.

| 누가 | 무슨 일 |
|---|---|
| 새 Pod (v2) | readinessProbe 실패 → Ready 아님 → Service 가 트래픽을 안 보냄. livenessProbe 도 `/healthz` 라 실패가 이어지면 컨테이너가 계속 재시작 |
| Deployment | ② 로 못 넘어감. 옛 Pod 3개가 계속 서비스. 기본 10분(`progressDeadlineSeconds`) 진척이 없으면 "진행 실패" 표시. **자동 롤백은 하지 않는다** |
| Argo CD | 앱 Health 를 Degraded 로 표시 |
| Jenkins | "배포 대기" 의 `argocd app wait --timeout 600` 실패 → 파이프라인 실패 (`ci/Jenkinsfile:179`) |

서비스는 멀쩡하지만 클러스터는 "v2 로 가려다 멈춘" 상태로 남는다. 되돌리기는 사람 몫이다.
GitOps 에서는 gitops 리포지토리의 해당 커밋을 `git revert` 하면 Argo CD 가 v1 으로 맞춘다(3장, 7장).

**확인 명령 (7장 구축 후)**

```bash
kubectl -n react-app-prod get deploy,rs,pod                        # 세 층. 옛 RS 는 DESIRED 0
kubectl -n react-app-prod rollout status deploy/prod-react-app     # 롤링 업데이트 진행
kubectl -n react-app-prod rollout history deploy/prod-react-app    # 버전 이력
kubectl -n react-app-prod get pod -o custom-columns=NAME:.metadata.name,OWNER:.metadata.ownerReferences[0].name
```

## 1.2 Service (ClusterIP, NodePort, headless)

Pod 는 새로 만들어질 때마다 IP 가 바뀐다. **Service** 는 라벨로 Pod 를 골라 고정된 이름과 IP 를 준다.
클러스터 안에서는 `<서비스이름>.<네임스페이스>.svc.cluster.local` 로 접근한다(같은 네임스페이스면 이름만).

### ClusterIP — 클러스터 안에서만 보이는 주소 (기본값)

`manifests/react-app/base/service.yaml:1-12`

```yaml
apiVersion: v1
kind: Service
metadata:
  name: react-app
spec:
  type: ClusterIP
  selector:
    app: react-app
  ports:
    - name: http
      port: 80
      targetPort: http
```

- `selector: app: react-app` — Deployment 가 Pod 에 붙인 라벨과 같다. 그래서 이 Service 가 그 Pod 들로 요청을 나눈다.
- `port: 80` — Service 가 받는 포트. `targetPort: http` — Pod 의 `http` 라는 이름의 포트(8080)로 보낸다.

### NodePort — 노드의 포트를 열어 밖에서 들어오게

`bootstrap/gitlab/nodeport.yaml:18-30`

```yaml
spec:
  type: NodePort
  selector:
    app: gitlab
  ports:
    - name: http
      port: 80
      targetPort: 80
      nodePort: 30080
    - name: ssh
      port: 22
      targetPort: 22
      nodePort: 30022
```

모든 노드의 30000~32767 범위 포트 하나를 연다. 이 프로젝트는 웹 UI 를 전부 Ingress 로 받고, HTTP 로 다룰 수 없는
git+ssh(30022)와 Docker 레지스트리(30082, 30083)만 NodePort 로 쓴다. kind 에서는 노드가 Docker 컨테이너라
`kind-config.yaml` 의 `extraPortMappings` 에 적은 포트만 밖으로 나온다(파일 헤더 주석 `bootstrap/gitlab/nodeport.yaml:6-9`, 6장).

### headless — IP 없이 Pod 를 직접 가리키는 Service

`manifests/postgres/base/service.yaml:1-15`

```yaml
# StatefulSet 의 serviceName 이 가리키는 headless Service. 인스턴스가 하나라
# 클러스터 안에서는 이 이름으로 바로 붙는다(README 9.4):
#   <prefix>-postgres.<namespace>.svc.cluster.local:5432   예) dev-postgres.postgres-dev.svc.cluster.local
apiVersion: v1
kind: Service
metadata:
  name: postgres
spec:
  clusterIP: None
  ...
```

`clusterIP: None` 이면 Service 에 가상 IP 가 없고, DNS 가 Pod IP 를 바로 돌려준다. DB 처럼 Pod 마다 정체성이 있는
**StatefulSet** 과 짝으로 쓴다(8장 응용).

| 종류 | 누가 접근하나 | 이 프로젝트에서 |
|---|---|---|
| ClusterIP | 클러스터 안 | react-app, python-api (Ingress 가 이 뒤에 붙는다) |
| NodePort | 노드 IP:포트 로 밖에서 | git+ssh, Nexus 레지스트리 |
| headless | 클러스터 안, Pod 에 직접 | PostgreSQL |

### 학습 노트: Service 더 알아보기 (2026-09-27)

**왜 필요한가.** Pod 는 새로 만들어질 때마다(ReplicaSet, 롤링 업데이트, drain) IP 가 바뀌고, 같은 앱 Pod 가 여러 개다.
"어느 IP 로 보내야 하나" 를 푸는 것이 Service 다.
비유: 회사 대표번호. 상담원(Pod)이 바뀌거나 늘어도 고객은 대표번호만 누르면, 교환기(Service)가 **지금 통화 가능한** 상담원에게 연결한다.

**Service 가 하는 일 세 가지**

1. 고정된 이름과 IP.
2. 라벨(`app: react-app`)로 대상 Pod 를 자동으로 모은다. 새 Pod 는 들어오고 지워진 Pod 는 빠진다(PDB 와 같은 방식).
3. **Ready 인 Pod 에게만** 나눠 보낸다. readinessProbe 실패 Pod 는 대상 목록(Endpoints)에서 빠진다.
   그래서 버그 있는 v2 Pod 가 떠 있어도 사용자는 v1 만 본다(1.1 문답과 연결).

```bash
kubectl -n react-app-prod get endpoints prod-react-app   # 나오는 IP:8080 = 지금 트래픽을 받는 Pod
```

**포트 4종류** — 요청 경로

```
Ingress ──(port: http = 80)──▶ Service prod-react-app :80
                                   │ targetPort: http
                                   ▼
                              Pod 컨테이너 :8080   (containerPort, 이름 "http")
```

| 이름 | 어디에 | 뜻 | react-app |
|---|---|---|---|
| `containerPort` | Deployment | 앱이 실제로 여는 포트 | 8080 (`manifests/react-app/base/deployment.yaml:31-33`) |
| `port` | Service | Service 가 받는 포트 | 80 |
| `targetPort` | Service | Pod 의 어느 포트로 넘길지 | `http` → 8080 |
| `nodePort` | Service (NodePort 만) | 노드 컴퓨터에 여는 포트 | 없음 |

포트에 이름(`http`)을 붙이면 앱 포트가 바뀌어도 Deployment 만 고치면 된다.

**DNS 이름**: `<서비스 이름>.<네임스페이스>.svc.cluster.local`. 예) `prod-react-app.react-app-prod.svc.cluster.local`.
파일에는 `react-app` 이지만 `namePrefix: prod-` 로 실제 이름은 `prod-react-app` 이다. 그래서 prod Ingress 도
`prod-react-app` 을 가리킨다(`manifests/react-app/overlays/prod/ingress.yaml:18`). 같은 네임스페이스면 이름만 써도 된다.

**종류별 비유**

| 종류 | 누가 접근 | 비유 | 이 프로젝트 |
|---|---|---|---|
| ClusterIP | 클러스터 안 | 사내 내선번호 | react-app, python-api (밖에서는 Ingress 로) |
| NodePort | 밖에서 `노드:포트` | 건물 외벽 출입문 | git+ssh 30022, Nexus 레지스트리 30082/30083 |
| headless | 클러스터 안, Pod 직통 | 대표번호 없이 직통번호 | PostgreSQL (StatefulSet `serviceName: postgres`, `manifests/postgres/base/statefulset.yaml:9`) |

- NodePort 는 웹(HTTP)으로 다루기 곤란한 것에만 쓴다. 웹 UI 는 전부 Ingress.
- kind 에서는 `kind-config.yaml` 의 `extraPortMappings`(`:87-112`)에 적은 포트만 PC 밖으로 이어진다.
  Nexus UI 30081 은 매핑이 없어 밖에서 안 열린다(`bootstrap/nexus/nodeport.yaml:12-13`).
- 클라우드에서 많이 쓰는 **LoadBalancer** 종류는 로컬 kind 에 외부 로드밸런서가 없어 쓰지 않는다.

**Q. prod Pod 3개 중 1개가 readinessProbe 실패 중이면 `kubectl get endpoints prod-react-app` 의 IP 는 몇 개?**
2개. 실패한 Pod 는 목록에서 빠지고, 다시 통과하면 자동으로 돌아온다.

**Q. 앱 포트를 8080 → 9090 으로 바꿀 때 Deployment 의 `containerPort` 만 고치면 Service 는 그대로 둬도 되나?**
된다. Service 는 `targetPort: http` 로 **이름**을 가리키므로 따라온다.

- probe 도 `port: http` 라서(`manifests/react-app/base/deployment.yaml:47`, `:53`) 함께 따라온다. 숫자로 적었다면 세 군데를 고쳐야 한다.
- `containerPort` 는 안내문일 뿐이다. 실제로 여는 포트는 앱이 정한다(react-app 은 `samples/react-app/nginx.conf` 의 `listen`).
  앱 설정과 `containerPort` 가 어긋나면 Service 는 9090 으로 보내는데 앱은 8080 에서 기다린다.

**Q. dev 네임스페이스(`react-app-dev`)의 Pod 에서 prod react-app 을 부르는 주소는?**
`prod-react-app.react-app-prod.svc.cluster.local` (FQDN). Pod 의 DNS 검색 목록 덕에 줄여 쓸 수 있다.

| 쓰는 곳 | 주소 |
|---|---|
| 어디서든 (정식) | `prod-react-app.react-app-prod.svc.cluster.local` |
| 다른 네임스페이스에서 | `prod-react-app.react-app-prod` |
| 같은 네임스페이스 안에서 | `prod-react-app` |

포트는 Service 의 `port` 80 이라 생략 가능하다(8080 아님).

**네임스페이스는 네트워크 벽이 아니다.** 이 프로젝트에서는 dev Pod 가 prod 를 실제로 부를 수 있다.
앱 네임스페이스에 NetworkPolicy 가 없기 때문이다(리포지토리의 NetworkPolicy 는 Argo CD 설치 기본값뿐, `README.md:2151`).
네임스페이스는 이름·권한을 나누는 "폴더" 이고, 트래픽을 막으려면 NetworkPolicy 를 따로 둔다
(운영이라면 "dev → prod DB 접속 금지" 같은 규칙이 일반적). → 1.4 Namespace 에서 이어서.

## 1.3 Ingress 와 ingress-nginx

사용자는 `http://react-app.dev.example.com` 처럼 **호스트 이름**으로 접속한다. 여러 서비스가 80 포트 하나를
나눠 쓰려면 "이 호스트는 저 Service 로" 를 정하는 규칙이 필요하다. 그 규칙이 **Ingress** 이고,
규칙을 실제로 수행하는 프로그램(리버스 프록시)이 **Ingress 컨트롤러** — 여기서는 **ingress-nginx** 다.

`manifests/react-app/overlays/dev/ingress.yaml:1-20`

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: react-app
  # 평문(HTTP) 전제. cert-manager 가 없어 TLS 시크릿이 생기지 않으므로 tls: 블록을 두지 않는다.
  ...
spec:
  ingressClassName: nginx
  rules:
    - host: react-app.dev.example.com
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: dev-react-app
                port:
                  name: http
```

- `ingressClassName: nginx` — ingress-nginx 가 처리할 규칙이라는 표시.
- `host` — 요청의 Host 헤더가 이 값이면 적용된다. prod 파일은 `react-app.example.com` 이다.
- `backend.service.name: dev-react-app` — Service 이름 앞에 `dev-` 가 붙어 있다. overlay 의 `namePrefix: dev-` 가
  Service 이름을 바꾸기 때문이다(2장).
- TLS(HTTPS) 없이 평문 HTTP 로 쓴다. 로컬 학습 환경이라 인증서 발급 도구(cert-manager)를 두지 않았다.

### 요청이 Pod 까지 가는 경로

```
브라우저 → http://react-app.dev.example.com
  │  Windows hosts 파일: 127.0.0.1 (README 1.5)
  ▼
kind control-plane 노드의 80 포트 (kind-config.yaml extraPortMappings)
  ▼
ingress-nginx 컨트롤러 — Host 헤더를 보고 Ingress 규칙 선택
  ▼
Service dev-react-app :80  (ClusterIP)
  ▼
Pod (label app=react-app) :8080
```

ingress-nginx 는 kind 전용 매니페스트로 설치하고, 80/443 이 매핑된 control-plane 노드에 고정한다(README 1.3).

```bash
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v1.15.1/deploy/static/provider/kind/deploy.yaml
kubectl -n ingress-nginx get pods -o wide     # NODE 가 control-plane 이어야 한다
```

### 학습 노트: Ingress 더 알아보기 (2026-09-27)

**왜 NodePort 가 아니라 Ingress 인가.** NodePort 를 서비스마다 쓰면 사용자가 포트 번호를 외워야 하고,
kind 에서는 포트마다 `extraPortMappings` 를 추가하고 클러스터를 다시 만들어야 한다.
Ingress 는 **문은 80 하나**만 열고, 들어온 요청을 **호스트 이름**으로 나눈다.

**Ingress 와 Ingress 컨트롤러는 한 쌍이다.**

| | 정체 | 비유 |
|---|---|---|
| Ingress | "이 이름은 저 Service 로" 라고 적은 규칙(YAML) | 1층 안내판 |
| Ingress 컨트롤러 (ingress-nginx) | 규칙을 읽고 실제로 전달하는 프로그램(Pod) | 안내 데스크 직원 |

컨트롤러 없이 Ingress 만 만들면 아무 일도 일어나지 않는다. 그래서 README 1.3 에서 ingress-nginx 부터 설치한다.

**같은 127.0.0.1:80 인데 어떻게 구분하나 → Host 헤더.** 브라우저는 요청에 `Host: react-app.dev.example.com` 을 적어 보낸다.
ingress-nginx 가 이 값으로 규칙을 고른다. README 는 hosts 파일 없이 `-H 'Host: ...'` 로 이름을 흉내 내 시험한다(`README.md:364-371`).

**요청 경로 (이 프로젝트)**

```
① 브라우저 http://react-app.dev.example.com
     │  Windows hosts → 127.0.0.1                     (README 1.5)
② Windows 127.0.0.1:80 → WSL 로 (localhost 포워딩)
③ WSL :80 → docker 포트 매핑                          (kind-config.yaml:90-91)
④ control-plane 노드(컨테이너) :80
⑤ ingress-nginx Pod ─ Host 헤더로 Ingress 규칙 선택
⑥ Service dev-react-app → Ready Pod 목록(Endpoints)
⑦ react-app Pod :8080
```

- ingress-nginx 는 **control-plane 에 고정**해야 한다. 80/443 매핑이 거기에만 있다(README 1.3 의 `nodeSelector` patch).
- ⑥ 에서 ingress-nginx 는 Service 가상 IP 를 거치지 않고 **Endpoints 의 Ready Pod IP 로 직접** 보낸다.
  "Ready 인 Pod 만 트래픽을 받는다" 가 여기서도 지켜진다.

**응답 코드로 막힌 곳 찾기**

| 결과 | 뜻 | 볼 곳 |
|---|---|---|
| 연결 안 됨 / `Connection reset` | ①~⑤ 통로 문제 (컨트롤러가 control-plane 이 아닌 노드 등) | `kubectl -n ingress-nginx get pods -o wide` |
| 404 | nginx 까지 왔지만 Host 와 맞는 규칙 없음 | Ingress `host` 철자, 배포 전인지 |
| 503 | 규칙은 맞는데 Ready Pod 0개 | `kubectl get endpoints`, Pod 상태 |
| 200 / 302 | 정상 | |

Ingress 가 하나도 없을 때 404 는 정상이다 — nginx 가 대답했다는 것이 ①~⑤ 통로가 열렸다는 증거다(`README.md:377-381`).
503 행은 README 가 아니라 ingress-nginx 의 일반 동작이다.

**이 프로젝트의 선택**

- 평문 HTTP. cert-manager 가 없어 `tls:` 블록을 일부러 뺐다. 넣으면 https 로 강제 이동 + 가짜 인증서(`manifests/react-app/overlays/dev/ingress.yaml:5-7`).
- `reuse-port: "false"`. 기본값이면 Ingress 추가·수정(설정 리로드) 뒤 요청 일부가 가끔 응답 없이 멈춘다.
  "되다 안 되다" 증상이면 이것부터 의심한다(`README.md:347-361`).

**Q. Windows hosts 파일에 `react-app.dev.example.com` 을 빠뜨리면 어디서 실패하나?**
① DNS 조회에서. hosts 에 없으면 인터넷 DNS 에 묻는데, 우리 PC 의 127.0.0.1 을 알려 줄 리 없다
(`example.com` 은 문서 예시용 예약 도메인). 요청이 127.0.0.1 에 닿지도 않으므로 nginx 는 모르고,
**HTTP 응답 코드가 아예 없다** — 브라우저가 "사이트에 연결할 수 없음" 같은 DNS 오류를 보인다.

| 증상 | 뜻 |
|---|---|
| 브라우저 DNS 오류, HTTP 코드 없음 | ① 실패 → hosts 파일 |
| 404 | nginx 까지 도착. 규칙 문제 |

`curl -H 'Host: ...' http://localhost/` 는 이름 풀이를 건너뛰고 Host 만 흉내 낸다. 이건 되는데 브라우저가 안 되면 hosts 파일 문제다.

**Q. prod 배포 전 `curl -H 'Host: react-app.example.com' http://localhost/` 는?** 404. 맞는 규칙이 없다.

**Q. dev Pod 가 모두 readinessProbe 실패 중일 때 브라우저로 열면?** 503 (500 아님).
Pod 는 떠 있지만 Endpoints 에서 빠져 명단이 0명 → nginx 가 앱에 전달도 안 하고 스스로 503 을 돌려준다.

| 코드 | 이름 | 뜻 | 비유 |
|---|---|---|---|
| 500 | Internal Server Error | **앱이** 처리하다 오류 | 상담원이 받았는데 실수 |
| 502 | Bad Gateway | nginx 가 앱에 연결했는데 이상한 응답·연결 거부 | 연결했더니 이상한 소리 |
| 503 | Service Unavailable | 보낼 대상이 아무도 없음 | 연결 가능한 상담원 0명 |
| 504 | Gateway Timeout | 앱이 너무 오래 대답 없음 | 연결됐는데 대답 없음 |

500 은 요청이 앱까지 **도착해서** 앱이 실패해야 나온다.

## 1.4 Namespace, ServiceAccount

### Namespace

클러스터 안을 나누는 칸막이. 이름은 네임스페이스 안에서만 유일하면 된다. 이 프로젝트는 앱과 환경마다 칸을 나눈다.

| 네임스페이스 | 내용 |
|---|---|
| `gitlab`, `jenkins`, `nexus`, `argocd`, `ingress-nginx` | 플랫폼 도구 |
| `react-app-dev`, `react-app-prod` | react-app 의 환경별 배포 |
| `python-api-dev`, `python-api-prod`, `postgres-dev`, `postgres-prod` | 8장 응용 |

앱 YAML(`manifests/react-app/base/*.yaml`)에는 `namespace:` 가 없다. overlay 의 `namespace: react-app-dev` 가
한꺼번에 채운다(`manifests/react-app/overlays/dev/kustomization.yaml:4`). Argo CD 의 `CreateNamespace=true` 옵션이
네임스페이스가 없으면 만든다(3장). Argo CD AppProject 는 네임스페이스 단위로 "dev 프로젝트는 dev 칸에만 배포" 를
강제한다(3장).

### ServiceAccount

Pod 가 쿠버네티스 API 를 부를 때 쓰는 **Pod 의 신분증**. 사람 계정과 별개다.

`manifests/react-app/base/serviceaccount.yaml:1-5`

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: react-app
automountServiceAccountToken: false
```

react-app 은 API 를 부를 일이 없으므로 토큰을 Pod 에 넣지 않는다(`automountServiceAccountToken: false`).
Deployment 는 `serviceAccountName: react-app` 으로 이것을 쓴다. 기본 ServiceAccount 를 공유하지 않고 앱마다 따로 두면
나중에 권한을 줄 때 그 앱에만 줄 수 있다. 권한이 필요한 예는 Jenkins 다 — 에이전트 Pod 를 만들어야 해서
`bootstrap/jenkins/rbac.yaml` 로 jenkins 네임스페이스 안의 권한을 준다(4장).

### 학습 노트: Namespace 와 ServiceAccount 더 알아보기 (2026-09-27)

**Namespace 비유.** 회사 건물의 층별 사무실. 층마다 "회의실 1" 이 있을 수 있고 열쇠(권한)도 따로지만,
복도·엘리베이터(네트워크)는 공용이라 막아 두지 않으면 누구든 다른 층에 간다(1.2 문답 "네임스페이스는 네트워크 벽이 아니다").

| 칸마다 따로 | 칸과 무관하게 공용 |
|---|---|
| 이름 (dev·prod 에 같은 이름 가능) | 네트워크 (NetworkPolicy 없으면 서로 통신) |
| 권한 (RBAC Role) | 노드 (모든 칸의 Pod 가 같은 노드 위에서 돈다) |
| Secret, ConfigMap (다른 칸 것을 못 쓴다) | 클러스터 전체 리소스 (Node, PV, Namespace 자체) |

**Secret 이 칸마다 따로인 게 드러나는 곳: `regcred`.** 앱 이미지를 Nexus 에서 받으려면 로그인 정보 Secret `regcred` 가 필요한데
Pod 는 자기 칸의 Secret 만 쓴다. 그래서 README 3.5 가 react-app·python-api 의 dev/prod 네 칸에 하나씩 만든다(`README.md:998-1005`).
Deployment 의 `imagePullSecrets: regcred`(`manifests/react-app/base/deployment.yaml:67-68`)는 같은 칸에서 찾는다.
PostgreSQL 은 공식 이미지를 익명으로 받으므로 필요 없다.

**네임스페이스를 적는 곳.** base YAML 에는 없고 overlay 의 `namespace:` 가 한꺼번에 채운다 → 같은 base 를 dev·prod 칸에 따로 배포(2장).
칸이 없으면 Argo CD `CreateNamespace=true`(`apps/react-app.yaml:24`)가 만들고, AppProject 가 배포 가능한 칸을 제한한다(3장).
칸이 달라 이름이 같아도 되는데 `dev-`/`prod-` 접두사를 또 붙이는 것은, 목록·로그에서 구분하기 쉽게 하려는 것으로 보인다(README 에 이유는 없음).

**ServiceAccount = Pod 의 사원증.** 사람은 kubeconfig 로, Pod 안의 프로그램은 ServiceAccount 로 API 에 인증한다.

- 기본값은 모든 Pod 에 토큰이 자동으로 들어간다. 앱이 해킹당하면 공격자가 주워 쓸 수 있다.
  react-app·python-api·postgres 는 API 를 부를 일이 없어 `automountServiceAccountToken: false` 로 아예 넣지 않는다.
- `default` 를 공유하지 않고 앱마다 따로 두면 권한을 그 앱에만 줄 수 있다.

**RBAC 세 조각**

| 조각 | 역할 | 비유 |
|---|---|---|
| ServiceAccount | 누구인가 | 사원증 |
| Role | 어느 칸에서, 어떤 리소스에, 어떤 동작 | 출입 권한 목록 |
| RoleBinding | 누구에게 어떤 Role 을 | 사원증에 권한 등록 |

```
Jenkins 컨트롤러 Pod (사원증: jenkins)
   │ "jenkins 칸에 에이전트 Pod 만들어 줘"
   ▼
API 서버: 사원증 → RoleBinding → Role jenkins-agent-manager 에 pods/create 있음 → 허용
```

- Role 은 한 칸 안에서만 유효하다(`bootstrap/jenkins/rbac.yaml:2` "ClusterRole 아님"). 클러스터 전체용은 ClusterRole. 필요한 만큼만 준다.
- 에이전트 계정 `jenkins-agent` 는 권한이 없다(`bootstrap/jenkins/rbac.yaml:9`). 일꾼은 빌드만 한다.

**칸을 넘는 권한: `manifests/postgres/base/rbac.yaml`.** Role 은 권한을 줄 칸(postgres-dev)에 두고,
RoleBinding 의 subject 는 다른 칸(jenkins)의 `jenkins-agent` 다. 5층 열쇠를 3층 직원 사원증에 등록하는 셈.
주의(`rbac.yaml:4`): 에이전트 템플릿 `build` 를 쓰는 **모든 잡**이 이 권한을 갖는다 — react-app 빌드 잡도 postgres Pod 안에서 명령을 실행할 수 있다.

**Q. `regcred` 를 react-app-dev 에만 만들고 prod 에는 깜빡했다. prod 배포 시 Pod 상태는?**
`ImagePullBackOff`.

```
① Pod 가 노드에 배치
② 노드가 Nexus(docker-hosted) 에서 이미지를 받으려 함
③ 로그인 필요 → prod 칸에서 regcred 를 찾음 → 없음 → 거부(401)
④ ErrImagePull
⑤ 대기 후 재시도, 대기 시간을 늘리며 반복 = ImagePullBackOff
```

README 3.5 에도 이 증상이 적혀 있다(`README.md:1020-1022`). 결과는 상황에 따라 다르다.
v1 이 떠 있을 때 v2 배포라면 롤링 업데이트가 ① 에서 멈추고 사용자는 v1 을 본다(`/healthz` 실패 문답과 같은 결과).
prod 첫 배포라면 Ready Pod 0개 → 503.

**Q. Jenkins 컨트롤러(ServiceAccount `jenkins`)가 에이전트 Pod 를 react-app-dev 칸에 만들 수 있나?**
없다 — 403 Forbidden. Role `jenkins-agent-manager` 는 `namespace: jenkins` 에 있어 **jenkins 칸에서만** 유효하다.
"Pod 를 만들 수 있다" 가 아니라 "jenkins 칸에서 Pod 를 만들 수 있다" 다(3층 열쇠로 5층 문 못 연다).

| 권한의 세 요소 | Jenkins |
|---|---|
| 누가 | ServiceAccount `jenkins` |
| 무엇을 | pods create/delete/get... |
| 어디서 | jenkins 네임스페이스만 |

**Q. 공격자가 react-app Pod 를 장악해 API 로 Secret 목록을 보려 한다. 왜 실패하나?**
네임스페이스는 이유가 아니다 — 공격자는 react-app-prod 칸 **안**에 있고, 그 칸에 `regcred` 가 있다. 이유는 사원증 쪽이다(이중 방어).

| 방어선 | 무엇이 막나 |
|---|---|
| ① 사원증이 없다 | `automountServiceAccountToken: false` → 토큰이 없어 API 서버가 거부 (`manifests/react-app/base/serviceaccount.yaml:5`) |
| ② 있어도 권한이 없다 | ServiceAccount `react-app` 에 연결된 Role 이 없음 → 403 |

NetworkPolicy 가 없어 네트워크로는 API 서버에 닿는다. 실제 벽은 인증(사원증)과 권한(Role)이다.

**헷갈린 지점 정리.** 네임스페이스가 막는 것은 이름 충돌과 **다른 칸의 Secret 을 가져다 쓰는 것**이다.
API 로 무언가 하려는 시도는 **권한**이 막고, Role 은 "누가 + 무엇을 + 어디서" 로 정해진다.

## 1.5 파일 읽기: `manifests/react-app/base/deployment.yaml`, `service.yaml`, `serviceaccount.yaml`

세 파일이 서로 이름과 라벨로 이어진다. 읽을 때 이 연결선을 확인한다.

```
serviceaccount.yaml   name: react-app
        ▲
        │ serviceAccountName: react-app
deployment.yaml       template.labels: app=react-app,  ports.name: http (8080)
        ▲                                   ▲
        │ selector: app=react-app           │ targetPort: http
service.yaml          name: react-app, port 80
```

읽으면서 답해 볼 것.

- [ ] 이미지가 바뀌면 Pod 가 한 번에 모두 바뀌는가? (`strategy`)
- [ ] 새 Pod 가 트래픽을 받기 시작하는 시점은? (`readinessProbe`)
- [ ] 컨테이너가 쓸 수 있는 디렉터리는 어디인가? (`readOnlyRootFilesystem`, `/tmp`)
- [ ] `imagePullSecrets: regcred` 는 무엇을 위한 것인가? — Nexus 에서 이미지를 받을 때 쓰는 로그인 정보다.
      앱 네임스페이스마다 README 3.5 에서 만든다(`manifests/react-app/base/deployment.yaml:67-68`).

## 1.6 파일 읽기: `manifests/react-app/overlays/dev/ingress.yaml`

1.3 에서 읽은 파일이다. prod 파일(`manifests/react-app/overlays/prod/ingress.yaml`)과 비교하면 두 줄만 다르다.

| | dev | prod |
|---|---|---|
| `host` | `react-app.dev.example.com` | `react-app.example.com` |
| `backend.service.name` | `dev-react-app` | `prod-react-app` |

Ingress 는 base 가 아니라 overlay 에만 있다. 호스트 이름이 환경마다 달라서다. 공통 부분은 base, 다른 부분은
overlay — 이것이 2장 Kustomize 의 기본 원칙이다.

```bash
# 클러스터가 있다면 (7장 이후)
kubectl -n react-app-dev get deploy,rs,pod,svc,ingress
kubectl -n react-app-dev describe ingress dev-react-app
curl -s -o /dev/null -w '%{http_code}\n' http://react-app.dev.example.com/healthz
```

### 학습 노트: 파일 읽기 복습 (2026-09-27)

**`automountServiceAccountToken: false`** (`manifests/react-app/base/serviceaccount.yaml:5`)

- 기본값(true)이면 Pod 안 `/var/run/secrets/kubernetes.io/serviceaccount/token` 에 ServiceAccount 토큰이 자동으로 들어간다.
  Pod 안의 프로그램은 이 토큰으로 쿠버네티스 API 를 부를 수 있다(권한은 1.4 의 RBAC 가 정한다).
- 이 React 앱은 정적 파일만 내주는 nginx 라 API 를 부를 일이 없다. 그런데도 토큰이 있으면, 컨테이너가 뚫렸을 때
  공격자가 그 토큰을 들고 API 를 두드릴 수 있다. **쓰지 않는 열쇠는 처음부터 주지 않는다**(최소 권한).
- 비유: 전단지 배달원에게 건물 출입카드를 쥐여 주지 않는 것과 같다.

**`/tmp` emptyDir 를 지우면?** → 이미지 pull 실패(ImagePullBackOff)가 **아니라**, 컨테이너가 뜨다가 죽는다(CrashLoopBackOff).

- 이미지 pull 은 컨테이너를 시작하기 **전** 단계다. 파일시스템 설정과 상관이 없다.
- 시작하면 `docker-entrypoint.sh:6` 이 `/tmp/config.js` 를 쓰고, nginx 가 `/tmp/nginx.pid`, `/tmp/client_temp` 등을
  쓴다(`samples/react-app/nginx.conf:7`, `22-26`). 루트 파일시스템이 읽기 전용이라 `Read-only file system` 오류로 죽는다.
- 실패 단계 구분:

| 단계 | 대표 증상 | 원인 예 |
|---|---|---|
| 스케줄링 | `Pending` | requests 만큼 자원이 있는 노드가 없음 |
| 이미지 받기 | `ErrImagePull` / `ImagePullBackOff` | 태그 없음, `regcred` 없음·틀림 |
| 컨테이너 실행 | `CrashLoopBackOff` | 쓰기 금지 경로에 쓰기, 설정 오류 |
| 실행 중 | ready 안 됨 / 재시작 반복 | readiness / liveness probe 실패 |

**Service 실제 이름** → `dev-react-app`. `overlays/dev/kustomization.yaml:5` 의 `namePrefix: dev-` 가 overlay 에 들어온
모든 리소스(Deployment, Service, ServiceAccount, Ingress) 이름 앞에 `dev-` 를 붙인다.
Ingress 의 backend 는 처음부터 `dev-react-app` 으로 적었다. Kustomize 가 참조까지 알아서 바꿔 주는지는 2장 실습에서 확인한다.

## 1.7 (심화) HPA, PDB: `manifests/react-app/overlays/prod/hpa.yaml`, `pdb.yaml`

prod 에만 있는 두 리소스다.

### HPA (HorizontalPodAutoscaler) — 부하에 따라 Pod 수를 자동 조절

`manifests/react-app/overlays/prod/hpa.yaml:1-18`

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: react-app
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: prod-react-app
  minReplicas: 3
  maxReplicas: 10
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 70
```

- Pod 들의 평균 CPU 사용량이 **requests(prod 는 500m)의 70%** 를 넘으면 늘리고, 밑돌면 줄인다. 3~10 개 사이.
- CPU 사용량은 **metrics-server** 가 모은다(README 1.8). 없으면 HPA 가 값을 못 받는다.
- 주의 1: HPA 가 Deployment 의 `replicas` 를 바꾸면 Git 에 적힌 값(3)과 달라져 Argo CD 가 OutOfSync 로 본다.
  그래서 `bootstrap/argocd/configs/argocd-cm.yaml` 에 `resource.customizations.ignoreDifferences.apps_Deployment` 를 두어
  이 차이를 무시한다(README `운영 관련 메모`).
- 주의 2: prod 를 처음 만들면 HPA 가 지표를 받기 전 잠깐 Degraded 로 보일 수 있다. `ci/Jenkinsfile` 의 배포 대기 단계가
  그래서 2분 더 지켜본다(`ci/Jenkinsfile:176-188`, 4장).

### PDB (PodDisruptionBudget) — 계획된 중단 때 최소 Pod 수 보장

`manifests/react-app/overlays/prod/pdb.yaml:1-9`

```yaml
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: react-app
spec:
  minAvailable: 2
  selector:
    matchLabels:
      app: react-app
```

노드 점검(`kubectl drain`)처럼 **사람이 일으키는** 중단 때 "react-app Pod 는 최소 2개는 살아 있어야 한다" 고 막는다.
노드가 갑자기 죽는 것까지 막지는 못한다. Pod 가 1개뿐인 dev 에 두면 drain 이 영영 진행되지 않으므로 prod 에만 둔다.

### 학습 노트: HPA 와 PDB 더 알아보기 (2026-09-26)

**비유.** HPA 는 손님이 몰리면 계산대 직원을 더 부르는 매니저, PDB 는 "휴가는 동시에 1명까지만" 이라는 근무 규칙이다.

**HPA 의 70% 는 requests 대비다.** 노드 CPU 가 아니라 Pod 의 `resources.requests.cpu` 에 대한 비율이다.
prod 는 requests 를 500m 으로 올리므로(`manifests/react-app/overlays/prod/kustomization.yaml` 의 patch) 목표는 Pod 당 평균 350m 이다.
requests 가 없는 Pod 에는 CPU 기준 HPA 가 동작하지 않는다.

**계산식.**

```
원하는 Pod 수 = ceil( 현재 Pod 수 × 현재 사용률 / 목표 사용률 )
```

- 3개 평균 140% → `ceil(3 × 140 / 70) = 6` 개로 늘린다.
- 3개 평균 20% → 계산은 1 이지만 `minReplicas: 3` 이라 3 개 유지.
- 늘릴 때는 빠르고, 줄일 때는 기본 5분 안정화 기간을 두고 줄인다(부하가 출렁일 때 Pod 가 들락날락하지 않게).

**metrics-server 가 없을 때** (`README.md:538-542`, `:1570`): HPA TARGETS 가 `<unknown>`, Pod 는 minReplicas 로 떠서
멀쩡해 보이지만 Argo CD 가 HPA 를 Degraded 로 판정해 Jenkins "배포 대기" 가 실패한다.

**ignoreDifferences 의 한계 (프로젝트 개선점).** `argocd-cm.yaml:26-28` 의 설정은 **비교(diff)** 에서만 replicas 를 무시한다.
sync 할 때는 Git 의 `replicas: 3` 이 그대로 적용된다. 이 리포지토리에는 `RespectIgnoreDifferences=true` sync 옵션이 없으므로,
HPA 가 7개로 늘린 상태에서 prod 에 새 버전을 배포하면 잠깐 3개로 줄었다가 HPA 가 다시 늘린다.
Kubernetes 공식 문서도 HPA 를 쓸 때는 매니페스트에서 `spec.replicas` 를 빼라고 권장한다. 고치는 방법은 둘 중 하나다.

- prod overlay 에서 replicas 지정을 없앤다.
- Application 에 `RespectIgnoreDifferences=true` 를 추가한다.

**PDB 가 막는 것 / 못 막는 것.**

| 자발적 중단 — PDB 가 막는다 | 비자발적 중단 — 못 막는다 |
|---|---|
| `kubectl drain` (노드 점검·업그레이드) | 노드 하드웨어 고장, 전원 꺼짐 |
| 클러스터 오토스케일러의 노드 축소 | OOM Kill (메모리 부족 강제 종료) |
| eviction API 로 내보내기 | 커널 패닉 |

**PDB 는 롤링 업데이트와 무관하다.** 배포 중 몇 개까지 내려도 되는지는 Deployment 의
`maxUnavailable: 0`, `maxSurge: 1` 이 정한다(`manifests/react-app/base/deployment.yaml:12-15`).

**HPA 와 PDB 숫자의 관계.** HPA `minReplicas: 3` > PDB `minAvailable: 2` 라서 항상 1개의 여유가 있다.
minReplicas 가 minAvailable 이하가 되면 drain 이 막힌다(dev 에 PDB 를 두면 안 되는 이유와 같다).

**PDB 셀렉터.** `app: react-app` 은 Deployment 가 Pod 에 붙이는 라벨이다(`manifests/react-app/base/deployment.yaml:19`).

**용어: 노드, drain, eviction(내보내기).** (2026-09-27 질문에서)

- **노드**: Pod 가 실제로 도는 컴퓨터 한 대. 이 프로젝트의 kind 클러스터는 `devops-control-plane`, `devops-worker`,
  `devops-worker2` 세 대다(`kind-config.yaml:74`, `:123`, `:140`). kind 에서는 이 "컴퓨터" 가 Docker 컨테이너다(6장).
- **drain** ("물을 빼다"): 점검·업그레이드로 노드를 끄기 전에 그 위의 Pod 를 모두 치우는 작업.
  `kubectl drain devops-worker --ignore-daemonsets` 는 두 가지를 한다.
  1. **cordon**: 이 노드에 새 Pod 를 배치하지 않도록 출입 금지 표시.
  2. **eviction**: 노드 위의 Pod 를 하나씩 내보낸다.
  점검 후 `kubectl uncordon devops-worker` 로 출입 금지를 푼다.
- **eviction 은 이사가 아니다.** Pod 는 다른 노드로 옮겨 가지 않는다.

  ```
  ① 파드 A 를 내보낸다 → A 는 종료(삭제)
  ② ReplicaSet(Deployment 가 만든) 이 "3개여야 하는데 2개" 를 알아챈다
  ③ 새 파드 A' 를 만들어 출입 금지가 아닌 노드에 배치
  ④ A' 가 Ready 가 되기까지 수 초~수십 초
  ```

  ①~④ 사이에 Pod 수가 잠깐 준다. 한꺼번에 여러 개를 내보내면 0개가 될 수도 있어서, PDB 가 "최소 N개" 를 지키게 한다.
- 비유: A점 리모델링으로 직원을 퇴근시키면 본사가 B점에 새 직원을 뽑는다. PDB 는 "항상 최소 2명은 일하고 있어야 한다" 는 규칙.
- drain 은 사람이 일으키는 중단이라 Kubernetes 가 PDB 를 확인하고 "기다려" 할 수 있다. 전원 사고는 그런 확인 절차가 없다.

**Q. Pod 3개가 한 노드에 있는데 그 노드 전원이 나가면 PDB 가 도움이 되나?**
도움이 안 된다(비자발적 중단). 대비책은 처음부터 Pod 를 여러 노드에 흩어 두는 것이다
(`topologySpreadConstraints`, `podAntiAffinity`). 이 프로젝트의 `manifests/` 에는 이런 설정이 없어 한 노드에 몰릴 수 있다.

**Q. HPA 가 Pod 를 5개로 늘린 상태에서 drain 하면, PDB(`minAvailable: 2`) 기준으로 동시에 몇 개까지 내보낼 수 있나?**
3개. `정상 Pod 수 − minAvailable = 5 − 2`. `kubectl get pdb` 의 **ALLOWED DISRUPTIONS** 가 이 값이고 계속 다시 계산된다.

| 상황 | 정상 Pod | ALLOWED DISRUPTIONS |
|---|---|---|
| HPA 최소치 | 3 | 1 |
| HPA 가 5개로 늘림 | 5 | 3 |
| 3개를 내보낸 직후 | 2 | 0 → 이후 eviction 은 거부되고 대기 |
| 새 Pod 1개 Ready | 3 | 1 → 다시 1개 내보낼 수 있음 |

drain 은 "허용되면 내보내고, 0 이면 기다렸다 재시도" 를 반복한다. PDB 때문에 drain 이 실패하는 게 아니라 **느려질 뿐**이고
서비스는 끊기지 않는다. 단, 새 Pod 가 영영 Ready 가 못 되면(Pod 1개짜리 dev 에 PDB, 다른 노드에 자리 없음) drain 이 끝나지 않는다.

**셋의 역할 분담.**

```
평소         HPA 가 부하를 보고 3~10개 사이에서 조절
노드 점검     PDB 가 "최소 2개" 를 지키며 한 번에 조금씩 내보냄
새 버전 배포  Deployment 의 rollingUpdate 설정이 담당 (PDB 무관)
```

**확인 명령.**

```bash
# 클러스터 없이 렌더링만 (namePrefix 로 HPA 이름이 prod-react-app 이 되고 scaleTargetRef 도 같은지)
kubectl kustomize manifests/react-app/overlays/prod | grep -A12 'kind: HorizontalPodAutoscaler'

# 7장 구축 후
kubectl -n react-app-prod get hpa        # TARGETS 가 "12%/70%" 처럼. <unknown> 이면 metrics-server 문제
kubectl -n react-app-prod get pdb        # Pod 3개면 ALLOWED DISRUPTIONS 1
kubectl -n react-app-prod describe hpa   # 스케일 이력(Events)
```

## 정리

| 리소스 | 역할 | react-app 파일 |
|---|---|---|
| Deployment | Pod 를 N 개 유지하고 롤링 업데이트 | `base/deployment.yaml` |
| Service | 바뀌는 Pod IP 앞의 고정 주소 | `base/service.yaml` |
| ServiceAccount | Pod 의 신분증 | `base/serviceaccount.yaml` |
| Ingress | 호스트 이름 → Service 라우팅 | `overlays/{dev,prod}/ingress.yaml` |
| HPA | CPU 에 따라 Pod 수 자동 조절 | `overlays/prod/hpa.yaml` |
| PDB | 계획된 중단 때 최소 Pod 수 | `overlays/prod/pdb.yaml` |

- 리소스들은 **이름**과 **라벨**로 서로를 찾는다.
- 요청 경로: 브라우저 → ingress-nginx → Service → Pod.
- 환경마다 같은 것은 base, 다른 것은 overlay 에 둔다 → 2장.

## 확인 문제

1. Deployment 의 `selector.matchLabels` 와 `template.metadata.labels` 가 서로 다르면 어떻게 되는가?

<details><summary>답</summary>

Deployment 가 자기가 만든 Pod 를 자기 것으로 셀 수 없다. `apps/v1` 에서는 API 서버가 이런 Deployment 를
만들지 않고 거부한다(selector 가 template 라벨과 맞지 않는다는 오류).
</details>

2. react-app Pod 는 8080 을 여는데 Ingress 는 Service 의 어느 포트로, Service 는 Pod 의 어느 포트로 보내는가?

<details><summary>답</summary>

Ingress → Service 의 `http` 포트(80). Service → Pod 의 `targetPort: http`, 즉 컨테이너 포트 이름 `http` 인 8080.
</details>

3. 이미지 태그만 바꿨을 때 업데이트 중 준비된 Pod 수가 줄지 않는 이유를 설정 두 가지로 설명하라.

<details><summary>답</summary>

`maxUnavailable: 0` 이라 새 Pod 가 준비되기 전에는 옛 Pod 를 내리지 않는다. `readinessProbe`(`/healthz`)를 통과해야
준비된 것으로 치므로, 뜨기만 하고 응답 못 하는 Pod 로 트래픽이 가지 않는다.
</details>

4. PostgreSQL 의 Service 는 왜 `clusterIP: None` 인가?

<details><summary>답</summary>

headless Service 다. StatefulSet 과 짝으로 써서 DNS 가 Pod 를 직접 가리키게 한다. 인스턴스가 하나라
`dev-postgres.postgres-dev.svc.cluster.local` 로 바로 붙는다(`manifests/postgres/base/service.yaml:1-3`).
</details>

5. HPA 가 Pod 를 5개로 늘렸는데 Git 에는 3 으로 적혀 있다. Argo CD 가 이것을 3 으로 되돌리지 않게 하는 설정은 어디 있는가?

<details><summary>답</summary>

`bootstrap/argocd/configs/argocd-cm.yaml` 의 `resource.customizations.ignoreDifferences.apps_Deployment`.
Deployment 의 replicas 차이를 무시해 OutOfSync 로 보지 않게 한다.

**보충 (2026-09-27, 3.2)**: 이 설정은 OutOfSync **판정** 과 selfHeal 만 막는다. prod 를 sync(배포)할 때는 매니페스트의
`replicas: 3` 이 그대로 적용되어 HPA 가 늘려 둔 Pod 가 3 으로 줄었다가 다시 늘어난다. 완전히 막으려면
`RespectIgnoreDifferences=true` sync 옵션이 필요하다(이 프로젝트엔 없음, `doc/03-argocd.md` 3.2).
</details>
