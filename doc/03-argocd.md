# 3. Argo CD

## 이 장의 목표

Argo CD 는 **Git 에 적힌 상태와 클러스터의 실제 상태를 비교하고, 다르면 Git 쪽으로 맞추는** CD 도구다.
이 장에서는 Argo CD 가 "무엇을, 어디에, 언제" 배포하는지 정하는 리소스(Application, AppProject)와
이 프로젝트가 쓰는 App of Apps 패턴, 변경 감지(webhook / 폴링), 배포 결과 알림을 실제 파일로 읽는다.

먼저 용어 몇 개를 정리한다.

| 용어 | 뜻 |
|---|---|
| **desired state** (원하는 상태) | Git 에 적힌 매니페스트. "이렇게 떠 있어야 한다" |
| **live state** (실제 상태) | 지금 클러스터에 떠 있는 리소스 |
| **Sync** (동기화) | live state 를 desired state 와 같게 만드는 동작 (내부적으로 `kubectl apply` 와 비슷) |
| **Synced / OutOfSync** | 두 상태가 같다 / 다르다 |
| **Healthy / Progressing / Degraded / Missing** | 리소스가 정상 / 뜨는 중 / 문제 있음 / 아직 없음 |
| **CRD** (Custom Resource Definition) | 쿠버네티스에 새 리소스 종류를 추가하는 방법. `Application`, `AppProject` 가 Argo CD 가 추가한 CRD 다 |

## 3.1 Application: source(repo, path) 와 destination(cluster, namespace)

`Application` 은 Argo CD 의 기본 단위다. 한 줄로 요약하면
**"이 Git 리포지토리의 이 경로(source)를, 이 클러스터의 이 네임스페이스(destination)에 배포하라"** 이다.

`apps/react-app.yaml:1`

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: react-app-dev
  namespace: argocd
  annotations:
    repo: react-app          # notifications 템플릿에서 사용
  finalizers:
    - resources-finalizer.argocd.argoproj.io
spec:
  project: dev
  source:
    repoURL: http://gitlab.example.com/my-group/gitops-manifests.git
    targetRevision: main
    path: manifests/react-app/overlays/dev
  destination:
    server: https://kubernetes.default.svc
    namespace: react-app-dev
```

필드별 의미:

| 필드 | 값 | 의미 |
|---|---|---|
| `metadata.namespace` | `argocd` | Application 리소스 **자체**가 사는 곳. 앱이 배포되는 곳이 아니다 |
| `spec.project` | `dev` | 소속 AppProject (3.3). 권한과 허용 범위를 여기서 받는다 |
| `source.repoURL` | gitops 리포지토리 | 앱 리포지토리가 **아니다**. Argo CD 는 매니페스트만 읽는다 |
| `source.targetRevision` | `main` | 읽을 브랜치 |
| `source.path` | `manifests/react-app/overlays/dev` | 리포지토리 안의 경로. `kustomization.yaml` 이 있으면 Argo CD 가 자동으로 kustomize 빌드를 한다 |
| `destination.server` | `https://kubernetes.default.svc` | 배포 대상 클러스터. 이 주소는 "Argo CD 자신이 떠 있는 클러스터" 를 뜻한다 |
| `destination.namespace` | `react-app-dev` | 앱이 실제로 뜨는 네임스페이스 |

`finalizers` 의 `resources-finalizer.argocd.argoproj.io` 는 **Application 을 지우면 그 Application 이 만든
리소스(Deployment 등)도 함께 지우라**는 표시다. 없으면 Application 만 사라지고 앱은 클러스터에 남는다.

`annotations.repo` 는 쿠버네티스가 쓰는 값이 아니라, 3.7 의 알림 템플릿이 GitLab 프로젝트 이름으로 읽는 값이다.

같은 파일의 prod Application 은 `path` 와 `namespace`, `project` 만 다르다.

`apps/react-app.yaml:43`

```yaml
spec:
  project: prod
  source:
    repoURL: http://gitlab.example.com/my-group/gitops-manifests.git
    targetRevision: main
    path: manifests/react-app/overlays/prod
  destination:
    server: https://kubernetes.default.svc
    namespace: react-app-prod
```

즉 **같은 리포지토리, 같은 브랜치에서 경로만 달리해** dev 와 prod 를 나눈다. 환경별 차이는 2장의
Kustomize overlay 가 담당하고, Argo CD 는 "어느 overlay 를 어디에" 만 정한다.

Argo CD 가 이 리포지토리를 읽으려면 자격증명이 필요하다. 읽기만 하므로 GitLab **Deploy token**
(`read_repository`) 이면 충분하다. 예시 파일에 이유가 적혀 있다.

`bootstrap/argocd/configs/repo-gitlab.yaml:7`

```yaml
# Argo CD 는 읽기만 하면 되므로 Deploy token(read_repository) 이면 충분하다.
#   프로젝트 Settings > Repository > Deploy tokens
# 쓰기가 필요한 쪽(CI 의 gitops 커밋, Image Updater write-back)은
# Project/Group access token 을 따로 발급한다.
```

실제 값은 파일에 적지 않고 README 4.5-2 에서 `kubectl create secret` 으로 넣는다.
핵심은 Secret 에 붙는 라벨 `argocd.argoproj.io/secret-type: repository` 다. Argo CD 는 이 라벨이
붙은 Secret 을 리포지토리 자격증명으로 인식한다.

```bash
argocd repo list --grpc-web --refresh hard     # STATUS 가 Successful 이어야 한다
argocd app get react-app-dev --grpc-web        # source, destination, 상태 확인
```

### 학습 노트: Application 은 "구독 신청서" (2026-09-27)

비유: Application 은 **신문 구독 신청서**다. "이 신문사(`repoURL`)의 이 판(`targetRevision`)에서 이 지면(`path`)을,
이 주소(`server`)의 이 집(`namespace`)으로 배달해 주세요." Argo CD 는 배달원으로서 신청서대로 계속 맞춰 준다.

**Argo CD 안에서 일어나는 일**

```
repoURL + targetRevision ── clone ──▶ repo-server ── path 에서 kustomize build (2.6 과 같은 결과) ──▶ desired state
                                                                                                        │ 비교
                                                     destination(server, namespace) 의 live state ◀────┘ → Synced / OutOfSync
```

- `path` 에 `kustomization.yaml` 이 있으면 Argo CD 가 알아서 kustomize 로 빌드한다. 2.6 에서 `kubectl kustomize` 로 본 YAML 이 곧 Argo CD 가 적용하는 것이다.
- `targetRevision` 은 브랜치(`main`), 태그(`v1.0`), 커밋 SHA 모두 된다. 브랜치면 "그 브랜치의 최신 커밋" 을 계속 따라간다.
  태그나 SHA 로 고정하면 새 커밋이 올라와도 따라가지 않는다.
- `destination.server: https://kubernetes.default.svc` — 1.2 에서 본 Service DNS 이름이다. `default` 네임스페이스의 `kubernetes` Service = 클러스터의 API 서버.
  "Argo CD 가 떠 있는 바로 이 클러스터" 라는 뜻이 되는 이유다. 다른 클러스터에 배포하려면 그 클러스터를 등록하고 그 API 주소를 적는다.

**네임스페이스는 누가 정하나** (2장 마무리 Q3 에서 이어짐)

| 상황 | 결과 |
|---|---|
| 매니페스트에 namespace 없음 (base 를 path 로 준 경우) | `destination.namespace` 가 채운다 |
| 매니페스트에 namespace 있음 (overlay 의 `namespace:` 필드) | **매니페스트 값이 이긴다.** `destination.namespace` 는 빈 곳만 채운다 |

- 이 프로젝트는 overlay 의 `namespace` 와 `destination.namespace` 를 **같은 값**으로 맞춰 두었다(`react-app-dev`). 헷갈릴 일을 없앤 것.
- `CreateNamespace=true`(`apps/react-app.yaml:24`) 로 만들어지는 네임스페이스는 `destination.namespace` 쪽이다.
- 어느 쪽이든 AppProject 의 `destinations` 에서 허용한 네임스페이스여야 한다(3.3). 허용 밖이면 sync 가 거부된다.

**상태는 두 축** — Sync 상태(Git 과 같은가)와 Health 상태(잘 돌고 있는가)는 따로다.
2장 마무리 Q3 처럼 `Synced` 인데 `Degraded` 일 수 있다: "Git 대로 적용은 됐는데, Git 에 적힌 것 자체가 잘못됐다."

**확인 문제 (2026-09-27)**

1. dev Application 의 `targetRevision` 을 태그 `v1.0` 으로 바꾸면, Jenkins 가 main 에 push 한 새 커밋이 배포되나?
   → **안 된다.** 이유: 태그는 한 커밋에 고정된 이름표라, Argo CD 는 `v1.0` 이 가리키는 커밋만 본다. main 에 새 커밋이 쌓여도 따라가지 않는다.
   (답안의 "dev repository 에 push 되어야" 는 정정: dev 전용 리포지토리는 없다. gitops 리포지토리 **하나**, main 브랜치 **하나**에서 dev/prod 는 **path** 로만 나뉜다.)
2. OutOfSync + Healthy 의 예
   - 누군가 `kubectl -n react-app-prod set env deploy/prod-react-app APP_ENV=test` → Git 과 env 가 달라 OutOfSync, Pod 는 잘 도니 Healthy.
     prod 는 자동 동기화가 없어 계속 OutOfSync 로 남는다. dev 에서 같은 짓을 하면 `selfHeal` 이 곧 되돌린다(3.2).
   - **정정 (3.2 에서 발견)**: 처음엔 `kubectl scale` 을 예로 들었는데 틀렸다. 이 프로젝트는 모든 Deployment 의 `/spec/replicas` 를
     비교에서 빼므로(`bootstrap/argocd/configs/argocd-cm.yaml:26-28`) replicas 만 바꾸면 **OutOfSync 가 되지 않는다.**
   - 가장 흔한 예: **prod 에 새 태그가 커밋됐지만 아직 sync 전.** 옛 버전이 멀쩡히 돌고 있으니 Healthy, Git 과는 다르니 OutOfSync.
     Jenkins 의 prod 흐름이 정확히 이 상태를 거친다(승인 → 커밋 → `argocd app sync`).
