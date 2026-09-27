# 8. 응용

## 이 장의 목표

7장까지 react-app 하나로 만든 CI/CD 흐름을 **넓혀 본다.** 앱을 하나 더 올릴 때 무엇이 재사용되고 무엇만
새로 필요한지, 상태(데이터)를 가진 DB 를 GitOps 로 다룰 때 무엇이 달라지는지 본다.
마지막으로 같은 흐름을 다른 도구(Argo CD Image Updater)로 바꾸는 방법을 비교한다.

---

## 8.1 두 번째 앱 추가: python-api

### 무엇을 재사용하고 무엇을 새로 만드나

두 번째 앱의 핵심은 **"앱마다 필요한 것만 새로 만든다"** 는 점이다. Jenkins 자격증명, GitLab 그룹 토큰,
Argo CD `cicd` 계정, Nexus 는 그대로 쓴다(README 8장 도입부).

| 재사용 (한 번만 만든 것) | 앱마다 새로 만드는 것 |
|---|---|
| Jenkins 자격증명 3개 (`nexus-registry`, `gitops-repo`, `argocd-auth-token`) | 앱 리포지토리 (`my-group/python-api`) |
| GitLab **그룹** 토큰 `ci-bot` | Jenkins Multibranch 잡 |
| Argo CD `cicd` 계정 | `manifests/python-api/` (base + overlays) |
| Nexus 레지스트리 | `apps/python-api.yaml` (Application 2개) |
| | 네임스페이스별 `regcred`, hosts 항목 |

그룹 토큰을 쓴 이유가 여기서 드러난다. 그룹 토큰은 그룹 안의 **새 프로젝트에도 그대로 통한다.**
앱이 늘어도 토큰을 다시 발급하지 않는다(README 8.3).

### 앱 자체: 메모리 CRUD

FastAPI(파이썬 웹 프레임워크)로 만든 아이템 목록 API 다. 저장소가 DB 가 아니라 **프로세스 메모리**다.

`samples/python-api/app/main.py:1`
```python
"""python-api: 메모리 저장소를 쓰는 FastAPI CRUD 예제.

데이터는 프로세스 메모리에만 있다. 파드가 재시작되면 사라지고, 파드가 둘 이상이면
파드마다 목록이 다르다. 그래서 매니페스트의 replicas 는 1 이다(README 8장).
"""
```

이것이 설계를 결정한다. 파드가 둘이면 요청마다 다른 파드가 받아 목록이 달라진다. 그래서 react-app 과 달리
prod 도 `replicas: 1` 이고 HPA·PDB 가 없다. 이런 앱을 **stateful(상태를 가진)** 앱이라 한다. 늘리려면 상태를 DB 로 빼야 한다 —
그 DB 가 8.2 의 PostgreSQL 이다.

`GET /` 는 배포 버전을 돌려준다. 버전은 이미지 빌드 때 들어간다.

`samples/python-api/app/main.py:15`
```python
# APP_ENV 는 Deployment 의 env(overlay 가 dev/prod 로 바꾼다),
# APP_VERSION 은 빌드 때 Dockerfile 의 ARG 로 이미지에 들어간다(이미지 태그).
APP_ENV = os.getenv("APP_ENV", "local")
APP_VERSION = os.getenv("APP_VERSION", "local")
```

`samples/python-api/Dockerfile:17`
```dockerfile
# CI 가 이미지 태그를 넘긴다(Jenkinsfile 의 --build-arg). GET / 의 version 에 표시된다
ARG APP_VERSION=local
ENV APP_VERSION=${APP_VERSION}
```

그래서 `curl http://python-api.dev.example.com/` 한 번으로 "지금 dev 에 어느 커밋이 떠 있나" 를 확인할 수 있다.

### Jenkinsfile: react-app 과 무엇이 다른가

`samples/python-api/Jenkinsfile` 은 `ci/Jenkinsfile` 과 흐름이 같다. 다른 점은 두 가지다.

**1. 앱 이름을 한 곳에서 정한다.**

