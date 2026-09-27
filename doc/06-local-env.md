# 6. 로컬 환경 (kind on WSL2)

## 이 장의 목표

이 프로젝트는 운영 클러스터가 아니라 **Windows PC 한 대 안**에 모든 것을 올린다. 그래서
"평범한 쿠버네티스"에는 없는 문제들이 생긴다. 이 장에서는 그 문제들이 **왜** 생기는지와
프로젝트가 **어떤 파일로** 해결하는지를 이해한다. README 1장과 3.4 가 이 내용이다.

전체 구조를 먼저 그림으로 본다.

```
Windows (브라우저, hosts 파일)
  └─ WSL2 (Ubuntu, docker 데몬, /data 디렉터리)
       └─ Docker 컨테이너 3개 = kind 노드
            ├─ devops-control-plane   80/443 매핑, ingress-nginx
            ├─ devops-worker          /data 마운트 (스토리지 노드)
            └─ devops-worker2         상태 없는 파드
                 └─ 파드들 (GitLab, Jenkins, Nexus, Argo CD, 앱)
```

**상자가 네 겹**이라는 점이 이 장의 모든 문제의 출발점이다.

## 6.1 kind 노드 = Docker 컨테이너, `extraPortMappings`

### kind 란

**kind**(Kubernetes IN Docker)는 쿠버네티스 노드를 VM 이 아니라 **Docker 컨테이너**로 만든다.
노드 하나가 컨테이너 하나이고, 그 컨테이너 안에서 kubelet 과 containerd 가 돌며 다시 파드를 띄운다.
설치가 빠르고 지우기도 쉬워서 학습용으로 널리 쓴다.

클러스터 설정은 리포지토리 루트의 `kind-config.yaml` 한 파일에 있다.

```bash
kind create cluster --name devops --config kind-config.yaml
kubectl get nodes -L ingress-ready,storage-node
```

### 노드 3대의 역할

`kind-config.yaml:74`
```yaml
  - role: control-plane
    labels:
      ingress-ready: "true"
    extraPortMappings:
      - containerPort: 80
        hostPort: 80
```

`kind-config.yaml:123`
```yaml
  - role: worker
    labels:
      storage-node: "true"
    extraMounts:
      - hostPath: /data
        containerPath: /data
```

| 노드 | 라벨 | 특별한 설정 | 여기에 뜨는 것 |
|---|---|---|---|
| control-plane | `ingress-ready=true` | 80/443 등 포트 매핑 | ingress-nginx 컨트롤러 |
| worker | `storage-node=true` | WSL 의 `/data` 마운트 | GitLab, Nexus, Jenkins, PostgreSQL |
| worker2 | 없음 | 없음 | Argo CD, 앱 레플리카, CI 잡 파드 |

**라벨**(label)은 노드에 붙이는 이름표다. 다른 리소스가 "이 이름표가 붙은 노드에만 떠라"라고
지정할 때 쓴다(6.4 의 `nodeAffinity`, 아래 ingress-nginx 의 `nodeSelector`).

### 왜 NodePort 를 그냥 쓸 수 없나

일반 클러스터에서는 NodePort 서비스를 만들면 `노드IP:30082` 로 바로 접속된다. kind 에서는
노드가 컨테이너라 **포트가 컨테이너 안에서만 열린다.** 컨테이너 밖(WSL, Windows)으로 꺼내려면
Docker 의 포트 매핑이 필요하고, 그것이 `extraPortMappings` 다. README 도 이렇게 요약한다.

`README.md:201`
> **NodePort 는 그대로 쓸 수 없다.** kind 노드는 WSL 안의 Docker
> 컨테이너이고, `extraPortMappings` 로 명시한 포트만 WSL 의 네트워크로 올라오기 때문이다.

그래서 이 프로젝트는 **80/443 만 열고 웹 UI 는 모두 Ingress 로** 받는다. 예외는 HTTP 로
처리할 수 없는 것들뿐이다.

`kind-config.yaml:96`
```yaml
      # --- 아래는 HTTP 로 뚫을 수 없는 것들만 NodePort 로 내보낸다. ---
      - containerPort: 30022   # git+ssh
      - containerPort: 30082   # Nexus docker-hosted
      - containerPort: 30083   # Nexus docker-group
```

### ingress-nginx 를 control-plane 에 고정하는 이유

**Ingress** 는 "호스트 이름을 보고 어느 서비스로 보낼지" 정하는 규칙이고, **ingress-nginx** 는
그 규칙대로 실제 요청을 나눠 주는 nginx 파드다. 80/443 매핑은 control-plane 노드에만 있으므로,
컨트롤러가 워커에 뜨면 밖에서 들어온 요청을 받을 파드가 없다. 그래서 `nodeSelector` 로 고정한다.

`README.md:328`
```bash
# control-plane 에만 80/443 매핑이 있으므로
# 컨트롤러를 거기에 고정하지 않으면 worker 에 떠서 접속이 안 된다.
kubectl -n ingress-nginx patch deploy ingress-nginx-controller --type=merge -p '{"spec":{"template":{"spec":{"nodeSelector":{"kubernetes.io/os":"linux","ingress-ready":"true"}}}}}'
```

### 그 밖에 알아둘 설정

- **API 서버 주소 고정** (`kind-config.yaml:44`): `127.0.0.1:6443` 으로 고정해 클러스터를
  다시 만들어도 kubeconfig 주소가 바뀌지 않는다.
- **노드 이미지 digest 고정** (`kind-config.yaml:79`): `kindest/node:v1.34.0@sha256:...`.
  생략하면 kind 를 업그레이드할 때 쿠버네티스 버전이 조용히 바뀐다.
- **WSL 을 끄면 노드도 멈춘다** (README 1.7): 노드는 결국 컨테이너라 docker 가 멈추면 같이 멈춘다.
  재시작 정책을 바꿔 두면 docker 가 뜰 때 함께 올라온다.

```bash
docker update --restart=unless-stopped $(docker ps -aq --filter label=io.x-k8s.kind.cluster=devops)
```

### 학습 노트: 마트료시카 인형과 건물 정문 (2026-09-27)

- **비유 — 마트료시카**: Windows ⊃ WSL2(리눅스 VM) ⊃ Docker ⊃ **노드 컨테이너 3개** ⊃ 그 안의 containerd ⊃ Pod.
  "노드" 라는 게 진짜 서버가 아니라 **Docker 컨테이너 하나**다(`docker ps` 에 `devops-control-plane`, `devops-worker`, `devops-worker2`).
