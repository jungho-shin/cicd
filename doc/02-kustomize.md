# 2. Kustomize

## 이 장의 목표

- 같은 앱을 dev / prod 에 조금씩 다르게 배포할 때 YAML 을 복사하지 않고 **공통(base) + 차이(overlay)** 로 나누는 방법을 안다.
- `kustomization.yaml` 의 필드(`namespace`, `namePrefix`, `replicas`, `images`, `patches`)를 읽을 수 있다.
- Jenkins 가 `kustomize edit set image` 로 무엇을 바꾸고, Argo CD 가 그 결과를 어떻게 쓰는지 안다.

## 2.1 base / overlay 구조를 쓰는 이유

### 학습 노트: 공통은 한 번, 차이만 따로 (2026-09-27)

**문제**: dev 와 prod 는 거의 같다. Deployment, Service, ServiceAccount 는 90% 가 같고 몇 줄만 다르다.
환경마다 YAML 을 통째로 복사하면, 공통 부분(예: probe 경로, 보안 설정)을 고칠 때 두 곳을 다 고쳐야 하고 한 곳을 빠뜨리기 쉽다.

**해결**: 공통은 `base/` 에 한 번만 적고, 환경별 차이만 `overlays/<환경>/` 에 적는다.
Kustomize 가 base 위에 overlay 를 덮어써서 최종 YAML 을 만든다.

비유: 공통 **양식지(base)** 한 장에, 부서마다 **포스트잇(overlay)** 으로 다른 칸만 덮어 붙이는 것.
양식지를 고치면 모든 부서에 반영되고, 포스트잇에는 "우리 부서만 다른 것" 만 남는다.

```
manifests/react-app/
├── base/                     ← 공통: 모든 환경에 똑같이
│   ├── kustomization.yaml    resources: deployment, service, serviceaccount
│   ├── deployment.yaml
│   ├── service.yaml
│   └── serviceaccount.yaml
└── overlays/
    ├── dev/                  ← dev 에서만 다른 것
    │   ├── kustomization.yaml    resources: ../../base + ingress.yaml
    │   └── ingress.yaml
    └── prod/                 ← prod 에서만 다른 것
        ├── kustomization.yaml    resources: ../../base + ingress, hpa, pdb
        ├── ingress.yaml
        ├── hpa.yaml
        └── pdb.yaml
```

overlay 는 `resources: - ../../base` 로 base 를 불러오고(`manifests/react-app/overlays/dev/kustomization.yaml:7-9`),
그 위에 자기 것을 더하거나 바꾼다.

**이 프로젝트에서 dev 와 prod 가 다른 것** (`overlays/dev/kustomization.yaml`, `overlays/prod/kustomization.yaml`)

| 항목 | dev | prod | 방식 |
|---|---|---|---|
| 네임스페이스 | `react-app-dev` | `react-app-prod` | `namespace` 필드 |
| 이름 앞머리 | `dev-` | `prod-` | `namePrefix` 필드 |
| Pod 수 | 1 | 3 | `replicas` 필드 |
| 이미지 태그 | `dev-0000000` (Jenkins 가 갱신) | `v0.1.0` | `images` 필드 |
| `APP_ENV` | `dev` | `prod` | `patches` (JSON patch) |
| CPU requests / limits | base 그대로 (100m / 500m) | 500m / 2 | `patches` |
| Ingress host | `react-app.dev.example.com` | `react-app.example.com` | overlay 에만 있는 파일 |
| HPA, PDB | 없음 | 있음 | overlay 에만 있는 파일 |

두 가지 방식으로 차이를 만든다.
1. **필드로 바꾸기**: 이미 base 에 있는 것의 값을 바꾼다(`namespace`, `replicas`, `images`, `patches`). → 2.2
2. **파일 더하기**: base 에 없는 리소스를 overlay 에서 추가한다(Ingress, HPA, PDB).

**왜 Ingress 는 base 에 없나**: host 가 환경마다 다르다. base 에 두면 어차피 양쪽에서 patch 해야 하므로 overlay 에 통째로 둔다.
HPA·PDB 는 prod 에만 필요하니 prod overlay 에만 있다.

**CI/CD 와의 연결** — 이 구조 덕분에 "환경 = 디렉터리 하나" 가 된다.

