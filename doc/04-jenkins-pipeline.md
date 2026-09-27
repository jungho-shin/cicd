# 4. Jenkins 파이프라인

## 이 장의 목표

Jenkins 가 이 프로젝트에서 맡는 **CI**(Continuous Integration, 코드를 테스트하고 배포 가능한 결과물로 만드는 일)를
어떻게 수행하는지 이해한다. `ci/Jenkinsfile` 을 한 단계씩 읽고, 그 파일이 돌아가기 위해 Jenkins 컨트롤러 쪽에
무엇이 준비돼 있어야 하는지(`bootstrap/jenkins/`)까지 연결해서 본다.

## 4.1 Declarative Pipeline 문법: `pipeline`, `agent`, `stages`, `when`, `post`

Jenkins 파이프라인은 **Jenkinsfile** 이라는 파일에 코드로 적는다. 문법은 두 가지가 있는데, 이 프로젝트는
정해진 블록 구조를 따르는 **Declarative(선언형) Pipeline** 을 쓴다. 뼈대만 추리면 이렇다.

`ci/Jenkinsfile:21-41` (요약)
```groovy
pipeline {
  agent { label 'build' }          // 어디서 돌릴지
  options { ... }                  // 파이프라인 전체 옵션
  environment { ... }              // 모든 단계에서 쓰는 환경변수
  stages {                         // 실제 작업 단계들
    stage('준비') { steps { ... } }
    ...
  }
  post { ... }                     // 끝난 뒤 처리
}
```

| 블록 | 뜻 | 이 프로젝트에서 |
|---|---|---|
| `agent` | 빌드를 실행할 장소 | 라벨 `build` 인 쿠버네티스 Pod (4.2) |
| `options` | 전체 동작 옵션 | 타임스탬프, 30분 타임아웃, 빌드 20개 보관, 동시 빌드 금지 |
| `environment` | 환경변수 | 레지스트리 주소, 이미지 이름, gitops 리포지토리 주소, Argo CD 주소 |
| `stages` / `stage` / `steps` | 단계 묶음 / 단계 하나 / 단계 안의 명령 | 6개 단계 (4.4) |
| `when` | 단계를 실행할 조건 | 배포 대상 브랜치일 때만 이미지 빌드 등 |
| `post` | 결과별 후처리 | 성공 시 URL 출력, 항상 워크스페이스 정리 |

`options` 부분을 보자.

`ci/Jenkinsfile:24-29`
```groovy
  options {
    timestamps()
    timeout(time: 30, unit: 'MINUTES')
    buildDiscarder(logRotator(numToKeepStr: '20'))
    disableConcurrentBuilds()
  }
```

`disableConcurrentBuilds()` 는 같은 브랜치의 빌드가 겹치지 않게 한다. 두 빌드가 동시에 gitops 리포지토리에
push 하면 한쪽이 거부되기 때문이다(README "운영 관련 메모").

`when` 은 `expression { ... }` 안의 값이 참일 때만 단계를 실행한다. 예를 들어 배포 대상이 정해진 경우에만
이미지를 빌드한다.

`ci/Jenkinsfile:84-85`
```groovy
    stage('이미지 빌드·푸시') {
      when { expression { env.DEPLOY_ENV } }
```

Groovy 에서 빈 문자열은 거짓이므로, `DEPLOY_ENV` 가 `''` 인 브랜치(develop, main 이외)는 이 단계를 건너뛴다.

`post` 는 결과에 따라 나뉜다. `success` 는 성공했을 때만, `cleanup` 은 항상 마지막에 실행된다.

`ci/Jenkinsfile:196-207` (요약)
```groovy
  post {
    success { script { ... echo "배포 완료: ${env.DEPLOY_ENV} → ${url} ..." } }
    cleanup { cleanWs() }       // 워크스페이스 삭제
  }
```

### 학습 노트: Jenkinsfile 은 "레시피 카드" (2026-09-27)

3장의 Argo CD 는 gitops 커밋 **이후**, 4장의 Jenkins 는 그 커밋을 **만들기까지**(테스트 → 이미지 → gitops 커밋)를 맡는다.

| 레시피 카드 | 블록 | 이 프로젝트 |
|---|---|---|
| 어느 주방에서 | `agent` | `label 'build'` Pod |
| 주방 규칙 | `options` | 30분 타임아웃, 동시 빌드 금지 |
| 공용 재료 이름표 | `environment` | 레지스트리·이미지·gitops·Argo CD 주소 |
| 조리 순서 | `stages` / `stage` / `steps` | 6단계 |
| "~일 때만" | `when` | 배포 대상 브랜치일 때만 |
| 설거지 | `post` | `success` 는 성공 시만, `cleanup` 은 항상 |

**브랜치별로 도는 단계** (`준비` 가 `DEPLOY_ENV` 를 정하고, 나머지는 `when` 으로 확인)

| 단계 | feature/xxx | develop | main |
|---|---|---|---|
| 준비, 테스트·빌드 | ✅ | ✅ | ✅ |
| 이미지 빌드·푸시 | ⏭ | ✅ | ✅ |
| prod 승인 | ⏭ | ⏭ | ✅ |
| gitops 태그 갱신, 배포 대기 | ⏭ | ✅ | ✅ |

- `if`, `def` 같은 일반 Groovy 코드는 `script { }` 안에서만 쓸 수 있다(`준비` 단계).
- 한 단계가 실패하면 뒤 단계는 건너뛰고 `post` 로 간다.

**확인 문제 (2026-09-27, 객관식)**

1. `feature/login` push → 도는 단계는? 정답 **준비, 테스트·빌드**. (답: 6단계 전부 ✗)
   - feature 브랜치는 `DEPLOY_ENV = ''`(`ci/Jenkinsfile:63`). Groovy 에서 빈 문자열은 거짓이라 `when { expression { env.DEPLOY_ENV } }` 인 단계가 모두 건너뛰어진다.
   - 의도: 아무 브랜치나 dev/prod 에 배포되면 안 되니, feature 브랜치는 "코드가 깨지지 않았나" 만 확인한다(= 순수 CI).