3. Argo CD 가 앱 리포지토리가 아니라 gitops 리포지토리를 읽는 이유
   - 답안의 핵심 "소스가 바뀌었다고 이미지가 정상 생성된 것은 아니다" 는 맞다. Jenkins 는 테스트·이미지 push 가 **성공한 뒤에만** gitops 에 커밋한다.
     그래서 gitops 커밋 = "배포할 준비가 된 버전" 이라는 보증이 된다.
   - 정정: Argo CD 는 **이미지(레지스트리)를 보지 않는다.** gitops 리포지토리만 본다. 이미지 갱신은 `newTag` 를 바꾼 **gitops 커밋**의 모습으로 도착한다.
   - 그 밖의 이유(0장): 배포 이력(어느 환경에 어느 버전)이 코드 이력과 섞이지 않는다 → revert 로 롤백. 권한 분리(Argo CD 는 매니페스트 읽기만).
     CI 가 앱 리포지토리에 커밋하면 그 커밋이 다시 빌드를 부르는 루프가 생긴다.

## 3.2 syncPolicy: automated, prune, selfHeal

`syncPolicy` 는 **Git 과 클러스터가 달라졌을 때 Argo CD 가 스스로 맞출지** 를 정한다.

`apps/react-app.yaml:19`

```yaml
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
      - PruneLast=true
    retry:
      limit: 5
      backoff:
        duration: 10s
        factor: 2
        maxDuration: 3m
```

| 설정 | 의미 | 없으면 |
|---|---|---|
| `automated` | Git 이 바뀌면 자동으로 Sync 한다 | OutOfSync 로 표시만 하고 사람(또는 CI)이 sync 를 눌러야 한다 |
| `prune: true` | Git 에서 **지운** 리소스를 클러스터에서도 지운다 | 파일을 지워도 리소스는 남는다(안전하지만 쓰레기가 쌓인다) |
| `selfHeal: true` | 누가 `kubectl edit` 로 클러스터를 직접 바꾸면 Git 상태로 **되돌린다** | 직접 바꾼 값이 OutOfSync 로 남는다 |
| `CreateNamespace=true` | destination 네임스페이스가 없으면 만든다 | 네임스페이스를 미리 만들어야 한다 |
| `PruneLast=true` | 지우는 작업을 다른 리소스 적용이 끝난 뒤에 한다 | |
| `retry` | 실패하면 10s → 20s → 40s ... 최대 3분 간격으로 5번 다시 시도 | 한 번 실패로 끝 |

`selfHeal` 이 GitOps 의 성격을 가장 잘 보여 준다. **클러스터를 직접 고치는 것이 의미가 없어진다.**
바꾸고 싶으면 Git 에 커밋해야 한다.

### dev 는 자동, prod 는 수동

prod Application 에는 `automated` 가 없다.

`apps/react-app.yaml:52`

```yaml
  syncPolicy:
    # 운영은 수동 승인 배포. 자동화하려면 automated 블록을 추가한다.
    syncOptions:
      - CreateNamespace=true
```

그래서 Jenkins 가 gitops 리포지토리에 prod 태그를 커밋해도 Argo CD 는 OutOfSync 로 표시만 한다.
실제 배포는 Jenkins 의 "배포 대기" 단계가 `argocd app sync` 를 호출할 때 일어난다.

`ci/Jenkinsfile:169`

```bash
              # dev 는 자동 동기화라 CLI 가 sync 를 또 걸면
              # "another operation is already in progress" 로 실패할 수 있다.
              # 수동 동기화 대상(prod)만 sync 한다.
              if [ "$DEPLOY_ENV" = "prod" ]; then
                argocd app sync "$APP" $OPTS
              fi
```

이 때문에 README 4.5 의 "기대 상태" 표에서 처음 등록 직후 prod Application 은 `OutOfSync / Missing` 이
정상이다.

### HPA 와 replicas 충돌 막기

prod 에는 HPA(파드 수를 부하에 따라 자동 조절)가 있다. HPA 가 replicas 를 3 → 5 로 바꾸면
Git 의 `replicas: 3` 과 달라져 OutOfSync 가 되고, selfHeal 이 있다면 다시 3 으로 되돌리려 싸운다.
이를 막으려고 Deployment 의 `/spec/replicas` 는 비교에서 뺀다.

`bootstrap/argocd/configs/argocd-cm.yaml:25`

```yaml
  # 리소스 diff 제외 (HPA 가 관리하는 replicas 등)
  resource.customizations.ignoreDifferences.apps_Deployment: |
    jsonPointers:
      - /spec/replicas
```

### 학습 노트: 세 스위치와 그 함정 (2026-09-27)

비유: Git 은 **방 배치도**, Argo CD 는 **관리인**이다.

| 스위치 | 관리인이 하는 일 | 끄면 |
|---|---|---|
| `automated` | 배치도가 바뀌면 **알아서** 방을 고친다 | 바뀐 걸 알려만 주고(OutOfSync) 누가 "고쳐" 라고 해야 한다 — prod |
| `prune` | 배치도에서 **지워진 가구를 버린다** | 지운 가구가 방에 계속 남는다 |
| `selfHeal` | 누가 몰래 가구를 옮기면 **도로 제자리**에 둔다 | 옮긴 채로 두고 OutOfSync 로 표시만 |

- `prune`, `selfHeal` 은 `automated` 아래에 있는 옵션이다. prod 처럼 `automated` 가 없으면 둘 다 없는 것과 같다.
- 감지 속도: Git 변경은 폴링(180초, `bootstrap/argocd/configs/argocd-cmd-params-cm.yaml:14`) 또는 webhook(3.6)으로 안다.
  클러스터 쪽 변경(kubectl edit)은 Argo CD 가 리소스를 watch 하고 있어서 **바로** 안다 → selfHeal 이 몇 초 안에 되돌린다.

**장면 1: prune 은 "Git 에 없으면 지운다" 이지 "내가 지운 것만 지운다" 가 아니다** (2장 마무리 Q3)
- dev Application 의 `path` 를 base 로 잘못 바꾸면, 새 결과에 `dev-react-app` 이 없으니 prune 이 기존 Deployment·Service·Ingress 를 지운다.
  Argo CD 입장에선 "Git 에서 사라진 리소스" 일 뿐 실수인지 모른다.
- 안전장치: `automated` 의 `allowEmpty` 기본값이 false 라서 결과가 **완전히 빈** 경우(path 가 빈 디렉터리 등)엔 자동 동기화를 거부한다. 리소스가 하나라도 있으면 막아 주지 않는다.
- `PruneLast=true`(`apps/react-app.yaml:25`): 새 리소스 적용을 먼저 끝내고 지우기는 마지막에. 이름을 바꾸는 변경에서 새 것이 뜨기 전에 옛 것이 사라지는 공백을 줄인다.

**장면 2: `kubectl scale` 은 selfHeal 이 되돌리지 않는다** (3.1 Q2 정정)
- selfHeal 은 **OutOfSync 일 때만** 동작한다. replicas 는 `ignoreDifferences` 로 비교에서 빠져 있으니 scale 해도 Synced 그대로 → 되돌리지 않는다.
- 이것은 의도된 동작이다. HPA 가 replicas 를 바꿀 때마다 selfHeal 이 3 으로 되돌리면 HPA 와 싸운다.
- 반면 `kubectl set env`, `kubectl edit` 로 이미지·env 를 바꾸면 OutOfSync → dev 는 selfHeal 이 되돌린다.

**남아 있는 함정: ignoreDifferences 는 "비교" 만 안 할 뿐 sync 때는 Git 값을 적용한다**
- `ignoreDifferences` 는 OutOfSync 판정에서만 replicas 를 뺀다. **실제 sync 를 할 때는 매니페스트 전체를 적용**하므로 `replicas: 3` 도 함께 들어간다.
- prod 는 배포(`argocd app sync`, `ci/Jenkinsfile:173`)마다 HPA 가 5개로 늘려 둔 Pod 가 **3개로 줄었다가**, HPA 가 다시 늘린다. 부하가 높을 때 배포하면 잠깐 용량이 모자란다.
- 해결책 두 가지:
  1. Application 의 `syncOptions` 에 `RespectIgnoreDifferences=true` 추가 → sync 때도 무시한 필드는 건드리지 않는다.
  2. HPA 를 쓰는 overlay 에서는 Deployment 의 `replicas` 자체를 빼고 HPA 의 `minReplicas` 에 맡긴다.
- 이 프로젝트에는 아직 둘 다 없다(`apps/react-app.yaml` 의 syncOptions 에 없음). 1.7 에서 "Argo CD 가 3 으로 되돌리지 않게 하는 설정" 이라고 한 것은 **판정** 에 대해서만 맞다.

**확인 문제 (2026-09-27)**

1. `kubectl delete service dev-react-app` 을 하면? prod 에서는?
   - 지운 것은 **Service** 다. Pod 가 아니다. Pod 는 ReplicaSet 이 다시 띄우지만(1.1), Service 를 다시 만들어 줄 쿠버네티스 컨트롤러는 없다. 다시 만들 수 있는 건 Git 을 들고 있는 **Argo CD 뿐**이다.
   - dev: Argo CD 가 곧바로 알아채고(watch) OutOfSync(Missing) → `selfHeal` 이 Service 를 다시 만든다. 몇 초간 Ingress 의 backend 가 없어 ingress-nginx 가 **503** 을 준다(1.3).
   - prod: `automated` 가 없으니 **아무도 다시 만들지 않는다.** OutOfSync + Health Missing/Degraded 로 표시만 되고, 누가 sync 할 때까지 사용자는 계속 503.
   - 답안의 "3개 유지, 80% 넘으면 5개까지" 는 Pod 와 HPA 이야기라 이 문제와 다르다. 숫자도 정정: HPA 목표는 **70%**, 최대 **10개**(`overlays/prod/hpa.yaml`).