- Argo CD Application 은 환경별 overlay 디렉터리를 가리킨다: `apps/react-app.yaml:15` (`overlays/dev`), `:48` (`overlays/prod`).
- Jenkins 는 배포할 환경의 overlay 로 들어가 이미지 태그만 바꾼다: `ci/Jenkinsfile:136` (`cd .../overlays/${DEPLOY_ENV}`).
- 그래서 dev 배포가 prod 파일을 건드릴 일이 없고, "prod 에 무엇이 다른가" 는 prod overlay 만 보면 된다.

**base 는 혼자 배포하지 않는다**: base 에는 네임스페이스가 없고 이미지 태그도 `latest` 자리표시자다
(`manifests/react-app/base/kustomization.yaml:15-17`). 항상 overlay 를 통해서만 쓴다.

### 확인 문제 (2026-09-27)

1. 모든 환경의 `livenessProbe.periodSeconds` 를 20 → 30 으로 바꾸려면 어디를 고치나?
   → `manifests/react-app/base/deployment.yaml` 한 곳. overlay 가 이 값을 patch 하지 않으므로 dev·prod 모두 반영된다.
2. PDB 를 base 가 아니라 prod overlay 에 둔 이유는?
   → dev 는 replicas 1 인데 PDB 는 `minAvailable: 2`(`overlays/prod/pdb.yaml:6`) 라 처음부터 만족할 수 없다.
   이때 PDB 는 eviction 을 한 건도 허용하지 않으므로 dev Pod 가 있는 노드를 `kubectl drain` 하면 **영원히 멈춘다**(1.7).
   "환경에 따라 있어야 할지 말지가 다른 것" 은 overlay 에 둔다.
3. `kubectl apply -k manifests/react-app/base` 로 base 를 직접 배포하면?
   - `namespace` 가 없어 **현재 컨텍스트의 네임스페이스(보통 `default`)** 에 생긴다.
   - `namePrefix` 가 없어 이름이 `react-app` 그대로다. Ingress 도 없어 밖에서 접근할 수 없다.
   - 이미지 태그가 `latest` 다. **정정**: `latest` 는 "마지막으로 빌드된 이미지" 를 자동으로 뜻하지 않는다. 그냥 이름이 latest 인 태그일 뿐이다.
     이 프로젝트의 Jenkins 는 `<브랜치>-<sha8>` 과 커밋 SHA 태그만 push 하고 `latest` 는 push 하지 않는다
     (`ci/Jenkinsfile:101-104` 의 `--destination`). 그래서 `latest` 태그가 레지스트리에 없어 **ImagePullBackOff** 가 된다.
   - 게다가 `default` 네임스페이스에는 `regcred` Secret 이 없다(README 3.5 는 앱 네임스페이스에만 만든다). 태그가 있어도 pull 인증에 실패한다.
   - 결론: base 는 "혼자 배포하는 것" 이 아니라 overlay 가 불러 쓰는 부품이다.

## 2.2 `namespace`, `namePrefix`, `replicas`, `images`, `patches` 필드

### 학습 노트: 필드마다 base 의 무엇을 바꾸나 (2026-09-27)

기준: `manifests/react-app/overlays/dev/kustomization.yaml` 이 base 에 하는 일.

| 필드 | 적은 곳 | 바뀌는 곳 | base → dev 결과 |
|---|---|---|---|
| `namespace` | `:4` | 모든 리소스의 `metadata.namespace` | (없음) → `react-app-dev` |
| `namePrefix` | `:5` | 모든 리소스의 `metadata.name` + **이름으로 가리키는 참조** | `react-app` → `dev-react-app` |
| `replicas` | `:11-13` | 이름이 맞는 Deployment 의 `spec.replicas` | 2 → 1 |
| `images` | `:17-19` | 이미지 이름이 맞는 컨테이너의 태그 | `...react-app:latest` → `...react-app:dev-0000000` |
| `patches` | `:21-28` | `target` 에 맞는 리소스의 지정한 경로 | `APP_ENV=base` → `dev` |

**`namespace`** — 파일마다 `namespace:` 를 적지 않아도 overlay 한 줄로 전부 채운다. base 에 네임스페이스가 없는 이유가 이것이다.
예외 하나: RoleBinding 의 `subjects[].namespace` 는 **바꾸지 않는다**(기본 동작은 이름이 `default` 인 ServiceAccount 만 바꾼다).
`manifests/postgres/base/rbac.yaml:26-29` 의 주석이 이것을 확인한 흔적이다 — jenkins 네임스페이스의 `jenkins-agent` 는 그대로 남아야 한다.