- **비유 — 건물 정문**: 노드 컨테이너 안의 포트는 건물 **안쪽 방문**이다. 밖(WSL·Windows)에서 들어오려면 건물 **정문**이
  있어야 하고, 정문을 내는 게 `extraPortMappings`(= `docker run -p`). 정문이 없는 방(NodePort 30xxx)은 밖에서 못 들어온다.
- **브라우저 → Jenkins 까지의 경로**

```
Windows 브라우저 http://jenkins.example.com
 → hosts 파일: 127.0.0.1                         (6.2)
 → WSL2 localhost 포워딩: Windows 127.0.0.1:80 = WSL 127.0.0.1:80
 → Docker 포트 매핑 hostPort 80 → control-plane 컨테이너 80   (kind-config.yaml:90-92)
 → ingress-nginx 컨트롤러 Pod (hostPort 80, control-plane 에 고정)
 → Host 헤더로 Ingress 규칙 선택 → Service → Jenkins Pod (다른 노드에 있어도 클러스터 네트워크로)
```

- **정문은 건축 때만 낼 수 있다**: Docker 는 실행 중인 컨테이너에 포트 매핑을 추가할 수 없다 → `extraPortMappings` 를 바꾸면
  **클러스터를 지우고 다시 만들어야** 한다(`containerdConfigPatches`, `extraMounts` 도 마찬가지). 그래서 30022·30082·30083 을
  "서비스가 없어도 무해하니" 처음부터 미리 뚫어 둔다(`kind-config.yaml:96-98`).
- **빈틈 (정리)**: 세 노드의 "확인" 주석이 모두 `docker inspect devops-worker` 로 복붙돼 있다(`:78`, `:127`, `:144`) — 동작엔 영향 없음.

#### 확인 문제 풀이 (2026-09-27) — 3/3

1. 매핑 없는 NodePort 30090 → **Windows 에서 접속 불가** (정답). 방은 있어도 정문이 없다.
2. ingress-nginx 가 worker 에 뜸 → **80 정문이 control-plane 에만 있어 접속 실패** (정답).
3. 포트 매핑 추가 → **클러스터 삭제 후 재생성** (정답). 데이터는 WSL `/data` 에 남으므로(6.4) 재생성해도 PV 내용은 산다.

## 6.2 Windows hosts 파일과 `*.example.com`

### 이름으로 접속하는 이유

Ingress 는 요청의 **Host 헤더**(접속한 호스트 이름)로 목적지를 고른다. 같은 `127.0.0.1:80` 으로
들어와도 `gitlab.example.com` 이면 GitLab 으로, `argocd.example.com` 이면 Argo CD 로 간다.
따라서 브라우저가 이 이름들을 **127.0.0.1 로 풀 수 있어야** 한다.

`example.com` 은 실제 DNS 에 이 서비스들이 없으므로, Windows 의 **hosts 파일**(DNS 보다 먼저
보는 이름표)에 직접 적는다.

`README.md:390`
```
127.0.0.1  nexus.example.com
127.0.0.1  gitlab.example.com
127.0.0.1  argocd.example.com
127.0.0.1  react-app.dev.example.com
...
```

### 왜 127.0.0.1 인가

WSL2 에는 **localhost 포워딩**이 있다. WSL 안에서 열린 포트가 Windows 의 `127.0.0.1` 에도
그대로 보인다. 경로를 이어 보면 이렇다.

```
브라우저 → Windows 127.0.0.1:80 → (localhost 포워딩) → WSL :80
        → (docker 포트 매핑) → control-plane 컨테이너 :80 → ingress-nginx → 서비스
```

### 주의할 점

- `*.example.com` 은 **placeholder 가 아니라 그대로 쓰는 이름**이다(README 0.3).
- WSL 의 `/etc/hosts` 는 Windows hosts 에서 **WSL 시작 시 한 번만** 자동 생성된다. 나중에 추가한
  줄은 `wsl --shutdown` 뒤에야 반영된다(README 1.5).
- Windows 에서는 등록 후 `ipconfig /flushdns` 를 실행한다.

### 확인

```bash
# 아직 Ingress 가 없으면 404(ingress-nginx 기본 백엔드)가 정상이다
curl -sS -o /dev/null -w '%{http_code}\n' -H 'Host: nexus.example.com' http://localhost/
```

`404` 는 "nginx 까지는 닿았다"는 뜻이다. `Connection reset` 이면 컨트롤러가 control-plane 이
아닌 노드에 떠 있는 것이다(README 1.4).

### 학습 노트: 내 수첩 주소록과 건물 안내 데스크 (2026-09-27)

- **비유**: hosts 파일 = **내 수첩 주소록**. 전화번호 안내(DNS)에 묻기 전에 먼저 본다. 주소록엔 "jenkins → 우리 집(127.0.0.1)",
  "argocd → 우리 집" 처럼 **모두 같은 주소**가 적혀 있다. 한 건물에 도착하면 **안내 데스크(ingress-nginx)** 가
  봉투에 적힌 **받는 사람 이름(Host 헤더)** 을 보고 층(Service)을 안내한다.
- **두 단계는 별개다**
  1. 이름 → IP: hosts 파일 (브라우저가 **어느 건물**로 갈지)
  2. Host 헤더 → Service: Ingress 규칙 (건물 안에서 **어느 층**으로 갈지)
  그래서 hosts 없이도 `curl -H 'Host: jenkins.example.com' http://localhost/` 는 Jenkins 에 닿는다 — 1단계를 건너뛰고
  헤더만 직접 적었기 때문. (README 가 확인 명령에 이 형태를 쓰는 이유)
- **hosts 파일에는 와일드카드(`*`)가 없다** → 이름마다 한 줄씩(README 1.5, 11줄). 새 앱을 추가하면 hosts 에도 줄을 추가.
- **증상으로 어느 단계가 막혔는지 가르기** (1장의 500·503, DNS 약점 복습)

| 증상 | 막힌 곳 |
|---|---|
| `ERR_NAME_NOT_RESOLVED` 또는 우리 것이 아닌 사이트·타임아웃 | 1단계 — hosts 에 줄이 없음(실제 인터넷 DNS 로 감) |
| 연결 거부 / reset | 건물 정문 — WSL 포워딩, 포트 매핑, 컨트롤러 위치(6.1) |
| nginx **404** | 2단계 — 그 Host 에 맞는 Ingress 규칙이 없음 |
| nginx **503** | Ingress 는 있으나 Service 뒤에 Ready Pod 가 없음(ImagePullBackOff 등) |

