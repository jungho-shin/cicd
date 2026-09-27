# 7. 실습: 처음부터 구축

## 이 장의 목표

1~6장에서 익힌 개념을 실제로 조립해, **develop 브랜치에 push 하면 dev 에 배포되고, main 은 승인 후 prod 에 배포되는** 환경을 내 PC(WSL2) 안에 만든다.
이 문서는 README 를 대신하지 않는다. 각 단계에서 **무엇을 왜 하는지, 끝났는지 어떻게 확인하는지, 어디서 자주 막히는지**를 알려 주는 길잡이다. 실제 명령 전체는 각 절에 적은 `README.md:줄` 을 열어 따라 한다.

진행 원칙:

- **순서를 바꾸지 않는다.** 뒤 단계가 앞 단계의 결과물(토큰, 레지스트리, DNS)을 쓴다.
- **단계마다 "확인" 명령을 통과한 뒤 넘어간다.** 문제는 생긴 단계에서 잡는 것이 가장 싸다.
- **비밀 값은 화면·파일·채팅에 남기지 않는다.** README 가 `read -rsp` 와 권한 600 파일을 쓰는 이유다.

```
0 사전 준비 → 1 클러스터 → 2 GitLab → 3 Nexus → 4 Argo CD → 5 Jenkins → 6 webhook → 7 파이프라인
                                                                                     └→ 7.5 prod → 롤백 → 10 재시작 점검
```

---

## 7.1 0장 사전 준비 (도구 버전, 토큰 보관 위치)

**무엇을 왜.** 도구 버전이 어긋나면 나중에 원인을 찾기 어려운 오류가 난다. 예를 들어 kubectl 은 클러스터(v1.34)와 마이너 버전 ±1 안이어야 한다. 또 구축 중에 비밀번호·토큰이 9개 가까이 생기므로, 어디에 보관할지 미리 정해 둔다.

**따라 할 곳.** `README.md:119` (0.1 도구 표), `README.md:151` (0.2 보관 위치 표)

**확인.**

```bash
ps -p 1 -o comm=                     # systemd
command -v docker                    # /usr/bin/docker (Docker Desktop 이 아니다)
kind version                         # v0.30.0 이상
kubectl version --client             # v1.33 ~ v1.35
git config --global user.name        # 값이 있어야 한다
```

**자주 막히는 곳.**

- 리포지토리를 `/mnt/c/...` 아래에 clone 한다. **WSL 의 ext4(`~/workspace/cicd`)에** 둔다(`README.md:144`).
- git 작성자가 없으면 나중에 gitops 커밋이 `Author identity unknown` 으로 실패한다.

> 0.2 표를 출력해 옆에 두고, 값을 만들 때마다 "어디에 보관했는지" 체크하면 헷갈리지 않는다.

## 7.2 1장 클러스터 준비

**무엇을 왜.** 모든 구성요소가 올라갈 쿠버네티스 클러스터를 kind 로 만든다. kind 노드는 Docker 컨테이너라서 **포트 매핑한 것만 밖(WSL/Windows)에서 보인다.** 그래서 웹 UI 는 모두 80 포트 하나(Ingress)로 받고, 호스트 이름(`*.example.com`)으로 나눈다.

이 절은 여섯 조각으로 되어 있고, 각각 이유가 있다.

| 순서 | 작업 | 왜 필요한가 | README |
|---|---|---|---|
| 1.1 | `/data` 디렉터리, DNS 고정, inotify | 영구 데이터를 WSL 디스크에 두고, 외부 DNS 가 멈추는 문제를 예방 | `README.md:213` |
| 1.2 | `kind create cluster` | 노드 3대, 80/443 매핑, 스토리지 노드 1대 | `README.md:269` |
| 1.3 | ingress-nginx (kind 판) | 80 포트로 들어온 요청을 호스트 이름별로 나눔 | `README.md:319` |
| 1.5 | Windows hosts 파일 | 브라우저에서 `gitlab.example.com` → `127.0.0.1` | `README.md:385` |
| 1.6 | CoreDNS rewrite | **클러스터 안**에서도 같은 이름이 Ingress 로 가게 함 | `README.md:412` |
| 1.8 | metrics-server | prod 의 HPA 가 CPU 지표를 읽음 | `README.md:536` |