**`namePrefix`** — 이름만 바꾸면 서로 가리키던 연결이 끊긴다. 그래서 Kustomize 는 **이름으로 가리키는 필드(nameReference)** 도 함께 바꾼다.

| 참조하는 곳 | 가리키는 것 | 바뀌나 |
|---|---|---|
| Deployment `serviceAccountName: react-app` | ServiceAccount | ✅ `dev-react-app` |
| Ingress `backend.service.name` | Service | ✅ (빌드 안에 있는 Service 이름과 같을 때) |
| HPA `scaleTargetRef.name` | Deployment | ✅ (같은 조건) |
| `imagePullSecrets: regcred` | Secret | ❌ `regcred` 는 이 빌드에 없는 리소스라 모른다 → 그대로 |
| 라벨 `app: react-app`, `selector` | (이름 아님) | ❌ 라벨은 이름이 아니다 |

- 규칙: **Kustomize 는 자기가 빌드하는 리소스만 안다.** 클러스터에 따로 만든 `regcred` 는 모르니 바꾸지 않는다 — 그래서 README 3.5 에서 `regcred` 라는 이름 그대로 만든다.
- 이 프로젝트는 Ingress backend(`overlays/dev/ingress.yaml:18`)와 HPA `scaleTargetRef`(`overlays/prod/hpa.yaml:9`)에
  prefix 붙은 이름을 **직접** 적었다. 결과는 같지만, 원래 이름 `react-app` 만 적어도 Kustomize 가 바꿔 준다. 실제로 그런지는 2.6 실습에서 확인.
- 라벨이 안 바뀌어도 괜찮은 이유: dev 와 prod 는 네임스페이스가 달라서 selector `app: react-app` 이 서로의 Pod 를 잡지 않는다(Service selector 는 자기 네임스페이스 안에서만 찾는다).

**`replicas`** — `name: react-app` 은 prefix 붙기 **전** 이름(base 에 적힌 이름)으로 찾는다. `patches` 의 `target.name` 도 마찬가지.
prod 에서는 HPA 가 이 값을 넘어서 늘리고, Argo CD 는 이 차이를 무시하도록 설정했다(1.7, 3장).

**`images`** — `name` 은 **태그를 뺀 이미지 이름**이다. 컨테이너 `image:` 에서 태그 앞부분이 이것과 같으면 `newTag` 로 바꾼다.
- `newTag` 말고도 `newName`(레지스트리·경로 교체), `digest`(태그 대신 `@sha256:...` 고정)가 있다.
- `name` 이 한 글자라도 다르면 **아무 오류 없이 안 바뀐다**. 조용히 `latest` 로 배포되는 사고가 난다(2.1 확인 문제 3 → ImagePullBackOff).
- Jenkins 가 갱신하는 것이 바로 이 `newTag` 값이다(2.5).

**`patches`** — 위 필드로 못 바꾸는 나머지를 바꾸는 범용 도구. 이 프로젝트는 **JSON Patch**(RFC 6902) 형식을 쓴다.

```yaml
patches:
  - target:            # 어느 리소스에
      kind: Deployment
      name: react-app
    patch: |-          # 무엇을
      - op: replace    # add / remove / replace ...
        path: /spec/template/spec/containers/0/env/0/value   # 경로 (숫자 = 배열 순번)
        value: dev
```

- `path` 는 YAML 을 위에서부터 따라 내려가는 주소다: `spec → template → spec → containers[0] → env[0] → value`.
- 약점: **순번(0)으로 가리킨다.** base 에서 `env` 맨 앞에 다른 변수를 끼워 넣으면 `env/0` 이 그 변수를 가리키게 되어 엉뚱한 값이 바뀐다. 오류도 나지 않는다.
- 다른 형식인 strategic merge patch(부분 YAML 을 겹쳐 쓰는 방식)는 컨테이너·env 를 **이름**으로 맞추므로 이 문제가 없다. 대신 길다.
- prod 는 같은 방식으로 CPU requests/limits 까지 바꾼다(`overlays/prod/kustomization.yaml:29-34`).

**base 에만 있는 필드** (`manifests/react-app/base/kustomization.yaml:9-13`)
- `labels` — 모든 리소스에 `app.kubernetes.io/name`, `app.kubernetes.io/managed-by` 라벨을 붙인다.
- `includeSelectors: false` — selector 에는 넣지 않는다. Deployment 의 selector 는 한 번 만들면 바꿀 수 없어서(immutable),
  나중에 라벨을 추가·변경할 때 selector 까지 바뀌면 배포가 실패하기 때문이다.