2. develop 에서 `npm test` 실패 → 정답 **뒤 단계 건너뜀, `success` 안 돎, `cleanup` 은 돎**. ✅
   - 그래서 테스트가 깨진 코드는 이미지도 안 만들어지고 gitops 도 안 바뀐다 → 배포 안 됨.
3. `disableConcurrentBuilds()` 가 없고 develop 빌드 두 개가 동시에 돌면? 정답 **gitops push 가 한쪽에서 거부될 수 있다**. (답: 이미지 태그가 같아져 덮어씀 ✗)
   - 태그는 `<브랜치 slug>-<커밋 SHA 8자리>` 라서 **커밋이 다르면 태그도 다르다**(develop-1a2b3c4d vs develop-5e6f7a8b). 덮어쓸 일이 없다.
   - 진짜 문제: 두 빌드가 gitops 를 같은 시점 기준으로 clone → 각자 커밋 → 먼저 push 한 쪽이 성공, 뒤쪽은 원격이 앞서 있어서 `rejected (non-fast-forward)` 로 실패한다(`git push origin HEAD:main`, `:148`).
   - 더 나쁜 경우: 오래된 커밋 A 의 빌드가 느려서, 새 커밋 B 의 빌드가 gitops 에 push 한 **뒤에** clone 하면 A 의 push 는 거부되지 않고 성공한다 → **오래된 이미지가 마지막에 배포**된다.
   - 동시 빌드 금지를 켜면 같은 브랜치의 빌드는 줄을 서서 차례로 돈다. 그래서 두 문제가 모두 없다.

## 4.2 쿠버네티스 에이전트 Pod 템플릿 (컨테이너 4개: node / kaniko / tools / argocd)

Jenkins 는 **컨트롤러**(화면, 잡 관리, 스케줄링)와 **에이전트**(실제 빌드 실행)로 나뉜다. 이 프로젝트의
컨트롤러는 직접 빌드하지 않는다.

`bootstrap/jenkins/casc.yaml:57-59`
```yaml
      # 컨트롤러에서는 빌드를 돌리지 않고 kubernetes 에이전트만 사용한다
      numExecutors: 0
      mode: EXCLUSIVE
```

대신 빌드가 시작될 때마다 kubernetes 플러그인이 `jenkins` 네임스페이스에 **에이전트 Pod** 를 새로 띄우고,
빌드가 끝나면 지운다. 그 Pod 의 모양을 정한 것이 **Pod 템플릿** `build` 다. Jenkinsfile 의
`agent { label 'build' }` 가 이 템플릿을 가리킨다.

`bootstrap/jenkins/casc.yaml:81-101` (요약)
```yaml
              - name: "build"
                label: "build"
                serviceAccount: "jenkins-agent"
                containers:
                  - name: "kaniko"
                    image: "nexus-docker-group.example.com/kaniko-project/executor:v1.23.2-debug"
                    command: "/busybox/cat"
                    ttyEnabled: true
                  - name: "tools"      # kubectl / kustomize / git / helm
                  - name: "node"       # npm ci / npm test / npm run build
                  - name: "python"     # python-api 의 pytest
                  - name: "argocd"     # argocd CLI
```

템플릿에는 컨테이너가 5개 있다. react-app 의 `ci/Jenkinsfile` 은 그중 4개(node / kaniko / tools / argocd)를
쓰고, python-api 의 Jenkinsfile 은 node 대신 python 을 쓴다. 여기에 Jenkins 가 자동으로 `jnlp` 컨테이너를
붙인다. `jnlp` 는 컨트롤러와 통신하는 연락 담당이다(README 5.4).

알아 둘 점 세 가지:

- **왜 컨테이너를 여러 개 두나?** 도구마다 필요한 이미지가 다르기 때문이다. 하나의 거대한 이미지를
  만드는 대신, 공식 이미지를 그대로 쓰고 단계마다 `container('이름') { ... }` 로 골라 들어간다.
- **워크스페이스를 공유한다.** 같은 Pod 안의 컨테이너들은 같은 작업 디렉터리를 본다. 그래서 node 가
  체크아웃한 소스를 kaniko 가 그대로 이미지로 만들 수 있다.
- **`command: "cat"` + `ttyEnabled: true`** 는 컨테이너가 할 일 없이 계속 살아 있게 하는 관용구다.
  Jenkins 가 필요할 때 그 안에서 명령을 실행한다.

`argocd` 컨테이너만 root 로 실행하도록 따로 설정돼 있다. 이 이미지의 기본 사용자(uid 999)는 `jnlp`(uid 1000)가
만든 워크스페이스에 쓰지 못해서, sh 스텝이 5분 뒤 `process apparently never started` 로 실패했기 때문이다.

`bootstrap/jenkins/casc.yaml:113-118`
```yaml
                    # 이 이미지만 비특권 사용자(uid 999)로 돈다. 워크스페이스는 jnlp(uid 1000)가
                    # 만들기 때문에 999 는 `@tmp/durable-*` 에 결과 파일을 쓰지 못하고,
                    # sh 스텝이 "process apparently never started" 로 5분 뒤 실패한다
                    ...
                    runAsUser: "0"
                    runAsGroup: "0"
```

컨트롤러가 Pod 를 만들고 지울 수 있는 권한은 `jenkins` 네임스페이스 안으로 한정된 Role 로 준다.

`bootstrap/jenkins/rbac.yaml:16-27` (요약)
```yaml
kind: Role
metadata:
  name: jenkins-agent-manager
  namespace: jenkins
rules:
  - apiGroups: [""]
    resources: ["pods"]
    verbs: ["create", "delete", "get", "list", "patch", "update", "watch"]
  - apiGroups: [""]
    resources: ["pods/exec"]
    ...
```

반면 에이전트 Pod 가 쓰는 `jenkins-agent` ServiceAccount 에는 아무 권한도 주지 않는다(`rbac.yaml:9`).
에이전트는 클러스터를 직접 건드리지 않고, 배포는 Git 커밋과 Argo CD API 로만 하기 때문이다.

### 학습 노트: 빌드마다 새로 세우는 "푸드트럭" (2026-09-27)