`samples/python-api/Jenkinsfile:32`
```groovy
  environment {
    APP_NAME      = 'python-api'
    ...
    IMAGE         = "${REGISTRY}/my-group/${APP_NAME}"
```

이미지 이름, overlay 경로(`manifests/${APP_NAME}/overlays/...`), Argo CD 앱 이름(`${APP_NAME}-${DEPLOY_ENV}`)이
모두 `APP_NAME` 에서 나온다. 세 번째 앱을 만들 때 이 한 줄만 바꾸면 된다. 이렇게 **이름 규칙을 맞춰 두는 것**이
앱을 늘리기 쉬운 파이프라인의 비결이다.

**2. 테스트 단계가 python 컨테이너에서 pytest 를 돈다.**

`samples/python-api/Jenkinsfile:68`
```groovy
    stage('테스트') {
      steps {
        container('python') {
          sh '''
            set -eu
            # 워크스페이스 안에 가상환경을 만든다(에이전트 Pod 는 빌드마다 새로 뜬다)
            python -m venv .venv
            . .venv/bin/activate
            pip install --no-cache-dir --disable-pip-version-check -r requirements-dev.txt
            pytest -q
          '''
```

`python` 컨테이너는 Jenkins 에이전트 Pod 템플릿(`bootstrap/jenkins/casc.yaml`)에 들어 있어야 한다.
없으면 `container python not found` 로 실패한다(README 8.5 표).

테스트는 `TestClient` 로 서버를 띄우지 않고 API 를 부른다. 테스트마다 저장소를 비워 서로 영향을 주지 않게 한다.

`samples/python-api/tests/test_main.py:9`
```python
@pytest.fixture(autouse=True)
def clean_store():
    # 테스트끼리 저장소를 공유하지 않게 매번 비운다
    store.reset()
```

### gitops 리포지토리에 올릴 때 주의

README 8.2-3 은 **새 파일만 골라서** 복사하라고 한다. `manifests/` 를 통째로 복사하면, Jenkins 가 그동안 커밋해 둔
react-app 의 이미지 태그가 이 학습용 리포지토리의 옛 값(`dev-0000000`)으로 되돌아간다.
gitops 리포지토리에서는 **Jenkins 도 커밋하는 공동 작성자**라는 점을 잊지 않는다.

```bash
cd ~/workspace/gitops-manifests && git pull
cp ~/workspace/cicd/apps/python-api.yaml apps/
cp ~/workspace/cicd/apps/project.yaml apps/
cp -r ~/workspace/cicd/manifests/python-api manifests/
git status --short            # 이 세 가지만 보여야 한다
```

`apps/` 에 파일을 넣기만 하면 App of Apps(`bootstrap` Application)가 새 Application 을 만든다.
AppProject 의 허용 네임스페이스에 `python-api-*` 가 없으면 `application destination ... is not permitted in project` 가 난다.

첫 빌드 전 dev 가 Degraded 인 것은 정상이다. overlay 의 태그가 아직 없는 이미지를 가리키기 때문이다.

`manifests/python-api/overlays/dev/kustomization.yaml:11`
```yaml
# Jenkins 가 `kustomize edit set image` 로 이 값을 갱신하고 gitops 리포지토리에 커밋한다.
# 첫 빌드 전에는 이 태그의 이미지가 없어 앱이 Degraded 인 것이 정상이다.
images:
  - name: nexus-docker.example.com/my-group/python-api
    newTag: dev-0000000
```

---

## 8.2 PostgreSQL 을 GitOps 로 배포: StatefulSet, PVC

### 앱과 DB 는 무엇이 다른가

앱은 파드를 지우고 새로 띄워도 된다. DB 는 **데이터가 파드보다 오래 살아야 한다.** 그래서 쓰는 리소스가 다르다.

| 용어 | 뜻 |
|---|---|
| **StatefulSet** | 파드에 고정된 이름(`dev-postgres-0`)과 고정된 저장소를 주는 컨트롤러. Deployment 의 DB 판 |
| **PV** (PersistentVolume) | 실제 저장 공간. 여기서는 노드의 디렉터리(`/data/postgres/dev`) |
| **PVC** (PersistentVolumeClaim) | "저장 공간 5Gi 주세요" 라는 요청서. 파드는 PVC 를 통해 PV 를 쓴다 |
| **headless Service** | `clusterIP: None`. 로드밸런싱 없이 파드 주소로 바로 연결되는 이름 |