### 확인 문제 (2026-09-27)

1. `images.name` 을 `.../react-ap` 로 오타 내면?
   → `kubectl kustomize` 는 **오류 없이 성공**한다. `images` 는 "이 이름의 이미지가 있으면 바꿔라" 라는 규칙일 뿐이라,
   맞는 게 없으면 아무것도 안 하고 넘어간다. 결과 YAML 의 이미지는 base 의 `:latest` 그대로 → 배포하면 **ImagePullBackOff**
   (`latest` 는 push 된 적이 없다, 2.1). 빌드 단계에서 못 잡고 **배포하고 나서야** 드러나는 것이 무섭다.
   예방: 2.6 처럼 `kubectl kustomize ... | grep image:` 로 결과를 눈으로 확인한다.
2. base `env` 맨 앞에 `TZ=Asia/Seoul` 을 끼워 넣으면?

   ```yaml
   # base (바뀐 뒤)            # dev 결과 (patch: env/0/value → dev)
   env:                        env:
     - name: TZ                  - name: TZ
       value: Asia/Seoul           value: dev        ← 엉뚱한 변수가 바뀜
     - name: APP_ENV             - name: APP_ENV
       value: base                 value: base       ← 바뀌어야 할 것은 그대로
   ```

   → `TZ=dev`, `APP_ENV=base`. 오류는 없다. 앱은 자기가 dev 인 줄 모르고, 시간대 값은 잘못된다.
   비유: "왼쪽에서 첫 번째 사람 모자를 바꿔라" 라고 지시했는데 누가 맨 왼쪽에 끼어들면 엉뚱한 사람 모자가 바뀐다.
   "홍길동의 모자를 바꿔라"(이름으로 가리키기 = strategic merge patch)였다면 끼어들어도 문제없다.
   JSON Patch 의 `test` op(`- op: test, path: /spec/.../env/0/name, value: APP_ENV`)를 앞에 두면 순번이 어긋날 때 **빌드가 실패**해서 잡을 수 있다.
3. `regcred` 가 `dev-regcred` 로 안 바뀌는 이유와, 바뀌면?
   → Kustomize 는 **자기가 빌드하는 리소스의 이름만** 바꾸고, 그 리소스를 가리키는 참조만 따라 바꾼다.
   `regcred` 는 Git 에 없고 README 3.5 에서 `kubectl create secret` 로 클러스터에 직접 만든 Secret(비밀번호가 Git 에 들어가면 안 되니까)이라 Kustomize 목록에 없다.
   만약 `dev-regcred` 로 바뀐다면 네임스페이스에 그런 Secret 이 없으니 Nexus 로그인 정보 없이 pull → 인증 실패 → **ImagePullBackOff**.

## 2.3 파일 읽기: `manifests/react-app/base/kustomization.yaml`

### 학습 노트: base 가 하는 일은 세 가지뿐 (2026-09-27)

| 줄 | 필드 | 하는 일 |
|---|---|---|
| `:4-7` | `resources` | 부품 목록: deployment, service, serviceaccount |
| `:9-13` | `labels` | 공통 라벨 2개. selector 에는 안 넣음(2.2) |
| `:15-17` | `images` | 태그 `latest` — 자리표시자. overlay 가 항상 덮어쓴다 |

- **`resources` 에 없는 파일은 없는 것과 같다.** 디렉터리에 `configmap.yaml` 을 만들어 두기만 하고 목록에 안 넣으면
  빌드 결과에 안 나오고 오류도 없다(또 하나의 "조용한 실수"). 파일을 추가하면 목록에도 추가한다.
- 없는 것: `namespace`, `namePrefix`, `replicas` — 모두 환경마다 다른 값이라 overlay 몫이다.
- `images` 가 base 와 overlay 양쪽에 있으면 **overlay 가 이긴다**(나중에 적용된다). base 의 `latest` 는 "overlay 가 안 바꾸면 이 값" 인 기본값일 뿐.

## 2.4 파일 읽기: `overlays/dev/kustomization.yaml`, `overlays/prod/kustomization.yaml`

### 학습 노트: dev 와 prod 를 나란히 (2026-09-27)