- 컨트롤러 = 본사 사무실(주문 접수·일정·화면, `numExecutors: 0` 이라 직접 빌드 안 함).
  에이전트 = 주문마다 새로 세우는 **푸드트럭**: 빌드마다 `jenkins` 네임스페이스에 Pod 를 띄우고, 끝나면 지운다.
- 트럭 설계도 = Pod 템플릿 `build`(`casc.yaml:81-118`), 조리대 = 컨테이너.
  `container('node') { sh ... }` = "node 조리대에서 이 명령 실행". 실제로는 **`kubectl exec` 와 같은 동작** → 그래서 컨트롤러 Role 에 `pods/exec` 가 있다.
- `command: "cat"` + `ttyEnabled: true`: 컨테이너는 메인 프로세스가 끝나면 종료된다. `cat` 은 입력을 기다리며 멈춰 있어 컨테이너가 계속 살아 있다.
- 같은 Pod 의 컨테이너는 워크스페이스를 공유한다 → node 가 체크아웃한 소스를 kaniko 가 그대로 빌드.

| ServiceAccount | 권한 | 이유 |
|---|---|---|
| `jenkins` (컨트롤러) | `jenkins` 네임스페이스에서 pods 생성·삭제, pods/exec, secrets 읽기 | 트럭을 세우고 치우고 명령을 실행 |
| `jenkins-agent` (에이전트 Pod) | 없음 | 배포는 Git 커밋 + Argo CD 로만 (GitOps) |

**확인 문제 (2026-09-27, 객관식)**

1. 빌드가 끝난 에이전트 Pod 는? 정답 **삭제된다, 다음 빌드 때 새로 뜬다**. ✅
2. kaniko 가 node 의 소스를 쓸 수 있는 이유? 정답 **같은 Pod 라 워크스페이스 공유**. ✅
3. 에이전트의 tools 컨테이너에서 `kubectl delete deployment dev-react-app -n react-app-dev` → 정답 **Forbidden 으로 실패**. (답: 모름)
   - kubectl 이 있다는 것 = 도구가 있다는 것뿐. 무엇을 할 수 있는지는 **누구로 요청하느냐**(RBAC)가 정한다.
   - kubeconfig 가 없는 Pod 안의 kubectl 은 자동으로 마운트된 ServiceAccount 토큰(`/var/run/secrets/kubernetes.io/serviceaccount/token`)을 쓴다 → 요청자는 `system:serviceaccount:jenkins:jenkins-agent`.
   - 이 계정에 묶인 RoleBinding 이 하나도 없다 → API 서버가 거절:
     `User "system:serviceaccount:jenkins:jenkins-agent" cannot delete resource "deployments" ... in the namespace "react-app-dev"`
   - 권한은 "없으면 거절"이 기본(허용 목록 방식). 따로 주지 않았으면 아무것도 못 한다.
   - "Argo CD 가 막는다" ✗: Argo CD 는 API 요청을 가로채는 문지기가 아니다. 권한 있는 누가 지웠다면 dev 는 selfHeal 이 **다시 만들 뿐**(3.2).
   - 1장 복습 연결: Role 은 네임스페이스 한정, 계정에 RoleBinding 으로 묶어야 효력이 있다.

## 4.3 자격증명 주입: `withCredentials`

파이프라인은 비밀 값 세 가지가 필요하다.

| 자격증명 ID | 종류 | 용도 |
|---|---|---|
| `nexus-registry` | 사용자명 + 비밀번호 | kaniko 가 Nexus 에 이미지 push |
| `gitops-repo` | 사용자명 + 비밀번호(토큰) | 앱 리포지토리 체크아웃 + gitops 리포지토리 push |
| `argocd-auth-token` | 문자열(Secret text) | argocd CLI 인증 |

Jenkinsfile 에는 값이 아니라 **ID** 만 적는다. `withCredentials` 블록 안에서만 값이 환경변수로 풀리고,
콘솔 로그에 찍히면 Jenkins 가 `****` 로 가린다.

`ci/Jenkinsfile:88-95`
```groovy
          withCredentials([usernamePassword(credentialsId: 'nexus-registry',
                                            usernameVariable: 'REG_USER',
                                            passwordVariable: 'REG_PASS')]) {
            sh '''
              ...
              AUTH=$(printf '%s:%s' "$REG_USER" "$REG_PASS" | base64 | tr -d '\\n')
              printf '{"auths":{"%s":{"auth":"%s"}}}' "$REGISTRY" "$AUTH" > /kaniko/.docker/config.json
```

값 자체는 어디서 오는가? 경로는 이렇다.

```
kubectl create secret (jenkins-secrets, README 5.2)
   → Jenkins 컨테이너의 환경변수 (jenkins.yaml 의 envFrom)
   → JCasC 가 ${GITOPS_TOKEN} 등을 치환해 자격증명 생성 (casc.yaml credentials:)
   → Jenkinsfile 의 withCredentials 가 ID 로 꺼내 씀
```

`bootstrap/jenkins/jenkins.yaml:117-120`
```yaml
          envFrom:
            # JCasC 안의 ${JENKINS_ADMIN_PASSWORD} 등이 이 값으로 치환된다
            - secretRef:
                name: jenkins-secrets
```

`bootstrap/jenkins/casc.yaml:128-133`
```yaml
              - usernamePassword:
                  scope: GLOBAL
                  id: "gitops-repo"
                  description: "gitops 매니페스트 리포지토리 push 용"
                  username: "${GITOPS_USER}"
                  password: "${GITOPS_TOKEN}"
```

이 리포지토리는 공개이므로 비밀 값을 매니페스트 파일에 적지 않고 `kubectl` 로만 넣는다(`casc.yaml:8-10`).
`envFrom` 은 파드가 뜰 때만 읽으므로 값을 바꾸면 컨트롤러를 재시작해야 한다(README 5.2).

### 학습 노트: 호텔 프런트의 열쇠 보관함 (2026-09-27)

- Jenkinsfile 에는 열쇠가 아니라 **보관함 번호(ID)** 만 적는다. `withCredentials { }` 는 "블록 안에서만 빌려 쓰고 반납".
- 전달 경로: ① `kubectl create secret jenkins-secrets`(README 5.2) → ② 컨트롤러 환경변수(`envFrom`) → ③ JCasC 가 `${...}` 치환해 자격증명 생성 → ④ `withCredentials` 가 ID 로 꺼냄.
  리포지토리가 공개라서 Git 에 들어가는 파일에는 `${이름}` 빈칸만 둔다.