이미지는 직접 빌드하지 않는다. 공식 `postgres` 이미지를 Nexus 프록시로 받아 쓴다. 그래서 CI 단계가 없고,
버전을 바꾸는 것이 곧 배포다(8.3).

### PV 는 Argo CD 밖, PVC 는 안

`bootstrap/postgres/local-pv.yaml:1`
```yaml
# PostgreSQL(dev/prod) 용 hostPath PV. nodeAffinity 로 storage-node=true 워커 한 대에 고정한다.
# PV 는 클러스터 범위 리소스라 Argo CD(AppProject 가 Namespace 만 허용)가 아니라
# kubectl 로 적용한다(README 9.2). PVC 는 manifests/postgres 에 있고 Argo CD 가 만든다.
```

PV 는 네임스페이스에 속하지 않는 **클러스터 범위** 리소스다. AppProject 의 `clusterResourceWhitelist` 는 Namespace 만 허용한다.
권한을 넓히는 대신 PV 만 사람이 kubectl 로 적용한다. **GitOps 라고 모든 것을 Argo CD 에 맡길 필요는 없다** — 경계를 의도적으로 긋는 예다.

PV 가 엉뚱한 PVC 에 붙지 않게 `claimRef` 로 짝을 미리 정한다.

`bootstrap/postgres/local-pv.yaml:24`
```yaml
  persistentVolumeReclaimPolicy: Retain
  storageClassName: manual
  claimRef:
    namespace: postgres-dev
    name: dev-postgres-data
  hostPath:
    path: /data/postgres/dev
    type: Directory
```

- `Retain`: PVC 가 지워져도 PV 의 데이터를 지우지 않는다.
- `type: Directory`: 디렉터리가 없으면 만들지 않고 실패한다. 자동으로 만들면 root 소유가 되어 UID 999 인 postgres 가 쓰지 못한다(파일 주석 10~11줄).

PVC 는 Argo CD 가 만들되 **지우지는 못하게** 한다.

`manifests/postgres/base/pvc.yaml:5`
```yaml
  annotations:
    # Application 을 지우거나 이 파일을 빼도 Argo CD 가 PVC 를 지우지 않게 한다.
    argocd.argoproj.io/sync-options: Delete=false
```

`prune: true` 인 Application 에서 실수로 파일을 빼도 데이터가 사라지지 않게 하는 안전장치다.

### StatefulSet 의 요점

`manifests/postgres/base/statefulset.yaml:6`
```yaml
  # hostPath PV 하나를 쓰는 단일 인스턴스. 2 이상으로 올리면 두 파드가 같은 디렉터리를
  # 동시에 쓴다(kind-config.yaml 헤더 2번). 복제가 필요하면 오퍼레이터(CloudNativePG 등)를 쓴다.
  replicas: 1
```

비밀번호는 git 에 없다. kubectl 로 만든 Secret 을 참조만 한다.

`manifests/postgres/base/statefulset.yaml:54`
```yaml
            # 비밀번호는 git 에 두지 않는다. kubectl 로 만든 Secret(README 9.2)에서 읽는다.
            # 초기화(빈 디렉터리에서 첫 기동) 때만 쓰인다 — 나중에 Secret 을 바꿔도 DB 비밀번호는 그대로다.
            - name: POSTGRES_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: postgres-auth
                  key: password
```

프로브(파드 상태 검사) 세 가지를 모두 쓴다. 첫 기동의 initdb(데이터 디렉터리 초기화)는 오래 걸리므로
**startupProbe** 로 최대 5분(5초 × 60회) 기다리고, 그동안 livenessProbe 가 파드를 재시작하지 못하게 한다(68~74줄).

### 접속 주소

외부 노출(Ingress·NodePort)이 없다. 클러스터 안에서는 headless Service 이름으로 붙는다.