- **WSL 쪽 주의**: WSL 의 `/etc/hosts` 는 **WSL 시작 때 한 번** Windows hosts 에서 복사된다 → 나중에 추가한 줄은
  `wsl --shutdown`(kind 도 멈춤) 하거나 WSL `/etc/hosts` 에도 직접 넣는다(README 1.5).
- **6.3 예고**: Pod 가 이 이름을 물으면 DNS 사슬이 결국 Windows hosts 까지 닿아 `127.0.0.1` 을 받는다 — Pod 에게 127.0.0.1 은 **자기 자신**.

#### 확인 문제 풀이 (2026-09-27) — 3/3 (보기 순서 섞음)

1. `argocd.example.com` 줄 누락 → **이름을 못 찾거나 엉뚱한 곳, ingress-nginx 까지 못 옴** (정답). 404·503 은 nginx 가 답한 것이므로 "건물엔 도착" 한 경우다.
2. hosts 없이 `curl -H 'Host: ...' http://localhost/` 성공 → **목적지는 Host 헤더로 고른다** (정답).
3. 다른 앱은 정상인데 dev 만 nginx 404 → **dev Ingress 가 아직 없음** (정답).
   오답 보기 구분: hosts 오타 → 이름 풀이 실패(nginx 응답 없음), `regcred` 누락 → Ingress 는 있고 Pod 가 ImagePullBackOff → **503**,
   WSL 포워딩 고장 → 다른 앱도 전부 안 됨.

## 6.3 CoreDNS rewrite 로 클러스터 안에서도 같은 이름 쓰기

### 문제: 이름이 "엉뚱하게" 풀린다

hosts 파일은 **클러스터 밖**(브라우저)을 해결한다. 그런데 **클러스터 안**의 파드도 같은 이름을
쓴다. kaniko 는 `nexus-docker.example.com` 에 이미지를 push 하고, Argo CD 는
`gitlab.example.com` 에서 매니페스트를 받는다.

**CoreDNS** 는 클러스터 안의 DNS 서버다. `example.com` 을 모르면 바깥 DNS 로 넘기는데,
그 사슬이 결국 Windows hosts 파일에 닿는다.

`README.md:429`
```
파드 → CoreDNS → 노드의 resolv.conf → Docker 내장 DNS(127.0.0.11)
     → WSL 리졸버 → Windows DNS 프록시 → Windows hosts 파일 → 127.0.0.1
```

파드 안에서 `127.0.0.1` 은 **그 파드 자신**이다. kaniko 는 자기 자신에게 push 하려다
`connection refused` 로 죽는다. 이름을 못 찾는 오류(NXDOMAIN)보다 원인을 찾기 훨씬 어렵다.

```bash
# 문제 확인 — 패치 전에는 Address: 127.0.0.1 이 나온다
kubectl run dnstest --rm -it --image=busybox:1.36 --restart=Never \
  -- nslookup nexus-docker.example.com
```

### 해결: rewrite

CoreDNS 설정(Corefile)에 `rewrite` 를 넣어, 이 이름들을 ingress-nginx 컨트롤러 서비스 이름으로
바꿔 푼다.

`bootstrap/coredns/coredns-cm.yaml:69`
```
        # --- 로컬 호스트명을 ingress-nginx 로 보낸다 (이 블록만 추가됨) ---
        rewrite name nexus.example.com              ingress-nginx-controller.ingress-nginx.svc.cluster.local
        rewrite name nexus-docker.example.com       ingress-nginx-controller.ingress-nginx.svc.cluster.local
        rewrite name gitlab.example.com             ingress-nginx-controller.ingress-nginx.svc.cluster.local
        ...
```

이제 클러스터 안에서도 밖과 **같은 경로(ingress-nginx)** 를 탄다.

```
밖: 브라우저 → hosts(127.0.0.1)            → ingress-nginx → 서비스
안: 파드     → CoreDNS rewrite(ClusterIP)  → ingress-nginx → 서비스
```

### 왜 Nexus 서비스로 바로 보내지 않나

`bootstrap/coredns/coredns-cm.yaml:14`
```
#   이미지 참조(nexus-docker.example.com/my-group/app:tag)에는 포트가 없어 80 으로
#   붙는데, nexus Service 는 8081/8082/8083 만 연다. 컨트롤러는 80 을 열고,
#   rewrite 는 DNS 질의만 바꾸고 HTTP Host 헤더는 건드리지 않으므로 기존 Ingress 의
#   host 기반 라우팅(nexus-docker→8082, nexus-docker-group→8083)이 그대로 살아난다.
```

DNS 는 "어느 IP 로 갈지"만 바꾸고, HTTP 요청 안의 Host 헤더는 원래 이름 그대로 남는다.
그래서 ingress-nginx 가 기존 규칙대로 알맞은 포트로 나눠 준다.

### 적용과 주의

```bash
kubectl apply -f bootstrap/coredns/coredns-cm.yaml
kubectl -n kube-system rollout restart deploy/coredns
kubectl -n kube-system rollout status  deploy/coredns
```

- **ingress-nginx 설치 뒤에** 적용한다. 컨트롤러 서비스가 없으면 rewrite 대상이 풀리지 않는다.
- Corefile 은 ConfigMap 안의 긴 문자열이라 **부분 병합이 안 되고 통째로 교체**된다. 이 파일은
  kind v0.30.0 / k8s v1.34.0 원본에 rewrite 만 끼운 것이다.
- 와일드카드 정규식 대신 **호스트를 한 줄씩** 적는다. 새 호스트를 추가하면 여기에도 한 줄 더한다.
- `kind delete cluster` 하면 사라진다. 클러스터를 다시 만들면 다시 적용한다.

### 학습 노트: 사내 교환원에게 붙인 메모 (2026-09-27)

- **비유**: CoreDNS = **사내 교환원**. Pod(직원)가 "gitlab.example.com 연결해 주세요" 하면 교환원은 모르는 이름이라
  **바깥 안내(Windows hosts 까지 이어진 DNS 사슬)** 에 묻고, "우리 집(127.0.0.1)" 이라는 답을 그대로 전한다 →
  직원은 **자기 자리 전화**로 건다(connection refused). `rewrite` = 교환원 책상에 붙인 메모:
  "이 11개 이름은 **1층 안내 데스크(ingress-nginx 컨트롤러 Service) 내선**으로 돌려라" (`coredns-cm.yaml:70-80`).