- `sh '''...'''`(작은따옴표): Groovy 가 `$VAR` 를 건드리지 않고 **셸이** 환경변수에서 꺼낸다 → 안전.
  `"""..."""` 이면 Groovy 가 값을 명령 문자열에 먼저 박아 넣는다 → Jenkins 가 경고.

**확인 문제 (2026-09-27, 객관식) — 3/3 정답**

1. `GITOPS_TOKEN` 실제 값은? **쿠버네티스 Secret `jenkins-secrets`**. ✅
2. `sh 'echo $REG_PASS'` → **`****`** 로 가려진다. ✅
   - 덧붙임: 가리기는 **값과 글자 그대로 똑같을 때만** 동작한다. 변형된 값은 못 가린다.
     예: `AUTH`(= `user:pass` 의 base64, `ci/Jenkinsfile:94`)를 echo 하면 그대로 찍히고, base64 는 누구나 되돌릴 수 있다.
     그래서 Jenkinsfile 은 AUTH 를 출력하지 않고 바로 파일(`/kaniko/.docker/config.json`)로 쓴다.
3. Secret 을 바꿨는데 여전히 인증 실패 → **`envFrom` 은 Pod 가 뜰 때만 읽는다, 컨트롤러 재시작**. ✅
   - 명령: `kubectl -n jenkins rollout restart sts/jenkins`(README.md:1325). Jenkins 는 StatefulSet 이다.
   - 재시작하면 새 환경변수로 JCasC 가 다시 적용되어 자격증명도 새 값으로 바뀐다.

## 4.4 단계별 해설: `ci/Jenkinsfile`

전체 흐름(README 7장):

```
준비 → 테스트·빌드(node) → 이미지 빌드·푸시(kaniko → Nexus)
  → [main 만] prod 승인(input) → gitops 태그 갱신(tools) → 배포 대기(argocd: Synced + Healthy)
```

### ① 준비

브랜치 이름과 커밋 SHA 로 **이미지 태그**를 만들고, 어느 환경에 배포할지 정한다.

`ci/Jenkinsfile:52-64`
```groovy
          def slug = env.BRANCH.toLowerCase().replaceAll('[^a-z0-9]+', '-')
          slug = slug.replaceAll('^-+', '').replaceAll('-+$', '')
          def sha = (env.GIT_COMMIT ?: '').take(8)
          env.IMAGE_TAG = slug + '-' + sha

          if (env.BRANCH == 'develop') {
            env.DEPLOY_ENV = 'dev'
          } else if (env.BRANCH == 'main') {
            env.DEPLOY_ENV = 'prod'
          } else {
            env.DEPLOY_ENV = ''
          }
```

`feature/Login` 브랜치라면 slug 는 `feature-login` 이 된다. `develop` 의 커밋 `a1b2c3d4...` 는 태그
`develop-a1b2c3d4` 가 된다. `script { }` 블록은 선언형 문법 안에서 Groovy 코드를 자유롭게 쓰는 탈출구다.

### ② 테스트·빌드

`ci/Jenkinsfile:72-79`
```groovy
        container('node') {
          sh '''
            set -eu
            npm ci --no-audit --no-fund
            npm test
            # 이미지 빌드는 Dockerfile 이 다시 하지만, 빌드 오류를 이 단계에서 먼저 잡는다
            npm run build
          '''
        }
```

이 단계에는 `when` 이 없다. **모든 브랜치**가 테스트는 거친다. `set -eu` 는 명령 하나라도 실패하면 즉시
멈추게 하는 셸 옵션이다. 테스트가 실패하면 이후 단계는 실행되지 않는다.

### ③ 이미지 빌드·푸시

kaniko 로 이미지를 만들고 Nexus 에 태그 두 개로 push 한다(`IMAGE_TAG` 와 전체 커밋 SHA). 자세한 내용은 5장.

`ci/Jenkinsfile:101-105`
```groovy
              /kaniko/executor --context "$WORKSPACE" --dockerfile Dockerfile \\
                --build-arg "APP_VERSION=${IMAGE_TAG}" \\
                --destination "${IMAGE}:${IMAGE_TAG}" \\
                --destination "${IMAGE}:${GIT_COMMIT}" \\
                --insecure --skip-tls-verify --insecure-pull
```

줄 끝이 `\\` 인 이유: Groovy 의 `'''` 문자열은 줄 끝 `\` 하나를 줄 연결로 먹어 버린다. 셸까지 `\` 를
전달하려면 두 개로 적는다(`ci/Jenkinsfile:99-100`).

### ④ prod 승인

`ci/Jenkinsfile:112-119`
```groovy
    stage('prod 승인') {
      when { expression { env.DEPLOY_ENV == 'prod' } }
      steps {
        timeout(time: 60, unit: 'MINUTES') {
          input message: "prod 에 ${env.IMAGE_TAG} 를 배포할까요?", ok: '배포'
        }
      }
    }
```

`input` 은 사람이 Jenkins 화면에서 버튼을 누를 때까지 빌드를 멈춘다. 60분 안에 누르지 않으면 빌드가
중단된다. 이것이 "운영 배포는 사람이 승인한다"는 규칙을 코드로 표현한 부분이다.

### ⑤ gitops 태그 갱신 — CI 와 CD 의 경계

`ci/Jenkinsfile:134-148` (요약)
```sh
git clone --depth 1 "http://${GITOPS_USER}:${GITOPS_TOKEN}@${GITOPS_REPO}" gitops
cd gitops/manifests/react-app/overlays/${DEPLOY_ENV}
kustomize edit set image "nexus-docker.example.com/my-group/react-app=${IMAGE}:${IMAGE_TAG}"
cd "$WORKSPACE/gitops"
git add -A
if git diff --cached --quiet; then
  echo "변경 없음 - 커밋 생략"
  exit 0