2. prod 에서 `pdb.yaml` 을 지우고 push 하면 PDB 는 언제 사라지나?
   - 답안 "배포 승인할 때(= Jenkins 가 sync 할 때)" 는 **틀렸다. 그때도 안 사라진다.**
   - prod 에는 `automated` 도 `prune` 도 없고, Jenkins 의 `argocd app sync "$APP" $OPTS`(`ci/Jenkinsfile:173`)에는 `--prune` 옵션이 없다.
     수동 sync 는 기본적으로 **지우지 않는다.** PDB 는 클러스터에 남고 Argo CD 화면에 "requires pruning" 으로 표시된다.
   - 사라지게 하려면 사람이 UI 에서 Prune 을 체크하고 sync 하거나 `argocd app sync react-app-prod --prune`.
   - 부작용 예상: prune 이 필요한 리소스가 있으면 Application 은 OutOfSync 로 남는다. 그러면 Jenkins 의 `argocd app wait --sync` 가 Synced 를 기다리다 실패할 수 있다 (7장 실습 때 실제로 확인할 것).
   - prod 에서 삭제를 사람 손에 남겨 둔 것은 의도로 볼 수 있다: 운영 리소스를 Git 커밋 하나로 지우지 않게.
3. HPA 가 6개로 늘린 상태에서 prod 를 sync 하면?
   - 매니페스트의 `replicas: 3` 이 적용되어 **6 → 3 으로 줄어든다.** HPA 가 다음 계산(약 15초 주기)에서 다시 늘리지만 그 사이 용량이 반으로 준다.
   - PDB(`minAvailable: 2`)는 막아 주지 않는다. PDB 는 drain 같은 **eviction** 만 제한하고, Deployment 의 replicas 축소는 막지 않는다(1.7).
   - 막는 법: `apps/react-app.yaml` 의 **react-app-prod** `syncOptions` 에 `- RespectIgnoreDifferences=true` 추가.
     (또는 prod overlay 에서 Deployment 의 replicas 를 빼고 HPA `minReplicas` 에 맡긴다. base 에 `replicas: 2` 가 있으니 patch 로 지워야 한다.)

## 3.3 AppProject: 허용 리포지토리, 허용 네임스페이스, syncWindows

`AppProject` 는 Application 들을 묶는 **울타리**다. "이 프로젝트에 속한 Application 은 어느 리포지토리를
읽을 수 있고, 어느 네임스페이스에 배포할 수 있는가" 를 제한한다. 실수로 dev 앱이 prod 네임스페이스에
배포되는 일을 막는다.

`apps/project.yaml:8`

```yaml
spec:
  description: 개발 환경 애플리케이션
  sourceRepos:
    - http://gitlab.example.com/my-group/*
  destinations:
    - server: https://kubernetes.default.svc
      namespace: 'dev-*'
    - server: https://kubernetes.default.svc
      namespace: react-app-dev
    ...
  clusterResourceWhitelist:
    - group: ''
      kind: Namespace
  namespaceResourceBlacklist:
    - group: ''
      kind: ResourceQuota
    - group: ''
      kind: LimitRange
```

| 필드 | dev | prod |
|---|---|---|
| `sourceRepos` (읽을 수 있는 리포지토리) | `my-group/*` (그룹 전체) | `gitops-manifests.git` 하나 |
| `destinations` (배포할 수 있는 네임스페이스) | `dev-*`, `react-app-dev`, `python-api-dev`, `postgres-dev` | `prod-*`, `react-app-prod`, `python-api-prod`, `postgres-prod` |
| `clusterResourceWhitelist` | Namespace 만 | Namespace 만 |
| `namespaceResourceBlacklist` | ResourceQuota, LimitRange 금지 | 없음 |
| `syncWindows` | 없음 | 평일 09~18시만 |

- **cluster 범위 리소스**(Namespace, ClusterRole 처럼 네임스페이스에 속하지 않는 것)는 기본적으로 막혀 있고,
  whitelist 에 적은 Namespace 만 만들 수 있다. `CreateNamespace=true` 가 동작하려면 이것이 필요하다.
- prod 는 `sourceRepos` 를 gitops 리포지토리 하나로 좁혀 더 엄격하다.

### syncWindows: 배포 가능 시간

`apps/project.yaml:59`

```yaml
  syncWindows:
    # 운영은 평일 업무시간에만 자동 동기화 허용
    - kind: allow
      schedule: '0 9 * * 1-5'
      duration: 9h
      applications:
        - '*'
      manualSync: true
      timeZone: Asia/Seoul
```

- `schedule` 은 cron 형식이다. `0 9 * * 1-5` = 월~금 09:00 에 창이 열리고 `duration: 9h` 동안 유지된다.
- `kind: allow` 창이 하나라도 있으면 **창 밖의 sync 는 막힌다.**
- `manualSync: true` 는 창 밖이라도 **수동 sync 는 허용**한다는 뜻이다. Jenkins 의 `argocd app sync` 는
  수동 sync 이므로 이 설정 덕분에 야간에도 prod 배포가 가능하다.

README 운영 관련 메모(`README.md:2235`)가 prod 를 자동 동기화로 바꿀 때 이 `syncWindows` 를 확인하라고
하는 이유가 이것이다. 자동 sync 는 창 안에서만 돈다.

### 프로젝트 역할과 RBAC

AppProject 에는 역할(`roles`)도 정의할 수 있다.

`apps/project.yaml:29`

```yaml
  roles:
    - name: ci
      description: CI(Jenkins) 동기화 트리거용
      policies:
        - p, proj:dev:ci, applications, sync, dev/*, allow
        - p, proj:dev:ci, applications, get, dev/*, allow
```

다만 이 프로젝트의 Jenkins 는 이 프로젝트 역할이 아니라 전역 RBAC 의 `cicd` 계정을 쓴다.

`bootstrap/argocd/configs/argocd-rbac-cm.yaml:27`

```csv
    # CI(Jenkins) 전용 계정: 동기화 트리거만 허용
    p, role:ci, applications, get, */*, allow
    p, role:ci, applications, sync, */*, allow

    g, cicd, role:ci
```

정책 한 줄은 `p, <주체>, <리소스>, <동작>, <프로젝트>/<앱>, allow` 로 읽는다. `cicd` 계정은 모든 앱을
조회(`get`)하고 동기화(`sync`)만 할 수 있다. 삭제나 설정 변경은 못 한다. `cicd` 계정 자체는
`argocd-cm` 의 `accounts.cicd: apiKey, login` 으로 만들어지고, README 4.4 에서 토큰을 발급해 Jenkins 에 넣는다.

```bash
argocd proj list --grpc-web
argocd proj windows list prod --grpc-web      # 지금 창이 열려 있는지(Active)
```

### 학습 노트: 울타리는 누구를 막나 (2026-09-27)

**왜 필요한가** — Argo CD 의 컨트롤러는 클러스터 거의 전체를 바꿀 수 있는 강한 ServiceAccount 로 돈다.
쿠버네티스 RBAC(1.4)으로는 Argo CD 를 막을 수 없다. 그래서 "어떤 Application 이 무엇을 할 수 있나" 는 Argo CD 가 **스스로** 검사해야 하고, 그 규칙이 AppProject 다.

비유: RBAC 는 **건물 출입카드**(Argo CD 는 마스터키를 가졌다), AppProject 는 마스터키를 든 관리인이 지키는 **업무 지시서** — "dev 팀 일로는 dev 층에만 간다".

| 검사 | 걸리면 |
|---|---|
| `sourceRepos` 에 없는 리포지토리를 source 로 씀 | Application 이 오류 상태(InvalidSpec), sync 불가 |
| `destinations` 에 없는 서버·네임스페이스 | 마찬가지로 거부. 3.1 에서 본 "overlay namespace 가 prod 인 dev 앱" 을 여기서 막는다 |
| whitelist 에 없는 cluster 범위 리소스(PV, ClusterRole...) | 그 리소스만 sync 실패 → 그래서 postgres PV 는 kubectl 로 따로 적용(8.2) |
| blacklist 에 있는 리소스(dev 의 ResourceQuota, LimitRange) | 그 리소스만 sync 실패 → 자원 한도는 관리자가 정하고 앱 팀이 Git 으로 못 바꾸게 |

**파일을 읽다 발견한 것**
1. **`dev-*` 는 이 프로젝트의 네임스페이스와 하나도 안 맞는다.** 와일드카드는 앞머리 `dev-` 로 시작하는 이름(`dev-foo`)에만 맞는데,
   실제 네임스페이스는 `react-app-dev` 처럼 **뒤**에 `-dev` 가 붙는다. 실제 허용은 그 아래 명시한 세 줄이 한다. `dev-*` 는 앞으로의 이름 규칙을 대비한 줄이거나, `*-dev` 를 의도한 것일 수 있다(prod 의 `prod-*` 도 같다).
2. **prod 의 syncWindows 는 지금 아무것도 막지 않는다.** 창은 **자동** sync 만 막는데 prod 에는 `automated` 가 없고, `manualSync: true` 라 수동 sync 는 창 밖에서도 된다.
   주석 "평일 업무시간에만 자동 동기화 허용" 은 나중에 prod 를 자동화할 때를 위한 준비다(README 운영 메모 `README.md:2235`).
   야간 배포를 정말 막고 싶다면 `manualSync: false` 로 바꿔야 한다 → 그러면 Jenkins 의 `argocd app sync` 도 창 밖에서 실패한다.
   - **"창(window)" 이란?** 가게의 **영업시간** 같은 것이다. `schedule: '0 9 * * 1-5'`(월~금 09:00 에 문을 연다) + `duration: 9h`(9시간 동안)
     = **평일 09:00~18:00(Asia/Seoul)** 이 "창 안", 그 밖의 시간(평일 18:00~다음 날 09:00, 토·일 전체)이 "창 밖" 이다.

     ```
     월 00:00 ─── 09:00 ████████████ 18:00 ─── 화 09:00 ████████████ 18:00 ─── ... 금 18:00 ─── 토·일 ─── 월 09:00
               창 밖      창 안(열림)        창 밖       창 안(열림)                  창 밖(주말 내내)
     ```

     | sync 종류 | 창 안 (평일 낮) | 창 밖 (밤·주말), `manualSync: true` (지금) | 창 밖, `manualSync: false` |
     |---|---|---|---|
     | 자동 sync (`automated`) | 됨 | 막힘 | 막힘 |
     | 수동 sync (UI 버튼, `argocd app sync`) | 됨 | **됨** | **막힘** |

     Jenkins 의 `argocd app sync` 는 수동 sync 다. 그래서 `manualSync: false` 로 바꾸면 main 에 금요일 밤 20시에 merge 한 경우,
     승인까지 눌러도 sync 가 "창이 닫혀 있다" 는 오류로 거부되어 파이프라인이 실패한다. 월요일 09시 이후 다시 돌려야 한다.