- **바꾸는 건 DNS 답뿐, 봉투(Host 헤더)는 그대로** → 안내 데스크가 기존 Ingress 규칙대로 층을 안내한다(6.2 의 ①·② 분리가 여기서도).
  그래서 nexus Service(8081~8083)로 직접 보내지 않는다 — 이미지 참조엔 포트가 없어 80 으로 붙고, 80 을 여는 건 컨트롤러.
- **같은 이름, 세 가지 길**

| 누가 | 이름 풀이 | 결과 |
|---|---|---|
| Windows 브라우저 | hosts 파일 → 127.0.0.1 | 포트 매핑 → 컨트롤러 (6.1·6.2) |
| **Pod** (kaniko, Argo CD, argocd CLI, Image Updater) | CoreDNS `rewrite` | 컨트롤러 Service ClusterIP |
| **노드의 containerd** (이미지 pull) | CoreDNS 를 **안 쓴다** (노드 자신의 DNS) | `kind-config.yaml:57-62` 미러 → `localhost:30082` (6.5) |

  → CoreDNS 를 고쳐도 노드 pull 은 해결되지 않는다. 그래서 미러 설정이 따로 있다.
- **증상이 헷갈리는 이유**: NXDOMAIN(이름 없음)이면 원인이 뻔한데, 여기선 **틀린 답(127.0.0.1)** 이 돌아와 "서버가 죽었나?" 로 보인다.
  진단: `kubectl run dnstest ... nslookup nexus-docker.example.com` → `127.0.0.1` 이면 rewrite 누락, 컨트롤러 ClusterIP 면 정상.
- **적용 주의**: Corefile 은 ConfigMap 안의 한 덩어리 문자열 → 통째 교체. ingress-nginx 설치 **뒤에**, 클러스터 재생성 때마다 다시.
  새 호스트 추가 = hosts 파일 + Ingress + **여기 한 줄** (세 곳).
- **빈틈 (정리)**: `coredns-cm.yaml:12` 주석의 확인 명령이 두 줄이 한 줄로 합쳐져 `#` 뒤가 잘렸다(README 1.6 에 정상본).

#### 확인 문제 풀이 (2026-09-27) — 3/3

1. rewrite 전 kaniko push → **connection refused** (정답). 틀린 답(127.0.0.1)이라 NXDOMAIN 이 아니고, push 는 pull 이 아니라 ImagePullBackOff 도 아니다.
2. `blog.example.com` 에 CoreDNS 누락 → **브라우저 OK, Pod 안에선 실패** (정답). 브라우저는 hosts, Pod 는 CoreDNS — 길이 다르다.
3. 컨트롤러로 보내는 이유 → **포트 없는 이미지 주소 = 80, 80 은 컨트롤러가 열고 Host 헤더로 8082/8083 분기** (정답).

## 6.4 hostPath PV 와 디렉터리 권한(UID)

### 용어

- **PV**(PersistentVolume): 파드가 죽어도 남는 저장 공간.
- **PVC**(PersistentVolumeClaim): 파드가 "이런 저장 공간이 필요하다"고 요청하는 것. PVC 가 PV 에 연결된다.
- **hostPath**: 노드의 디렉터리를 그대로 PV 로 쓰는 방식. 클라우드 디스크가 없는 로컬 환경용이다.

### 데이터는 결국 WSL 의 `/data` 에 있다

```
WSL /data  ──extraMounts──▶  devops-worker 컨테이너 /data  ──hostPath PV──▶  파드
```

`extraMounts` 가 없으면 데이터가 노드 컨테이너 안에만 생겨 `kind delete cluster` 와 함께 사라진다.
이 연결 덕에 클러스터를 지워도 GitLab 리포지토리와 Nexus 이미지가 WSL 에 남는다.

### nodeAffinity 로 스토리지 노드에 고정

`bootstrap/nexus/local-pv.yaml:23`
```yaml
  persistentVolumeReclaimPolicy: Retain
  storageClassName: manual
  hostPath:
    path: /data/nexus
    type: DirectoryOrCreate
  nodeAffinity:
    required:
      nodeSelectorTerms:
        - matchExpressions:
            - key: storage-node
              operator: In
              values:
                - "true"
```

- `Retain`: PVC 를 지워도 데이터를 지우지 않는다.
- `storageClassName: manual`: 자동 프로비저너 없이 미리 만든 이 PV 를 쓴다는 표시다.
- `nodeAffinity`: 이 PV 를 쓰는 파드는 `storage-node=true` 노드에만 뜬다.

**왜 모든 워커에 `/data` 를 주지 않나?** 세 노드가 같은 WSL 디렉터리를 공유하게 되고,
업데이트(롤아웃) 중에 옛 파드와 새 파드가 서로 다른 노드에서 **같은 DB 파일을 동시에 쓴다.**

`kind-config.yaml:19`
```
#      모든 노드에 /data 를 마운트하면 세 노드가 같은 WSL 디렉터리를 보게 되어,
#      StatefulSet 롤아웃 중 구/신 파드가 서로 다른 워커에 뜨는 순간 같은
#      디렉터리를 동시에 쓴다. GitLab(PostgreSQL+Gitaly) 과 Nexus(내장 DB) 는
#      이를 견디지 못한다. hostPath 에서는 ReadWriteOnce 도 강제되지 않는다.
```

### 디렉터리 권한(UID)

리눅스 파일은 소유자 **UID**(사용자 번호)가 있다. 컨테이너는 보안을 위해 root 가 아닌 사용자로
도는데, 디렉터리 소유자가 다르면 쓰기 권한이 없어 뜨지 못한다. 그래서 미리 `chown` 한다.

`README.md:223`
```bash
mkdir -p /data/gitlab/config /data/gitlab/data /data/nexus /data/jenkins /data/postgres/dev /data/postgres/prod
sudo chown -R 200:200   /data/nexus       # nexus
sudo chown -R 1000:1000 /data/jenkins     # jenkins
sudo chown -R 999:999   /data/postgres    # postgres
```

| 구성요소 | 컨테이너 UID | 비고 |
|---|---|---|
| Nexus | 200 | initContainer 로 한 번 더 맞춘다 |
| Jenkins | 1000 | initContainer 로 한 번 더 맞춘다 |
| PostgreSQL | 999 | `type: Directory` — 미리 없으면 실패 |
| GitLab | (root) | omnibus 가 스스로 맞춰 chown 불필요 |