`manifests/postgres/base/service.yaml:1`
```yaml
# StatefulSet 의 serviceName 이 가리키는 headless Service. 인스턴스가 하나라
# 클러스터 안에서는 이 이름으로 바로 붙는다(README 9.4):
#   <prefix>-postgres.<namespace>.svc.cluster.local:5432   예) dev-postgres.postgres-dev.svc.cluster.local
```

데이터가 파드보다 오래 사는지 직접 확인해 본다(README 9.4).

```bash
kubectl -n postgres-dev exec dev-postgres-0 -- psql -U app -d app -c 'create table if not exists t(id int); insert into t values (1);'
kubectl -n postgres-dev delete pod dev-postgres-0
kubectl -n postgres-dev wait --for=condition=Ready pod/dev-postgres-0 --timeout=120s
kubectl -n postgres-dev exec dev-postgres-0 -- psql -U app -d app -c 'select count(*) from t;'   # 1 이상
```

---

## 8.3 버전 변경 잡: pipelines/postgres/Jenkinsfile, list-versions.py

### 앱 파이프라인과의 차이

| | 앱 파이프라인 (`ci/Jenkinsfile`) | `postgres-deploy` 잡 |
|---|---|---|
| 시작 | push 하면 자동 (Multibranch) | 사람이 **Build with Parameters** |
| 바꾸는 것 | 새로 빌드한 이미지 태그 | 공식 이미지의 버전 태그 |
| 파일 위치 | 앱 리포지토리 | **gitops 리포지토리** `pipelines/postgres/` |
| 승인 | prod 만 "prod 승인" 단계 | 버전 선택 화면이 곧 승인 |

잡이 gitops 리포지토리에서 Jenkinsfile 을 읽고, 또 그 리포지토리에 커밋한다. 그래서 README 9.5-1 은
**Build Triggers 를 켜지 말라**고 한다. SCM 폴링을 켜면 자기 커밋으로 다시 실행된다.

### 흐름

`pipelines/postgres/Jenkinsfile:7`
```
// 흐름: 현재 태그 읽기 → Docker Hub 에서 버전 목록 조회 → [버전 선택 화면(input)]
//       → (메이저가 바뀌면) 확인 → pg_dumpall 백업
//       → gitops 태그 갱신 커밋 → Argo CD 배포 완료까지 대기
//       → (메이저가 바뀌면) 새 메이저에 복원
```

**현재 버전 읽기** — overlay 파일을 직접 파싱하지 않고, `kustomize build` 로 렌더링한 결과에서 실제로 배포될 태그를 읽는다(68~70줄).

**버전 목록** — `list-versions.py` 가 Docker Hub API 에서 태그를 모아 `versions.txt` 에 쓴다.

`pipelines/postgres/list-versions.py:1`
```python
"""postgres 배포 잡(Jenkinsfile)의 "버전 목록" 단계에서 쓴다.

MAJORS(공백 구분, 예: "15 16 17 18 19")의 각 메이저에 대해 Docker Hub 에서 X.Y 태그를 찾아
versions.txt 에 최신순으로 쓴다. 현재 메이저는 PER_MAJOR_CURRENT 개, 다른 메이저는 PER_MAJOR_OTHER 개까지.
-alpine, -bookworm 같은 변형과 17 같은 움직이는 태그는 뺀다. 아직 나오지 않은 메이저는 조용히 건너뛴다.
"""
```

`17` 같은 **움직이는 태그**(가리키는 이미지가 계속 바뀌는 태그)를 빼고 `17.6` 처럼 고정된 태그만 고르는 점에 주목한다.
GitOps 에서는 "git 의 내용 = 클러스터의 상태" 여야 하므로 태그가 몰래 바뀌면 안 된다.

**버전 선택** — `input` 단계에 드롭다운을 띄우고, 고른 값으로 메이저 변경 여부를 판단한다.