클러스터 설정 파일에서 "control-plane 에만 80/443 을 연다"는 부분이 핵심이다.

`kind-config.yaml:85`

```yaml
    labels:
      ingress-ready: "true"
    extraPortMappings:
      # ingress-nginx hostPort. 모든 웹 UI(gitlab/jenkins/nexus/argocd)가 여기로 들어온다.
      - containerPort: 80
        hostPort: 80
```

**확인.**

```bash
kubectl get nodes -L ingress-ready,storage-node        # 3대 Ready, 라벨이 각각 한 대씩
kubectl -n ingress-nginx get pods -o wide              # NODE 가 devops-control-plane
curl -sS -o /dev/null -w '%{http_code}\n' -H 'Host: nexus.example.com' http://localhost/   # 지금은 404 가 정상
kubectl top nodes                                      # 1분쯤 뒤 값이 나온다
```

`404` 는 실패가 아니다. "nginx 까지는 도착했는데 아직 그 이름의 Ingress 가 없다"는 뜻이다(`README.md:373`).

**자주 막히는 곳.**

- **CoreDNS rewrite(1.6)를 건너뛴다.** 파드 안에서 `nexus-docker.example.com` 이 `127.0.0.1`(파드 자신)로 풀려, 나중에 kaniko push·Argo CD 의 Git 접근이 모두 `connection refused` 로 죽는다. 이름이 "안 풀리는" 게 아니라 "엉뚱하게 풀리는" 문제라 진단이 어렵다(`README.md:425`).
- ingress-nginx 를 kind 판이 아닌 매니페스트로 설치하면 80 포트가 열리지 않는다(`README.md:321`).
- 1.6·1.8 은 `kind delete cluster` 와 함께 사라진다. 클러스터를 다시 만들면 다시 적용한다.

## 7.3 2장 GitLab CE 설치 + 토큰 발급

**무엇을 왜.** 앱 리포지토리와 gitops 리포지토리가 모두 여기에 산다. omnibus 이미지 하나로 DB·Redis·git 서버까지 한 파드에 띄우므로 가장 무겁고(메모리 4~6Gi), 첫 기동에 8~15분 걸린다.

**따라 할 곳.** `README.md:593` (설치), `README.md:760` (토큰)

GitLab 은 `external_url` 로 clone 주소와 리디렉션을 만든다. 그래서 브라우저에서 쓰는 주소와 반드시 같아야 한다.

`bootstrap/gitlab/gitlab.yaml:113`

```ruby
external_url 'http://gitlab.example.com'
```

**토큰 두 종류를 구분한다.** 이 실습에서 가장 헷갈리는 부분이다.

| 누가 쓰나 | 종류 | 권한 | 언제 발급 |
|---|---|---|---|
| Argo CD (gitops 읽기만) | Deploy token | `read_repository` | 4장에서 gitops 프로젝트를 만든 직후 |
| Jenkins (앱 체크아웃 + gitops push) | Group access token `ci-bot`, **Maintainer** | `read_repository`, `write_repository` | 지금, 그룹 `my-group` 을 만든 뒤 |

권한을 최소로 나누는 것이 원칙이다. Argo CD 는 읽기만 하면 되므로 쓰기 권한을 주지 않는다.

**확인.**

```bash
curl -sS -o /dev/null -w '%{http_code}\n' -H 'Host: gitlab.example.com' http://localhost/   # 302 (로그인 페이지로)
kubectl -n gitlab exec sts/gitlab -- gitlab-rails runner \
  'u = User.find_by(username: "root"); puts u ? "root EXISTS" : "root MISSING"'
ls -l ~/gitlab-group-token.txt       # 크기만 본다. 내용은 출력하지 않는다
```

**자주 막히는 곳.**

- **약한 root 비밀번호**(`admin123` 등)면 컨테이너가 exit 1 로 재시작을 반복한다. 메모리 문제처럼 보여서 헷갈린다. 비밀번호를 고친 뒤에도 root 계정이 안 생기는 함정이 있다(`README.md:652`).
- 로그인 ID 는 이메일이 아니라 `root` 다.
- Auto DevOps 를 끄지 않으면 push 할 때마다 쓸모없는 파이프라인이 Pending 으로 쌓인다(`README.md:646`).
- 토큰 role 을 Developer 로 주면 7장에서 gitops push 가 `pre-receive hook declined` 로 거부된다.

