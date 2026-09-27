# 0. 전체 그림

> 학습 노트. 함께 공부하면서 나온 내용을 그때그때 덧붙인다.

## GitOps 의 뜻

- 클러스터에 무엇이 떠 있어야 하는지를 **Git 에 적어 두고**, 도구(Argo CD)가 클러스터를 Git 과 똑같이 맞춘다.
- 사람이 `kubectl apply` 를 직접 치지 않는다. **Git 커밋이 곧 배포**다.
- 이 프로젝트의 한 줄 요약: 코드를 push 하면 테스트 → 이미지 빌드 → 쿠버네티스 배포까지 자동으로 이어지는 환경을 PC(WSL2) 안에 통째로 만든다.

## 도구별 역할

| 도구 | 역할 | 비유 |
|---|---|---|
| kind | PC 안의 Docker 컨테이너로 만든 쿠버네티스 클러스터 | 공장 부지 |
| GitLab | 코드 저장소 (git 호스트) | 설계도 보관소 |
| Jenkins | **CI**: 테스트, 빌드, 이미지 만들기 | 생산 라인 |
| Nexus | 컨테이너 이미지 저장소 (레지스트리) | 완제품 창고 |
| Argo CD | **CD**: Git 내용대로 클러스터를 맞추기 | 설계도대로 진열하는 관리자 |

설치 순서는 클러스터(kind) → GitLab → Nexus → Argo CD → Jenkins → 파이프라인이다 (`README.md:100` 설치 순서 표).

## 리포지토리 2개로 나누는 이유

```
[앱 리포지토리]      react-app          소스 코드 + Jenkinsfile
[gitops 리포지토리]  gitops-manifests   무엇을 어떻게 띄울지 (apps/, manifests/)
```

- 앱 리포지토리는 개발자가 코드를 고치는 곳이다. gitops 리포지토리는 "지금 dev 와 prod 에 어떤 버전이 떠 있는가"를 기록하는 곳이다.
- 배포 이력이 gitops 리포지토리의 커밋으로 남는다. 그래서 **롤백은 이전 커밋으로 되돌리기(revert)** 한 번이면 된다.
- Jenkins 는 앱 리포지토리만 지켜본다. 그래서 gitops 에 커밋해도 빌드가 다시 도는 무한 루프가 생기지 않는다 (`README.md:2241`).

## push → 배포 흐름 7단계

출처: `ci/Jenkinsfile`

```
개발자: develop 브랜치에 push
   │
   ▼  Jenkins
① 준비          이미지 태그 만들기 → develop-a1b2c3d4 (브랜치 slug + 커밋 SHA 8자리)   Jenkinsfile:42
② 테스트·빌드    npm ci / npm test / npm run build                                   Jenkinsfile:70
③ 이미지 푸시    kaniko 로 빌드해서 Nexus 에 push                                      Jenkinsfile:84
④ gitops 갱신   gitops 리포지토리 clone → kustomize edit set image → 커밋 & push      Jenkinsfile:122
   │                                                   ← 여기가 CI 와 CD 의 경계
   ▼  Argo CD
⑤ gitops 리포지토리 변경 감지 (webhook, 없으면 180초 폴링)
⑥ 클러스터에 새 이미지로 배포
   │
   ▼  Jenkins
⑦ argocd app wait 로 Synced + Healthy 가 될 때까지 대기                               Jenkinsfile:155
```

- 핵심은 ④다. Jenkins 는 클러스터에 직접 배포하지 않는다. Git 에 "이 버전으로 바꿔 달라"고 적기만 하고, 실제 배포는 Argo CD 가 한다.
- develop, main 이 아닌 브랜치는 ② 테스트·빌드까지만 돌고 끝난다 (`Jenkinsfile:57-64`).

## dev 와 prod 의 차이

| | dev (`develop` 브랜치) | prod (`main` 브랜치) |
|---|---|---|
| 승인 | 없음 | Jenkins 에서 사람이 "배포" 버튼 클릭 (`ci/Jenkinsfile:112`) |
| Argo CD 동기화 | 자동: `syncPolicy.automated` (`apps/react-app.yaml:19-22`) | 수동: Jenkins 가 `argocd app sync` 실행 (`apps/react-app.yaml:52-53`) |
| 파드 수 | 1 | 3 + HPA(자동 확장) + PDB |
| 설정 위치 | `manifests/react-app/overlays/dev/` | `manifests/react-app/overlays/prod/` |

같은 `base/` 를 공유하고 환경별 차이만 `overlays/` 에 적는 방식이 **Kustomize** 다 (2장에서 자세히).

## 발견한 프로젝트 이슈

- ~~`manifests/react-app/overlays/dev/kustomization.yaml` 의 주석은 태그를 갱신하는 주체를 "GitLab CI" 라고 적었다.~~ → 2026-09-27 GitLab CI 선택지(부록)를 통째로 삭제하면서 "Jenkins" 로 고쳤다.
- 같은 파일의 초기 태그는 `dev-0000000` 이지만, Jenkins 가 만드는 태그는 `develop-<SHA 8자리>` 형식이다. 첫 배포 때 덮어써지므로 동작에는 문제가 없다.