| 줄 (dev / prod) | dev | prod | 비고 |
|---|---|---|---|
| `:4` / `:4` | `namespace: react-app-dev` | `react-app-prod` | |
| `:5` / `:5` | `namePrefix: dev-` | `prod-` | |
| `:7-9` / `:7-11` | base + ingress | base + ingress + **hpa + pdb** | prod 만 파일 2개 더 |
| `:11-13` / `:13-15` | replicas 1 | replicas 3 | prod 는 HPA `minReplicas: 3` 과 맞춘 값 |
| `:17-19` / `:17-19` | `newTag: dev-0000000` | `newTag: v0.1.0` | **둘 다 자리표시자** (아래) |
| `:21-28` / `:21-34` | patch 1개: `APP_ENV=dev` | patch 3개: `APP_ENV=prod`, CPU requests 500m, limits 2 | |

**태그 자리표시자와 실제 태그**
- Jenkins 는 `<브랜치 slug>-<SHA 8자리>` 로 태그를 만든다(`ci/Jenkinsfile:52-55`). develop → `develop-1a2b3c4d`, main → `main-1a2b3c4d`.
- 파일에 적힌 `dev-0000000`(7자리), `v0.1.0` 은 이 규칙과 다르다. 첫 파이프라인이 돌면 `kustomize edit set image` 가
  덮어쓰므로(`ci/Jenkinsfile:138`) 동작에는 문제없지만, **읽는 사람은 헷갈린다**. 그 전까지는 존재하지 않는 태그라 ImagePullBackOff.
- dev overlay 주석(`overlays/dev/kustomization.yaml:15-16`)은 "GitLab CI 가 갱신" 이라고 적혀 있었다. **정정**: 옛 설정이 아니라 README 부록 "GitLab CI 로 돌리기" 라는 대체 선택지의 흔적이었다. 2026-09-27 이 선택지(`ci/.gitlab-ci.yml`, `bootstrap/gitlab/runner.yaml`, README 부록, 관련 주석)를 삭제하고 주석을 "Jenkins" 로 고쳤다. GitLab 서버는 Git 저장소로 계속 쓴다.

**prod 의 patch 가 더 많은 이유** — prod 는 실제 사용자 트래픽을 받는다. CPU requests 를 500m 로 올려 스케줄러가 넉넉한 노드에
배치하게 하고, limits 2 코어로 순간 부하를 견딘다. HPA 는 requests 대비 사용률로 계산하므로(1.7), requests 를 바꾸면 **HPA 가 늘리는 시점도 바뀐다**.

### 확인 문제 (2026-09-27)

1. `configmap.yaml` 을 만들고 Deployment 에서 참조했지만 `resources` 에 안 넣었다면?
   - **빌드**: 성공한다. ConfigMap 은 결과에 없고, Deployment 의 참조 이름도 Kustomize 가 모르는 이름이라 prefix 없이 그대로 남는다(2.2 의 `regcred` 와 같은 원리).
   - **배포**: 클러스터에 그 ConfigMap 이 없으니 Pod 가 시작하지 못한다. 참조 방식에 따라 증상이 다르다.
     - env 로 참조(`configMapKeyRef`, `envFrom`) → `CreateContainerConfigError`
     - volume 으로 참조 → `ContainerCreating` 에서 멈춤, 이벤트에 `FailedMount`
   - 1장 실패 단계 표에 한 줄 추가: 이미지는 받았지만 **컨테이너를 만들 재료(설정·볼륨)가 없는** 단계.
2. HPA 목표 70%, 평균 사용량 400m 일 때 — **둘 다 늘린다.**
   - requests 100m: 400 ÷ 100 = **400%**. 원하는 Pod 수 = ⌈3 × 400/70⌉ = 18 → `maxReplicas: 10` 에 막혀 10.
   - requests 500m: 400 ÷ 500 = **80%**. ⌈3 × 80/70⌉ = ⌈3.43⌉ = **4**.
   - 같은 부하인데 requests 에 따라 3→10 과 3→4 로 반응 크기가 크게 다르다. requests 가 실제 사용량보다 너무 작으면 HPA 가 과민 반응한다.