## 7.4 3장 Nexus 설치 + 레지스트리 구성

**무엇을 왜.** CI 가 만든 이미지를 보관하고, 클러스터가 그 이미지를 내려받는 창고다. 동시에 Docker Hub·gcr.io·quay.io 의 **프록시(캐시)** 역할도 해서, Jenkins 에이전트 이미지(kaniko, argocd)를 여기서 받는다.

**따라 할 곳.** `README.md:820` (설치), `README.md:848` (UI 에서 리포지토리 만들기), `README.md:985` (`regcred`)

만들 레지스트리의 관계를 그림으로 잡아 두면 UI 설정이 쉽다.

```
docker-hosted (8082)  ← CI 가 앱 이미지를 push. pull 은 인증 필요(regcred)
docker-group  (8083)  ← 익명 pull. 멤버 순서: hosted → docker-hub → gcr-proxy → quay-proxy
```

이 레지스트리는 TLS 없는 평문 HTTP 다. 그래서 접속하는 주체마다 예외 처리가 필요하고, 이 프로젝트는 세 곳에 나눠 이미 넣어 두었다(`README.md:909` 표).

- 노드의 이미지 pull: `kind-config.yaml:57` 의 `containerdConfigPatches`
- kaniko 의 push: CoreDNS rewrite + `--insecure` 옵션 (`ci/Jenkinsfile:105`)
- WSL 에서 수동 push: `localhost:30082` (Docker 가 localhost 는 예외로 취급)

마지막으로 앱이 뜰 네임스페이스 4곳에 pull 용 Secret `regcred` 를 만든다. Deployment 가 이 이름을 참조한다.

`manifests/react-app/base/deployment.yaml:67`

```yaml
      imagePullSecrets:
        - name: regcred
```

**확인.**

```bash
curl -sS -o /dev/null -w '%{http_code}\n' -H 'Host: nexus.example.com' http://localhost/   # 200
docker exec devops-worker crictl pull nexus-docker-group.example.com/library/alpine:3.20  # 익명 pull 성공
kubectl get secret -A --field-selector metadata.name=regcred                              # 4개
```

**자주 막히는 곳.**

- 초기 비밀번호 `admin123` 을 바로 바꾸고, 첫 로그인 마법사의 익명 접근은 **Enable** 로 고른다.
- 8082/8083 포트는 **UI 에서 커넥터를 만들어야** 열린다.
- `docker-group` 멤버 순서를 바꾸면 Docker Hub 의 엉뚱한 `argoproj/argocd` 이미지를 받을 수 있다(`README.md:868`).
- `no basic auth credentials` 는 경로는 맞고 인증에서 막힌 것이다. 익명 접근 설정을 다시 본다.

## 7.5 4장 Argo CD 설치 + Application 등록

**무엇을 왜.** GitOps 의 "CD" 담당이다. gitops 리포지토리를 읽어 클러스터를 그 내용과 똑같이 맞춘다. 이 절에서 **gitops 리포지토리를 처음 만든다.**

**따라 할 곳.** `README.md:1060` (설치), `README.md:1130` (로그인), `README.md:1156` (CI 토큰), `README.md:1175` (Application 등록)

단계는 다음과 같다.

1. 설치 — `--server-side` 필수. 빼면 ApplicationSet CRD 가 annotation 크기 한도를 넘어 만들어지지 않는다.
2. admin 로그인과 비밀번호 변경.
3. CI 전용 계정 `cicd` 의 토큰 발급. 이 계정은 설정에 이미 정의돼 있다.

   `bootstrap/argocd/configs/argocd-cm.yaml:17`

   ```yaml
   accounts.cicd: apiKey, login
   ```