3. dev 는 `sourceRepos: my-group/*` 라 그룹 안 **아무 리포지토리**(앱 리포지토리 포함)나 source 로 쓸 수 있다. prod 는 gitops 리포지토리 하나로 좁혔다.
4. 프로젝트 역할 `proj:dev:ci`(`apps/project.yaml:29-34`)는 정의만 있고 아무 계정·토큰에도 연결되지 않았다. Jenkins 는 전역 `cicd` 계정(`role:ci`)을 쓴다 — 아래 "프로젝트 역할과 RBAC".

**확인 문제 (2026-09-27)**

1. dev 프로젝트의 Application 이 `destination.namespace: react-app-prod` 로 바뀌면?
   → **배포되지 않는다.** dev AppProject 의 `destinations` 에 `react-app-prod` 가 없으므로 Argo CD 가 Application 을
   오류 상태로 표시하고(`application destination ... is not permitted in project 'dev'`) sync 를 거부한다. 이것이 AppProject 의 존재 이유다.
   (답안 "react-app-prod 에 dev-react-app 이 배포된다" 는 틀림. 게다가 설령 허용됐어도 overlay 의 `namespace: react-app-dev` 가 이겨서(3.1)
   리소스는 `react-app-dev` 로 간다.)
2. `web-dev` 네임스페이스를 AppProject 수정 없이 쓰면?
   → **배포 안 된다.** 결론은 정답. 이유 정정: `namePrefix` 와는 관계없다. `dev-*` 는 네임스페이스 **이름 패턴**이고 "`dev-` 로 시작하는 이름" 만 맞는다.
   `web-dev` 는 `-dev` 로 **끝나므로** 안 맞는다. 해결: `destinations` 에 `web-dev` 를 추가하거나 패턴을 `'*-dev'` 로 바꾼다.
3. "prod 는 평일 09~18시에만 배포" 를 지키려면?
   → 지금은 **못 지킨다.** prod 는 수동 sync 뿐이고 `manualSync: true` 라 창 밖에서도 된다.
   `manualSync: false` 로 바꿔야 한다. Jenkins 영향:
   - 창 밖에 main 파이프라인이 돌면 승인 후 `argocd app sync`(`ci/Jenkinsfile:173`)가 거부되어 **빌드 실패**.
   - 그런데 "gitops 태그 갱신" 단계는 sync **전에** 이미 끝났다 → Git 에는 새 태그가 들어갔고 클러스터는 옛 버전 → prod 가 OutOfSync 로 남는다.
     prod 는 `automated` 가 없어 창이 열려도 저절로 배포되지 않는다. 월요일 09시 이후 누가 sync 를 누르거나 파이프라인을 다시 돌려야 한다.
   - 개선 방향: 승인 단계 앞에서 창이 열렸는지 먼저 확인(`argocd proj windows list prod`)하거나, 창 밖이면 gitops 커밋을 하지 않도록 순서를 조정.

## 3.4 App of Apps 패턴 (`apps/app-of-apps.yaml` 하나만 apply)

Application 이 6개(react-app, python-api, postgres 의 dev/prod)와 AppProject 2개가 있다.
하나씩 `kubectl apply` 하면 새 앱을 추가할 때마다 사람이 클러스터에 손을 대야 한다.
**App of Apps** 는 "Application 매니페스트들을 배포하는 Application" 을 하나 두는 패턴이다.

`apps/app-of-apps.yaml:1`

```yaml
# 이 Application 하나만 등록하면 apps/ 디렉터리의 모든 Application 이 따라 들어온다.
# kubectl apply -f apps/app-of-apps.yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: bootstrap
  namespace: argocd
...
  source:
    repoURL: http://gitlab.example.com/my-group/gitops-manifests.git
    targetRevision: main
    path: apps
    directory:
      recurse: true
      exclude: '{app-of-apps.yaml,applicationset-gitlab.yaml}'
  destination:
    server: https://kubernetes.default.svc
    namespace: argocd
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
```

동작 순서:

```
kubectl apply -f apps/app-of-apps.yaml         ← 사람이 딱 한 번
   │
   ▼
[bootstrap] Application  ─ source: gitops-manifests 의 apps/ 디렉터리
   │  apps/ 의 YAML 을 argocd 네임스페이스에 적용
   ├─ AppProject dev, prod                  (project.yaml)
   ├─ Application react-app-dev / -prod     (react-app.yaml)
   ├─ Application python-api-dev / -prod    (python-api.yaml)
   └─ Application postgres-dev / -prod      (postgres.yaml)
          │
          ▼ 각 Application 이 자기 manifests/<앱>/overlays/<환경> 을 배포
```

포인트:

- `source.path: apps` 이고 `directory` 를 쓴다. `apps/` 에는 `kustomization.yaml` 이 없으므로 Argo CD 는
  디렉터리 안의 YAML 을 그대로 적용한다(`recurse: true` 는 하위 디렉터리까지).
- `destination.namespace: argocd` — 여기서 배포하는 "리소스" 가 Application 이므로 argocd 네임스페이스에 들어간다.
- `exclude` — `app-of-apps.yaml` 자신을 빼고, `applicationset-gitlab.yaml` 도 뺀다(3.8 에서 이유).
- 이후 새 앱을 추가할 때는 **gitops 리포지토리의 `apps/` 에 파일을 커밋하기만 하면** 된다.
  `bootstrap` 이 automated 라 자동으로 새 Application 이 생긴다. 이것도 GitOps 로 관리되는 셈이다.

```bash
cd ~/workspace/cicd
kubectl apply -f apps/app-of-apps.yaml
argocd app list --grpc-web      # bootstrap 과 그 아래 Application 들이 보인다
```

### 학습 노트: 부모가 자식을 관리한다는 것의 무게 (2026-09-27)

비유: `bootstrap` 은 **본사**, 각 Application 은 **지점장**이다. 본사는 "지점장 명단(`apps/`)" 만 관리하고, 각 지점장이 자기 가게(`manifests/<앱>/overlays/<환경>`)를 운영한다.
본사 명단에서 이름을 지우면 그 지점장은 해고되고 — 가게도 문을 닫는다.

**① 파일 하나를 지우면 앱이 통째로 사라진다 (prune × finalizer)**

```
gitops 에서 apps/react-app.yaml 삭제 → push
  → bootstrap(automated, prune: true, apps/app-of-apps.yaml:26-28)이 "Git 에 없는 Application" 으로 보고 react-app-dev, react-app-prod 를 지움
    → 각 Application 의 finalizer(resources-finalizer, apps/react-app.yaml:8-9)가 "내가 만든 리소스도 같이 지워라"
      → Deployment, Service, Ingress, HPA, PDB 모두 삭제 → dev 와 prod 서비스 중단
```

- 3.2 에서 "prod 에는 prune 이 없어 지워지지 않는다" 고 했다. 그건 **prod Application 이 자기 리소스를** 지우는 이야기다.
  여기서는 **부모(bootstrap)가 prod Application 자체를** 지운다. 부모의 prune 이 자식의 안전장치를 건너뛴다.
- 앱을 옮기거나 이름을 바꿀 때 조심한다. 리소스를 남기고 Application 만 빼려면 먼저 finalizer 를 떼야 한다.

**② Application 을 UI 에서 고쳐도 되돌아간다 (selfHeal)**
- Argo CD UI 에서 react-app-dev 의 `targetRevision` 을 바꾸면, 그건 bootstrap 입장에서 "누가 몰래 옮긴 가구" 다 → selfHeal 이 Git 값으로 되돌린다.
- Application 설정도 **Git 으로만** 바꾼다. 앱 매니페스트와 똑같은 규칙이 한 층 위에도 적용된다.

**③ bootstrap 은 `project: default` 다 — 울타리 밖에 있다** (`apps/app-of-apps.yaml:11`)
- 울타리(AppProject dev/prod)를 **만드는** 쪽이라 울타리 안에 들어갈 수 없다(닭과 달걀). `default` 프로젝트는 기본적으로 아무 제한이 없다.
- 그래서 `apps/` 에 `project: default` 인 Application 을 하나 커밋하면 dev/prod 울타리를 우회해 **어디든** 배포할 수 있다.
  → **gitops 리포지토리의 `apps/` 에 커밋할 수 있는 권한 = 클러스터를 다룰 권한.** GitLab 에서 main 을 보호 브랜치로 두고 merge request 리뷰를 거치게 하는 이유.
  (운영에서는 `default` 프로젝트의 sourceRepos/destinations 를 비워 막아 두기도 한다.)

**④ 자기 자신은 제외** (`exclude` 의 `app-of-apps.yaml`)
- bootstrap 이 자기 파일까지 관리하면, 설정을 잘못 커밋했을 때 스스로를 망가뜨릴 수 있다. 그래서 bootstrap 자체의 변경은 사람이 `kubectl apply -f apps/app-of-apps.yaml` 로 한다.

**확인 문제 (2026-09-27)**

1. 새 앱 `web` 을 dev 에 추가하려면 gitops 리포지토리에서 할 일
   1. `manifests/web/base/` — deployment, service, serviceaccount, kustomization (2장 구조 그대로)
   2. `manifests/web/overlays/dev/` — kustomization(`namespace: web-dev`, `namePrefix: dev-`, `images`), ingress
   3. `apps/web.yaml` — Application `web-dev` (`project: dev`, `path: manifests/web/overlays/dev`, `destination.namespace: web-dev`, automated)
   4. `apps/project.yaml` — dev 의 `destinations` 에 `web-dev` 추가 (**빠뜨리면 3.3 Q2 처럼 거부된다.** `dev-*` 는 안 맞는다)
   5. 커밋·push → bootstrap 이 `web-dev` Application 을 만들고 → `web-dev` 가 overlay 를 배포
   - gitops 밖에서 할 일: 앱 리포지토리 + Jenkinsfile + Jenkins 잡, `web-dev` 네임스페이스의 `regcred`(없으면 ImagePullBackOff), hosts 항목.
     python-api 를 추가하는 README 8장이 바로 이 순서다(`doc/08-advanced.md` 8.1 표).