보통은 파드의 `fsGroup` 설정이 권한을 맞춰 주지만 **hostPath 에는 적용되지 않는다.**
그래서 Nexus 는 root 로 도는 initContainer 가 먼저 소유권을 고친다.

`bootstrap/nexus/nexus.yaml:61`
```yaml
        # hostPath PV 는 kubelet 이 fsGroup 을 적용해 주지 않는다.
        # (type: DirectoryOrCreate 로 만들어진 디렉터리는 root:root 0755)
        # 그래서 root 로 한 번 소유권을 맞춰 준다. 동적 프로비저너를 쓰면 불필요.
        - name: fix-permissions
          image: busybox:1.36
```

PostgreSQL 은 반대로 `type: Directory` 로 둬서, 디렉터리가 없으면 root 소유로 몰래 만들지 않고
**바로 실패**하게 했다(`bootstrap/postgres/local-pv.yaml:10`).

### `/mnt/c` 에 두면 안 되는 이유

`/mnt/c` 는 Windows 디스크(DrvFs)라 리눅스의 `chown` 과 파일 모드가 먹지 않는다. 그러면 Nexus(UID 200),
Jenkins(UID 1000)가 쓰기에 실패한다. **반드시 WSL 의 ext4(`/`) 아래**에 둔다.

```bash
df -hT /data | tail -1                    # ext4 여야 한다
kubectl get nodes -l storage-node=true    # 정확히 1개여야 한다
docker exec devops-worker ls -ln /data    # WSL 의 /data 와 같은 내용
```

### 학습 노트: 가건물과 바깥 창고, 사물함 번호표 (2026-09-27)

- **비유**: kind 노드 컨테이너 = **가건물**(허물면 안의 것도 사라짐). WSL `/data` = 가건물 **밖의 영구 창고**.
  `extraMounts` = 가건물 벽에 낸 **창고 연결 문**(`kind-config.yaml:131-133`). 이 문은 **worker 한 채에만** 있다.
  `nodeAffinity` = "창고 물건을 쓰는 직원(Pod)은 문이 있는 그 가건물에서만 일한다" 는 배치 규칙.
- **데이터가 사는 곳 3층 구조**

```
WSL /data/nexus (ext4, 진짜 데이터)  ← kind delete cluster 해도 남음
 └ extraMounts → devops-worker 컨테이너 /data/nexus
    └ hostPath PV (nexus-data-pv, nodeAffinity storage-node=true)
       └ PVC → Nexus Pod /nexus-data
```

- **PV 필드에서 오해하기 쉬운 것**
  - `capacity: 50Gi` (`nexus/local-pv.yaml:20`) — hostPath 는 용량을 **강제하지 않는다.** PVC 와 짝짓기용 숫자일 뿐, 디스크가 차면 WSL 디스크 전체가 찬다.
  - `accessModes: ReadWriteOnce` — hostPath 에선 이것도 사실상 강제되지 않는다(`kind-config.yaml:22`). 그래서 **nodeAffinity + 스토리지 노드 1대** 로 막는다.
  - `Retain` — PVC 를 지워도 데이터 유지. 대신 PV 가 `Released` 로 남아 **자동으로 다시 붙지 않는다**(claimRef 정리 필요).
- **UID = 사물함 번호표** (5.2 연결): 디렉터리 주인 UID 와 컨테이너 UID 가 다르면 쓰기 실패 → **CrashLoopBackOff**(실행 단계 실패, 5.2 표).
  보통 Pod 의 `fsGroup` 이 맞춰 주지만 **hostPath 에는 적용 안 됨** → 두 겹으로 대비:
  1. 사람이 미리 `chown` (README 1장: nexus 200, jenkins 1000, postgres 999)
  2. root 로 도는 `fix-permissions` initContainer 가 한 번 더 (`nexus.yaml:64`, `jenkins.yaml:73`)
  PostgreSQL 은 반대로 `type: Directory` — 없으면 조용히 root 소유로 만들지 말고 **즉시 실패**(빨리 드러나게).
- react-app(UID 10001)은 hostPath 를 안 쓰고 `/tmp` emptyDir 만 쓰므로(5.2) 이 문제와 무관 — 상태 없는 앱이라 worker2 에도 뜬다.
- `/mnt/c` 금지: Windows 디스크(DrvFs)는 `chown` 이 먹지 않아 번호표를 바꿀 수 없다.

#### 확인 문제 풀이 (2026-09-27) — 2/3

1. 클러스터 재생성 후 GitLab 데이터 → 답 **WSL `/data` 에 남아 있어 새 PV 가 같은 경로를 가리키면 보인다** ("PV 객체가 밖에 저장돼 유지" 라고 오답).
   **PV 는 "창고 위치를 적은 서류"일 뿐 창고 자체가 아니다.** 서류(PV·PVC 같은 모든 K8s 객체)는 etcd 에 있고,
   etcd 는 control-plane **컨테이너 안**에 있다 → `kind delete cluster` 때 서류는 전부 사라진다.
   살아남는 건 **창고(WSL `/data`)** 뿐이고, `local-pv.yaml` 을 다시 apply 해 **같은 경로를 적은 새 서류**를 만들면 다시 연결된다.
   (`Retain` 도 서류가 살아 있을 때의 규칙 — 클러스터째 지우는 경우와는 무관.)
2. chown 누락 + initContainer 없음 → **CrashLoopBackOff** (정답). fsGroup 은 hostPath 에 적용 안 됨.
3. worker2 에도 `/data` → **롤아웃 중 두 노드의 Pod 가 같은 DB 를 동시에 씀** (정답).

## 6.5 평문 HTTP 레지스트리 설정 (README 3.4)

### 문제

Docker 와 containerd 는 레지스트리에 **HTTPS** 로 접속하는 것이 기본이다. 이 프로젝트의 Nexus 는
로컬이라 인증서 없는 **평문 HTTP** 다. 그래서 접속하는 주체마다 "이 레지스트리는 HTTP 로 붙어도 된다"는
예외 설정이 필요한데, **주체가 셋이고 방식이 각각 다르다.**

`README.md:909`

| 주체 | 무엇을 하나 | 주소 | 예외 등록 방법 |
|---|---|---|---|
| 노드의 containerd | 이미지 pull | `nexus-docker.example.com` | `kind-config.yaml` 의 `containerdConfigPatches` |
| kaniko (Jenkins 에이전트 Pod) | 이미지 push | `nexus-docker.example.com` | CoreDNS rewrite(1.6) + kaniko `--insecure` |
| WSL/Windows 의 docker | 수동 login/push | `localhost:30082` | 없음 (Docker 가 localhost 를 기본 insecure 취급) |