4. GitLab 에 `gitops-manifests` 프로젝트를 만들고 이 리포지토리의 `apps/`, `manifests/`, `pipelines/` 를 **루트에** 올린다.
5. Deploy token 을 발급해 Argo CD 에 리포지토리 자격증명으로 등록한다.
6. `apps/app-of-apps.yaml` **하나만** apply 한다. 나머지 Application 은 Argo CD 가 gitops 리포지토리에서 읽어 스스로 만든다(App of Apps).

**확인.**

```bash
argocd repo list --grpc-web --refresh hard     # STATUS Successful
argocd app list --grpc-web
```

이 시점의 기대 상태는 README 표(`README.md:1240`)와 비교한다. **Degraded·Missing 이 섞여 있는 것이 정상이다.**

| Application | 기대 상태 | 이유 |
|---|---|---|
| `bootstrap` | Synced / Healthy | |
| `react-app-dev` | Synced / Degraded | 이미지 태그 `dev-0000000` 이 아직 없다. 7장 첫 빌드에서 풀린다 |
| `react-app-prod` | OutOfSync / Missing | 수동 동기화 대상이라 아직 아무것도 안 만들었다 |

봐야 할 것은 **`ComparisonError` 가 없느냐**다. 있으면 Git 을 못 읽고 있는 것이다.

**자주 막히는 곳.**

- `argocd login` 은 평문 HTTP 라 `argocd.example.com:80 --grpc-web --plaintext --skip-test-tls` 네 가지가 모두 필요하다. 하나라도 빠지면 `context deadline exceeded` 로 멈춘다(`README.md:1143`).
- gitops 프로젝트를 만들 때 *Initialize repository with a README* 를 켜면 첫 push 가 거부된다.
- Deploy token 의 사용자명은 `root` 가 아니라 `gitlab+deploy-token-<N>` 이다.
- 한 번 틀린 자격증명을 고쳐도 `repo list` 는 캐시 때문에 계속 `Failed` 로 보인다. `--refresh hard` 를 붙인다.

## 7.6 5장 Jenkins 설치

**무엇을 왜.** CI 담당이다. 설치 마법사 없이 **JCasC**(설정을 코드로 적는 방식)로 뜨므로, 관리자 계정·자격증명·에이전트 Pod 템플릿이 `bootstrap/jenkins/casc.yaml` 에 이미 다 들어 있다. 우리가 할 일은 **비밀 값 Secret 을 넣어 주는 것**뿐이다.

**따라 할 곳.** `README.md:1286` (시크릿), `README.md:1329` (설치), `README.md:1366` (에이전트)

컨트롤러는 빌드를 직접 돌리지 않는다. 빌드마다 에이전트 Pod 를 새로 띄운다.

`bootstrap/jenkins/casc.yaml:58`

```yaml
      numExecutors: 0
```

`jenkins-secrets` 에는 앞 단계에서 모은 값이 한꺼번에 들어간다. 여기서 **2장·3장·4장의 결과물이 처음 한데 모인다.**

| Secret 키 | 어디서 왔나 |
|---|---|
| `GITOPS_USER` / `GITOPS_TOKEN` | 2.4 의 그룹 토큰 `ci-bot` |
| `NEXUS_USER` / `NEXUS_PASSWORD` | 3장 Nexus 계정 |
| `ARGOCD_AUTH_TOKEN` | 4.4 의 `cicd` 토큰 |

**확인.**

```bash
kubectl -n jenkins get secret jenkins-secrets                                # DATA 7
kubectl -n jenkins get pods                                                  # jenkins-0 1/1
curl -sS -o /dev/null -w '%{http_code}\n' http://jenkins.example.com/login   # 200
```

UI 에서 **Manage Jenkins > Credentials** 에 자격증명 3개, **Clouds > kubernetes** 에 Pod 템플릿 `build` 가 보이면 된다. 첫 빌드 전에 두 워커 노드에 에이전트 이미지를 미리 받아 두면(`README.md:1390`) 이미지 경로 문제를 빌드 전에 잡을 수 있다.

**자주 막히는 곳.**