2. `apps/python-api.yaml` 을 지우고 push 하면 5분 뒤 `python-api-prod` 에 남는 것
   - bootstrap 의 prune 이 `python-api-dev`, `python-api-prod` Application 을 지우고, finalizer 가 Deployment·Service·Ingress·ServiceAccount 를 모두 지운다.
     prod 에 prune 이 없어도 소용없다(부모가 지우므로).
   - **남는 것: 네임스페이스와 그 안의 `regcred` Secret**(그리고 쿠버네티스가 자동으로 만드는 `default` ServiceAccount 등).
     네임스페이스는 `CreateNamespace=true` 로 만들어졌을 뿐 Argo CD 가 관리하는 리소스 목록에 없어서 지우지 않는다.
     `regcred` 는 README 3.5 에서 kubectl 로 직접 만든 것이라 Argo CD 가 모른다(2.2 와 같은 원리).
   - 5분이면 충분하다: 폴링 180초 안에 감지(webhook 이면 즉시).
3. UI 에서 `react-app-prod` 의 `targetRevision` 을 어제 SHA 로 바꾸는 롤백
   - **제대로 동작하지 않는다.** 정답. 이유: bootstrap 의 selfHeal 이 Application 을 Git 값(`main`)으로 곧 되돌린다(3.4 ②).
     그 사이 sync 를 눌렀다면 잠시 옛 버전이 뜨지만, 설정은 main 으로 돌아가 OutOfSync 가 되고 다음 sync 때 다시 문제 버전이 배포된다. 이력도 Git 에 안 남는다.
   - 올바른 방법: gitops 리포지토리에서 `chore(prod): react-app -> main-xxxxxxxx` 커밋을 revert → push →
     **prod 는 automated 가 없으므로 Argo CD 에서 sync**(또는 `argocd app sync react-app-prod`). 답안의 "gitops 에서 revert" 에 이 sync 가 더해져야 끝난다.

## 3.5 파일 읽기: `apps/project.yaml`, `apps/react-app.yaml`, `apps/app-of-apps.yaml`

3.1–3.4 에서 대부분의 줄을 이미 봤다. 여기서는 세 파일을 **한 장의 지도**로 묶고, 아직 안 본 줄만 짚는다.

### 학습 노트: 세 파일은 "정관 · 계약서 · 명단" (2026-09-27)

비유: `project.yaml` 은 **회사 정관**(무엇을 어디에 해도 되나), `react-app.yaml` 은 **지점별 운영 계약서**(무엇을 어디에 어떻게),
`app-of-apps.yaml` 은 **본사 명단**(계약서 파일들을 모아 등록).

```
app-of-apps.yaml (bootstrap, project: default)
  └─ apps/ 폴더를 읽어서 만든다 ─┬─ AppProject dev, prod          ← project.yaml
                                ├─ Application react-app-dev/prod ← react-app.yaml ─ project: dev / prod 로 정관을 따름
                                ├─ python-api.yaml, postgres.yaml
                                └─ (제외) app-of-apps.yaml, applicationset-gitlab.yaml
```

**react-app.yaml — dev 와 prod 를 줄 단위로 비교**

| 줄 | dev (`:1-32`) | prod (`:34-56`) | 의미 |
|---|---|---|---|
| `project` | dev | prod | 어느 정관(울타리)을 따르나 |
| `path` | `overlays/dev` | `overlays/prod` | 같은 리포지토리, 같은 브랜치(`main`) — 폴더만 다르다 |
| `automated` | prune + selfHeal | **없음** | prod 는 사람이 sync |
| `syncOptions` | CreateNamespace, PruneLast | CreateNamespace 만 | prune 을 안 하니 PruneLast 도 필요 없다 |
| `retry` | 5번, 10s→20s→40s→80s→160s | **없음** | 자동 sync 는 사람이 안 보므로 스스로 재시도. prod 는 사람이 보고 다시 누른다 |
| `revisionHistoryLimit` | 10 | **20** | 새로 본 줄. 아래 설명 |

- **`revisionHistoryLimit`** (`apps/react-app.yaml:32`, `:56`) — Argo CD 가 Application 의 `status.history` 에 남기는 **지난 sync 기록 개수**(기본 10).
  UI 의 History and Rollback 화면과 `argocd app rollback` 이 이 목록을 쓴다. prod 는 되돌릴 일이 더 중요해서 20 개.
  (Deployment 의 `revisionHistoryLimit` = 옛 ReplicaSet 보관 개수와 이름만 같고 다른 것이다.)
  단, 3.4 Q3 처럼 **정식 롤백은 Git revert** 다. `argocd app rollback` 은 Git 을 안 바꾸므로 Application 이 OutOfSync 로 남는다 — 급할 때 쓰는 비상 버튼.
- **`retry` 의 계산** — 대기 시간이 `duration × factor^n`(10, 20, 40, 80, 160초)이고 `maxDuration: 3m` 을 넘지 않는다. 5번 다 합치면 약 5분 동안 버틴다.
  예: dev 네임스페이스에 regcred 가 늦게 만들어지거나, CRD 가 아직 없을 때 잠깐 기다려 주는 효과.

**project.yaml — 아직 안 본 줄**
- **AppProject 의 `finalizers`** (`apps/project.yaml:6-7`, `:41-42`) — Application 의 finalizer("내 리소스도 같이 지워라")와 이름은 같지만 뜻이 다르다.
  AppProject 에서는 **"이 프로젝트를 쓰는 Application 이 남아 있으면 프로젝트 삭제를 미룬다"**. 정관을 먼저 없애 계약서가 붕 뜨는 걸 막는다.
- `clusterResourceWhitelist: Namespace` (`:21-23`) — `CreateNamespace=true` 가 동작하려면 **필요한 줄**이다. 네임스페이스는 클러스터 범위 리소스라 여기서 허용해야 만들 수 있다.
- prod 에는 `namespaceResourceBlacklist` 와 `roles` 가 없다 — dev 보다 느슨한 부분. (prod `sourceRepos` 는 오히려 더 좁다.)
- 3.3 에서 찾은 문제 그대로: `dev-*`/`prod-*` 패턴은 실제 네임스페이스(`react-app-dev`)와 안 맞아서, 결국 아래 줄에 네임스페이스를 **하나씩** 적어 두었다(`:15-20`, `:50-55`).

**app-of-apps.yaml — 아직 안 본 줄**
- `destination.namespace: argocd` (`:24`) — bootstrap 이 만드는 것은 Application/AppProject 이고, 이것들은 **argocd 네임스페이스에 있어야** Argo CD 가 인식한다.
  각 파일이 `metadata.namespace: argocd` 를 직접 적어 두었지만, 빠뜨린 파일이 있어도 여기로 들어가게 하는 안전장치.
- `directory.recurse: true` + `exclude` 는 glob 패턴(`{a,b}` = a 또는 b). 파일을 **하위 폴더**에 넣어도 읽힌다.

**확인 문제 (2026-09-27)**

1. `clusterResourceWhitelist`(`apps/project.yaml:21-23`)를 지우고 push → 새 앱 `web-dev` 첫 배포, 이미 있는 `react-app-dev` 는?
   - 답: 모름 → 설명. whitelist 가 **비어 있으면 클러스터 범위 리소스는 전부 금지**다(허용 목록 방식: 적힌 것만 된다).
   - `web-dev` — 네임스페이스가 아직 없어 `CreateNamespace=true` 가 Namespace 를 만들려다 **"not permitted in project"** 로 sync 실패. 아무것도 배포되지 않는다.
     (관리자가 `kubectl create ns web-dev` 로 먼저 만들어 두면 피해 갈 수 있다.)
   - `react-app-dev` — 이미 떠 있는 파드는 **그대로 돈다.** Argo CD 는 권한이 줄었다고 리소스를 지우지 않는다. 영향은 "새로 만들 때" 드러난다.
2. retry 없는 쪽(prod)에서 sync 가 한 번 실패하면?
   - 답: "build 부터 다시" → **틀림.** sync 가 실패했다는 것은 "Git 에 적힌 대로 클러스터에 적용하는 단계" 가 실패한 것이다.
     이미지는 Nexus 에 있고 gitops 커밋도 이미 main 에 있다. 원인(예: regcred 없음, 권한)을 고친 뒤 **Argo CD 에서 sync 만 다시 누르면** 된다.
     빌드를 다시 하면 같은 코드로 새 태그가 생길 뿐이다. 실패한 sync 는 OutOfSync + 마지막 operation 이 Failed 인 상태로 남는다.
   - "사람이 직접 눈으로 확인하니 retry 가 없어도 된다" → **정답.** dev 는 아무도 안 보는 자동 sync 라 스스로 재시도가 필요하다.
3. `kubectl delete appproject dev` 하면?
   - 답: "dev 가 모두 사라짐" → **틀림.** Application 의 finalizer("내 리소스도 같이 지워라")와 헷갈린 것 — 이번 절의 핵심 함정이다.
   - AppProject 의 finalizer 는 **"이 프로젝트를 쓰는 Application 이 남아 있으면 삭제를 미뤄라"**. react-app-dev, python-api-dev, postgres-dev 가 `project: dev` 를 쓰므로
     dev 는 `Terminating` 상태로 멈춰 있고 **사라지지 않는다.** AppProject 삭제는 Application 을 지우지 않으므로 dev 서비스도 그대로다.
   - bootstrap 의 selfHeal 은 "삭제 중" 인 객체를 되살리지 못한다(쿠버네티스에서 삭제는 취소가 없다). Terminating 으로 계속 남는다.
     누가 finalizer 를 억지로 떼면 그때 실제로 지워지고 → bootstrap selfHeal 이 Git 의 `apps/project.yaml` 로 **곧바로 다시 만든다.**
   - 두 finalizer 비교

     | 붙은 곳 | 지우려 하면 |
     |---|---|
     | Application | 그 Application 이 만든 리소스를 먼저 지우고 사라진다 (**연쇄 삭제**) |
     | AppProject | 쓰는 Application 이 있으면 사라지지 않고 기다린다 (**삭제 보류**) |

## 3.6 webhook 과 폴링(180초) 차이 (README 6장)

Argo CD 가 "Git 이 바뀌었다" 를 아는 방법은 두 가지다.