`pipelines/postgres/Jenkinsfile:103`
```groovy
          env.CUR_VERSION  = current
          env.CUR_MAJOR    = current.tokenize('.')[0]
          env.NEW_MAJOR    = env.PG_VERSION.tokenize('.')[0]
          env.MAJOR_CHANGE = (env.CUR_MAJOR != env.NEW_MAJOR) ? 'true' : 'false'
```

### 마이너 vs 메이저

PostgreSQL 은 **메이저 버전(17 → 18)마다 데이터 파일 형식이 다르다.** 17 의 데이터로 18 은 뜨지 못한다.
그래서 데이터 디렉터리를 메이저별로 나눈다.

`manifests/postgres/base/statefulset.yaml:42`
```yaml
          command:
            - bash
            - -c
            - export PGDATA="/var/lib/postgresql/${PG_MAJOR}/data"; exec docker-entrypoint.sh postgres
```

| 변경 | 잡이 하는 일 |
|---|---|
| 마이너 (17.6 → 17.x) | 태그만 바꾼다. 같은 디렉터리를 그대로 쓴다 |
| 메이저 (17 → 18) | 17 에서 `pg_dumpall` 백업 → 태그 변경 → 18 이 빈 `18/data` 에 initdb → 덤프 복원 |

백업은 PV 안에 둔다. 새 파드도 같은 PV 를 붙이므로 복원할 때 그대로 읽을 수 있다.

`pipelines/postgres/Jenkinsfile:134`
```bash
            # PV 안에 둔다. 새 파드도 같은 PV 를 붙이므로 복원 때 그대로 읽는다
            BACKUP="/var/lib/postgresql/backup/pg${CUR_MAJOR}-to-pg${NEW_MAJOR}-${TS}.sql"
```

잡이 `kubectl exec` 로 postgres 파드 안에서 명령을 돌리려면 권한이 필요하다. 이 권한을 **postgres 네임스페이스에만** 준다.

`manifests/postgres/base/rbac.yaml:9`
```yaml
rules:
  - apiGroups: [""]
    resources: ["pods"]
    verbs: ["get", "list"]
  - apiGroups: [""]
    resources: ["pods/exec"]
    verbs: ["create"]
```

복원 단계는 `already exists` 오류를 예상된 오류로 걸러 낸다. initdb 가 이미 만든 `app` 사용자·DB 를 덤프가 다시 만들려 하기 때문이다(`pipelines/postgres/Jenkinsfile:223~226`).

### 되돌리기의 함정

복원이 실패했을 때 **잡으로 이전 메이저를 다시 고르면 안 된다**(README 9.5). 잡은 이것도 메이저 변경으로 보고
불완전한 18 의 데이터를 덤프해 17 에 복원하며, 멀쩡한 `17/` 을 치운다. 대신 태그만 손으로 되돌린다.
이전 메이저의 디렉터리가 그대로 남아 있으므로 17 이 옛 데이터로 다시 뜬다.

```bash
cd ~/workspace/gitops-manifests && git pull
cd manifests/postgres/overlays/dev && kustomize edit set image nexus-docker-group.example.com/library/postgres=nexus-docker-group.example.com/library/postgres:17.6
cd ~/workspace/gitops-manifests && git commit -am "revert(dev): postgres -> 17.6" && git push
```

자동화 도구가 있어도 **자동화가 무엇을 가정하는지** 알아야 안전하게 되돌릴 수 있다는 예다.

---

## 8.4 이미지 태그 갱신 방식 비교: CI 커밋 vs Argo CD Image Updater

"새 이미지가 나왔다" 는 사실을 gitops 리포지토리에 적는 주체를 누구로 할지의 선택이다.

`README.md:2213`
| 방식 | 설명 |
|---|---|
| **CI 커밋** (기본) | Jenkins 가 `kustomize edit set image` 후 gitops 리포지토리에 커밋. 이력이 git 에 남고 롤백이 쉽다. |
| **Argo CD Image Updater** | `bootstrap/image-updater/` 적용. 레지스트리를 폴링해 새 태그를 write-back. CI 가 gitops 쓰기 권한을 가질 필요가 없다. |