- `/data/jenkins` 소유자가 1000 이 아니면 `Permission denied` 로 CrashLoopBackOff.
- 토큰 파일 끝의 줄바꿈이 섞이면 인증이 401 로 실패한다. README 명령이 `tr -d '\n'` 을 쓰는 이유다.
- Secret 값을 나중에 바꾸면 `rollout restart sts/jenkins` 를 해야 반영된다. 환경 변수는 파드가 뜰 때만 읽힌다.
- 플러그인은 인터넷에서 받는다. `install-plugins` 가 이름 해석 실패로 죽으면 1.1 의 DNS 고정을 확인한다.

## 7.7 6장 GitLab webhook 연결

**무엇을 왜.** Argo CD 는 기본으로 180초마다 Git 을 확인한다(폴링). webhook 을 달면 gitops 리포지토리에 push 되는 즉시 GitLab 이 Argo CD 에 알려 준다. **없어도 동작은 한다.** 배포가 최대 3분 늦어질 뿐이다.

`bootstrap/argocd/configs/argocd-cmd-params-cm.yaml:14`

```yaml
  timeout.reconciliation: 180s
```

**따라 할 곳.** `README.md:1400`

webhook 시크릿은 "이 요청이 진짜 GitLab 에서 왔는가"를 확인하는 암호다. 같은 값을 **Argo CD 의 `argocd-secret`** 과 **GitLab webhook 설정** 양쪽에 넣는다.

**확인.** GitLab 의 webhook 화면에서 **Test > Push events** → `Hook executed successfully: HTTP 200`.

```bash
kubectl -n argocd logs deploy/argocd-server --since=2m | grep -i webhook
```

**자주 막히는 곳.**

- GitLab 은 기본으로 내부망 주소로 webhook 을 보내지 못하게 막는다. **Admin Area > Settings > Network > Outbound requests** 에서 먼저 허용한다(`README.md:1423`).
- 시크릿 파일이 비어 있는 채로 patch 하면 Argo CD 가 서명 검증 없이 모든 요청을 받는다. README 명령의 `test -s` 가 이것을 막는다.

## 7.8 7장 react-app 파이프라인: develop push → dev 배포 확인

**무엇을 왜.** 드디어 앱을 올린다. 지금까지 만든 모든 조각이 한 번에 동작하는지 보는 단계다.

**따라 할 곳.** `README.md:1472` (로컬 확인), `README.md:1497` (GitLab push), `README.md:1523` (Jenkins 잡), `README.md:1544` (dev 확인)

1. **로컬에서 먼저 확인한다.** 이미지가 클러스터와 같은 조건(읽기 전용 루트, UID 10001)으로 뜨는지 WSL 의 docker 로 본다. CI 에서 실패하면 원인 찾기가 훨씬 오래 걸린다.
2. GitLab 에 `react-app` 프로젝트를 만들고 `main`, `develop` 두 브랜치를 push 한다.
3. Jenkins 에 **Multibranch Pipeline** 잡을 만든다. 저장하면 브랜치 스캔이 돌고 두 브랜치에 빌드가 걸린다.

그다음 무슨 일이 일어나는지는 0장에서 본 흐름 그대로다.

```
develop 빌드: 준비 → 테스트·빌드 → 이미지 푸시(Nexus) → gitops 태그 갱신 → 배포 대기
                                                           │
                             gitops-manifests 에 커밋: chore(dev): react-app -> develop-xxxxxxxx
                                                           │
                             Argo CD 가 감지(webhook) → react-app-dev 자동 동기화
```

**확인.** 콘솔 로그 마지막 줄에 `배포 완료: dev → http://react-app.dev.example.com (version: develop-xxxxxxxx)`.

```bash
argocd app get react-app-dev --grpc-web | grep -E 'Sync Status|Health Status'   # Synced / Healthy
kubectl -n react-app-dev get pods
curl -s http://react-app.dev.example.com/healthz                                 # ok
```

브라우저에서 `environment: dev`, `version: develop-<sha>` 가 보이면 성공이다. 4장에서 Degraded 였던 `react-app-dev` 가 이제 Healthy 로 바뀐 것도 확인한다.

**자주 막히는 곳.** README 의 표(`README.md:1602`)가 잘 정리돼 있다. 입문자가 특히 많이 막히는 것:

- **push 해도 빌드가 안 걸린다.** 자동 트리거가 없다. **Scan Multibranch Pipeline Now** 를 누른다.
- 에이전트 Pod 가 `ImagePullBackOff` → Nexus `gcr-proxy`/`quay-proxy` 설정(3.3).
- `main` 빌드가 "prod 승인"에서 멈춰 있다 → 정상이다. 다음 단계에서 승인한다.

## 7.9 7.5 prod 배포 (승인 → 수동 sync)

**무엇을 왜.** 운영은 사람이 확인하고 배포한다. dev 와 무엇이 다른지 직접 체감하는 단계다.

**따라 할 곳.** `README.md:1560`

**사전 조건 두 가지**: metrics-server(1.8)와 hosts 의 `react-app.example.com`.

prod Application 에는 `automated` 가 없다. 그래서 gitops 커밋이 생겨도 Argo CD 는 "OutOfSync" 라고 표시만 하고 스스로 배포하지 않는다.

`apps/react-app.yaml:52`

```yaml
  syncPolicy:
    # 운영은 수동 승인 배포. 자동화하려면 automated 블록을 추가한다.
    syncOptions:
      - CreateNamespace=true
```

그래서 Jenkins 가 승인 후 직접 sync 를 건다(`ci/Jenkinsfile:172`). 흐름: **배포 버튼 클릭 → gitops 커밋 → `argocd app sync` → Synced + Healthy 대기.**

**확인.** 핵심은 **같은 태그(`main-xxxxxxxx`)가 모든 단계에서 보이는지**다. 태그가 달라지는 곳 바로 앞이 멈춘 단계다.

```bash
# Argo CD 가 본 커밋과 상태
kubectl -n argocd get application react-app-prod \
  -o jsonpath='{.status.sync.status} {.status.health.status} {.status.sync.revision}{"\n"}'
# 클러스터에 실제로 뜬 이미지
kubectl -n react-app-prod get deploy prod-react-app \
  -o jsonpath='{.spec.template.spec.containers[0].image}{"\n"}'
kubectl -n react-app-prod get pods,hpa,pdb      # 파드 3개
curl -s http://react-app.example.com/config.js  # env: "prod"
```

**자주 막히는 곳.**

- 첫 prod 배포에서 `app wait 실패 - 일시적인 Degraded 인지 2분 더 지켜본다` 가 찍힌다 → 정상이다. 새 HPA 가 첫 지표를 받기 전 잠깐 Degraded 로 보인다.
- 같은 커밋을 다시 빌드하면 파드가 안 바뀐다 → 정상이다. 태그가 같아 gitops 커밋이 생략된다.
- 승인 대기는 60분이 지나면 빌드가 중단된다. 그때는 `main` 을 다시 빌드한다.

## 7.10 롤백 해 보기: gitops 리포지토리 커밋 revert

> **이 절은 README 에 없는 내용이다.** GitOps 의 일반적인 롤백 방법을 이 프로젝트 구조에 맞춰 정리한 것이므로, 실습 전에 무엇이 바뀌는지 한 번 더 확인하고 진행한다.

**무엇을 왜.** GitOps 에서는 "지금 떠 있어야 할 버전"이 gitops 리포지토리에 적혀 있다. 따라서 롤백 = **그 파일을 이전 태그로 되돌리는 커밋**이다. 클러스터에 `kubectl` 로 직접 손대지 않는다. dev 는 `selfHeal: true`(`apps/react-app.yaml:22`)라서, 손으로 이미지를 바꿔도 Argo CD 가 Git 내용으로 곧바로 되돌려 버린다.

**준비.** develop 에 두 번 이상 배포해서 gitops 리포지토리에 `chore(dev): ...` 커밋이 두 개 이상 있어야 한다. 앱 화면 문구를 조금 고쳐 push 하고 빌드하면 된다.

**절차 (dev 기준).**

```bash
cd ~/workspace/gitops-manifests
git pull                                   # Jenkins 가 쌓은 커밋을 먼저 받는다
git log --oneline -5 -- manifests/react-app/overlays/dev
#   b2c3d4e chore(dev): react-app -> develop-bbbbbbbb   ← 되돌릴 커밋
#   a1b2c3d chore(dev): react-app -> develop-aaaaaaaa

git revert --no-edit <되돌릴 커밋 SHA>
git show --stat HEAD                       # overlays/dev/kustomization.yaml 한 파일만 바뀌어야 한다
git push origin main
```