| 방식 | 동작 | 반영 시간 |
|---|---|---|
| **폴링** (polling) | Argo CD 가 주기적으로 Git 을 직접 확인한다 | 최대 180초 |
| **webhook** | GitLab 이 push 를 받는 순간 Argo CD 에 HTTP 요청으로 알려 준다 | 거의 즉시 |

폴링 주기는 여기서 정한다.

`bootstrap/argocd/configs/argocd-cmd-params-cm.yaml:13-14`

```yaml
  # 리포지토리 폴링 주기 (webhook 을 쓰면 길게 잡아도 된다)
  timeout.reconciliation: 180s
```

webhook 은 폴링을 **대체**하지 않고 **앞당긴다.** webhook 이 실패해도 180초 뒤 폴링이 잡아 준다.
참고로 Jenkins 도 `argocd app get --refresh` 로 한 번 더 확인을 요청한다(`ci/Jenkinsfile:167`,
"webhook 이 먼저 했어도 무해").

### webhook 설정 (README 6장 요약)

1. **공유 비밀값 만들기** — GitLab 과 Argo CD 가 같은 값을 알고 있어야, Argo CD 가 "진짜 GitLab 이 보낸
   요청인지" 확인할 수 있다.

   ```bash
   ( umask 077; openssl rand -hex 20 > ~/argocd-webhook-secret.txt )
   test -s ~/argocd-webhook-secret.txt && kubectl -n argocd patch secret argocd-secret --type merge \
     -p "{\"stringData\":{\"webhook.gitlab.secret\":\"$(cat ~/argocd-webhook-secret.txt)\"}}"
   kubectl -n argocd rollout restart deploy/argocd-server
   ```

   `test -s` 로 빈 값을 막는 이유: 빈 값이 들어가면 Argo CD 는 **서명 검증 없이 모든 요청을 받는다.**

2. **GitLab 에 등록** — gitops 리포지토리의 Settings > Webhooks
   - URL: `http://argocd-server.argocd.svc.cluster.local/api/webhook` (클러스터 내부 주소)
   - Secret token: 위 파일의 값
   - Trigger: Push events
   - 먼저 Admin Area 에서 *Allow requests to the local network* 를 켜야 내부 주소가 저장된다.

3. **확인** — webhook 화면의 Test > Push events 가 `HTTP 200` 이면 된다.

   ```bash
   kubectl -n argocd logs deploy/argocd-server --since=2m | grep -i webhook
   ```

주의: webhook payload 의 리포지토리 URL 이 Application 의 `repoURL` 과 **일치해야** refresh 가 일어난다.
이 프로젝트는 둘 다 `http://gitlab.example.com/...` 로 맞춰 두었다(GitLab `external_url` 과 같다).

### 왜 gitops 리포지토리에 거는가

webhook 은 **앱 리포지토리가 아니라 gitops 리포지토리**에 건다. Argo CD 가 보는 것은 gitops 리포지토리이기
때문이다. 앱 리포지토리의 push 는 Jenkins 가 받고, Jenkins 가 gitops 에 커밋하면 그 push 가 webhook 으로
Argo CD 에 전달된다.

### 학습 노트: 택배 조회 vs 도착 문자 (2026-09-27)

비유: 폴링은 **택배 조회 페이지를 3분마다 새로고침**하는 것, webhook 은 **"도착했습니다" 문자**를 받는 것이다.
문자가 안 와도(webhook 실패) 3분마다 새로고침은 계속하니 결국 안다. 문자는 "빨리 아는 것" 만 바꾼다.

**① webhook 이 바꾸는 것은 "감지" 뿐이다 — 배포 여부는 syncPolicy 가 정한다**

| | dev (automated) | prod (수동) |
|---|---|---|
| webhook 도착 | 즉시 refresh → OutOfSync → **바로 sync(배포)** | 즉시 refresh → **OutOfSync 표시만** |
| webhook 없음 | 최대 180초 뒤 같은 일 | 최대 180초 뒤 OutOfSync 표시 |

- prod 에서 webhook 은 "UI 에 OutOfSync 가 빨리 뜬다" 는 효과밖에 없다. 배포는 여전히 사람이(또는 Jenkins 의 `argocd app sync`) 한다.
- refresh = "Git 을 다시 읽고 비교" / sync = "비교 결과대로 적용". 3.2 의 두 단계가 여기서도 그대로다.

**② 이 프로젝트는 감지 경로가 세 겹이다**

```
Jenkins 가 gitops 에 커밋·push
  ├─ (1) GitLab webhook → argocd-server /api/webhook → 해당 repoURL 의 Application refresh   ← 거의 즉시
  ├─ (2) Jenkins 가 직접 argocd app get --refresh (ci/Jenkinsfile:167)                       ← 파이프라인이 기다리지 않으려고
  └─ (3) 폴링 timeout.reconciliation: 180s (argocd-cmd-params-cm.yaml:14)                    ← 최후의 안전망
```

- 셋 중 하나만 살아 있어도 배포는 된다. 그래서 webhook 이 깨져도 **느려질 뿐 멈추지 않는다** — 장애를 늦게 알아채는 원인이 되기도 한다.
- (정정) 폴링은 **Git 쪽 변경**을 알아채는 주기다. 클러스터 쪽 변경(누가 kubectl 로 고침)은 Argo CD 가 리소스를 **watch** 하고 있어서 폴링과 상관없이 바로 알아채고, selfHeal 도 그 즉시 동작한다.

**③ webhook 이 "성공" 해도 아무 일이 안 일어나는 경우**

| 원인 | 증상 |
|---|---|
| payload 의 리포지토리 URL ≠ Application `repoURL` (예: `http://gitlab.example.com/...` vs `http://gitlab.gitlab.svc...`) | GitLab 은 HTTP 200 을 받지만 **매칭되는 Application 이 없어 refresh 안 됨** → 180초 폴링으로 반영 |
| 비밀값 불일치 | Argo CD 가 요청을 거부 → GitLab Recent events 에 오류 응답 |
| 비밀값이 **빈 값** | 검증 없이 누구 요청이든 받음(README 6장 `test -s` 가 막는 것). 기능은 되니 더 위험하다 |
| 로컬 네트워크 요청 차단(GitLab Admin 설정) | webhook 저장 자체가 `Url is blocked` 로 거부 |

- 첫 줄이 제일 헷갈린다: **200 = "받았다"** 이지 "refresh 했다" 가 아니다. 확인은 `argocd-server` 로그의 webhook 줄로 한다.


**확인 문제 (2026-09-27)**

1. Test 는 `HTTP 200` 인데 develop push 후 dev 배포가 늘 2~3분 늦다 — 원인과 확인 방법
   - 답: "비밀값 불일치 또는 빈 값" → **틀림.** 불일치면 Argo CD 가 거부해서 Test 가 200 이 아니다. 빈 값이면 검증 없이 받으니 오히려 **즉시** 반영된다(위험하지만 느리지는 않다).
   - 정답: **payload 의 리포지토리 URL ≠ Application `repoURL`.** 200 은 받았다는 뜻일 뿐, 맞는 Application 이 없어 refresh 가 안 되고 180초 폴링이 잡아 준다 → "2~3분 지연" 이 폴링 주기와 맞아떨어지는 게 단서.
     확인: GitLab webhook 의 Recent events 에서 요청 본문의 project URL(`git_http_url`) 과 `apps/*.yaml` 의 `repoURL` 비교, `argocd-server` 로그의 webhook 줄.
   - **(문제 정정)** 이 프로젝트에서 Jenkins 가 만든 커밋은 `ci/Jenkinsfile:167` 의 `--refresh` 가 바로 알려 주므로 webhook 이 고장 나도 지연이 **안 보인다.**
     지연이 드러나는 것은 **사람이 직접 한 gitops 커밋**(3.4 Q3 의 revert 롤백, `apps/` 수정 등)이다. 문제를 "Jenkins 를 거치지 않은 커밋" 으로 읽어야 맞다.
2. `timeout.reconciliation: 0`(폴링 끄기)으로 잃는 것
   - 답: "webhook 이 실패하면 변경을 모른다" → **정답(첫 번째).** 특히 argocd-server 재시작 중에 온 webhook 처럼 **한 번 놓친 알림은 다시 오지 않는다** → 누가 수동 refresh 할 때까지 영영 모른다.
     이 프로젝트에서 Jenkins 커밋은 `--refresh` 로 살지만, 사람의 revert 커밋은 반영되지 않는다 — 롤백이 안 되는 셈.
   - 두 번째 힌트("Git 은 그대로인데 클러스터가 바뀐 경우")는 **내 힌트가 틀렸다.** 클러스터 변경은 watch 로 바로 감지되므로 폴링을 꺼도 selfHeal 은 동작한다(위 ② 정정).
     대신 두 번째로 잃는 것: **webhook 을 걸지 않은 곳의 변경.** webhook 은 gitops 리포지토리에만 걸었으므로, 다른 리포지토리를 source 로 쓰는 Application(dev 는 `my-group/*` 허용, 3.3)은 변경을 영영 모른다.
3. prod gitops 커밋 push 1분 뒤 `react-app-prod` 상태
   - 답: "배포 대기, 새 버전 안 떠 있음" → **Argo CD 만 놓고 보면 정답**(automated 없음 → OutOfSync 표시만).
   - 하지만 이 프로젝트의 전체 흐름에서는 **새 버전이 뜨고 있다.** 사람의 승인은 커밋 **전에** Jenkins `prod 승인` 단계(`ci/Jenkinsfile:112-120`)에서 이미 받았고,
     커밋 직후 `배포 대기` 단계가 `argocd app sync`(`:173`)를 직접 건다. "수동 sync" 의 "수동" 은 **Argo CD 가 스스로 하지 않는다**는 뜻이지, 사람이 UI 에서 누른다는 뜻이 아니다.
   - 사람이 UI 에서 sync 를 눌러야 하는 경우는 Jenkins 를 거치지 않은 prod 커밋(예: revert 롤백)뿐이다.

## 3.7 배포 결과 알림: `bootstrap/argocd/configs/notifications-cm.yaml`

Argo CD Notifications 는 Application 의 상태 변화(배포 성공, 실패 등)를 외부로 알리는 기능이다.
이 프로젝트는 **GitLab 커밋 옆에 배포 결과 아이콘(commit status)** 을 남기도록 설정했다.

알림은 세 조각으로 이루어진다.