### ① 노드 pull: containerd 미러

파드가 뜰 때 이미지를 받는 것은 노드 안의 **containerd**(컨테이너 런타임)다. containerd 는 파드가
아니므로 CoreDNS 를 거치지 않는다. 대신 "이 이름은 이 주소로 보내라"는 **미러** 설정을 준다.

`kind-config.yaml:57`
```yaml
containerdConfigPatches:
  - |-
    [plugins."io.containerd.grpc.v1.cri".registry.mirrors."nexus-docker.example.com"]
      endpoint = ["http://localhost:30082"]
```

매니페스트에는 `nexus-docker.example.com/...` 을 그대로 쓰지만, 실제 통신은 노드 자신의 NodePort
30082 로 간다. endpoint 가 `http://` 라 별도의 insecure 설정도 필요 없다. 이 설정이 `kind-config.yaml`
에 있으니 클러스터를 다시 만들어도 유지된다. **Nexus 의 `nodeport.yaml` 이 기본 활성인 이유**가 이것이다.

### ② kaniko push: CoreDNS + `--insecure`

kaniko 는 파드 안에서 직접 레지스트리에 붙는다. 이름은 6.3 의 CoreDNS rewrite 가 해결하고,
평문 접속은 옵션으로 허용한다.

`ci/Jenkinsfile:97`
```bash
              # --insecure / --skip-tls-verify : 평문 HTTP 레지스트리로 push
              # --insecure-pull                : 평문 HTTP 레지스트리(docker-group)에서 베이스 이미지 pull
              /kaniko/executor --context "$WORKSPACE" --dockerfile Dockerfile \\
                ...
                --insecure --skip-tls-verify --insecure-pull
```

### ③ 사람이 직접 push: `localhost:30082`

```bash
docker login localhost:30082 -u admin
docker tag myapp:1.0 localhost:30082/myapp:1.0
docker push localhost:30082/myapp:1.0
```

이미지 이름의 앞부분(호스트)이 곧 레지스트리 주소다. 클러스터에서 쓰려면
`nexus-docker.example.com/myapp:1.0` 으로 다시 태그해 push 한다. 같은 Nexus 라 레이어는 다시 보내지 않는다.

### 도달 확인

```bash
# 클러스터 안(CoreDNS + Ingress 경로). 401 이면 도달은 된 것이다
kubectl run curltest --rm -it --image=curlimages/curl --restart=Never -- curl -sS -o /dev/null -w '%{http_code}' http://nexus-docker.example.com/v2/
```

> kind 에서는 README 3.4 아래쪽의 `daemon.json` · `certs.d` 절차를 **따라 하지 않는다.** 노드가
> 실제 호스트인 일반 클러스터용이다.

### 학습 노트: 같은 창고로 가는 세 사람, 세 갈래 길 (2026-09-27)

- **문제는 두 겹이다**: ① 이름을 어디로 풀까(DNS) ② HTTPS 가 기본인데 Nexus 는 평문 HTTP. 세 주체가 이 두 문제를 **각자 다르게** 푼다.

| 주체 (비유) | ① 이름 | ② 평문 허용 | 근거 |
|---|---|---|---|
| 노드 containerd (**트럭 기사**) | 미러: `nexus-docker.example.com` → `localhost:30082` (내비에 "창고 = 옆 뒷문" 저장) | endpoint 가 `http://` 라 끝 | `kind-config.yaml:57-62` |
| kaniko Pod (**기술자**) | CoreDNS rewrite → ingress-nginx (6.3) | `--insecure --skip-tls-verify --insecure-pull` | `ci/Jenkinsfile:101-105` |
| WSL/Windows docker (**사장님**) | 주소 자체를 `localhost:30082` 로 | Docker 가 `localhost` 는 기본 insecure 취급 | README 3.4 |

- **왜 노드는 Ingress 길을 못 쓰나**: 노드의 DNS 사슬도 Windows hosts 까지 가서 `127.0.0.1` 을 받는다. 80 포트의 ingress-nginx 는
  **control-plane 에만** 있으므로 worker 의 `127.0.0.1:80` 에는 아무도 없다. 게다가 HTTPS 가 기본. → 미러가 두 문제를 한 번에 해결:
  모든 노드에서 열리는 **NodePort 30082** 로 곧장 간다(그래서 `nexus/nodeport.yaml` 이 기본 활성).
- **이름은 그대로, 길만 바뀐다**: 매니페스트·gitops 는 `nexus-docker.example.com/...` 한 가지 이름만 쓴다. 누가 부르느냐에 따라
  containerd 는 미러로, kaniko 는 CoreDNS 로 간다. `regcred` 도 이 이름 기준으로 매칭되어 미러 endpoint 에 그대로 쓰인다.
- 30083(docker-group) 도 같은 방식으로 미러링돼 있어 에이전트 이미지·베이스 이미지 pull 에 쓰인다(`kind-config.yaml:61-62`).
- `daemon.json`·`certs.d` 절차(README 3.4 아래쪽)는 kind 에선 **하지 않는다** — 노드가 컨테이너라 재생성 때 사라진다.

#### 확인 문제 풀이 (2026-09-27) — 0/3

공통 원인: **"이 설정을 쓰는 게 누구인가"** 를 먼저 묻지 않았다. 설정 하나는 **주체 하나**에만 영향을 준다.

| 지운 설정 | 쓰는 주체 | 깨지는 것 | 멀쩡한 것 |
|---|---|---|---|
| `containerdConfigPatches` (미러) | 노드 containerd (pull) | 앱·에이전트 Pod **ImagePullBackOff** | kaniko push, 브라우저 |
| kaniko `--insecure` | kaniko (push) | 빌드의 **이미지 push 단계** | 이미 떠 있는 앱 Pod |
| CoreDNS rewrite | Pod 들 | kaniko push·Argo CD repo 접속 (connection refused) | 노드 pull, 브라우저 |
| Windows hosts 줄 | 브라우저 | 브라우저 접속 | 클러스터 안 전부 |

1. 미러 삭제 → 답 **앱 Pod ImagePullBackOff, kaniko 정상** ("Nexus UI 접속 불가" 라고 오답). 브라우저는 hosts → 포트 80 → Ingress 길이라 미러와 무관.
2. `--insecure` 삭제 → 답 **kaniko 가 HTTPS 로 시도하다 push 단계 실패** ("앱 Pod ImagePullBackOff" 라고 오답).
   push 가 실패하면 새 이미지도, gitops 커밋도 없으니 **기존 Pod 는 옛 이미지로 그대로 잘 돈다** — 5.7 의 "Git 이 안 바뀌면 아무 일 없다".