3. develop 브랜치, SHA `a1b2c3d4e5f6...` → **`develop-a1b2c3d4`**.
   - 앞부분은 **환경 이름(dev)이 아니라 브랜치 이름의 slug**(`develop`)다(`ci/Jenkinsfile:52`).
   - SHA 는 **8자리**(`ci/Jenkinsfile:54` `take(8)`).
   - 파일의 `dev-0000000` 자리표시자가 오히려 착각을 부른다(2.4). README 의 Image Updater 정규식 `^dev-[0-9a-f]{7}$` 도 같은 착각으로 실제 태그와 안 맞는다(8장).

## 2.5 `kustomize edit set image` 가 파일의 무엇을 바꾸는지

### 학습 노트: 직접 돌려 본 결과 (2026-09-27, kustomize v5.8.1)

Jenkins 가 하는 일(`ci/Jenkinsfile:136-138`):

```bash
cd gitops/manifests/react-app/overlays/${DEPLOY_ENV}
kustomize edit set image "nexus-docker.example.com/my-group/react-app=${IMAGE}:${IMAGE_TAG}"
#                          └──── 왼쪽: 찾을 이름 (images[].name) ────┘ └ 오른쪽: 새 이미지 ┘
```

`IMAGE` 는 `nexus-docker.example.com/my-group/react-app`(`ci/Jenkinsfile:34`) 이라 왼쪽과 같다. 결국 **태그만** 바뀐다.
이 명령은 클러스터에 아무것도 하지 않는다. **파일(`kustomization.yaml`)만 고쳐 쓴다.** 배포는 이 파일이 커밋·push 된 뒤 Argo CD 가 한다.

overlay 복사본에서 `develop-a1b2c3d4` 로 실행한 결과:

```yaml
# 전                                          # 후
images:                                       images:
  - name: nexus-docker.example.com/...app     - name: nexus-docker.example.com/...app
    newTag: dev-0000000                         newName: nexus-docker.example.com/...app   ← 추가됨
                                                newTag: develop-a1b2c3d4                  ← 바뀜
```

`kubectl kustomize`(= `kustomize build`) 결과의 컨테이너: `image: nexus-docker.example.com/my-group/react-app:develop-a1b2c3d4`.

**직접 돌려 보고 알게 된 것**

1. **`newName` 이 함께 들어간다.** `이름=새이미지:태그` 형식은 오른쪽을 "새 이름 + 새 태그" 로 보고 둘 다 적는다.
   새 이름이 원래 이름과 같아 결과는 같다. 태그만 바꾸려면 `kustomize edit set image nexus-docker.example.com/my-group/react-app:develop-a1b2c3d4`
   (`=` 없이 `이름:태그`) 로 쓰면 `newTag` 만 바뀐다.
2. **파일 전체가 다시 쓰인다.** 들여쓰기(`  - ` → `- `)와 키 순서(`name`/`count` → `count`/`name`, `target`/`patch` → `patch`/`target`)가 바뀐다.
   주석은 남는다. 그래서 첫 Jenkins 커밋의 diff 는 태그 한 줄이 아니라 파일 대부분이 바뀐 것처럼 보인다. 두 번째 커밋부터는 태그 줄만 바뀐다.
3. **왼쪽 이름이 틀리면 조용히 항목을 하나 더 만든다.** `.../react-ap=...` 로 실행하면 기존 항목은 그대로 두고 `name: .../react-ap` 항목을 **추가**한다.
   오류가 없고, 새 항목은 어떤 컨테이너와도 이름이 안 맞으니 배포되는 이미지는 여전히 `dev-0000000`. 2.2 의 "조용한 실수" 와 같은 뿌리다.
   Jenkinsfile 주석(`ci/Jenkinsfile:137`) "왼쪽은 overlay 의 images[].name (고정 이름)" 이 이 경고다.

**왜 `sed` 로 태그 줄을 바꾸지 않고 이 명령을 쓰나**
- YAML 구조를 알고 고친다. 들여쓰기나 줄 위치가 바뀌어도 `images[].name` 으로 정확한 항목을 찾는다.
- 항목이 없으면 새로 만든다(초기 overlay 에 `images` 가 없어도 동작).

**뒤이어 일어나는 일** (`ci/Jenkinsfile:140-148`)
- `git diff --cached --quiet` 로 바뀐 게 없으면(같은 커밋을 다시 빌드) 커밋을 건너뛴다.
- 바뀌었으면 `chore(dev): react-app -> develop-a1b2c3d4` 로 커밋하고 gitops 리포지토리 main 에 push → Argo CD 가 감지(3장).
- 롤백은 이 커밋을 `git revert` 하면 태그가 이전 값으로 돌아간다. **이미지 태그의 이력 = gitops 리포지토리의 커밋 이력.**