| 조각 | 역할 | 이 파일의 이름 |
|---|---|---|
| **service** | 어디로 보낼지 (Slack, 이메일, webhook ...) | `service.webhook.gitlab` |
| **template** | 무엇을 보낼지 (본문) | `template.app-deployed`, `template.app-sync-failed` |
| **trigger** | 언제 보낼지 (조건) | `trigger.on-deployed`, `trigger.on-sync-failed` |

`bootstrap/argocd/configs/notifications-cm.yaml:11`

```yaml
  service.webhook.gitlab: |
    url: http://gitlab.example.com/api/v4
    headers:
      - name: PRIVATE-TOKEN
        value: $gitlab-token
```

`$gitlab-token` 은 `argocd-notifications-secret` 의 `gitlab-token` 키를 가리킨다. 값은 README 4.2 에서
`kubectl patch` 로 넣는다(선택 사항).

`bootstrap/argocd/configs/notifications-cm.yaml:20`

```yaml
  template.app-deployed: |
    webhook:
      gitlab:
        method: POST
        path: /projects/my-group%2F{{.app.metadata.annotations.repo}}/statuses/{{.app.status.operationState.syncResult.revision}}
        body: |
          {
            "state": "success",
            "name": "argocd/{{.app.metadata.name}}",
            ...
```

- `{{ ... }}` 는 Go 템플릿이다. 보낼 때 Application 의 값으로 채워진다.
- `{{.app.metadata.annotations.repo}}` — 3.1 에서 본 `annotations.repo: react-app` 이 여기서 쓰인다.
- GitLab API `POST /projects/:id/statuses/:sha` 로 해당 커밋에 success / failed 상태를 기록한다.

`bootstrap/argocd/configs/notifications-cm.yaml:46`

```yaml
  trigger.on-deployed: |
    - when: app.status.operationState.phase in ['Succeeded'] and app.status.health.status == 'Healthy'
      send: [app-deployed]
  trigger.on-sync-failed: |
    - when: app.status.operationState.phase in ['Error', 'Failed']
      send: [app-sync-failed]
```

> **주의 — 이 저장소 그대로는 알림이 나가지 않는다.** Notifications 는 Application(또는 AppProject)에
> `notifications.argoproj.io/subscribe.<trigger>.<service>: ""` 형태의 **구독 어노테이션**이 있어야
> 동작하는데, `apps/` 의 어떤 Application 에도 이 어노테이션이 없다. 또 `syncResult.revision` 은
> **gitops 리포지토리**의 커밋 SHA 인데 경로는 앱 리포지토리(`my-group%2Freact-app`)를 가리키므로,
> 켜더라도 GitLab 이 커밋을 찾지 못할 수 있다. 이 절은 "알림이 이런 구조로 설정된다" 를 이해하는 용도로 본다.

### 학습 노트: 신문사는 있는데 구독자가 없다 (2026-09-27)

비유: 알림은 **신문 구독**이다.

| 신문 | Notifications | 이 프로젝트 |
|---|---|---|
| 배달 경로(우체국) | service | `service.webhook.gitlab` (`notifications-cm.yaml:11-17`) |
| 기사 내용 | template | `app-deployed`, `app-sync-failed` (`:20-44`) |
| 발행 조건 | trigger | `on-deployed`, `on-sync-failed` (`:46-51`) |
| **구독 신청서** | Application 의 `notifications.argoproj.io/subscribe.<trigger>.<service>` annotation | **없음** → 아무것도 배달되지 않는다 |

신문사(ConfigMap)는 다 차려 놨는데 구독 신청서가 한 장도 없다. 켜려면 `apps/react-app.yaml` 의 annotations 에 다음을 더한다.

```yaml
  annotations:
    repo: react-app
    notifications.argoproj.io/subscribe.on-deployed.gitlab: ""
    notifications.argoproj.io/subscribe.on-sync-failed.gitlab: ""
```

(값 `""` 은 "받는 사람" 자리다. Slack 이면 채널 이름을 적지만, webhook 은 주소가 service 에 이미 있으니 비워 둔다.)
토큰(`$gitlab-token`, api 스코프)도 README 4.2 의 `kubectl patch` 로 `argocd-notifications-secret` 에 넣어야 한다 — `notifications-secret.yaml` 은 예시일 뿐 적용되지 않는다.

**구독해도 남는 문제 ① — 주소가 틀린 커밋으로 간다** (`:24`, `:37`)

```
path: /projects/my-group%2F{{repo}}/statuses/{{syncResult.revision}}
                   └ react-app (앱 리포지토리)      └ gitops-manifests 의 커밋 SHA
```

- Argo CD 가 아는 SHA 는 **자기가 읽은 리포지토리(gitops)** 의 커밋뿐이다. 그 SHA 를 앱 리포지토리에서 찾으니 GitLab 이 404 → 알림 실패(`argocd-notifications-controller` 로그에 남는다).
- 고치는 방향 두 가지

  | 방법 | 결과 |
  |---|---|
  | path 를 `my-group%2Fgitops-manifests` 로 | gitops 커밋 옆에 ✔/✘. 간단하지만 개발자가 보는 앱 커밋에는 안 뜬다 |
  | 앱 커밋에 달기 | Argo CD 는 앱 SHA 를 모른다. Jenkins 가 알고 있으니(`IMAGE_TAG` 의 SHA 8자리, 이미지도 전체 SHA 태그로 push) Jenkins 의 `배포 대기` 단계 끝에서 직접 GitLab API 를 부르는 편이 자연스럽다 |

**구독해도 남는 문제 ② — trigger 의 빈틈**
- `on-deployed` 에 `oncePer` 가 없다. 조건이 false→true 로 바뀔 때마다 보내므로, sync 는 그대로인데 health 가 Healthy→Progressing→Healthy 로 흔들리면(파드 재시작, 노드 drain) **같은 알림이 또 간다.**
  Argo CD 기본 카탈로그는 `oncePer: app.status.operationState.syncResult.revision` 으로 "커밋당 한 번" 을 보장한다.
- `on-sync-failed` 는 **적용(apply) 실패**만 잡는다. apply 는 성공했는데 파드가 못 뜨는 경우(ImagePullBackOff, CrashLoopBackOff)는
  phase=Succeeded, health=Degraded/Progressing 이라 **두 trigger 모두 조건이 안 맞아 아무 알림도 없다.** 카탈로그의 `on-health-degraded` 같은 trigger 가 필요하다.
  (이 프로젝트에서 그 빈틈은 Jenkins 의 `argocd app wait --health`(`ci/Jenkinsfile:179`)가 파이프라인 실패로 대신 알려 준다.)

**확인 문제 (2026-09-27)** — 세 문제 모두 "모름". 풀이를 단계별로 남긴다.

1. 구독 annotation + 토큰만 추가 → dev 배포 성공 → 앱 커밋 옆에 ✔ 가 뜨나?
   - **안 뜬다.** 알림은 **보내진다**(trigger 조건 충족). 그런데 보내는 주소가 `/projects/my-group%2Freact-app/statuses/<gitops 커밋 SHA>` 다.
     비유: "A동(앱 리포지토리) 의 B동 호수(gitops SHA)" 로 보낸 택배 → 그런 집이 없어 반송(GitLab 404).
   - 확인: `kubectl -n argocd logs deploy/argocd-notifications-controller` — 알림 전송 실패와 GitLab 응답 코드가 찍힌다.
     (Argo CD 본체 로그가 아니라 **알림 전용 컨트롤러**의 로그를 본다.)
2. 파드 재시작마다 같은 커밋에 success 가 쌓임
   - trigger 조건은 `phase == Succeeded` **그리고** `health == Healthy`. phase 는 마지막 sync 결과라 계속 Succeeded 로 남는다.
     파드가 재시작하면 health 가 잠깐 Progressing → 조건 false → 다시 Healthy → 조건 true → **"새로 조건이 맞았다" 로 보고 또 보낸다.**
   - 고침: `trigger.on-deployed`(`notifications-cm.yaml:46-48`)에 `oncePer: app.status.operationState.syncResult.revision` → "같은 커밋에는 한 번만".
3. prod 에 regcred 없이 sync → ImagePullBackOff
   - `operationState.phase` = **Succeeded.** sync 는 "매니페스트를 쿠버네티스 API 에 적용" 까지다. Deployment 객체는 문제없이 만들어졌다. 이미지를 받는 건 그 뒤 kubelet 의 일이다.
   - `health.status` = **Progressing**(새 파드가 준비되길 기다리는 중) → 오래 못 뜨면(Deployment 의 진행 기한 기본 600초) **Degraded**.
   - 알림: `on-deployed` 는 Healthy 가 아니라서 ✗, `on-sync-failed` 는 phase 가 Error/Failed 가 아니라서 ✗ → **아무 알림도 안 간다.**
   - 결국 알려 주는 것: Jenkins `배포 대기` 단계의 `argocd app wait --sync --health --timeout 600`(`ci/Jenkinsfile:179`)이 Healthy 를 못 받아 **파이프라인 실패(빨간불)**.
   - 교훈: "sync 성공 ≠ 배포 성공". 1장의 실패 단계(적용 → 이미지 pull → 컨테이너 실행 → readiness)를 알림도 따로 잡아야 한다(`on-health-degraded`).

## 3.8 (선택) ApplicationSet: `apps/applicationset-gitlab.yaml`

App of Apps 는 Application 파일을 사람이 하나씩 써야 한다. **ApplicationSet** 은 **규칙(generator)으로
Application 을 자동 생성**한다. 예를 들어 "GitLab 그룹의 리포지토리마다 Application 하나씩" 같은 식이다.

`apps/applicationset-gitlab.yaml:11`

```yaml
  generators:
    - scmProvider:
        gitlab:
          group: my-group
          api: http://gitlab.example.com/
          includeSubgroups: true
          tokenRef:
            secretName: gitlab-scm-credentials
            key: token
        filters:
          - repositoryMatch: '^svc-.*'
            pathsExist: [.argocd.yaml, manifests]
```

- `scmProvider` generator 가 GitLab API 로 `my-group` 의 리포지토리 목록을 가져온다.
- `filters` — 이름이 `svc-` 로 시작하고, 루트에 `.argocd.yaml` 과 `manifests` 가 모두 있는 리포지토리만 고른다.