```
CI 커밋 방식         Jenkins ──push 이미지──▶ Nexus
                    Jenkins ──커밋(태그)──▶ gitops 리포지토리 ──▶ Argo CD

Image Updater 방식   Jenkins ──push 이미지──▶ Nexus ◀──폴링── Image Updater
                                                    Image Updater ──커밋(태그)──▶ gitops 리포지토리 ──▶ Argo CD
```

Image Updater 는 레지스트리를 주기적으로 조회(폴링)해 새 태그를 찾는다. 설정에 레지스트리 주소가 들어 있다.

`bootstrap/image-updater/config.yaml:15`
```yaml
  registries.conf: |
    registries:
      # Nexus docker-hosted. 평문(HTTP)이므로 api_url 도 http 이고 insecure 를 켠다.
      - name: private-registry
        api_url: http://nexus-docker.example.com
        prefix: nexus-docker.example.com
        insecure: yes
        credentials: pullsecret:argocd/regcred
        default: true
```

어느 이미지를 어떤 규칙으로 따라갈지는 Application 의 어노테이션에 적는다(`README.md:2222` 의 예시:
`image-list`, `update-strategy: newest-build`, `allow-tags`, `write-back-method: git`).

| 기준 | CI 커밋 | Image Updater |
|---|---|---|
| 배포 시점 | 빌드 직후, 확정적 | 폴링 주기만큼 늦다 |
| CI 권한 | gitops 쓰기 필요 | 앱 리포지토리 읽기만 |
| 배포 완료 대기 | Jenkins 가 `argocd app wait` 로 결과를 안다 | CI 는 결과를 모른다 |
| 구성요소 | 추가 없음 | 컨트롤러 하나 추가 |

**둘을 동시에 쓰면 같은 파일에 서로 커밋해 충돌한다.** 하나만 고른다. 이 프로젝트는 CI 커밋 방식이라
`apps/react-app.yaml` 에 Image Updater 어노테이션이 없다(`README.md:2219`).

---

## 정리

| 주제 | 핵심 |
|---|---|
| 두 번째 앱 | 공용 자원(자격증명·그룹 토큰·Argo CD 계정)은 재사용, `APP_NAME` 하나로 이름 규칙 통일 |
| PostgreSQL | StatefulSet + PVC. PV 는 kubectl, PVC 는 Argo CD(`Delete=false`), 비밀번호는 Secret |
| 버전 변경 잡 | 수동 실행, 고정 태그만 선택. 메이저 변경은 백업 → initdb → 복원 |
| 태그 갱신 방식 | CI 커밋(기본, 확정적) vs Image Updater(폴링, CI 권한 축소). 하나만 |

## 확인 문제

1. python-api 의 prod 가 react-app 과 달리 `replicas: 1` 이고 HPA 가 없는 이유는?

<details><summary>답</summary>

데이터를 파드 메모리에 두기 때문이다. 파드가 둘 이상이면 요청마다 다른 파드가 받아 목록이 달라진다.
늘리려면 저장소를 DB 로 빼야 한다.
</details>

2. PostgreSQL 의 PV 를 Argo CD 가 아니라 kubectl 로 적용하는 이유는?

<details><summary>답</summary>

PV 는 클러스터 범위 리소스인데 AppProject 의 `clusterResourceWhitelist` 가 Namespace 만 허용한다.
권한을 넓히는 대신 PV 만 kubectl 로 적용한다.
</details>

3. 메이저 버전 변경이 태그만 바꿔서는 안 되는 이유와, 잡이 대신 하는 일은?

<details><summary>답</summary>

메이저마다 데이터 파일 형식이 달라 새 메이저가 옛 데이터로 뜨지 못한다. 잡은 옛 메이저에서 `pg_dumpall` 로 백업하고,
태그를 바꿔 새 메이저가 빈 `<메이저>/data` 에 initdb 하게 한 뒤, 덤프를 복원한다.
</details>

4. CI 커밋 방식과 Image Updater 를 함께 쓰면 안 되는 이유는?

<details><summary>답</summary>

둘 다 같은 overlay 의 이미지 태그를 gitops 리포지토리에 커밋하므로 서로 충돌한다.
</details>
