# KindTisSkills

개인 Codex 전역 지침과 플러그인 소스를 Git으로 추적·관리하고, 필요한 플러그인을 선택 설치하는 마켓플레이스입니다.

## 구성

| 경로 | 내용 | 버전 |
| --- | --- | --- |
| [.agents/plugins/marketplace.json](.agents/plugins/marketplace.json) | `kindtis-skills` 마켓플레이스와 선택 설치 목록 | — |
| [AGENTS.md](AGENTS.md) | 전역 작업 지침 전체 사본 | 2026-11-11 기준 |
| [agents](agents) | Codex 커스텀 역할 설정(Planner·Architect·두 Reviewer·Implementer·Tester·Advisor·Art Director·Watchdog) | — |
| [archive](archive) | 플러그인에서 분리한 과거 기록(설치 대상 아님) | — |
| [plugins/goal-report](plugins/goal-report) | 구현 완료 보고서 스킬, 실행 코드, hook, 예시, 글꼴 | 1.4.9 |
| [plugins/improve](plugins/improve) | 명세 작성·검토·개선 및 확정 명세 기반 계획·구현 스킬, 참조 문서, 테스트 자료 | 3.0.0 |
| [plugins/jira](plugins/jira) | Jira 자연어 요청 진입점과 에픽·명세·개발·상태 관리 절차 | 2.0.0 |

## 마켓플레이스 등록과 설치

이 구성이 GitHub의 `main` 브랜치에 반영된 후 다음 명령으로 마켓플레이스를 등록합니다.

```powershell
codex plugin marketplace add https://github.com/KindTis/KindTisSkills.git --ref main
codex plugin list --marketplace kindtis-skills --available --json
```

목록에서 원하는 플러그인을 고른 뒤 해당 설치 명령을 실행합니다. 마켓플레이스를 등록하는 것만으로 세 플러그인이 모두 설치되지는 않습니다.

```powershell
codex plugin add goal-report@kindtis-skills
codex plugin add improve@kindtis-skills
codex plugin add jira@kindtis-skills
```

jira의 새 명세 작성·개선은 `brainstorming`의 요구 구체화 helper와 improve의 `spec`을 사용합니다. Planner가 문안을 작성하고 두 Reviewer가 독립 검토하며 실제 실행 주 에이전트가 저장·승인 소통을 조정합니다. 확정 명세 구현은 `jira:work → improve:implement → Architect`로 계획 작성을 한 번 위임합니다. 완성 명세 발급·상태 작업에는 작성자를 추가하지 않습니다.

improve를 쓰려면 [agents](agents)의 `planner`·`architect`·`planner_reviewer`·`programmer_reviewer`·`implementer`·`tester`·`advisor`가 등록돼 있어야 합니다. Planner·Architect·두 Reviewer는 `gpt-6-astra / xhigh / read-only`입니다. 역할 분담과 모델·추론 수준, Advisor 자문·위임 경계는 역할 설정과 [공통 계약](plugins/improve/references/delegation.md)을 따릅니다. 도움 스킬이 있으면 `writing-plans`는 Architect의 계획 구성·자체 검토에, `executing-plans`는 Implementer의 승인 작업에만 사용합니다. worktree를 지시받았으면 `using-git-worktrees`를 사용하며 단순화에는 사용 가능한 `ponytail-review`를 보조로 씁니다. 도움 스킬로 별도 집필·승인·실행 전환 절차를 추가하지 않습니다.

Jira 작업의 공통 진입점은 **`jira:manage`**입니다. 세부 스킬 이름을 지정하지 않고 다음처럼 요청할 수 있습니다.

| 요청 예시 | 담당 절차 |
| --- | --- |
| 이 명세를 Jira로 발행해 | `spec-ticket` + `status` |
| JIRA-1을 구체화해 | `specify` + `status` |
| 단일 이슈 JIRA-1을 구현해 | `work` + `status` |
| 이 요구를 에픽과 하위 이슈로 등록해 | `epic` + `status` |
| 에픽 JIRA-10을 실행해 | `epic` + `status` |
| JIRA-1 내용을 설명해 | 조회·설명 |

`manage`는 요청에 필요한 지침 조합만 선택하는 라우터입니다. 작업 에이전트가 선택된 업무 지침과 `status`의 상태·댓글 지침을 각각 적용합니다. 업무 스킬은 자기 작업을 수행하며 `manage`나 `status`를 호출하거나 보고하지 않습니다. 조회·설명은 Jira를 변경하지 않고, 발행·구체화 요청만으로 구현을 시작하지 않습니다.

명시적으로 실행할 때는 사용자가 필요한 스킬을 함께 지정합니다. 예: `$jira:specify $jira:status로 JIRA-1을 구체화해`. 업무 스킬만 지정한 요청에 `manage`나 `status`를 자동으로 추가하지 않습니다.

`epic`은 큰 작업의 분해·작업별 명세와 이슈 발급을 맡고, 실행 요청에는 의존성에 맞춰 작업 에이전트에게 `work`를 배정합니다. `work`는 배정된 단일 이슈의 명세 확인·구현·검증을 담당합니다. 준비·등록만 요청하면 그 범위를 유지하고, 기존 에픽 실행 요청에 다시 분해·등록을 강제하지 않습니다.