### 확인 문제 (2026-09-27)

1. `edit set image` 직후, push 전에 Pod 는 바뀌었나?
   → **안 바뀐다.** 이 명령은 작업 공간의 파일만 고친다. 커밋을 해도 그 커밋은 Jenkins 에이전트 안의 로컬 clone 에만 있다.
   Argo CD 는 **GitLab 에 있는 gitops 리포지토리(원격)** 를 보므로 push 가 되어야 비로소 알 수 있다.
   (답안의 "빌드가 진행된다" 는 표현 정정: 여기서 일어나는 것은 빌드가 아니라 Argo CD 의 **동기화(배포)** 다. 빌드는 앱 리포지토리 push 로 Jenkins 가 한다.)
2. 같은 커밋으로 다시 빌드하면?
   → 태그가 같은 `develop-<같은 sha8>` 이라 `edit set image` 가 파일을 바꾸지 않는다 → `git diff --cached --quiet` 가 참 →
   "변경 없음 - 커밋 생략" 후 `exit 0`(`ci/Jenkinsfile:143-146`). gitops 커밋이 없으니 Argo CD 도 할 일이 없다.
   단, 그 앞 단계(테스트·이미지 빌드·push)는 다시 돈다. 같은 태그로 이미지를 한 번 더 push 한다.
3. dev 를 바로 전 버전으로 되돌리려면?
   → GitLab 의 **gitops 리포지토리**에서 `chore(dev): react-app -> develop-a1b2c3d4` 커밋을 revert 한다
   (앱 리포지토리가 아니다). `newTag` 가 이전 값으로 돌아가고, dev 는 자동 동기화라 Argo CD 가 곧 이전 이미지로 되돌린다.
   - 이미지를 다시 빌드하지 않는다. 이전 태그의 이미지는 Nexus 에 이미 있다 → 가장 빠른 롤백.
   - prod 는 자동 동기화가 없어서 revert 후 Argo CD 에서 sync 를 눌러야 한다(3장).
   - 주의: 앱 리포지토리의 버그는 그대로다. 다음에 develop 에 push 하면 새 태그로 다시 덮어쓴다. 앱 쪽 수정이 뒤따라야 한다.
   - 대안: 앱 리포지토리에서 revert → 파이프라인이 새 sha 로 다시 빌드·배포(roll forward). 느리지만 앱과 배포 이력이 맞는다.

## 2.6 실습: `kubectl kustomize manifests/react-app/overlays/dev` 로 결과 YAML 비교

### 학습 노트: 빌드 결과 비교 (2026-09-27, kustomize v5.8.1)

`kubectl kustomize <디렉터리>` 와 `kustomize build <디렉터리>` 는 같은 일을 한다. 클러스터 없이 **최종 YAML 을 화면에 찍기만** 한다.
Argo CD 도 Application 의 `path` 에서 이것과 똑같이 빌드한 결과를 클러스터에 적용한다.

```bash
kubectl kustomize manifests/react-app/overlays/dev  > /tmp/dev.yaml
kubectl kustomize manifests/react-app/overlays/prod > /tmp/prod.yaml
diff /tmp/dev.yaml /tmp/prod.yaml
kubectl kustomize manifests/react-app/overlays/dev | grep 'image:'     # 2.2 의 images 오타 확인용
```

**결과 요약** — dev 119줄(리소스 4개), prod 150줄(리소스 6개)

| 리소스 | dev | prod |
|---|---|---|
| ServiceAccount / Service / Deployment / Ingress | `dev-react-app` @ `react-app-dev` | `prod-react-app` @ `react-app-prod` |
| PodDisruptionBudget, HorizontalPodAutoscaler | 없음 | `prod-react-app` |

`diff` 로 나온 차이는 2.4 표와 정확히 같았다: 이름·네임스페이스, `replicas` 1/3, `APP_ENV`, 이미지 태그, CPU requests/limits,
`serviceAccountName`, Ingress host·backend, 그리고 prod 에만 있는 PDB·HPA. **표에 없는 차이는 하나도 없었다** → overlay 파일만 보면 환경 차이를 다 안다는 2.1 의 주장이 확인됨.