**확인.**

```bash
argocd app get react-app-dev --grpc-web | grep -E 'Sync Status|Health Status'
kubectl -n react-app-dev get deploy dev-react-app \
  -o jsonpath='{.spec.template.spec.containers[0].image}{"\n"}'   # develop-aaaaaaaa
```

브라우저의 `version` 이 이전 태그로 돌아오면 성공이다. Argo CD UI 의 **History** 에서도 배포 이력을 볼 수 있다.

**prod 라면.** 커밋을 revert 하고 push 한 뒤, prod 는 자동 동기화가 아니므로 직접 sync 한다.

```bash
argocd app sync react-app-prod --grpc-web
```

prod 프로젝트에는 `syncWindows` 가 있지만 `manualSync: true` 라서 수동 sync 는 허용된다(`apps/project.yaml:66`).

**주의할 점.**

- 롤백 커밋은 **다음 빌드가 덮어쓴다.** 새 커밋을 develop 에 push 하면 Jenkins 가 새 태그로 다시 커밋한다. 근본 수정은 앱 리포지토리에서 한다.
- 되돌릴 이미지가 Nexus 에 남아 있어야 한다. 지웠다면 파드가 `ImagePullBackOff` 가 된다.
- Argo CD UI 의 **Rollback** 버튼은 Git 을 바꾸지 않고 클러스터만 과거 상태로 돌린다. 자동 동기화가 켜진 dev 에서는 쓸 수 없고, 쓰더라도 Git 과 클러스터가 어긋나므로 이 실습에서는 git revert 방식을 쓴다.

## 7.11 10장 재시작 후 점검

**무엇을 왜.** 로컬 환경은 PC 재부팅, `wsl --shutdown` 으로 자주 멈췄다 다시 뜬다. 그때마다 어디까지 살아났는지 **아래에서 위로** 점검한다: docker → 노드 → DNS → 파드 → Ingress → Argo CD.

**따라 할 곳.** `README.md:2102` (점검 명령), `README.md:2137` (알려진 문제)

**확인.** 재시작 직후에는 **5~10분 기다린 뒤** 본다. 그 전의 `503` 은 GitLab 이 기동 중이라 정상이다.

```bash
docker ps -a --filter label=io.x-k8s.kind.cluster=devops --format 'table {{.Names}}\t{{.Status}}'   # 3개 Up
kubectl get nodes                                  # 3대 Ready
getent ahostsv4 pypi.org | head -1                 # 외부 DNS
kubectl get pods -A | grep -vE 'Running|Completed' # 비어 있어야 한다
for h in gitlab nexus argocd jenkins; do
  printf '%-8s ' "$h"; curl -sS -o /dev/null -w '%{http_code}\n' -H "Host: $h.example.com" http://localhost/
done
argocd app list --grpc-web                         # ComparisonError 없음
```

**자주 막히는 곳.**

- 노드 컨테이너 일부가 `Exited` → `docker start $(docker ps -aq --filter label=io.x-k8s.kind.cluster=devops)`. 1.2 에서 재시작 정책을 `unless-stopped` 로 바꿔 두면 대부분 예방된다.
- `argocd` 명령이 `token is expired` → admin 세션은 24시간이면 만료된다. 다시 `argocd login`.
- `argocd.example.com` 이 504 → kindnet 문제. `kubectl -n kube-system rollout restart ds/kindnet` (`README.md:2151`).

**처음부터 다시 하고 싶을 때.** `README.md:2168` 의 초기화 절차를 따른다. 클러스터, `/data`, 로컬 작업 리포지토리, 토큰 파일을 지운다. **되돌릴 수 없으므로** 필요하면 `/data` 를 먼저 백업한다. Windows hosts, WSL 설정, 설치한 CLI 는 남겨 둬도 된다.

---

## 정리