데스크톱 앱에서는 등록 후 앱을 다시 시작하고 플러그인 디렉터리에서 **KindTisSkills** 마켓플레이스를 선택해 원하는 플러그인을 설치합니다.

GitHub에 반영하기 전에는 이 저장소 루트에서 `codex plugin marketplace add .`로 로컬 구성을 확인할 수 있습니다. 로컬 등록과 GitHub 등록은 같은 마켓플레이스 이름을 사용하므로, GitHub 원본으로 전환할 때는 `codex plugin marketplace remove kindtis-skills`로 로컬 등록을 제거한 뒤 위의 GitHub 등록 명령을 실행합니다.

전역 `AGENTS.md`와 [agents](agents)의 역할 설정 적용은 별도로 관리합니다. 전역 지침은 위임을 지시하지 않으며, 역할은 스킬이 요구하거나 사용자가 지시할 때 사용합니다. Watchdog도 goal 감시가 필요할 때 직접 지시합니다. goal-report의 PC별 실행·배포 설정은 [REPORTING.md](plugins/goal-report/REPORTING.md)를 참고합니다. Jira 작업에는 사용 환경에서 제공하는 Jira 도구와 해당 서비스 접근 권한이 필요합니다.

### 역할 식별자 변경과 전역 전환

호환되는 변경 묶음은 새 역할 설정, improve `3.0.0`, jira `2.0.0`입니다. 기존 `planner`·`programmer` 리뷰어는 각각 `planner_reviewer`·`programmer_reviewer`로 바뀌며, `planner`는 새 명세 작성자이고 `architect`는 구현 계획 작성자입니다. 저장소 수정만으로 전역 역할이나 설치본이 바뀌지는 않습니다.

전역 적용을 요청받으면 기존 계약을 사용하는 작업과 전환 시점을 확인하고, 새 리뷰어·Architect를 준비한 뒤 두 플러그인과 전역 `planner`·`implementer`를 한 전환 작업으로 맞춥니다. 전역의 옛 `programmer`는 새 리뷰어로 전환합니다. 설치 캐시를 직접 편집하거나 Git 마켓플레이스를 로컬 등록으로 바꾸지 않습니다. 실제 설치된 두 스킬과 역할 설정이 일치하는지 대조하고 새 세션에서 역할 식별자·모델과 새 계약이 함께 제공되는지 확인한 뒤 사용합니다. 기존 세션이 자동 갱신됐다고 간주하지 않습니다.

일부만 적용된 상태에서는 관련 새 작업을 시작하지 않습니다. 필요하면 전환 전 Git 커밋의 소스로 역할·플러그인을 다시 적용해 맞춥니다. 소스 계약 확인과 전역 설치·새 세션 실제 호출 확인은 결과를 구분해 보고합니다.

## 플러그인 추가와 갱신

새 플러그인은 `plugins/<이름>/` 아래에 패키지를 넣고, `marketplace.json`의 `plugins` 배열에 이름과 `./plugins/<이름>` 상대 경로를 등록합니다. `source.path`는 저장소 루트를 기준으로 해석합니다. 각 항목에는 선택 설치 정책(`AVAILABLE`), 사용 시 인증 정책(`ON_USE`), 분류(`category`)를 함께 지정합니다.

기존 플러그인을 변경할 때는 해당 디렉터리와 `plugin.json` 버전을 함께 갱신해 GitHub에 반영합니다. Git 원본의 마켓플레이스는 다음 명령으로 갱신할 수 있습니다.

```powershell
codex plugin marketplace upgrade kindtis-skills
```

이 명령은 마켓플레이스 원본을 갱신합니다. 설치된 플러그인까지 자동 갱신하는 구성은 포함하지 않으며, 설치본에 새 버전을 적용하는 동작은 별도로 확인해야 합니다.

마켓플레이스 형식과 명령은 [OpenAI 플러그인 문서](https://developers.openai.com/plugins/build/plugins#marketplace-metadata)와 [CLI 문서](https://learn.chatgpt.com/docs/developer-commands#codex-plugin-marketplace)를 참고합니다.

## 원본과 갱신

2026-10-29에 다음 로컬 원본에서 복사했습니다. 세 플러그인은 같은 버전의 설치 캐시와 파일 내용이 일치하는 것을 확인했습니다.

- `AGENTS.md`: `~/.codex/AGENTS.md`
- `plugins/goal-report/`: `~/.codex/plugins/goal-report/1.4.8/`
- `plugins/improve/`: `~/.codex/plugins/improve/`
- `plugins/jira/`: `~/.codex/plugins/jira/`

각 플러그인의 `plugin.json`과 내부 디렉터리 구조를 유지했습니다. 이후 갱신할 때는 해당 디렉터리의 변경과 `plugin.json` 버전을 함께 검토합니다.

최초 복사 원본과 이 저장소는 자동으로 동기화되지 않습니다. 이후 이 저장소에서 관리하는 플러그인은 위 마켓플레이스에서 선택 설치하고, PC별 실행 환경은 별도로 관리합니다.

Python 캐시, 과거 버전 디렉터리, PC별 설정, 인증 정보, 실제 보고서와 실행 기록은 복사 대상에서 제외했습니다. goal-report에 포함된 글꼴의 이용 조건은 [글꼴 라이선스](plugins/goal-report/runtime/fonts/RIDIBatang-LICENSE.html)를 따릅니다.