**결과에서 확인한 2.2 의 규칙들**
- 라벨 `app: react-app` 과 selector 는 dev/prod 모두 그대로다(prefix 가 붙지 않음).
- base 의 공통 라벨(`app.kubernetes.io/name`, `managed-by`)은 각 리소스의 `metadata.labels` 에만 있고 Pod template·selector 에는 없다(`includeSelectors: false`).
- `serviceAccountName` 은 `dev-react-app` 으로 바뀌었다(nameReference). `imagePullSecrets: regcred` 는 그대로.

**남겨 둔 질문의 답: Ingress 에 `react-app` 만 적어도 되나?** → **된다.**
복사본에서 `overlays/dev/ingress.yaml:18`, `overlays/prod/ingress.yaml:18`, `overlays/prod/hpa.yaml:9` 를 `name: react-app` 으로 바꾸고 빌드했더니
결과는 각각 `dev-react-app`, `prod-react-app` 으로 원래와 똑같았다. Kustomize 가 Ingress 의 backend Service 이름과 HPA 의 `scaleTargetRef` 를 따라 바꾼다.
- 그러니 지금처럼 prefix 붙은 이름을 직접 적은 것은 **없어도 되는 중복**이다. 원래 이름으로 적으면 `namePrefix` 를 바꿀 때 한 곳만 고치면 되고,
  base 파일처럼 dev/prod 파일 내용이 같아진다.
- 반대로 직접 적은 `dev-react-app` 은 빌드 안의 어떤 리소스 원래 이름과도 같지 않아 Kustomize 가 손대지 않는다 → `dev-dev-react-app` 같은 이중 prefix 는 생기지 않는다.

**Windows 에서 돌리는 법** (kubectl 이 없을 때): kustomize 릴리스(`kustomize_v5.x_windows_amd64.zip`)를 받아 `kustomize.exe build manifests\react-app\overlays\dev`.

### 2장 마무리 확인 문제 (2026-09-27)

1. `kubectl kustomize` vs `kubectl apply -k`
   → 앞의 것은 빌드 결과를 **화면(stdout)에 출력만** 한다(파일로 남기려면 `> dev.yaml` 로 리다이렉트). 뒤의 것은 같은 빌드를 한 뒤 **클러스터에 적용**한다.
   실습에서 앞의 것을 쓴 이유: 클러스터 없이, 아무것도 바꾸지 않고 결과를 미리 볼 수 있다. GitOps 에서는 적용을 Argo CD 가 하므로 사람은 주로 앞의 것만 쓴다.
2. 조용히 틀리는 경우와 빌드 결과에서 잡는 곳
   - `images.name` 오타 / `edit set image` 왼쪽 이름 오타 → `kubectl kustomize ... | grep image:` 에 기대한 태그가 아니라 `:latest` 나 옛 태그가 보인다.
   - 파일을 만들고 `resources` 에 안 넣음 → `kubectl kustomize ... | grep '^kind:'` 목록에 그 리소스(예: ConfigMap)가 없다.
   - JSON Patch 순번 어긋남 → `grep -A1 'name: APP_ENV'` 로 값이 dev/prod 인지 본다.
   (답안은 증상 ImagePullBackOff 를 들었는데, 이 문제의 요점은 **배포 전에 빌드 결과에서** 잡는 것이다.)
3. Argo CD Application 의 `path` 를 `manifests/react-app/base` 로 잘못 적으면? — 2.1 Q3(kubectl 로 base 배포)과 **다른 점이 있다.**
   - 네임스페이스: base 에 없지만 Application 의 `destination.namespace: react-app-dev`(`apps/react-app.yaml:18`)가 채운다 → `default` 가 아니라 `react-app-dev` 에 생긴다.
   - 이름: prefix 가 없어 `react-app`. 이미지: `:latest` → push 된 적 없는 태그라 **ImagePullBackOff**. (여기에는 `regcred` 가 있으니 인증 문제는 아니다.)
   - **더 큰 문제**: `prune: true`(`apps/react-app.yaml:21`) 라서 Git 에 더는 없는 기존 `dev-react-app` Deployment·Service·Ingress 를 **지운다.**
     잘 돌던 dev 가 사라지고, 새로 뜬 `react-app` 은 이미지를 못 받는다 → dev 서비스 중단. Argo CD 화면은 Synced 이지만 Health 는 Degraded.
   - 답안의 "이미지가 없다는 오류" 는 맞는 증상 하나. prune 으로 기존 리소스가 지워지는 것까지 봐야 한다(3장 syncPolicy 에서 자세히).