`apps/applicationset-gitlab.yaml:28`

```yaml
  template:
    metadata:
      name: '{{ .repository }}-dev'
    spec:
      project: dev
      source:
        repoURL: '{{ .url }}'
        targetRevision: '{{ .branch }}'
        path: manifests/overlays/dev
      destination:
        namespace: '{{ .repository }}-dev'
```

고른 리포지토리마다 이 template 에 값을 채워 `<리포지토리>-dev` Application 을 만든다.
이 방식은 **매니페스트가 앱 리포지토리 안에 있는** 구조를 전제로 한다(이 프로젝트의 기본 구조인
"gitops 리포지토리 분리" 와 다르다).

### 왜 기본 제외인가

`apps/app-of-apps.yaml:18`

```yaml
      # applicationset-gitlab.yaml 은 기본 제외. placeholder 토큰 Secret 이 함께 들어 있어
      # 포함하면 selfHeal 이 kubectl 로 넣은 실제 토큰을 계속 __REPLACE_ME__ 로 되돌린다.
```

같은 파일 끝에 `token: __REPLACE_ME__` 인 Secret 이 들어 있다. `bootstrap` 은 selfHeal 이 켜져 있으므로,
사람이 `kubectl` 로 실제 토큰을 넣어도 곧바로 Git 의 `__REPLACE_ME__` 로 되돌려 버린다. 3.2 의 selfHeal 이
"항상 Git 이 이긴다" 는 뜻임을 보여 주는 좋은 예다. 그래서 **비밀값은 Git 에 두지 않고** 쓰려면 Secret 을
파일에서 빼야 한다.

### 학습 노트: 명단을 손으로 쓰나, 규칙으로 뽑나 (2026-09-27)

비유: App of Apps 는 **본사가 손으로 쓴 지점장 명단**, ApplicationSet 은 **"조건에 맞는 사람은 자동 채용" 규칙**이다.
규칙에 맞는 리포지토리가 생기면 Application 이 생기고, 조건에서 벗어나면 Application 도 사라진다.

| | App of Apps (`apps/app-of-apps.yaml`) | ApplicationSet (`apps/applicationset-gitlab.yaml`) |
|---|---|---|
| Application 을 만드는 것 | 사람이 쓴 YAML 파일 | generator(규칙) + template(틀) |
| 앱 추가 | `apps/<앱>.yaml` 커밋 | 조건을 갖춘 리포지토리를 만들면 끝 |
| 매니페스트 위치 | gitops 리포지토리 (이 프로젝트 기본) | **각 앱 리포지토리 안** `manifests/overlays/dev` |
| 적합한 곳 | 앱 수가 적고, 앱마다 설정이 다를 때 | 같은 모양의 앱이 많을 때 (팀 서비스 수십 개) |

**줄 단위로 새로 본 것**
- `filters` (`:21-23`) — 한 항목 안의 조건은 **AND**(이름이 `svc-` 로 시작 **그리고** `.argocd.yaml`, `manifests` 둘 다 존재), 항목을 여러 개 쓰면 **OR**.
  `.argocd.yaml` 은 내용을 읽지 않는 **"참여 신청 표시"** 다. 파일만 있으면 된다.
- `{{ .repository }}`, `{{ .url }}`, `{{ .branch }}` — scmProvider 가 리포지토리마다 채워 주는 값. `.branch` 는 리포지토리의 **기본 브랜치**(보통 main)다.
  → 이 템플릿대로면 `-dev` Application 이 **main** 을 배포한다. 이 프로젝트의 "develop → dev, main → prod" 규칙과 다르다.
- `goTemplateOptions: ["missingkey=error"]` (`:10`) — 템플릿에 없는 키(오타)를 쓰면 빈 문자열로 넘어가지 않고 **오류**로 멈춘다. 빈 이름의 Application 이 생기는 사고를 막는다.

**켜더라도 걸리는 것 (3.3 의 문제가 또 나온다)**
- 만들어지는 네임스페이스는 `svc-foo-dev` 인데 AppProject dev 의 destinations(`apps/project.yaml:12-20`)는 `dev-*` 와 이름 세 개뿐 → **전부 거부된다.**
  `'*-dev'` 패턴을 추가하거나 네임스페이스 규칙을 맞춰야 한다.
- 이미지 태그를 누가 바꾸나: Jenkinsfile 은 gitops 리포지토리의 overlay 를 고친다. 이 구조에서는 앱 리포지토리의 `manifests/` 를 고치도록 파이프라인도 바뀌어야 한다.

**왜 기본 제외인가 — 한 줄 요약**: 같은 파일의 Secret(`:48-57`, `token: __REPLACE_ME__`)을 bootstrap 이 selfHeal 로 계속 되돌리기 때문. 쓰려면
① 파일에서 Secret 을 지우고 ② `kubectl` 로 Secret 을 따로 만들고 ③ `app-of-apps.yaml:21` 의 exclude 에서 이 파일을 뺀다. (README 2.4 의 Group access token, `read_api`·`read_repository`)

**확인 문제 (2026-09-27)** — 복습 문제 포함 모두 "모름". 답을 짧게 남긴다.

0. (3.7 복습) Synced / Degraded 면 배포 성공인가? → **아니다.** Synced = Git 대로 API 에 넣었다, Degraded = 파드가 제대로 안 떴다. 성공은 **Synced + Healthy**.
1. `svc-order` 에 `manifests/` 는 있고 `.argocd.yaml` 은 없다 → **안 생긴다.** filters 한 항목의 조건은 AND 라 하나라도 빠지면 탈락.
2. `.argocd.yaml` 추가 후 → 이름 `svc-order-dev`, 네임스페이스 `svc-order-dev`(template `:30`, `:41`).
   하지만 AppProject dev 의 destinations 에 없고 `dev-*` 에도 안 맞아 **거부 → 배포 안 됨.**
3. 서비스 3개, 서비스마다 syncPolicy 가 다름 → **App of Apps.** ApplicationSet 은 한 template 으로 똑같이 찍어 내는 도구라 "제각각" 에 약하다.
   (이 프로젝트가 App of Apps 를 기본으로 쓰는 이유이기도 하다.)

## 정리

| 개념 | 이 프로젝트에서 |
|---|---|
| Application | 앱 × 환경마다 하나. source = gitops 리포지토리의 overlay 경로, destination = `<앱>-<환경>` 네임스페이스 |
| syncPolicy | dev: automated + prune + selfHeal / prod: 수동(Jenkins 가 승인 후 `argocd app sync`) |
| AppProject | dev / prod 두 울타리. 허용 리포지토리·네임스페이스 제한, prod 는 syncWindows |
| App of Apps | `bootstrap` 하나만 apply → `apps/` 의 모든 Application 이 자동 생성 |
| 변경 감지 | webhook(즉시) + 폴링(180초) 병행 |
| 권한 | `cicd` 계정 = `role:ci` (get, sync 만). Jenkins 가 토큰으로 사용 |
| 알림 | GitLab commit status 템플릿이 있으나 구독 어노테이션이 없어 기본으로는 꺼진 상태 |
| ApplicationSet | 규칙으로 Application 자동 생성. 이 프로젝트에서는 선택, 기본 제외 |

## 확인 문제

1. `react-app-dev` Application 의 `metadata.namespace` 는 `argocd` 이고 `destination.namespace` 는 `react-app-dev` 이다. 두 값의 차이는?

   <details><summary>답</summary>

   `metadata.namespace` 는 Application 리소스 자체가 저장되는 곳(Argo CD 가 설치된 네임스페이스)이다.
   `destination.namespace` 는 그 Application 이 배포하는 앱의 Deployment, Service 등이 실제로 만들어지는 곳이다.
   </details>

2. dev 환경에서 누군가 `kubectl scale deploy/dev-react-app --replicas=5 -n react-app-dev` 를 실행했다. 어떻게 될까? prod 에서 같은 일을 하면?

   <details><summary>답</summary>

   selfHeal 때문에 Git 의 값(1)으로 되돌아간다고 생각하기 쉽지만, 5 가 유지된다. 이 프로젝트는
   `argocd-cm` 에서 Deployment 의 `/spec/replicas` 를 비교에서 제외(ignoreDifferences)했으므로 Argo CD 가
   이 차이를 보지 않는다. prod 는 애초에 automated 가 없고, 역시 replicas 는 비교 대상이 아니다. 게다가 prod 는 HPA 가
   replicas 를 관리하므로 HPA 가 다시 조절한다. (replicas 이외의 필드, 예를 들어 이미지를 바꿨다면 dev 는
   selfHeal 로 되돌아가고 prod 는 OutOfSync 로 표시만 된다.)
   </details>

3. 새 앱 `order-api` 를 추가하려 한다. Argo CD 쪽에서 해야 할 일은? 클러스터에 `kubectl apply` 가 필요한가?

   <details><summary>답</summary>

   gitops 리포지토리에 `manifests/order-api/overlays/{dev,prod}` 와 `apps/order-api.yaml`(Application 2개)을
   커밋한다. `bootstrap`(App of Apps)이 automated 이므로 Application 이 자동으로 생긴다. `kubectl apply` 는
   필요 없다. 다만 destination 네임스페이스 `order-api-dev` 가 AppProject `dev` 의 `destinations` 에 허용돼
   있어야 한다(`dev-*` 패턴에도 맞지 않으므로 `apps/project.yaml` 에 추가해야 한다).
   </details>

4. 토요일 새벽 2시에 Jenkins 가 prod 배포 승인 후 `argocd app sync react-app-prod` 를 실행했다. syncWindows 때문에 막힐까?

   <details><summary>답</summary>

   막히지 않는다. prod AppProject 의 allow 창은 평일 09~18시지만 `manualSync: true` 라 창 밖에서도 수동 sync 는
   허용된다. CLI 의 `argocd app sync` 는 수동 sync 다.
   </details>

5. GitLab webhook 설정을 빠뜨렸다. 배포는 안 되는가?

   <details><summary>답</summary>

   된다. Argo CD 가 `timeout.reconciliation: 180s` 주기로 폴링하므로 최대 3분 늦게 반영될 뿐이다.
   이 프로젝트에서는 Jenkins 가 `argocd app get --refresh` 로 즉시 확인을 요청하므로 실제 지연은 더 짧다.
   </details>