| 단계 | 끝났다는 신호 | 여기서 만든 것이 쓰이는 곳 |
|---|---|---|
| 0 사전 준비 | 도구 표 전부 통과 | 전 단계 |
| 1 클러스터 | `localhost` 가 `404`, 파드 안 DNS 가 Ingress IP | 모든 접속 |
| 2 GitLab | `302`, root 로그인, 그룹 토큰 파일 | 4장(gitops), 5장(Jenkins) |
| 3 Nexus | `200`, 익명 pull 성공, `regcred` 4개 | 5장 에이전트 이미지, 7장 push/pull |
| 4 Argo CD | `repo list` Successful, `ComparisonError` 없음, `cicd` 토큰 | 5장 Jenkins, 7장 배포 |
| 5 Jenkins | 자격증명 3개, Pod 템플릿 `build` | 7장 |
| 6 webhook | Test → HTTP 200 | 배포 지연 제거 |
| 7 파이프라인 | dev·prod 에서 같은 태그가 브라우저까지 보임 | — |

- 문제가 생기면 **태그를 따라가며** 어느 단계에서 끊겼는지 찾는다: Jenkins 로그 → gitops 커밋 → Argo CD revision → Deployment 이미지 → 브라우저.
- 롤백은 **Git 에서** 한다. 클러스터를 직접 고치지 않는다.

## 확인 문제

1. 1장 직후 `curl -H 'Host: nexus.example.com' http://localhost/` 가 `404` 를 돌려줬다. 실패인가?

<details><summary>답</summary>

실패가 아니다. ingress-nginx 까지는 도착했지만 아직 그 이름의 Ingress 가 없어서 기본 백엔드가 404 를 준 것이다. 오히려 docker 포트 매핑 → 컨트롤러까지 통로가 열렸다는 증거다. Nexus 를 설치한 뒤에는 200 이 된다.

</details>

2. CoreDNS rewrite(1.6)를 빼먹으면 7장의 어느 단계가 어떻게 실패하는가?

<details><summary>답</summary>

파드 안에서 `nexus-docker.example.com` 이 Windows hosts 를 거쳐 `127.0.0.1`(파드 자신)로 풀린다. 그래서 "이미지 빌드·푸시" 단계에서 kaniko 가 자기 자신에게 붙으려다 `connection refused` 로 실패한다. Argo CD 의 `gitlab.example.com` 접근, "배포 대기" 의 `argocd.example.com` 접근도 같은 이유로 깨진다.

</details>

3. Argo CD 에는 Deploy token 을, Jenkins 에는 Group access token 을 쓴다. 왜 하나로 통일하지 않는가?

<details><summary>답</summary>

권한을 필요한 만큼만 주기 위해서다. Argo CD 는 gitops 리포지토리를 **읽기만** 하므로 그 프로젝트 전용 `read_repository` 토큰이면 된다. Jenkins 는 앱 리포지토리를 읽고 gitops 리포지토리에 **써야** 하므로 그룹 범위의 쓰기 권한(Maintainer)이 필요하다.

</details>

4. 4장 직후 `react-app-dev` 가 Degraded, `react-app-prod` 가 OutOfSync/Missing 이다. 각각 언제, 무엇 때문에 풀리는가?

<details><summary>답</summary>

`react-app-dev` 는 overlay 의 이미지 태그 `dev-0000000` 이 Nexus 에 없어서 Degraded 다. 7장에서 Jenkins 가 첫 태그를 gitops 에 커밋하면 풀린다. `react-app-prod` 는 자동 동기화가 없어서 아직 아무것도 만들지 않은 상태다. 7.5 에서 승인 후 Jenkins 가 `argocd app sync` 를 걸면 풀린다.

</details>

5. dev 에 문제가 있는 버전이 배포됐다. `kubectl set image` 로 이전 이미지를 넣으면 어떻게 되고, 올바른 롤백 방법은 무엇인가?

<details><summary>답</summary>

dev Application 은 `selfHeal: true` 라서 Argo CD 가 곧바로 Git 에 적힌 (문제 있는) 버전으로 되돌린다. 올바른 방법은 gitops 리포지토리에서 해당 `chore(dev): ...` 커밋을 `git revert` 하고 push 하는 것이다. 그러면 Argo CD 가 이전 태그로 동기화한다. (이 절차는 README 에 없는 일반적인 GitOps 방법이다.)

</details>