3. `localhost:30082` 는 되고 `nexus-docker.example.com` 은 실패 → 답 **Docker 는 `localhost` 만 기본 평문 허용** ("hosts 에 없다" 라고 오답).
   이 이름은 README 1.5 hosts 목록에 **있다**(두 번째 줄). 이름은 풀리지만 Docker 가 HTTPS 를 요구해서 실패.

#### 재확인 (2026-09-27) — 1/2

- CoreDNS rewrite 만 삭제 → **kaniko push 가 connection refused** (정답).
- push 성공 + dev Pod ImagePullBackOff(연결 오류) → 답 **미러 설정·`nodeport.yaml`** (`--insecure` 라고 오답).
  **진단 순서: ① 실패한 주체가 누구인가 → ② 그 주체의 설정을 본다.**
  ImagePullBackOff 는 **노드가 이미지를 받다가** 실패한 것 → 노드의 설정(미러 → `localhost:30082` → NodePort)을 본다.
  `--insecure` 는 kaniko 의 설정이고, push 가 성공했다는 건 kaniko 쪽은 이미 멀쩡하다는 증거다. (401 이었다면 `regcred`, 5.6)
- 마지막 한 문제: `Failed to pull image` 의 주체 → 답 **노드의 kubelet/containerd** (Argo CD 라고 오답).
  **Argo CD 는 이미지를 만지지 않는다.** 3.1 의 "Argo CD 가 이미지를 감시한다" 오해와 같은 뿌리.
  배포 사슬 (누가 무엇을 하나):

```
Argo CD      : Git 의 YAML 을 API 서버에 apply (Deployment 의 image 필드 = 글자일 뿐)
API 서버     : 객체 저장 → ReplicaSet → Pod 생성
스케줄러     : Pod 를 어느 노드에 둘지 결정
kubelet      : 자기 노드에 배정된 Pod 를 발견 → containerd 에게 "이 이미지 받아 와"
containerd   : 미러(localhost:30082) + regcred 로 Nexus 에서 pull  ← 여기서 실패하면 ImagePullBackOff
```

  비유: Argo CD = 본사 기획팀(매장 **배치도** 전달), kubelet/containerd = 물건을 실어 오는 **트럭 기사**.
  배치도가 잘못되면 Argo CD 쪽(OutOfSync·sync 실패)에, 물건을 못 가져오면 노드 쪽(Pod 이벤트)에 흔적이 남는다.
  Argo CD 화면에서 ImagePullBackOff 가 보이는 건 Pod 상태를 **보여 줄 뿐**이다(health `Progressing`/`Degraded`, 3.7).
- 워밍업 재확인 (6.6 뒤, 보기 순서 바꿈): Synced 인데 ImagePullBackOff → pull 주체 **노드의 kubelet/containerd** (정답). 오해 해소 — 7장에서 `kubectl describe pod` 이벤트로 눈으로 확인할 것.

## 6.6 metrics-server (README 1.8)

### 무엇이고 왜 필요한가

**metrics-server** 는 노드와 파드의 CPU·메모리 사용량을 모아 `metrics.k8s.io` API 로 제공한다.
`kubectl top` 과 **HPA**(부하에 따라 파드 수를 자동으로 늘리고 줄이는 리소스)가 이 값을 읽는다.
kind 에는 기본으로 들어 있지 않다.

prod overlay 에는 HPA 가 있다(`manifests/react-app/overlays/prod/hpa.yaml`). metrics-server 가 없으면
이런 일이 연쇄로 일어난다.

```
HPA 가 지표를 못 읽음 (<unknown>)
  → Argo CD 가 HPA 를 Degraded 로 판정
  → 앱 Health 가 Degraded
  → Jenkins 의 argocd app wait --health 실패 → prod 배포 파이프라인 실패
```

`kubectl get pods` 로 보면 파드는 정상이라 원인을 찾기 어렵다는 점을 기억해 둔다.

### kind 용 패치

`bootstrap/metrics-server/kustomization.yaml:7`
```yaml
resources:
  - https://github.com/kubernetes-sigs/metrics-server/releases/download/v0.8.0/components.yaml

# kind 노드의 kubelet 서빙 인증서는 자체 서명이고 노드 IP 가 SAN 에 없다.
# 검증을 끄지 않으면 스크랩이 x509 오류로 실패해 파드가 Ready 가 되지 않는다(README 1.8).
patches:
  - target:
      kind: Deployment
      name: metrics-server
    patch: |-
      - op: add
        path: /spec/template/spec/containers/0/args/-
        value: --kubelet-insecure-tls
```

공식 매니페스트를 그대로 가져오고 **인자 하나만 더하는** Kustomize 패턴이다(2장 참고).
`--kubelet-insecure-tls` 는 **로컬 전용**이다. 운영 클러스터에서는 kubelet 인증서를 정식으로 발급하고 이 패치를 뺀다.

```bash
kubectl apply -k bootstrap/metrics-server
kubectl -n kube-system rollout status deploy/metrics-server
kubectl top nodes        # Ready 후 1분쯤 지나야 값이 나온다
```

### 학습 노트: 검침원과 자동 인력 배치 매니저 (2026-09-27)

- **비유**: metrics-server = **검침원**. 각 노드의 kubelet(계량기)을 돌며 CPU·메모리 사용량을 읽어 `metrics.k8s.io` 에 올린다.
  HPA = **자동 인력 배치 매니저**. 검침 결과를 보고 직원(Pod) 수를 3~10 명 사이에서 조절한다(`overlays/prod/hpa.yaml:10-11`).
  검침원이 없으면 매니저는 "모름(`<unknown>`)" → 본사(Argo CD)는 HPA 를 **Degraded** 로 보고 → Jenkins 의 `argocd app wait --health` 실패.
  그런데 `kubectl get pods` 는 3개 Running — **조용한 실패**(5.3 과 같은 유형).
- **70% 의 기준은 `requests`**: `averageUtilization: 70` (`hpa.yaml:18`) = Pod 평균 사용량 ÷ `requests.cpu`(100m, `base/deployment.yaml:39`).
  즉 평균 **70m** 를 넘으면 늘린다. limits(500m)나 노드 CPU 가 기준이 아니다. requests 가 없으면 비율을 계산할 수 없어 HPA 가 동작하지 않는다.