fi
git commit -m "chore(${DEPLOY_ENV}): react-app -> ${IMAGE_TAG}"
git push origin HEAD:main
```

Jenkins 는 클러스터에 `kubectl apply` 를 하지 않는다. gitops 리포지토리의 overlay 에 새 태그를 **적고
커밋할 뿐**이다. 실제 배포는 Argo CD 가 이 커밋을 보고 한다. 같은 커밋을 다시 빌드하면 태그가 같아서
`변경 없음 - 커밋 생략` 으로 끝난다(README 7.5).

### ⑥ 배포 대기

`ci/Jenkinsfile:163-179` (요약)
```sh
OPTS="--grpc-web --plaintext --server $ARGOCD_SERVER --auth-token $ARGOCD_AUTH_TOKEN"
APP="react-app-${DEPLOY_ENV}"
argocd app get "$APP" --refresh $OPTS > /dev/null
if [ "$DEPLOY_ENV" = "prod" ]; then
  argocd app sync "$APP" $OPTS
fi
if ! argocd app wait "$APP" --sync --health --timeout 600 $OPTS; then
  ...   # 10초 간격으로 12번(2분) 더 확인
fi
```

- `--refresh`: Argo CD 에게 방금 커밋을 바로 보라고 알린다.
- dev 는 Argo CD 가 **자동 동기화**하므로 CLI 가 sync 를 또 걸지 않는다. 걸면 `another operation is
  already in progress` 로 실패할 수 있다. prod 만 **수동 동기화**이므로 Jenkins 가 `app sync` 를 건다.
- `app wait --sync --health`: Git 과 클러스터가 일치(Synced)하고 파드가 정상(Healthy)일 때까지 기다린다.
  prod 를 처음 만들 때 HPA 가 지표를 받기 전 잠깐 Degraded 로 보이는 경우를 위해 2분 더 지켜본다.

이 단계 덕분에 **Jenkins 빌드의 성공 = 실제 배포 완료**가 된다.

### 학습 노트 (전반부): 준비 → 테스트·빌드 → 이미지 빌드·푸시 (2026-09-27)

예시: develop 에 커밋 `1a2b3c4d5e…` push.

- **0단계(숨은 단계)**: Declarative Pipeline 은 에이전트가 뜨면 자동으로 앱 리포지토리를 체크아웃하고 `GIT_COMMIT` 등을 채운다. Jenkinsfile 에 `checkout` 줄이 없는 이유.
- **준비**: `develop` → slug `develop`, sha `1a2b3c4d` → `IMAGE_TAG=develop-1a2b3c4d`, `DEPLOY_ENV=dev`.
  slug 가 필요한 이유: 이미지 태그에는 `/`, 대문자를 쓸 수 없다.
- **테스트·빌드**(node): `npm ci`(lock 파일 그대로 설치, CI 용) → `npm test`(실패 시 중단) → `npm run build`(빌드 오류 조기 발견용).
  여기서 만든 `dist/` 는 `.dockerignore` 로 제외되고, Dockerfile 이 이미지 안에서 다시 빌드한다.
  `set -eu`: 명령 실패 시 즉시 중단 + 정의 안 된 변수는 오류.
- **이미지 빌드·푸시**(kaniko): ① `nexus-registry` 로 `/kaniko/.docker/config.json` 생성 ② 한 번 빌드해 태그 2개로 push.
  `--build-arg APP_VERSION` 은 화면의 version 표시로 쓰인다(`samples/react-app/Dockerfile:12-13`).
- Groovy `'''` 는 줄 끝 `\` 하나를 먹는다 → 셸에 넘기려면 `\\`.

**확인 문제 (2026-09-27, 객관식) — 2/3**

1. `feature/Login_Page` + `abcdef1234567…` → **`feature-login-page-abcdef12`**. ✅ (소문자화, `/`·`_` → `-`, SHA 8자리)
   - feature 브랜치도 태그 **계산**은 한다. 이미지를 만들지 않을 뿐이다.
2. develop 빌드 성공 시 Nexus 에 생기는 태그 수 → **2개**. (답: 1개 ✗)
   - `--destination` 이 두 줄이다(`ci/Jenkinsfile:103-104`): `develop-1a2b3c4d` + 40자 전체 SHA. 같은 이미지에 이름표 두 장.
   - 40자 태그의 쓸모: GitLab 커밋 화면의 SHA 로 이미지를 **바로** 찾을 수 있고, 브랜치 이름과 무관하며, 8자리 잘림에 따른 중복 걱정이 없다.
     이 프로젝트의 매니페스트는 `develop-…` 태그만 쓰고, 40자 태그는 추적용이다.
   - `latest` 는 만들지 않는다. 2장 복습: `latest` 는 "최신 빌드" 를 자동으로 뜻하지 않고, 쓰면 무엇이 배포됐는지 Git 에 남지 않는다.
3. `npm run build` 의 `dist/` → **쓰이지 않는다(.dockerignore 제외, Dockerfile 이 다시 빌드)**. ✅

### 학습 노트 (후반부): prod 승인 → gitops 태그 갱신 → 배포 대기 (2026-09-27)

전반부가 끝나면 이미지는 Nexus(창고)에 있을 뿐, 클러스터는 그대로다. 주문서(gitops)를 고쳐야 배포된다.

- **prod 승인**(main 만): `input` 이 파이프라인을 멈추고 [배포]/[Abort] 버튼을 띄운다. gitops 커밋 **앞**에 있으므로 승인 전엔 prod 가 바뀌지 않는다.
- **gitops 태그 갱신**(tools): `clone --depth 1` → `cd overlays/${DEPLOY_ENV}` → `kustomize edit set image`(2장) → 변경 없으면 커밋 생략 → `commit` → `push origin HEAD:main`.
  **이 push 가 곧 배포 지시**이고, 여기서부터 Argo CD(3장)가 이어받는다.
- **배포 대기**(argocd):

| 순서 | 명령 | 이유 |
|---|---|---|
| ① | `argocd app get --refresh` | 폴링(180초)을 기다리지 않고 바로 Git 을 다시 보게 함 |
| ② | prod 만 `argocd app sync` | dev 는 automated 라 이미 sync 중 → 또 걸면 `another operation is already in progress` |
| ③ | `argocd app wait --sync --health --timeout 600` | Synced + Healthy 까지 최대 10분 |
| ④ | ③ 실패 시 10초 × 12번 재확인 | prod 첫 생성 때 HPA 지표 전 잠깐 Degraded 되는 경우 |

  끝내 Healthy 가 안 되면(ImagePullBackOff 등) Jenkins 빌드가 **실패** → 개발자가 Argo CD 를 안 열어도 배포 실패를 안다.

**발견한 문제: 승인 대기는 실제로 60분이 아니다**
- `ci/Jenkinsfile:115` 주석은 "타임아웃은 전체 파이프라인 것과 별개" 라고 하지만, `options { timeout(30분) }`(`:26`)은 **파이프라인 실행 전체**에 걸린다. 안쪽 `timeout(60)` 이 바깥 30분을 늘리지 못한다.
- 실제 승인 가능 시간 = 30분 − 앞 단계 소요 시간. 넘으면 빌드가 중단(ABORTED)된다.
- 기다리는 동안 에이전트 Pod(컨테이너 5개 + jnlp)가 계속 떠서 자원을 잡고 있다.
- 흔한 해결: 승인 단계에 `agent none` 을 주고 Pod 밖에서 기다리게 하거나, 바깥 timeout 을 단계별로 옮긴다. (아직 수정 안 함)

**확인 문제 (2026-09-27, 객관식) — 3/3 정답**

1. main, 이미지 push 완료, 승인 전 → **prod 그대로(gitops 커밋이 승인 뒤)**. ✅
   - 보기 3 "Argo CD 가 새 이미지를 감지" ✗: Argo CD 는 레지스트리가 아니라 **Git** 을 본다(3.1 복습). 이미지 자동 감지는 8장 Image Updater 의 일.
2. 같은 커밋 Replay → **"변경 없음 - 커밋 생략" 후 성공**. ✅
   - `exit 0` 은 그 `sh` 스크립트만 끝낸다. 다음 `배포 대기` 단계는 그대로 돌고, 이미 Synced + Healthy 라 곧바로 통과한다.
3. dev 에서 sync 를 안 부르는 이유 → **automated 로 이미 진행 중이라 충돌**. ✅

## 4.5 JCasC (Configuration as Code): `bootstrap/jenkins/casc.yaml`

보통 Jenkins 는 처음 켜면 설치 마법사가 뜨고, 관리자 계정·플러그인·자격증명을 화면에서 클릭으로 설정한다.
이렇게 하면 설정이 어디에도 기록되지 않아 다시 만들기 어렵다. **JCasC** 는 그 설정을 YAML 파일로 적어 두고
Jenkins 가 기동할 때 읽게 하는 플러그인이다.

이 프로젝트에서는 두 ConfigMap 으로 나뉜다.

| ConfigMap | 내용 | 마운트 위치 |
|---|---|---|
| `jenkins-plugins` | `plugins.txt` (설치할 플러그인 목록) | init 컨테이너가 `/plugins` 로 읽음 |
| `jenkins-casc` | `jenkins.yaml` (계정, 권한, kubernetes 클라우드, Pod 템플릿, 자격증명) | `/var/jenkins_home/casc.d` |

`bootstrap/jenkins/jenkins.yaml:121-127`
```yaml
          env:
            - name: CASC_JENKINS_CONFIG
              value: /var/jenkins_home/casc.d
            # 설치 마법사를 건너뛰고 JCasC 설정으로 기동한다
            - name: JAVA_OPTS
              value: >-
                -Djenkins.install.runSetupWizard=false
```

플러그인은 컨트롤러보다 먼저 뜨는 init 컨테이너 `install-plugins` 가 내려받는다(`jenkins.yaml:91-101`).
컨트롤러와 init 컨테이너는 **반드시 같은 Jenkins 이미지 태그**를 써야 한다. 플러그인 호환성이 Jenkins
버전에 묶이기 때문이다(`jenkins.yaml:92`).

설정을 바꾼 뒤의 반영 방법(README 5.3):

```bash
kubectl kustomize bootstrap/jenkins | kubectl apply -f -
# casc.yaml 의 jenkins.yaml 부분을 바꿨다면 → 1분쯤 뒤
#   Manage Jenkins > Configuration as Code > Reload existing configuration
# plugins.txt 를 바꿨다면 → 재시작
kubectl -n jenkins rollout restart sts/jenkins
```

### 학습 노트: 프랜차이즈 매장 오픈 매뉴얼 (2026-09-27)

손으로 Jenkins 를 설정하는 건 사장님이 매장을 직접 꾸미는 것, JCasC 는 **본사 매뉴얼**대로 차리는 것이다.
매장(파드)이 무너져도 매뉴얼만 있으면 똑같이 다시 차린다.

설정 재료는 세 군데에서 오고, **언제 읽히는지**가 서로 다르다.

| 재료 | 비유 | 읽히는 때 | 바꾼 뒤 |
|---|---|---|---|
| `jenkins-plugins` (`plugins.txt`) | 주방 설비 | init 컨테이너 `install-plugins` 가 **파드 시작 때만** 내려받음 (`jenkins.yaml:91-106`) | 재시작 |
| `jenkins-casc` (`jenkins.yaml`) | 메뉴판·직원 명단 | 기동 때 + **Reload** 누를 때. 볼륨 마운트라 ConfigMap 변경이 약 1분 뒤 파일에 반영 | Reload |
| `jenkins-secrets` Secret | 금고 비밀번호 (매뉴얼엔 안 적음) | `envFrom` → **컨테이너 시작 때 환경변수로 고정** (`jenkins.yaml:117-120`) | 재시작 |

JCasC 가 하는 일은 결국 `${GITOPS_TOKEN}` 같은 자리에 환경변수 값을 끼워 넣어 자격증명을 만드는 것이다
(`casc.yaml:124-144`). 4.3 의 "열쇠 보관함"에 열쇠를 채워 넣는 사람이 JCasC 인 셈이다.
Reload 는 YAML 을 다시 읽지만 환경변수는 컨테이너가 시작할 때 값 그대로라서, **Secret 만 바꾸고 Reload 하면 옛 값이 남는다.**

`casc.yaml` 에서 앞 장과 이어지는 곳:

- `numExecutors: 0` (`casc.yaml:58`) — 컨트롤러(사장)는 요리하지 않는다. 빌드는 전부 쿠버네티스 에이전트 파드에서.
- `templates: - name: "build"`, `label: "build"` (`casc.yaml:81-83`) — `ci/Jenkinsfile:22` 의 `agent { label 'build' }` 가 이 이름표로 찾는다. 4.2 의 "푸드트럭" 설계도가 여기 있다.
- `serviceAccount: "jenkins-agent"` (`casc.yaml:84`) — 4.2 에서 본 RBAC 제한(Forbidden)의 출발점.
- `jobs:` 블록은 주석 (`casc.yaml:153-164`) — 그래서 잡은 아직 UI 에서 만든다 → 4.6.

**GitOps 와 비교:** UI 에서 바꾼 설정 중 YAML 에 적힌 항목은 다음 기동/Reload 때 YAML 값으로 되돌아간다.
Argo CD 의 selfHeal 과 닮았지만 **자동으로 감시하지 않는다** — 누군가 Reload 하거나 재시작해야 되돌아간다.
또 `checksum/casc: manual` (`jenkins.yaml:62`) 은 이름만 체크섬이지 실제 해시가 아니어서, ConfigMap 이 바뀌어도 파드가 자동 재시작되지 않는다.

#### 확인 문제 풀이 (2026-09-27) — 1/3

| 문제 | 답 | 해설 |
|---|---|---|
| Secret 의 `NEXUS_PASSWORD` 만 바꾸고 Reload → push 는? | **옛 비밀번호** (정답) | 환경변수는 컨테이너 시작 때 고정. Reload 는 YAML 을 다시 읽어도 `${NEXUS_PASSWORD}` 에는 옛 값이 들어간다 → `rollout restart sts/jenkins` |
| `plugins.txt` 에 `slack` 추가 + apply + Reload → ? | **설치 안 됨** (모름) | `plugins.txt` 를 읽는 건 Jenkins 가 아니라 init 컨테이너 `install-plugins` 이고, init 컨테이너는 파드가 시작할 때만 돈다. Reload 는 이미 켜진 Jenkins 에 설정만 다시 넣을 뿐, 주방 공사(설치)는 못 한다 |
| UI 에서 node 이미지를 `node:20` 으로 바꾼 뒤 재시작 → ? | **`casc.yaml` 의 `node:22` 로 돌아감** (모름) | JCasC 는 기동할 때마다 YAML 을 적용하고, YAML 에 적힌 항목은 덮어쓴다. UI 수정은 `/var/jenkins_home` 에 저장돼도 다음 적용 때 사라진다. Argo CD 는 Jenkins *내부 설정*을 모르므로 관여하지 않는다(쿠버네티스 매니페스트만 본다) |

정리: "무엇을 바꿨나"보다 **"그 값을 누가, 언제 읽나"** 를 물으면 반영 방법이 나온다.
- init 컨테이너가 읽는다 → 파드 시작 때만 → 재시작
- 환경변수로 들어간다 → 컨테이너 시작 때만 → 재시작
- 마운트된 파일을 Jenkins 가 읽는다 → Reload 로 충분
- UI 에서 바꾼 것 → YAML 에 같은 항목이 있으면 다음 적용 때 사라진다 → 영구히 바꾸려면 `casc.yaml` 을 고친다

## 4.6 Multibranch Pipeline 잡과 브랜치 규칙 (develop → dev, main → prod)

**Multibranch Pipeline** 은 리포지토리의 브랜치를 스캔해서 `Jenkinsfile` 이 있는 브랜치마다 잡을
자동으로 만드는 잡 종류다. 이 덕분에 `env.BRANCH_NAME` 이 채워지고, 하나의 Jenkinsfile 이 브랜치에 따라
다르게 동작한다.

| 브랜치 | 이미지 태그 | 배포 | Argo CD |
|---|---|---|---|
| `develop` | `develop-<sha 8자리>` | dev (`overlays/dev`) | 자동 동기화 — Jenkins 는 기다리기만 한다 |
| `main` | `main-<sha 8자리>` | prod (`overlays/prod`), **승인 후** | 수동 동기화 — Jenkins 가 `argocd app sync` 를 건다 |
| 그 외 | — | 테스트·빌드까지만 | — |

(README 7장 표)

등록은 Jenkins UI 에서 한다(README 7.3).

- **새로운 Item** > 이름 `react-app` > **Multibranch Pipeline**
- Branch Sources > Git > `http://gitlab.gitlab.svc.cluster.local/my-group/react-app.git`, 자격증명 `gitops-repo`

주의할 점: **push 해도 빌드가 자동으로 걸리지 않는다.** 잡 화면에서 **Scan Multibranch Pipeline Now** 를
누르거나, 잡 설정에서 주기적 스캔을 켠다(README 7.3). 잡을 코드로 관리하고 싶으면 `casc.yaml` 끝의 `jobs:`
블록(job-dsl, 현재 주석 처리)을 쓴다(`casc.yaml:151-164`).

두 번째 앱인 python-api 의 Jenkinsfile 은 흐름이 같고, 앱 이름을 변수 하나로 뺐다.

`samples/python-api/Jenkinsfile:33-36`
```groovy
    APP_NAME      = 'python-api'
    ...
    IMAGE         = "${REGISTRY}/my-group/${APP_NAME}"
```

이미지 이름, overlay 경로, Argo CD 앱 이름이 모두 `APP_NAME` 에서 파생된다. 앱을 더 늘릴 때 이 방식이 편하다.

### 학습 노트: 아파트 우편함과 순찰하는 관리인 (2026-09-27)

- **비유**: Multibranch 잡 = 아파트 건물. 브랜치 = 세대. `Jenkinsfile` 이 있는 세대에만 우편함(하위 잡)이
  생긴다. 우편함을 만들고 새 우편물(새 커밋)을 확인하는 건 **순찰하는 관리인 = 스캔**이다.
- **잡 모양**: `react-app` 은 폴더처럼 보이고 그 아래 `react-app/develop`, `react-app/main`,
  `react-app/feature%2Fx` 처럼 브랜치별 하위 잡이 생긴다. 빌드 번호·로그·승인 대기도 브랜치마다 따로다.
- **브랜치 규칙은 Jenkins 설정이 아니라 Jenkinsfile 안에 있다.** Multibranch 가 `BRANCH_NAME` 을 채워 주고
  (`ci/Jenkinsfile:47`), 준비 단계의 `if` 문이 `DEPLOY_ENV` 를 정한다(`ci/Jenkinsfile:58-64`).
  prod 로 가는 브랜치를 바꾸려면 이 `if` 문을 고친다.

| 스캔이 발견한 것 | 결과 |
|---|---|
| 새 브랜치 (Jenkinsfile 있음) | 하위 잡 생성 + 첫 빌드 |
| 기존 브랜치에 새 커밋 | 그 브랜치만 빌드 |
| 변화 없음 | 아무 빌드도 안 걸림 |
| 브랜치가 사라짐 | 하위 잡 정리 (orphaned item strategy) |
| Jenkinsfile 없는 브랜치 | 무시 — 잡이 안 생김 |

- **트리거는 스캔뿐이다.** 이 프로젝트는 Branch Source 로 plain `Git` 을 쓰고 GitLab → Jenkins webhook 이 없다
  (`gitlab-branch-source` 플러그인은 `casc.yaml:39` 에 설치만 돼 있다). 그래서 push 후 **Scan Multibranch
  Pipeline Now** 를 누르거나 주기 스캔을 켠다(README 7.3). 3.6 의 webhook 은 gitops 리포 → **Argo CD** 용이지
  Jenkins 용이 아니다 — 헷갈리기 쉽다.
- **자격증명**: 앱 리포지토리 체크아웃에도 `gitops-repo`(ci-bot, Group access token)를 쓴다. 토큰이 그룹 단위라
  `my-group` 아래 두 리포지토리를 모두 읽을 수 있기 때문이다.
- **코드로 관리**: `casc.yaml:151-164` 의 job-dsl `multibranchPipelineJob` 블록(주석)을 풀면 UI 등록 없이 잡이 생긴다.

#### 확인 문제 풀이 (2026-09-27) — 1/3

1. develop 에 push 하고 아무것도 안 누르면? — 답 **빌드 안 걸림, 스캔 필요**. (자동 빌드된다고 오답)
   Jenkins 는 push 를 모른다. GitLab → Jenkins webhook 이 없고 주기 스캔도 기본은 꺼져 있다.
   관리인이 순찰을 안 돌면 우편물이 와도 아무도 모른다.
2. `release/1.0` 새 브랜치 + 스캔 → **하위 잡 생성, 준비·테스트·빌드까지만** (정답). `DEPLOY_ENV` 가 빈 문자열이라
   `when { expression { env.DEPLOY_ENV } }` 단계가 모두 건너뛰어진다(4.1 의 "빈 문자열은 거짓").
3. prod 브랜치를 `main` → `release` 로 바꾸려면? — 답 **`ci/Jenkinsfile` 준비 단계의 `if` 문** (모름).
   Jenkins 설정·JCasC 는 "어느 리포를 스캔하나" 만 알고, "어느 브랜치가 어디로 가나" 는 Jenkinsfile 이 정한다.
   Argo CD 는 브랜치를 모른다 — gitops 리포의 `overlays/prod` 만 본다. 바꾸면 이미지 태그도 `release-<sha8>` 이 된다.

## 정리

| 질문 | 답 |
|---|---|
| 빌드는 어디서 도나? | 빌드마다 새로 뜨는 에이전트 Pod (템플릿 `build`) |
| 비밀 값은 어떻게 들어오나? | K8s Secret → 환경변수 → JCasC 자격증명 → `withCredentials` |
| Jenkins 가 클러스터에 직접 배포하나? | 아니다. gitops 리포지토리에 태그를 커밋할 뿐, 배포는 Argo CD |
| dev 와 prod 의 차이 | prod 는 `input` 승인 + `argocd app sync` 를 Jenkins 가 건다 |
| Jenkins 설정은 어디에? | `bootstrap/jenkins/casc.yaml` (JCasC). 화면 클릭 설정 없음 |

## 확인 문제

1. `feature/add-button` 브랜치에 push 하고 스캔하면 어떤 단계까지 실행되는가?

<details><summary>답</summary>

`준비` 와 `테스트·빌드` 까지만 실행된다. `DEPLOY_ENV` 가 `''` 이므로 `when { expression { env.DEPLOY_ENV } }`
가 붙은 이미지 빌드, gitops 갱신, 배포 대기 단계는 건너뛴다. `prod 승인` 도 `DEPLOY_ENV == 'prod'` 가 아니라서 건너뛴다.
</details>

2. 배포 대기 단계에서 dev 일 때 `argocd app sync` 를 호출하지 않는 이유는?

<details><summary>답</summary>

dev Application 은 자동 동기화(`syncPolicy.automated`)라서 Argo CD 가 이미 동기화를 시작했을 수 있다.
그때 CLI 로 sync 를 또 걸면 `another operation is already in progress` 로 실패할 수 있다.
</details>

3. `jenkins-secrets` 의 `GITOPS_TOKEN` 값을 바꿨는데 여전히 401 이 난다. 무엇을 빠뜨렸나?

<details><summary>답</summary>

컨트롤러 재시작. `envFrom` 은 파드가 뜰 때만 읽히므로 `kubectl -n jenkins rollout restart sts/jenkins` 가 필요하다(README 5.2).
</details>

4. 컨트롤러의 `numExecutors: 0` 은 무슨 뜻이고, 왜 그렇게 하나?

<details><summary>답</summary>

컨트롤러 자신은 빌드를 실행하지 않는다는 뜻이다. 모든 빌드는 kubernetes 플러그인이 띄우는 에이전트 Pod 에서
돈다. 빌드마다 깨끗한 환경이 생기고, 컨트롤러가 빌드 부하로 느려지지 않는다.
</details>

5. 같은 커밋으로 main 빌드를 다시 돌렸다. prod 파드가 교체되는가?

<details><summary>답</summary>

교체되지 않는다. 태그(`main-<sha>`)가 같아서 gitops 태그 갱신 단계가 `변경 없음 - 커밋 생략` 으로 끝나고,
Argo CD 가 바꿀 것이 없다(README 7.5).
</details>