- **"누가 누구에게" (6.5 교훈 적용)**: 스크랩하는 주체 = metrics-server Pod → 대상 = 각 노드의 kubelet(10250, HTTPS).
  kind 의 kubelet 인증서는 자체 서명 + SAN 에 노드 IP 없음 → x509 실패 → metrics-server 가 `0/1 Running`.
  그래서 `--kubelet-insecure-tls` 인자 하나를 kustomize JSON patch 로 추가(`bootstrap/metrics-server/kustomization.yaml`, 2장 패턴). 로컬 전용.
- **이미 본 것과 연결**: 1장 HPA·PDB, 3.2 `ignoreDifferences`(HPA 가 바꾼 replicas 를 Argo CD 가 되돌리지 않게) — metrics-server 가 있어야 그 HPA 가 실제로 움직인다.
- 클러스터 재생성 때마다 다시 설치(CoreDNS 패치와 같음).

#### 확인 문제 풀이 (2026-09-27) — 2/3

1. metrics-server 없이 prod → **Pod 3개 Running, HPA `<unknown>` → Argo CD Degraded → Jenkins wait 실패** (정답).
2. `--kubelet-insecure-tls` 누락 → **`0/1 Running` + x509 로그** (정답).
3. `averageUtilization: 70` 의 기준 → 답 **`requests.cpu`(100m) 대비** (limits 라고 오답).
   - `requests` = **예약석**: 스케줄러가 "이 Pod 에 이만큼은 보장" 하고 노드에 자리를 잡아 둔 양. HPA 는 "예약한 만큼 대비 얼마나 쓰나" 를 본다.
   - `limits` = **천장**: 넘으면 CPU 는 throttle(느려짐), 메모리는 OOMKilled. 스케일 판단 기준이 아니다.
   - 계산: Pod 3개 사용량 80m·60m·100m → 평균 80m ÷ 100m = **80%** > 70% → 늘림. limits 기준이었다면 16% 라 절대 안 늘었을 것.

## 정리

| 문제 | 원인 | 해결 | 파일 |
|---|---|---|---|
| NodePort 로 접속 불가 | 노드가 Docker 컨테이너 | `extraPortMappings` 로 80/443 등만 노출, 나머지는 Ingress | `kind-config.yaml` |
| 브라우저가 서비스 이름을 모름 | `example.com` 은 가짜 도메인 | hosts 파일에 `127.0.0.1` 등록 | Windows hosts |
| 파드 안에서 이름이 127.0.0.1 로 풀림 | DNS 사슬이 Windows hosts 까지 닿음 | CoreDNS rewrite → ingress-nginx | `bootstrap/coredns/coredns-cm.yaml` |
| 데이터 유실·동시 쓰기 | 노드 컨테이너는 지우면 사라짐 | `/data` 를 한 워커에만 마운트, PV `nodeAffinity` | `kind-config.yaml`, `bootstrap/*/local-pv.yaml` |
| 컨테이너가 디렉터리에 못 씀 | hostPath 에는 `fsGroup` 미적용 | 미리 `chown`, initContainer | README 1.1, `bootstrap/nexus/nexus.yaml` |
| 평문 레지스트리 거부 | 기본은 HTTPS | containerd 미러 / kaniko `--insecure` / `localhost` | `kind-config.yaml`, `ci/Jenkinsfile` |
| HPA 때문에 prod 배포 실패 | kind 에 metrics-server 없음 | metrics-server + `--kubelet-insecure-tls` | `bootstrap/metrics-server/` |

클러스터를 지우면 **CoreDNS 패치, metrics-server, ingress-nginx 설정은 사라지고**, `/data` 의
데이터와 `kind-config.yaml` 에 적힌 설정(포트 매핑, containerd 미러)은 남는다.

## 확인 문제

1. `kind-config.yaml` 에 30082 포트 매핑을 지웠다. Nexus 의 NodePort 서비스는 그대로 있다. WSL 에서 `docker push localhost:30082/...` 는 어떻게 되는가?

<details><summary>답</summary>

실패한다. NodePort 는 kind 노드 컨테이너 안에서만 열리고, `extraPortMappings` 로 매핑한 포트만 WSL 로 올라온다. 매핑이 없으면 WSL 의 30082 에는 아무것도 없다.
</details>

2. CoreDNS 패치 전, 파드 안에서 `nexus-docker.example.com` 이 `127.0.0.1` 로 풀리는 이유는? 그리고 그게 왜 NXDOMAIN 보다 나쁜가?

<details><summary>답</summary>

CoreDNS 가 모르는 이름을 바깥 DNS 로 넘기고, 그 사슬(노드 resolv.conf → Docker DNS → WSL → Windows DNS 프록시)이 Windows hosts 파일에 닿기 때문이다. 파드 안의 127.0.0.1 은 파드 자신이라 `connection refused` 가 나는데, 이름은 풀렸으니 DNS 문제라고 의심하기 어렵다.
</details>

3. CoreDNS rewrite 대상을 Nexus 서비스가 아니라 ingress-nginx 컨트롤러로 잡은 이유는?

<details><summary>답</summary>

이미지 참조에는 포트가 없어 80 으로 접속하는데 Nexus 서비스는 8081/8082/8083 만 연다. 컨트롤러는 80 을 열고, rewrite 는 Host 헤더를 바꾸지 않으므로 기존 Ingress 의 호스트별 라우팅(docker-hosted/8082, docker-group/8083)이 그대로 동작한다.
</details>

4. `/data` 를 worker 두 대 모두에 마운트하면 어떤 문제가 생기는가?

<details><summary>답</summary>

두 노드가 같은 WSL 디렉터리를 공유한다. StatefulSet 롤아웃 중 옛 파드와 새 파드가 서로 다른 노드에 뜨면 같은 DB 파일을 동시에 쓰게 되고, GitLab·Nexus 의 내장 DB 가 망가질 수 있다. hostPath 는 ReadWriteOnce 도 강제하지 않는다.
</details>

5. 노드의 containerd 는 이미지 pull 에 CoreDNS rewrite 를 쓰지 않는다. 그럼 `nexus-docker.example.com` 을 어떻게 찾아가는가?

<details><summary>답</summary>

`kind-config.yaml` 의 `containerdConfigPatches` 미러 설정으로 `http://localhost:30082`(노드 로컬 NodePort)로 보낸다. containerd 는 파드가 아니라 CoreDNS 를 거치지 않으며, endpoint 가 http 라 insecure 설정도 따로 필요 없다.
</details>
